"""Long-term Memory: 사용자별 학습 프로필(목표·선호·약점·강점)과 퀴즈 이력.

- 의미 기억(semantic): memories 테이블 — Agent가 대화 중 저장하거나, 퀴즈 결과로 자동 갱신
- 일화 기억(episodic): messages(대화 기록), quiz_attempts(퀴즈 이력)
- 매 대화마다 프로필 요약을 시스템 프롬프트에 자동 주입해 맥락을 이어간다.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .db import Database, new_id, to_kst_display, utcnow

CATEGORIES: dict[str, str] = {
    "goal": "🎯 목표",
    "preference": "💡 선호",
    "weak_topic": "⚠️ 약점",
    "strength": "💪 강점",
    "fact": "📌 기타",
}
CATEGORY_ORDER = list(CATEGORIES)
MAX_MEMORIES_PER_USER = 60
MAX_MEMORY_CHARS = 200


def _key(text: str) -> str:
    """'재귀 함수'와 '재귀함수'를 같은 주제로 보기 위한 비교 키."""
    return re.sub(r"[\s\-_·.,()\[\]\"']", "", text).lower()


def list_memories(db: Database, user_id: str, category: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT id, category, content, weight, source, created_at, updated_at FROM memories WHERE user_id = ?"
    params: list[Any] = [user_id]
    if category:
        sql += " AND category = ?"
        params.append(category)
    rows = db.query(sql + " ORDER BY weight DESC, updated_at DESC", params)
    return rows


def _find(db: Database, user_id: str, category: str, content: str) -> dict[str, Any] | None:
    key = _key(content)
    for row in list_memories(db, user_id, category):
        if _key(row["content"]) == key:
            return row
    return None


def add_memory(db: Database, user_id: str, category: str, content: str, source: str = "agent") -> tuple[str, str]:
    """기억을 저장한다. 같은 내용이 있으면 가중치만 올린다. 반환: (상태, memory_id)."""
    category = category if category in CATEGORIES else "fact"
    content = re.sub(r"\s+", " ", (content or "")).strip()[:MAX_MEMORY_CHARS]
    if not content:
        return "skipped", ""
    now = utcnow()
    existing = _find(db, user_id, category, content)
    if existing:
        db.execute("UPDATE memories SET weight = weight + 1, updated_at = ? WHERE id = ?", (now, existing["id"]))
        return "updated", existing["id"]
    memory_id = new_id()
    db.execute(
        "INSERT INTO memories (id, user_id, category, content, weight, source, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
        (memory_id, user_id, category, content, source, now, now),
    )
    _enforce_cap(db, user_id)
    return "created", memory_id


def _enforce_cap(db: Database, user_id: str) -> None:
    rows = db.query(
        "SELECT id FROM memories WHERE user_id = ? ORDER BY weight DESC, updated_at DESC", (user_id,)
    )
    for row in rows[MAX_MEMORIES_PER_USER:]:
        db.execute("DELETE FROM memories WHERE id = ?", (row["id"],))


def delete_memory(db: Database, user_id: str, memory_id: str) -> None:
    db.execute("DELETE FROM memories WHERE user_id = ? AND id = ?", (user_id, memory_id))


def update_topic_from_quiz(db: Database, user_id: str, topic: str, correct: bool) -> str:
    """오답 → 약점 가중치 +1, 약점 주제를 맞히면 -1, 0이 되면 약점 해제 + 강점 등록."""
    topic = (topic or "").strip()
    if not topic:
        return "none"
    weak = _find(db, user_id, "weak_topic", topic)
    if not correct:
        add_memory(db, user_id, "weak_topic", topic, source="quiz")
        return "weak+"
    if weak:
        if weak["weight"] <= 1:
            delete_memory(db, user_id, weak["id"])
            add_memory(db, user_id, "strength", f"{weak['content']} (복습으로 극복)", source="quiz")
            return "recovered"
        db.execute("UPDATE memories SET weight = weight - 1, updated_at = ? WHERE id = ?", (utcnow(), weak["id"]))
        return "weak-"
    return "ok"


def record_quiz_attempt(db: Database, user_id: str, topic: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """퀴즈 결과 저장 + 주제별 약점/강점 갱신. results: [{topic, correct, question, ...}]"""
    num_correct = sum(1 for r in results if r.get("correct"))
    attempt_id = new_id()
    db.execute(
        "INSERT INTO quiz_attempts (id, user_id, topic, num_questions, num_correct, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (attempt_id, user_id, topic or "전체 자료", len(results), num_correct, json.dumps(results, ensure_ascii=False), utcnow()),
    )
    changes: dict[str, list[str]] = {"new_weak": [], "recovered": []}
    for r in results:
        status = update_topic_from_quiz(db, user_id, r.get("topic", ""), bool(r.get("correct")))
        if status == "weak+" and r.get("topic") not in changes["new_weak"]:
            changes["new_weak"].append(r.get("topic", ""))
        elif status == "recovered":
            changes["recovered"].append(r.get("topic", ""))
    return {"attempt_id": attempt_id, "num_correct": num_correct, "num_questions": len(results), **changes}


def recent_quizzes(db: Database, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT topic, num_questions, num_correct, created_at FROM quiz_attempts WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    )
    return rows[:limit]


def weak_topics(db: Database, user_id: str, limit: int = 5) -> list[str]:
    return [r["content"] for r in list_memories(db, user_id, "weak_topic")[:limit]]


def build_memory_block(db: Database, user_id: str, limit: int = 15) -> str:
    """시스템 프롬프트에 자동 주입하는 학습자 프로필 요약."""
    rows = list_memories(db, user_id)
    if not rows:
        return "(아직 저장된 기억이 없음 — 첫 대화)"
    rows.sort(key=lambda r: (CATEGORY_ORDER.index(r["category"]) if r["category"] in CATEGORY_ORDER else 99, -r["weight"]))
    lines = []
    for r in rows[:limit]:
        label = CATEGORIES.get(r["category"], r["category"])
        extra = f" (오답 {r['weight']}회)" if r["category"] == "weak_topic" else ""
        lines.append(f"- {label}: {r['content']}{extra}")
    quizzes = recent_quizzes(db, user_id, 3)
    if quizzes:
        summary = ", ".join(f"{q['topic']} {q['num_correct']}/{q['num_questions']}" for q in quizzes)
        lines.append(f"- 최근 퀴즈: {summary}")
    return "\n".join(lines)


def learner_profile(db: Database, user_id: str) -> dict[str, Any]:
    """Agent 도구(get_learner_profile)가 돌려주는 상세 프로필."""
    return {
        "memories": [
            {"category": CATEGORIES.get(r["category"], r["category"]), "content": r["content"], "weight": r["weight"]}
            for r in list_memories(db, user_id)
        ],
        "recent_quizzes": [
            {
                "topic": q["topic"],
                "score": f"{q['num_correct']}/{q['num_questions']}",
                "date": to_kst_display(q["created_at"]),
            }
            for q in recent_quizzes(db, user_id, 10)
        ],
    }


# ---------- 대화 기록(일화 기억) ----------
def save_message(
    db: Database,
    user_id: str,
    role: str,
    content: str,
    sources: list[dict[str, Any]] | None = None,
    tools_used: list[str] | None = None,
) -> str:
    message_id = new_id()
    db.execute(
        "INSERT INTO messages (id, user_id, role, content, sources, tools_used, rating, created_at) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
        (
            message_id,
            user_id,
            role,
            content,
            json.dumps(sources or [], ensure_ascii=False),
            json.dumps(tools_used or [], ensure_ascii=False),
            utcnow(),
        ),
    )
    return message_id


def load_messages(db: Database, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT id, role, content, sources, tools_used, rating, created_at FROM messages WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    )[:limit]
    rows.reverse()
    for r in rows:
        r["sources"] = json.loads(r["sources"] or "[]")
        r["tools_used"] = json.loads(r["tools_used"] or "[]")
    return rows


def rate_message(db: Database, user_id: str, message_id: str, rating: int) -> None:
    db.execute("UPDATE messages SET rating = ? WHERE user_id = ? AND id = ?", (rating, user_id, message_id))


def clear_messages(db: Database, user_id: str) -> None:
    db.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
