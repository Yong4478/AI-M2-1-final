import pytest

from studymate.documents import CHUNK_SIZE, DocumentError, chunk_pages, chunk_text, extract_text, mask_pii

from .conftest import make_pdf


def test_mask_pii_masks_phone_email_rrn():
    text = "연락처 010-1234-5678, 메일 hong@example.com, 주민번호 900101-1234567, 사무실 02-123-4567"
    masked = mask_pii(text)
    assert "010-1234-5678" not in masked and "[전화번호]" in masked
    assert "hong@example.com" not in masked and "[이메일]" in masked
    assert "900101-1234567" not in masked and "[주민번호]" in masked
    assert "02-123-4567" not in masked


def test_mask_pii_keeps_normal_numbers():
    text = "2024년 3월 15일, 정답률 85%, 버전 1.2.3"
    assert mask_pii(text) == text


def test_chunk_text_respects_size_and_overlap():
    sentences = [f"이것은 {i}번째 문장으로 청크 분할을 시험하기 위한 내용이다." for i in range(80)]
    chunks = chunk_text(" ".join(sentences))
    assert len(chunks) > 3
    assert all(len(c) <= CHUNK_SIZE + 160 for c in chunks)
    # 겹침: 다음 청크의 시작 일부가 앞 청크 끝에 들어 있어야 한다
    assert chunks[0][-40:].split(" ")[-1] in chunks[1]


def test_chunk_text_splits_very_long_sentence():
    chunks = chunk_text("가" * 2000)
    assert len(chunks) >= 3


def test_chunk_pages_keeps_page_numbers():
    pages = [(1, "첫 페이지 내용입니다. " * 10), (2, "두 번째 페이지 내용입니다. " * 10)]
    chunks = chunk_pages(pages)
    assert {c.page for c in chunks} == {1, 2}
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_extract_text_txt_utf8_and_cp949():
    assert extract_text("a.txt", "안녕하세요 RAG".encode("utf-8"))[0][1] == "안녕하세요 RAG"
    assert extract_text("b.md", "윈도우 메모장 파일".encode("cp949"))[0][1] == "윈도우 메모장 파일"


def test_extract_text_pdf_pages():
    pdf = make_pdf(["Embedding vectors measure similarity.", "Retrieval augmented generation."])
    pages = extract_text("lecture.pdf", pdf)
    assert [p for p, _ in pages] == [1, 2]
    assert "Embedding" in pages[0][1]


def test_extract_text_rejects_unsupported_and_empty():
    with pytest.raises(DocumentError):
        extract_text("slides.pptx", b"x")
    with pytest.raises(DocumentError):
        extract_text("empty.txt", b"   \n  ")
    with pytest.raises(DocumentError):
        extract_text("broken.pdf", b"not a pdf")
