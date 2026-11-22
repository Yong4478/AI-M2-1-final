from pathlib import Path

import numpy as np
import pytest

from studymate import rag
from studymate.documents import DocumentError

from .conftest import SAMPLE_TEXT

SAMPLE_FILE = Path(__file__).resolve().parents[1] / "sample_data" / "sample_ai_basics.md"


def test_ingest_and_search_finds_relevant_chunk(db, user, fake_llm):
    info = rag.ingest_document(db, fake_llm, user.id, "notes.txt", SAMPLE_TEXT.encode())
    assert info.num_chunks >= 1
    hits = rag.search(db, fake_llm, user.id, "코사인 유사도로 벡터 비교", top_k=2, min_score=0.0)
    assert hits and "코사인 유사도" in hits[0].content
    assert hits[0].score >= hits[-1].score


def test_sample_file_retrieval_quality(db, user, fake_llm):
    rag.ingest_document(db, fake_llm, user.id, "sample.md", SAMPLE_FILE.read_bytes())
    cases = {
        "함수 호출은 무엇인가": "함수 호출",
        "프롬프트 인젝션 공격": "프롬프트 인젝션",
        "컨텍스트 윈도우 최대 토큰": "컨텍스트 윈도우",
    }
    for query, expected in cases.items():
        hits = rag.search(db, fake_llm, user.id, query, top_k=3, min_score=0.0)
        assert any(expected in h.content for h in hits), query


def test_search_isolated_per_user(db, fake_llm):
    from studymate.auth import register

    a = register(db, "유저A", "1234", True)
    b = register(db, "유저B", "1234", True)
    rag.ingest_document(db, fake_llm, a.id, "a.txt", SAMPLE_TEXT.encode())
    assert rag.search(db, fake_llm, b.id, "임베딩", min_score=0.0) == []


def test_pii_is_masked_before_storage(db, user, fake_llm):
    text = "담당 조교 연락처는 010-9876-5432 입니다. 임베딩 과제는 금요일까지 제출합니다. " * 3
    rag.ingest_document(db, fake_llm, user.id, "notice.txt", text.encode())
    stored = " ".join(r["content"] for r in db.query("SELECT content FROM chunks"))
    assert "010-9876-5432" not in stored and "[전화번호]" in stored


def test_limits_duplicates_and_delete(db, user, fake_llm):
    rag.ingest_document(db, fake_llm, user.id, "one.txt", SAMPLE_TEXT.encode(), max_documents=2)
    with pytest.raises(DocumentError):
        rag.ingest_document(db, fake_llm, user.id, "one.txt", SAMPLE_TEXT.encode(), max_documents=2)
    rag.ingest_document(db, fake_llm, user.id, "two.txt", SAMPLE_TEXT.encode(), max_documents=2)
    with pytest.raises(DocumentError):
        rag.ingest_document(db, fake_llm, user.id, "three.txt", SAMPLE_TEXT.encode(), max_documents=2)
    with pytest.raises(DocumentError):
        rag.ingest_document(db, fake_llm, user.id, "long.txt", ("긴 문장입니다. " * 3000).encode(), max_documents=5, max_chunks=10)

    docs = rag.list_documents(db, user.id)
    rag.search(db, fake_llm, user.id, "임베딩", min_score=0.0)  # 캐시 적재
    rag.delete_document(db, user.id, docs[0].id)
    remaining = rag.search(db, fake_llm, user.id, "임베딩", top_k=10, min_score=0.0)
    assert all(h.document_id == docs[1].id for h in remaining)


def test_document_filter_and_sampling(db, user, fake_llm):
    d1 = rag.ingest_document(db, fake_llm, user.id, "a.txt", SAMPLE_TEXT.encode())
    rag.ingest_document(db, fake_llm, user.id, "b.md", SAMPLE_FILE.read_bytes())
    hits = rag.search(db, fake_llm, user.id, "임베딩", top_k=5, min_score=0.0, document_id=d1.id)
    assert hits and all(h.document_id == d1.id for h in hits)
    sampled = rag.sample_chunks(db, user.id, n=3)
    assert len(sampled) == 3


def test_vector_roundtrip_and_context_format():
    vec = np.random.rand(8).astype(np.float32)
    assert np.allclose(rag.decode_vector(rag.encode_vector(vec)), vec)
    hit = rag.SearchHit("c", "d", "강의.pdf", 3, 0, "내용", 0.9)
    assert rag.format_context([hit]) == "[S1] (강의.pdf p.3)\n내용"


def test_embedding_dimension_mismatch(db, user, fake_llm):
    rag.ingest_document(db, fake_llm, user.id, "a.txt", SAMPLE_TEXT.encode())

    class OtherModel:
        def embed(self, texts):
            return np.ones((len(texts), 10), dtype=np.float32)

    with pytest.raises(DocumentError):
        rag.search(db, OtherModel(), user.id, "임베딩")


def test_eval_script_runs_in_fake_mode():
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, str(root / "scripts" / "eval_rag.py"), "--fake"], capture_output=True, text=True, check=True)
    assert "Hit@4" in out.stdout and "MRR" in out.stdout
