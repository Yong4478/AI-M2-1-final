"""자료 기반 객관식 퀴즈 생성·채점. 결과는 장기 기억(약점/강점)에 반영된다."""
from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass
from typing import Any

from . import memory, rag
from .prompts import DIFFICULTY_GUIDE, QUIZ_SYSTEM_PROMPT, QUIZ_USER_PROMPT


class QuizError(Exception):
    """사용자에게 보여줄 수 있는 퀴즈 오류."""


@dataclass
class QuizQuestion:
    question: str
    options: list[str]
    answer_index: int
    explanation: str
    topic: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def parse_quiz(text: str, rng: random.Random | None = None) -> list[QuizQuestion]:
    """LLM 출력(JSON)을 검증하고, 정답 위치 편향을 없애기 위해 보기를 섞는다."""
    rng = rng or random.Random()
    try:
        data = _extract_json(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise QuizError("퀴즈 형식이 올바르지 않아요.") from exc
    raw = data.get("questions", []) if isinstance(data, dict) else data
    questions: list[QuizQuestion] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        q = str(item.get("question", "")).strip()
        options = [str(o).strip() for o in item.get("options", []) if str(o).strip()]
        try:
            answer = int(item.get("answer_index"))
        except (TypeError, ValueError):
            continue
        if not q or len(options) != 4 or len(set(options)) != 4 or not 0 <= answer < 4 or q in seen:
            continue
        seen.add(q)
        correct_text = options[answer]
        rng.shuffle(options)
        questions.append(
            QuizQuestion(
                question=q,
                options=options,
                answer_index=options.index(correct_text),
                explanation=str(item.get("explanation", "")).strip(),
                topic=str(item.get("topic", "")).strip()[:30] or "기타",
                source=str(item.get("source", "")).strip(),
            )
        )
    if not questions:
        raise QuizError("문제를 만들지 못했어요.")
    return questions


def generate_quiz(
    db: Any,
    llm: Any,
    user_id: str,
    *,
    topic: str | None = None,
    num_questions: int = 5,
    difficulty: str = "보통",
    focus_weak: bool = False,
    document_id: str | None = None,
) -> tuple[list[QuizQuestion], list[rag.SearchHit], str]:
    """반환: (문제 목록, 근거 청크 목록, 출제 범위 이름)."""
    num_questions = max(1, min(10, int(num_questions)))
    weak = memory.weak_topics(db, user_id, 5)
    query = (topic or "").strip()
    if focus_weak and weak and not query:
        query = ", ".join(weak)
    n_context = min(8, num_questions + 3)
    if query:
        hits = rag.search(db, llm, user_id, query, top_k=n_context, min_score=0.0, document_id=document_id)
    else:
        hits = rag.sample_chunks(db, user_id, n=n_context, document_id=document_id)
    if not hits:
        raise QuizError("퀴즈를 낼 자료가 없어요. 먼저 '📚 내 자료'에서 공부 자료를 올려 주세요.")

    hint = (
        f"이미 기록된 약점 주제가 있으면 같은 개념일 때 이 표현을 그대로 쓴다: {', '.join(weak)}"
        if weak
        else "같은 개념에는 항상 같은 표현을 쓴다."
    )
    prompt = QUIZ_USER_PROMPT.format(
        num_questions=num_questions,
        weak_topic_hint=hint,
        difficulty=f"{difficulty} — {DIFFICULTY_GUIDE.get(difficulty, DIFFICULTY_GUIDE['보통'])}",
        context=rag.format_context(hits),
    )
    messages = [{"role": "system", "content": QUIZ_SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    last_error: Exception | None = None
    for _ in range(2):  # 형식 오류 시 1회 재시도
        reply = llm.chat(messages, json_mode=True)
        try:
            questions = parse_quiz(reply.content or "")
            scope = query or "전체 자료"
            return questions[:num_questions], hits, scope
        except QuizError as exc:
            last_error = exc
    raise QuizError("퀴즈를 만드는 데 실패했어요. 다시 시도해 주세요.") from last_error


def grade(questions: list[QuizQuestion], answers: list[int | None]) -> list[dict[str, Any]]:
    results = []
    for i, q in enumerate(questions):
        chosen = answers[i] if i < len(answers) else None
        results.append(
            {
                "question": q.question,
                "topic": q.topic,
                "chosen": chosen,
                "answer_index": q.answer_index,
                "correct": chosen == q.answer_index,
            }
        )
    return results
