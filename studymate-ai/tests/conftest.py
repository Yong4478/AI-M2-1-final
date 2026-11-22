"""테스트 공용 도구: 실제 API를 부르지 않는 가짜 LLM과 임시 DB."""
from __future__ import annotations

import hashlib
import json
import re
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np
import pytest

from studymate import rag
from studymate.db import Database
from studymate.llm import normalize_rows

DIM = 256


def fake_embed_one(text: str) -> np.ndarray:
    """글자 2-gram 해시 벡터: 같은 단어를 공유할수록 코사인 유사도가 높아진다."""
    vec = np.zeros(DIM, dtype=np.float32)
    compact = re.sub(r"\s+", "", text.lower())
    for i in range(len(compact) - 1):
        h = int(hashlib.md5(compact[i:i + 2].encode()).hexdigest(), 16)
        vec[h % DIM] += 1.0
    return vec


def tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(id=call_id, type="function", function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)))


def reply(content: str | None = None, tool_calls: list[SimpleNamespace] | None = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls)


class FakeLLM:
    """chat()은 미리 정한 응답(또는 함수)을 차례로 돌려주고, 호출 내역을 기록한다."""

    def __init__(self, script: list[Any] | None = None):
        self.script = list(script or [])
        self.calls: list[dict[str, Any]] = []
        self.embed_calls = 0

    def chat(self, messages, tools=None, tool_choice=None, json_mode=False):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools, "tool_choice": tool_choice, "json_mode": json_mode})
        if not self.script:
            return reply("기본 답변")
        item = self.script.pop(0)
        return item(messages) if callable(item) else item

    def embed(self, texts):
        self.embed_calls += 1
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        return normalize_rows(np.vstack([fake_embed_one(t) for t in texts]))


@pytest.fixture()
def db(tmp_path) -> Database:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    database.init_schema()
    yield database
    rag._CACHE.clear()


@pytest.fixture()
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture()
def user(db):
    from studymate.auth import register

    return register(db, "테스터1", "1234", consent=True)


SAMPLE_TEXT = (
    "임베딩은 문장의 의미를 숫자 벡터로 바꾼 것이다. 코사인 유사도로 두 벡터가 얼마나 비슷한지 계산한다.\n\n"
    "RAG는 검색 증강 생성으로, 답하기 전에 관련 자료를 검색해 프롬프트에 넣는다. 환각을 줄이고 출처를 표시할 수 있다.\n\n"
    "AI 에이전트는 도구를 사용해 목표를 달성한다. 함수 호출 기능으로 도구 이름과 인자를 JSON으로 출력한다.\n\n"
    "장기 기억은 사용자의 선호와 목표를 저장했다가 다음 대화에서 다시 불러오는 방식이다."
)


def make_pdf(pages: list[str]) -> bytes:
    """ASCII 텍스트만 담은 최소 PDF (테스트용)."""
    objects: list[bytes] = []
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    font_id = 3 + len(pages) * 2
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    for i, text in enumerate(pages):
        content_id = 4 + i * 2
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


Script = list[Callable[[list[dict[str, Any]]], Any] | SimpleNamespace]
