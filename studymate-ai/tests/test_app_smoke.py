"""app.py 화면 흐름 스모크 테스트.

실제 Streamlit 대신 가짜 모듈을 끼워 넣고 화면 코드를 끝까지 실행해,
가입 → 샘플 자료 등록 → 질문 → 퀴즈 → 학습 기록 → 피드백 → 관리자 → 데이터 삭제 흐름에서
코드 오류(오타, 잘못된 인자, 누락된 함수 등)가 없는지 확인한다.
"""
from __future__ import annotations

import json
import runpy
import sys
import types
from pathlib import Path

import pytest

from .conftest import FakeLLM, reply, tool_call

APP = Path(__file__).resolve().parents[1] / "app.py"


class Rerun(Exception):
    pass


class Stop(Exception):
    pass


class SessionState(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class Element:
    def __init__(self, st):
        self._st = st

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __getattr__(self, name):
        return getattr(self._st, name)


class FakeStreamlit(types.ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.session_state = SessionState()
        self.secrets = {}
        self.script: dict = {}
        self.calls: list = []
        self.sidebar = Element(self)

    def _get(self, kind, label, default=None):
        return self.script.get(kind, {}).get(label, default)

    def cache_resource(self, fn):
        return fn

    def columns(self, spec, **kwargs):
        return [Element(self) for _ in range(spec if isinstance(spec, int) else len(spec))]

    def tabs(self, labels):
        return [Element(self) for _ in labels]

    def form(self, *a, **k):
        self.calls.append(("container", a))
        return Element(self)

    expander = container = chat_message = spinner = form

    def button(self, label, key=None, on_click=None, args=(), kwargs=None, **k):
        clicked = self._get("button", label, False)
        if clicked and on_click:
            on_click(*args, **(kwargs or {}))
        return clicked

    def form_submit_button(self, label, **k):
        return self._get("form_submit_button", label, False)

    def radio(self, label, options, index=0, key=None, format_func=str, **k):
        options = list(options)
        if key and key in self.session_state and self.session_state[key] in options:
            return self.session_state[key]
        override = self._get("radio", label)
        value = override if override is not None else (options[index] if index is not None else None)
        for o in options:
            format_func(o)
        if key:
            self.session_state[key] = value
        return value

    def selectbox(self, label, options, index=0, format_func=str, **k):
        options = list(options)
        for o in options:
            format_func(o)
        return self._get("selectbox", label, options[index])

    def select_slider(self, label, options, value=None, **k):
        return value

    def slider(self, label, min_value, max_value, value, **k):
        return value

    def text_input(self, label, **k):
        return self._get("text_input", label, "")

    def text_area(self, label, **k):
        return self._get("text_area", label, "")

    def checkbox(self, label, **k):
        return self._get("checkbox", label, False)

    def file_uploader(self, label, **k):
        return self.script.get("file_uploader")

    def chat_input(self, *a, **k):
        return self.script.get("chat_input")

    def feedback(self, options="thumbs", key=None, **k):
        return self.script.get("feedback")

    def rerun(self):
        raise Rerun()

    def stop(self):
        raise Stop()

    def __getattr__(self, name):
        def display(*args, **kwargs):
            self.calls.append((name, args))
            return Element(self)

        return display

    def texts(self):
        return " ".join(str(a) for _, args in self.calls for a in args)


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    fake_st = FakeStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", fake_st)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
    llm = FakeLLM()
    monkeypatch.setattr("studymate.llm.LLMClient", lambda settings: llm)

    def run(script=None, page=None):
        fake_st.script = script or {}
        fake_st.calls = []
        if page:
            fake_st.session_state["page"] = page
        try:
            runpy.run_path(str(APP), run_name="__main__")
            return "done"
        except Rerun:
            return "rerun"

    yield fake_st, llm, run
    from studymate import rag

    rag._CACHE.clear()


QUIZ_JSON = json.dumps(
    {
        "questions": [
            {"question": "임베딩이란?", "options": ["의미를 숫자 벡터로 바꾼 것", "이미지 압축", "암호화", "정렬"],
             "answer_index": 0, "explanation": "자료 4장", "topic": "임베딩", "source": "S1"},
            {"question": "RAG의 효과는?", "options": ["환각 감소", "속도 저하", "재학습 필요", "비용 0원"],
             "answer_index": 0, "explanation": "근거를 함께 넣음", "topic": "RAG", "source": "S2"},
        ]
    },
    ensure_ascii=False,
)


def test_full_user_journey(app_env):
    st, llm, run = app_env
    ss = st.session_state

    # 1) 첫 화면 → 동의 없이 가입 시도하면 실패, 동의하면 가입
    signup = {
        "text_input": {"닉네임 (실명 대신 별명, 2~20자)": "스모크", "PIN (숫자 4~6자리 — 다음에 들어올 때 필요해요)": "1234"},
        "form_submit_button": {"시작하기": True},
    }
    assert run(signup) == "done" and ss.user is None
    assert "동의" in st.texts()
    signup["checkbox"] = {"위 내용을 확인했으며 개인정보 수집·이용 및 국외 이전에 동의합니다. (필수)": True}
    assert run(signup) == "rerun" and ss.user.nickname == "스모크"

    # 2) 자료 없음 안내 → 샘플 자료 등록
    assert run(page="💬 AI 튜터") == "done" and "아직 올린 자료가 없어요" in st.texts()
    assert run({"button": {"샘플 자료 넣기": True}}, page="📚 내 자료") == "done"
    assert "등록 완료" in st.texts()

    # 3) AI 튜터: 검색 도구 호출 → 출처가 붙은 답변 → 👍 평가
    llm.script = [
        reply(tool_calls=[tool_call("search_study_materials", {"query": "임베딩"})]),
        reply(tool_calls=[tool_call("save_learner_memory", {"category": "preference", "content": "예시 위주 설명 선호"}, "c2")]),
        reply("임베딩은 의미를 숫자 벡터로 바꾼 것이에요 [S1]."),
    ]
    assert run({"chat_input": "임베딩이 뭐야? 예시 위주로 설명해줘"}, page="💬 AI 튜터") == "rerun"
    assert len(ss.chat) == 2 and ss.chat[1]["sources"][0]["cited"]
    assert ss.flash and "기억했어요" in ss.flash[0]
    assert run({"feedback": 1}, page="💬 AI 튜터") == "done"
    assert "근거 자료" in st.texts() and "자료 검색" in st.texts()

    # 4) 퀴즈 생성 → 1번 틀리고 2번 맞힘 → 약점 기록
    llm.script = [reply(QUIZ_JSON)]
    assert run({"form_submit_button": {"퀴즈 만들기": True}}, page="📝 퀴즈") == "done"
    qs = ss.quiz["questions"]
    wrong = (qs[0]["answer_index"] + 1) % 4
    answer_script = {"form_submit_button": {"채점하기": True}, "radio": {"Q1": wrong, "Q2": qs[1]["answer_index"]}}
    assert run(answer_script, page="📝 퀴즈") == "rerun"
    assert run(page="📝 퀴즈") == "done"
    assert "약점으로 기억했어요" in st.texts() and "임베딩" in st.texts()
    # 틀린 개념 다시 배우기 → 튜터로 이동 + 질문 예약
    run({"button": {"틀린 개념 AI 튜터에게 다시 배우기": True}}, page="📝 퀴즈")
    assert ss.page == "💬 AI 튜터" and "임베딩" in ss.pending_prompt

    # 예약된 질문이 튜터에서 처리될 때 장기 기억(선호·약점)이 시스템 프롬프트에 들어간다
    llm.script = [reply("다시 설명할게요 (일반 지식)")]
    assert run(page="💬 AI 튜터") == "rerun"
    system_prompt = llm.calls[-1]["messages"][0]["content"]
    assert "예시 위주 설명 선호" in system_prompt and "임베딩 (오답 1회)" in system_prompt

    # 5) 학습 기록 화면
    assert run(page="🧠 학습 기록") == "done"
    assert "AI가 기억하고 있는 나" in st.texts()

    # 6) 피드백 제출 (점수 누락 시 경고, 모두 고르면 저장)
    fb = {"form_submit_button": {"피드백 보내기": True}}
    run(fb, page="🗣️ 피드백")
    assert "모두 골라" in st.texts()
    fb["radio"] = {
        "1. 전반적으로 만족하시나요?": 5, "2. AI 답변·퀴즈가 공부에 도움이 됐나요?": 4,
        "3. 사용하기 쉬웠나요?": 5, "4. 친구에게 추천할 의향은? (0~10)": 9,
    }
    fb["text_area"] = {"6. 불편했던 점, 있었으면 하는 기능 (가장 중요해요!)": "답변이 조금 느려요. 010-1111-2222"}
    run(fb, page="🗣️ 피드백")
    assert "감사합니다" in st.texts()

    # 7) 관리자 로그인 → 현황 화면
    assert run({"text_input": {"관리자 비밀번호": "admin-pass"}, "button": {"확인": True}}) == "rerun" and ss.is_admin
    assert run(page="🔧 관리자") == "done"
    assert "사용자 테스트 현황" in st.texts()

    from studymate.config import load_settings
    from studymate.db import Database

    db = Database(load_settings().database_url)
    improve = db.query_one("SELECT improve FROM feedback")["improve"]
    assert "010-1111-2222" not in improve  # 피드백 속 개인정보도 마스킹

    # 8) 내 데이터 전체 삭제
    delete = {
        "checkbox": {"계정과 모든 데이터(자료·대화·기억·퀴즈)를 삭제합니다. 되돌릴 수 없어요.": True},
        "button": {"내 데이터 전체 삭제": True},
    }
    assert run(delete, page="🧠 학습 기록") == "rerun" and ss.user is None
    assert db.scalar("SELECT COUNT(*) FROM users") == 0 and db.scalar("SELECT COUNT(*) FROM chunks") == 0
    assert db.scalar("SELECT COUNT(*) FROM feedback") == 1


def test_login_lockout_and_wrong_admin(app_env):
    st, _, run = app_env
    login = {"text_input": {"닉네임": "없는사람", "PIN": "1234"}, "form_submit_button": {"들어가기": True}}
    for _ in range(5):
        run(login)
    run(login)
    assert "시도가 너무 많아요" in st.texts()
