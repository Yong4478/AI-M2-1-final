"""OpenAI API 래퍼 (Chat Completions + Embeddings).

- OpenAI 호환 API(예: 다른 제공사의 OpenAI 호환 엔드포인트)도 OPENAI_BASE_URL 로 연결 가능
- 테스트에서는 같은 인터페이스(chat, embed)를 가진 가짜 객체로 바꿔 끼운다
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .config import Settings

EMBED_BATCH = 64
MAX_EMBED_CHARS = 6000


class LLMError(Exception):
    """사용자에게 보여줄 수 있는 AI 호출 오류."""


def normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class LLMClient:
    def __init__(self, settings: Settings, client: Any = None):
        self.settings = settings
        self.chat_model = settings.chat_model
        self.embedding_model = settings.embedding_model
        self._reasoning_effort = settings.reasoning_effort
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            if not self.settings.openai_api_key:
                raise LLMError("OPENAI_API_KEY가 설정되지 않았어요. 관리자에게 문의해 주세요.")
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.settings.openai_api_key,
                base_url=self.settings.openai_base_url or None,
                timeout=60,
                max_retries=2,
            )
        return self._client

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        json_mode: bool = False,
    ) -> Any:
        """assistant 메시지 객체(.content, .tool_calls)를 돌려준다."""
        kwargs: dict[str, Any] = {"model": self.chat_model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if self._reasoning_effort:
            kwargs["reasoning_effort"] = self._reasoning_effort
        try:
            response = self.client.chat.completions.create(**kwargs)
        except LLMError:
            raise
        except Exception as exc:
            # 비추론 모델에 reasoning_effort를 보내면 400 오류 → 한 번만 빼고 재시도
            if "reasoning_effort" in kwargs and "reasoning" in str(exc).lower():
                self._reasoning_effort = None
                kwargs.pop("reasoning_effort")
                try:
                    response = self.client.chat.completions.create(**kwargs)
                except Exception as exc2:
                    raise LLMError(_friendly(exc2)) from exc2
            else:
                raise LLMError(_friendly(exc)) from exc
        return response.choices[0].message

    def embed(self, texts: list[str]) -> np.ndarray:
        """L2 정규화된 (n, d) float32 행렬. 코사인 유사도 = 내적."""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        vectors: list[list[float]] = []
        try:
            for start in range(0, len(texts), EMBED_BATCH):
                batch = [t[:MAX_EMBED_CHARS] or " " for t in texts[start:start + EMBED_BATCH]]
                response = self.client.embeddings.create(model=self.embedding_model, input=batch)
                vectors.extend(item.embedding for item in response.data)
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(_friendly(exc)) from exc
        return normalize_rows(np.asarray(vectors, dtype=np.float32))


def _friendly(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if "api key" in lowered or "401" in lowered or "authentication" in lowered:
        return "AI 서비스 인증에 실패했어요. API 키 설정을 확인해 주세요."
    if "429" in lowered or "rate limit" in lowered or "quota" in lowered:
        return "AI 서비스 사용량이 몰려 잠시 응답할 수 없어요. 1분 뒤 다시 시도해 주세요."
    if "timeout" in lowered or "timed out" in lowered:
        return "AI 응답이 너무 오래 걸려요. 잠시 후 다시 시도해 주세요."
    if "model" in lowered and ("not found" in lowered or "does not exist" in lowered):
        return "설정된 AI 모델을 찾을 수 없어요. CHAT_MODEL / EMBEDDING_MODEL 설정을 확인해 주세요."
    return f"AI 호출 중 오류가 발생했어요: {exc.__class__.__name__}"
