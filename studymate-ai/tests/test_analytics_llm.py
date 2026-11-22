from types import SimpleNamespace

import numpy as np
import pytest

from studymate import analytics, memory
from studymate.config import load_settings
from studymate.llm import LLMClient, LLMError


def test_feedback_payload_is_anonymous(db, user):
    payload = analytics.save_feedback(db, user.id, "1.0.0", satisfaction=4, usefulness=5, ease=3, recommend=8,
                                      liked="출처 표시가 좋아요", improve="속도가 느려요", tester_group="대학생")
    assert "user_id" not in payload and "nickname" not in payload
    assert payload["tester_id"] == analytics.anonymous_id(user.id) and len(payload["tester_id"]) == 8
    assert analytics.send_webhook(None, payload) is False


def test_stats_and_versions(db, user):
    analytics.save_feedback(db, user.id, "1.0.0", satisfaction=3, usefulness=3, ease=3, recommend=5)
    analytics.save_feedback(db, user.id, "1.1.0", satisfaction=5, usefulness=4, ease=5, recommend=9)
    m1 = memory.save_message(db, user.id, "user", "질문")
    m2 = memory.save_message(db, user.id, "assistant", "별로인 답변")
    memory.rate_message(db, user.id, m2, 0)
    stats = analytics.global_stats(db)
    assert stats["users"] == 1 and stats["questions"] == 1 and stats["thumbs_up_rate"] == 0
    assert stats["avg_satisfaction"] == 4.0
    versions = analytics.feedback_by_version(db)
    assert [v["app_version"] for v in versions] == ["1.0.0", "1.1.0"] and versions[1]["satisfaction"] == 5.0
    low = analytics.low_rated_answers(db)
    assert low[0]["question"] == "질문" and low[0]["answer"] == "별로인 답변"
    assert m1


def test_events_daily_limit_and_activity(db, user):
    for _ in range(3):
        analytics.log_event(db, user.id, "chat")
    analytics.log_event(db, user.id, "upload")
    assert analytics.count_today(db, user.id, "chat") == 3
    activity = analytics.daily_activity(db)
    assert {a["event_type"]: a["n"] for a in activity} == {"chat": 3, "upload": 1}
    assert analytics.user_stats(db, user.id)["accuracy"] is None


class FakeOpenAI:
    """openai.OpenAI 와 같은 모양의 가짜 클라이언트."""

    def __init__(self, reject_reasoning=False):
        self.reject_reasoning = reject_reasoning
        self.create_calls = []
        self.embed_calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.embeddings = SimpleNamespace(create=self._embed)

    def _create(self, **kwargs):
        self.create_calls.append(kwargs)
        if self.reject_reasoning and "reasoning_effort" in kwargs:
            raise RuntimeError("Unsupported parameter: 'reasoning_effort' is not supported with this model.")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))])

    def _embed(self, model, input):
        self.embed_calls.append(len(input))
        return SimpleNamespace(data=[SimpleNamespace(embedding=[3.0, 4.0]) for _ in input])


def test_llm_reasoning_effort_fallback(monkeypatch):
    monkeypatch.setenv("REASONING_EFFORT", "low")
    fake = FakeOpenAI(reject_reasoning=True)
    client = LLMClient(load_settings(), client=fake)
    assert client.chat([{"role": "user", "content": "hi"}], json_mode=True).content == "ok"
    assert "reasoning_effort" not in fake.create_calls[-1]
    assert fake.create_calls[-1]["response_format"] == {"type": "json_object"}
    client.chat([{"role": "user", "content": "again"}])
    assert len(fake.create_calls) == 3  # 이후로는 처음부터 빼고 호출


def test_llm_embed_batches_and_normalizes():
    fake = FakeOpenAI()
    vectors = LLMClient(load_settings(), client=fake).embed(["a"] * 130)
    assert fake.embed_calls == [64, 64, 2]
    assert vectors.shape == (130, 2) and np.allclose(vectors[0], [0.6, 0.8])


def test_llm_missing_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMError):
        LLMClient(load_settings()).chat([{"role": "user", "content": "hi"}])


def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("REASONING_EFFORT", "off")
    monkeypatch.setenv("DAILY_MESSAGE_LIMIT", "not-a-number")
    s = load_settings()
    assert s.reasoning_effort is None and s.daily_message_limit == 100
    assert s.embedding_model == "text-embedding-3-small"
