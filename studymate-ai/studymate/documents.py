"""업로드 자료 → 텍스트 추출 → 개인정보 마스킹 → 청크 분할."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

SUPPORTED_EXTENSIONS = (".pdf", ".txt", ".md")
CHUNK_SIZE = 800  # 글자 수 (한국어 기준 약 400~600 토큰)
CHUNK_OVERLAP = 150  # 문맥이 끊기지 않도록 앞 청크 끝부분을 겹쳐 넣는다
MIN_CHUNK_CHARS = 20


class DocumentError(Exception):
    """사용자에게 그대로 보여줄 수 있는 자료 처리 오류."""


@dataclass(frozen=True)
class Chunk:
    index: int
    page: int | None
    content: str


# ---------- 개인정보 마스킹 ----------
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b\d{6}\s?-\s?[1-4]\d{6}\b"), "[주민번호]"),
    (re.compile(r"\b01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}\b"), "[전화번호]"),
    (re.compile(r"\b0\d{1,2}[-.\s]\d{3,4}[-.\s]\d{4}\b"), "[전화번호]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[이메일]"),
]


def mask_pii(text: str) -> str:
    """주민등록번호·휴대폰/유선 전화번호·이메일 패턴을 마스킹한다."""
    for pattern, repl in _PII_PATTERNS:
        text = pattern.sub(repl, text)
    return text


# ---------- 텍스트 추출 ----------
def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_text(filename: str, data: bytes) -> list[tuple[int | None, str]]:
    """(페이지 번호, 텍스트) 목록을 돌려준다. TXT/MD는 페이지 번호가 None."""
    name = filename.lower()
    if not name.endswith(SUPPORTED_EXTENSIONS):
        raise DocumentError("PDF, TXT, MD 파일만 올릴 수 있어요.")
    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            pages = [(i + 1, _normalize(p.extract_text() or "")) for i, p in enumerate(reader.pages)]
        except Exception as exc:
            raise DocumentError(f"PDF를 읽지 못했어요. 암호가 걸려 있거나 손상된 파일일 수 있어요. ({exc.__class__.__name__})") from exc
    else:
        pages = [(None, _normalize(_decode(data)))]
    pages = [(p, t) for p, t in pages if t]
    if not pages:
        raise DocumentError("텍스트를 찾지 못했어요. 스캔한 이미지 PDF는 지원하지 않아요(텍스트 PDF로 변환해 주세요).")
    return pages


# ---------- 청크 분할 ----------
def _split_units(text: str, chunk_size: int) -> list[str]:
    parts = re.split(r"(?<=[.!?。])\s+|\n+", text)
    units: list[str] = []
    for part in parts:
        part = part.strip()
        while len(part) > chunk_size:  # 아주 긴 문장은 강제로 자른다
            units.append(part[:chunk_size])
            part = part[chunk_size:]
        if part:
            units.append(part)
    return units


def _overlap_tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    space = tail.find(" ")
    return tail[space + 1:] if space != -1 else tail


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    chunks: list[str] = []
    current = ""
    for unit in _split_units(text, chunk_size):
        if current and len(current) + 1 + len(unit) > chunk_size:
            chunks.append(current)
            tail = _overlap_tail(current, overlap)
            current = f"{tail} {unit}".strip() if tail else unit
        else:
            current = f"{current} {unit}".strip() if current else unit
    if current:
        chunks.append(current)
    return [c for c in chunks if len(c.replace(" ", "")) >= MIN_CHUNK_CHARS]


def chunk_pages(
    pages: list[tuple[int | None, str]], chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[Chunk]:
    """페이지 단위로 청크를 만들어 출처(페이지)를 정확히 남긴다."""
    result: list[Chunk] = []
    for page, text in pages:
        for piece in chunk_text(text, chunk_size, overlap):
            result.append(Chunk(index=len(result), page=page, content=piece))
    return result
