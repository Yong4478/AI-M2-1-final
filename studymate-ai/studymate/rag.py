"""RAG(검색 증강 생성) 파이프라인: 자료 등록(임베딩 저장)과 의미 검색.

벡터 DB를 따로 두지 않고 임베딩을 DB(TEXT, base64 float32)에 저장한 뒤
사용자별로 메모리에 올려 numpy 내적(코사인 유사도)으로 검색한다.
학습자 1명당 자료 수십 개(청크 수천 개) 규모에서는 이 방식이 가장 단순하고 빠르다.
"""
from __future__ import annotations

import base64
import random
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import numpy as np

from .db import Database, new_id, utcnow
from .documents import CHUNK_OVERLAP, CHUNK_SIZE, DocumentError, chunk_pages, extract_text, mask_pii


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    document_id: str
    filename: str
    page: int | None
    chunk_index: int
    content: str
    score: float

    def location(self) -> str:
        return f"{self.filename} p.{self.page}" if self.page else self.filename


@dataclass(frozen=True)
class DocumentInfo:
    id: str
    filename: str
    num_chunks: int
    num_chars: int
    created_at: str


# ---------- 임베딩 직렬화 ----------
def encode_vector(vec: np.ndarray) -> str:
    return base64.b64encode(np.asarray(vec, dtype=np.float32).tobytes()).decode("ascii")


def decode_vector(text: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(text), dtype=np.float32)


# ---------- 사용자별 인덱스 캐시 ----------
_CACHE: "OrderedDict[str, tuple[list[dict[str, Any]], np.ndarray]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_MAX_USERS = 50


def invalidate(user_id: str) -> None:
    with _CACHE_LOCK:
        _CACHE.pop(user_id, None)


def _load_index(db: Database, user_id: str) -> tuple[list[dict[str, Any]], np.ndarray]:
    with _CACHE_LOCK:
        if user_id in _CACHE:
            _CACHE.move_to_end(user_id)
            return _CACHE[user_id]
    rows = db.query(
        """SELECT c.id, c.document_id, c.chunk_index, c.page, c.content, c.embedding, d.filename
           FROM chunks c JOIN documents d ON d.id = c.document_id
           WHERE c.user_id = ? ORDER BY d.created_at, c.chunk_index""",
        (user_id,),
    )
    metas = [{k: r[k] for k in ("id", "document_id", "chunk_index", "page", "content", "filename")} for r in rows]
    matrix = np.vstack([decode_vector(r["embedding"]) for r in rows]) if rows else np.zeros((0, 0), np.float32)
    with _CACHE_LOCK:
        _CACHE[user_id] = (metas, matrix)
        while len(_CACHE) > _CACHE_MAX_USERS:
            _CACHE.popitem(last=False)
    return metas, matrix


# ---------- 자료 관리 ----------
def list_documents(db: Database, user_id: str) -> list[DocumentInfo]:
    rows = db.query(
        "SELECT id, filename, num_chunks, num_chars, created_at FROM documents WHERE user_id = ? ORDER BY created_at",
        (user_id,),
    )
    return [DocumentInfo(**r) for r in rows]


def delete_document(db: Database, user_id: str, document_id: str) -> None:
    db.execute("DELETE FROM chunks WHERE user_id = ? AND document_id = ?", (user_id, document_id))
    db.execute("DELETE FROM documents WHERE user_id = ? AND id = ?", (user_id, document_id))
    invalidate(user_id)


def ingest_document(
    db: Database,
    llm: Any,
    user_id: str,
    filename: str,
    data: bytes,
    *,
    max_documents: int = 10,
    max_chunks: int = 400,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> DocumentInfo:
    """파일 → 텍스트 추출 → 개인정보 마스킹 → 청크 → 임베딩 → 저장."""
    existing = list_documents(db, user_id)
    if len(existing) >= max_documents:
        raise DocumentError(f"자료는 최대 {max_documents}개까지 올릴 수 있어요. 안 쓰는 자료를 지운 뒤 올려 주세요.")
    if any(d.filename == filename for d in existing):
        raise DocumentError(f"'{filename}'은(는) 이미 등록돼 있어요.")

    pages = [(page, mask_pii(text)) for page, text in extract_text(filename, data)]
    chunks = chunk_pages(pages, chunk_size, overlap)
    if not chunks:
        raise DocumentError("공부에 쓸 만한 텍스트가 너무 적어요.")
    if len(chunks) > max_chunks:
        raise DocumentError(
            f"자료가 너무 길어요(조각 {len(chunks)}개). {max_chunks}개 이하가 되도록 파일을 나눠서 올려 주세요."
        )

    vectors = llm.embed([c.content for c in chunks])
    doc_id = new_id()
    now = utcnow()
    num_chars = sum(len(c.content) for c in chunks)
    db.execute(
        "INSERT INTO documents (id, user_id, filename, num_chunks, num_chars, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, user_id, filename, len(chunks), num_chars, now),
    )
    try:
        db.executemany(
            "INSERT INTO chunks (id, document_id, user_id, chunk_index, page, content, embedding) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (new_id(), doc_id, user_id, c.index, c.page, c.content, encode_vector(vectors[i]))
                for i, c in enumerate(chunks)
            ],
        )
    except Exception:
        db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        raise
    invalidate(user_id)
    return DocumentInfo(doc_id, filename, len(chunks), num_chars, now)


# ---------- 검색 ----------
def search(
    db: Database,
    llm: Any,
    user_id: str,
    query: str,
    top_k: int = 4,
    min_score: float = 0.2,
    document_id: str | None = None,
) -> list[SearchHit]:
    metas, matrix = _load_index(db, user_id)
    if not metas or not query.strip():
        return []
    q = llm.embed([query])[0]
    if matrix.shape[1] != q.shape[0]:
        raise DocumentError("임베딩 모델이 바뀌어 기존 자료를 검색할 수 없어요. 자료를 지우고 다시 올려 주세요.")
    scores = matrix @ q
    if document_id:
        mask = np.array([m["document_id"] == document_id for m in metas])
        scores = np.where(mask, scores, -1.0)
    order = np.argsort(-scores)[: max(1, top_k)]
    hits: list[SearchHit] = []
    for i in order:
        score = float(scores[i])
        if score < min_score:
            continue
        m = metas[int(i)]
        hits.append(
            SearchHit(m["id"], m["document_id"], m["filename"], m["page"], m["chunk_index"], m["content"], round(score, 3))
        )
    return hits


def sample_chunks(
    db: Database, user_id: str, n: int = 6, document_id: str | None = None, rng: random.Random | None = None
) -> list[SearchHit]:
    """주제 없이 퀴즈를 낼 때 자료 전체에서 고르게 청크를 뽑는다."""
    metas, _ = _load_index(db, user_id)
    pool = [m for m in metas if not document_id or m["document_id"] == document_id]
    if not pool:
        return []
    rng = rng or random.Random()
    picked = rng.sample(pool, min(n, len(pool)))
    picked.sort(key=lambda m: (m["filename"], m["chunk_index"]))
    return [
        SearchHit(m["id"], m["document_id"], m["filename"], m["page"], m["chunk_index"], m["content"], 1.0)
        for m in picked
    ]


def format_context(hits: list[SearchHit], start: int = 1) -> str:
    """LLM 프롬프트에 넣을 [S1] 형식의 근거 블록."""
    return "\n\n".join(f"[S{start + i}] ({h.location()})\n{h.content}" for i, h in enumerate(hits))
