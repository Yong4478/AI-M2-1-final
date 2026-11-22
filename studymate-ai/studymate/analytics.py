"""사용 로그·피드백·통계. 사용자 테스트 결과 보고서와 발표 자료의 근거 데이터가 된다."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .db import Database, kst_today_start_utc, new_id, to_kst_display, utcnow


# ---------- 이벤트 로그 ----------
def log_event(db: Database, user_id: str | None, event_type: str, detail: dict[str, Any] | None = None) -> None:
    try:
        db.execute(
            "INSERT INTO events (id, user_id, event_type, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (new_id(), user_id, event_type, json.dumps(detail or {}, ensure_ascii=False), utcnow()),
        )
    except Exception:
        pass  # 로그 실패가 서비스 흐름을 막지 않도록


def count_today(db: Database, user_id: str, event_type: str) -> int:
    return int(
        db.scalar(
            "SELECT COUNT(*) AS n FROM events WHERE user_id = ? AND event_type = ? AND created_at >= ?",
            (user_id, event_type, kst_today_start_utc()),
        )
    )


# ---------- 피드백 ----------
def anonymous_id(user_id: str | None) -> str:
    return hashlib.sha256((user_id or "anonymous").encode()).hexdigest()[:8]


def save_feedback(db: Database, user_id: str | None, app_version: str, **fields: Any) -> dict[str, Any]:
    row = {
        "id": new_id(),
        "user_id": user_id,
        "satisfaction": fields.get("satisfaction"),
        "usefulness": fields.get("usefulness"),
        "ease": fields.get("ease"),
        "recommend": fields.get("recommend"),
        "liked": (fields.get("liked") or "").strip()[:2000],
        "improve": (fields.get("improve") or "").strip()[:2000],
        "tester_group": fields.get("tester_group"),
        "app_version": app_version,
        "created_at": utcnow(),
    }
    cols = ", ".join(row)
    db.execute(f"INSERT INTO feedback ({cols}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
    payload = {k: v for k, v in row.items() if k not in ("id", "user_id")}
    payload["tester_id"] = anonymous_id(user_id)  # 외부 전송 시 닉네임 대신 익명 ID
    return payload


def send_webhook(url: str | None, payload: dict[str, Any], timeout: float = 5.0) -> bool:
    """n8n 자동화 워크플로우로 피드백 전달 (구글 시트 기록 + 팀 알림). 실패해도 서비스는 계속."""
    if not url:
        return False
    try:
        import requests

        response = requests.post(url, json={"type": "feedback", **payload}, timeout=timeout)
        return response.ok
    except Exception:
        return False


def list_feedback(db: Database) -> list[dict[str, Any]]:
    rows = db.query(
        """SELECT f.created_at, f.app_version, f.tester_group, f.satisfaction, f.usefulness, f.ease, f.recommend,
                  f.liked, f.improve, f.user_id
           FROM feedback f ORDER BY f.created_at DESC"""
    )
    for r in rows:
        r["tester_id"] = anonymous_id(r.pop("user_id")) if r.get("user_id") else "삭제된 사용자"
    return rows


# ---------- 통계 ----------
def _avg(db: Database, sql: str, params: tuple[Any, ...] = ()) -> float | None:
    value = db.scalar(sql, params, default=None)
    return round(float(value), 2) if value is not None else None


def user_stats(db: Database, user_id: str) -> dict[str, Any]:
    attempts = db.query("SELECT num_questions, num_correct FROM quiz_attempts WHERE user_id = ?", (user_id,))
    total_q = sum(a["num_questions"] for a in attempts)
    total_c = sum(a["num_correct"] for a in attempts)
    return {
        "documents": int(db.scalar("SELECT COUNT(*) FROM documents WHERE user_id = ?", (user_id,))),
        "questions": int(db.scalar("SELECT COUNT(*) FROM messages WHERE user_id = ? AND role = 'user'", (user_id,))),
        "quizzes": len(attempts),
        "accuracy": round(100 * total_c / total_q) if total_q else None,
        "memories": int(db.scalar("SELECT COUNT(*) FROM memories WHERE user_id = ?", (user_id,))),
    }


def global_stats(db: Database) -> dict[str, Any]:
    rated = db.query("SELECT rating FROM messages WHERE role = 'assistant' AND rating IS NOT NULL")
    ups = sum(1 for r in rated if r["rating"] == 1)
    return {
        "users": int(db.scalar("SELECT COUNT(*) FROM users")),
        "active_users": int(db.scalar("SELECT COUNT(DISTINCT user_id) FROM messages WHERE role = 'user'")),
        "documents": int(db.scalar("SELECT COUNT(*) FROM documents")),
        "questions": int(db.scalar("SELECT COUNT(*) FROM messages WHERE role = 'user'")),
        "quizzes": int(db.scalar("SELECT COUNT(*) FROM quiz_attempts")),
        "thumbs_rated": len(rated),
        "thumbs_up_rate": round(100 * ups / len(rated)) if rated else None,
        "feedback_count": int(db.scalar("SELECT COUNT(*) FROM feedback")),
        "avg_satisfaction": _avg(db, "SELECT AVG(satisfaction) FROM feedback"),
        "avg_usefulness": _avg(db, "SELECT AVG(usefulness) FROM feedback"),
        "avg_ease": _avg(db, "SELECT AVG(ease) FROM feedback"),
        "avg_recommend": _avg(db, "SELECT AVG(recommend) FROM feedback"),
    }


def feedback_by_version(db: Database) -> list[dict[str, Any]]:
    """버전별 만족도 비교 (피드백 반영 전후 개선 효과 확인용)."""
    rows = db.query(
        """SELECT app_version, COUNT(*) AS n, AVG(satisfaction) AS satisfaction, AVG(usefulness) AS usefulness,
                  AVG(ease) AS ease, AVG(recommend) AS recommend
           FROM feedback GROUP BY app_version ORDER BY app_version"""
    )
    for r in rows:
        for k in ("satisfaction", "usefulness", "ease", "recommend"):
            r[k] = round(float(r[k]), 2) if r[k] is not None else None
        r["n"] = int(r["n"])
    return rows


def daily_activity(db: Database) -> list[dict[str, Any]]:
    """한국 시간 기준 일별·이벤트별 건수."""
    counts: dict[tuple[str, str], int] = {}
    for r in db.query("SELECT event_type, created_at FROM events"):
        day = to_kst_display(r["created_at"])[:10]
        counts[(day, r["event_type"])] = counts.get((day, r["event_type"]), 0) + 1
    return [{"day": d, "event_type": e, "n": n} for (d, e), n in sorted(counts.items())]


def low_rated_answers(db: Database, limit: int = 30) -> list[dict[str, Any]]:
    """👎 받은 답변과 그 직전 질문 (닉네임 없이). 프롬프트·검색 개선의 근거 자료."""
    bad = db.query(
        "SELECT user_id, content, created_at FROM messages WHERE role = 'assistant' AND rating = 0 ORDER BY created_at DESC"
    )[:limit]
    items = []
    for b in bad:
        q = db.query_one(
            "SELECT content FROM messages WHERE user_id = ? AND role = 'user' AND created_at < ? ORDER BY created_at DESC LIMIT 1",
            (b["user_id"], b["created_at"]),
        )
        items.append({"question": q["content"] if q else "", "answer": b["content"], "created_at": b["created_at"]})
    return items
