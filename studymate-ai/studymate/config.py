"""환경 설정 로더.

우선순위: 환경변수 → Streamlit secrets(.streamlit/secrets.toml 또는 Cloud의 Secrets) → 기본값
"""
from __future__ import annotations

import os
from dataclasses import dataclass

try:  # 로컬 개발 시 .env 파일 지원
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - python-dotenv 미설치 환경
    pass


def _from_streamlit_secrets(key: str) -> str | None:
    try:
        import streamlit as st

        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        # secrets.toml 이 없거나 streamlit 미설치(테스트 환경)인 경우
        return None
    return None


def get_setting(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key)
    if value not in (None, ""):
        return value
    value = _from_streamlit_secrets(key)
    if value not in (None, ""):
        return value
    return default


def _int(key: str, default: int) -> int:
    try:
        return int(get_setting(key, str(default)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(get_setting(key, str(default)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_base_url: str | None
    chat_model: str
    embedding_model: str
    reasoning_effort: str | None
    database_url: str
    admin_password: str | None
    n8n_webhook_url: str | None
    feedback_form_url: str | None
    daily_message_limit: int
    max_upload_mb: int
    max_documents_per_user: int
    max_chunks_per_document: int
    retrieval_top_k: int
    retrieval_min_score: float
    data_retention_until: str


def load_settings() -> Settings:
    effort = get_setting("REASONING_EFFORT", "low")
    return Settings(
        openai_api_key=get_setting("OPENAI_API_KEY"),
        openai_base_url=get_setting("OPENAI_BASE_URL"),
        chat_model=get_setting("CHAT_MODEL", "gpt-5.4-mini"),  # type: ignore[arg-type]
        embedding_model=get_setting("EMBEDDING_MODEL", "text-embedding-3-small"),  # type: ignore[arg-type]
        # 추론형 모델(GPT-5 계열)의 사고량. 비추론 모델을 쓸 땐 REASONING_EFFORT=off
        reasoning_effort=None if (effort or "").strip().lower() in ("", "off", "disable") else effort,
        database_url=get_setting("DATABASE_URL", "sqlite:///data/studymate.db"),  # type: ignore[arg-type]
        admin_password=get_setting("ADMIN_PASSWORD"),
        n8n_webhook_url=get_setting("N8N_WEBHOOK_URL"),
        feedback_form_url=get_setting("FEEDBACK_FORM_URL"),
        daily_message_limit=_int("DAILY_MESSAGE_LIMIT", 100),
        max_upload_mb=_int("MAX_UPLOAD_MB", 10),
        max_documents_per_user=_int("MAX_DOCUMENTS_PER_USER", 10),
        max_chunks_per_document=_int("MAX_CHUNKS_PER_DOCUMENT", 400),
        retrieval_top_k=_int("RETRIEVAL_TOP_K", 4),
        retrieval_min_score=_float("RETRIEVAL_MIN_SCORE", 0.2),
        data_retention_until=get_setting("DATA_RETENTION_UNTIL", "프로젝트 종료일(발표일) + 30일"),  # type: ignore[arg-type]
    )
