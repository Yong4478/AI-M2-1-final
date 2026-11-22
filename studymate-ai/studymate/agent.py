"""AI 튜터 Agent: OpenAI Function Calling 기반의 도구 사용 루프.

사용자 의도에 따라 LLM이 스스로 도구를 고른다.
- search_study_materials : RAG 검색 (학습 내용 질문)
- get_learner_profile    : 장기 기억 조회 (약점·이력 질문, 복습 추천)
- save_learner_memory    : 장기 기억 저장 (목표·선호·약점 등)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import memory, rag
from .prompts import TUTOR_SYSTEM_PROMPT

MAX_TOOL_STEPS = 5
HISTORY_TURNS = 12

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_study_materials",
            "description": "학습자가 업로드한 공부 자료에서 질문과 관련된 내용을 의미 기반으로 검색한다. 학습 내용 질문에는 항상 먼저 호출한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "검색할 내용. 질문의 핵심 개념을 포함한 짧은 문장"},
                    "top_k": {"type": "integer", "description": "가져올 결과 수(1~8)", "minimum": 1, "maximum": 8},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_learner_profile",
            "description": "학습자의 장기 기억(목표, 선호, 약점, 강점)과 최근 퀴즈 결과를 조회한다.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_learner_memory",
            "description": "다음 대화에도 유용한 학습자 정보를 장기 기억에 저장한다. 민감한 개인정보는 저장하지 않는다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["goal", "preference", "weak_topic", "strength", "fact"],
                        "description": "goal=학습 목표/시험 일정, preference=선호하는 설명 방식, weak_topic=어려워하는 개념, strength=잘하는 개념, fact=기타",
                    },
                    "content": {"type": "string", "description": "저장할 내용(한 문장, 100자 이내)"},
                },
                "required": ["category", "content"],
            },
        },
    },
]


@dataclass
class AgentResult:
    answer: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    memories_saved: list[str] = field(default_factory=list)


class StudyAgent:
    def __init__(self, db: Any, llm: Any, user_id: str, nickname: str, top_k: int = 4, min_score: float = 0.2):
        self.db = db
        self.llm = llm
        self.user_id = user_id
        self.nickname = nickname
        self.top_k = top_k
        self.min_score = min_score

    # ---------- 프롬프트 ----------
    def system_prompt(self) -> str:
        docs = rag.list_documents(self.db, self.user_id)
        doc_list = "\n".join(f"- {d.filename}" for d in docs) if docs else "(아직 없음 — 자료 업로드를 권해 줄 것)"
        return TUTOR_SYSTEM_PROMPT.format(
            nickname=self.nickname,
            memory_block=memory.build_memory_block(self.db, self.user_id),
            document_list=doc_list,
        )

    # ---------- 도구 실행 ----------
    def _tool_search(self, args: dict[str, Any], result: AgentResult) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        try:
            top_k = int(args.get("top_k") or self.top_k)
        except (TypeError, ValueError):
            top_k = self.top_k
        top_k = max(1, min(8, top_k))
        hits = rag.search(self.db, self.llm, self.user_id, query, top_k=top_k, min_score=self.min_score)
        known = {s["chunk_id"]: s["ref"] for s in result.sources}
        items = []
        for hit in hits:
            if hit.chunk_id not in known:
                ref = f"S{len(result.sources) + 1}"
                known[hit.chunk_id] = ref
                result.sources.append(
                    {
                        "ref": ref,
                        "chunk_id": hit.chunk_id,
                        "filename": hit.filename,
                        "page": hit.page,
                        "score": hit.score,
                        "excerpt": hit.content[:300],
                    }
                )
            items.append({"ref": known[hit.chunk_id], "location": hit.location(), "score": hit.score, "text": hit.content})
        if not items:
            return {"results": [], "note": "관련 자료를 찾지 못함. 자료에 없다고 밝히고, 보충 설명은 (일반 지식)으로 표시할 것."}
        return {"results": items}

    def _tool_profile(self, args: dict[str, Any], result: AgentResult) -> dict[str, Any]:
        profile = memory.learner_profile(self.db, self.user_id)
        profile["documents"] = [d.filename for d in rag.list_documents(self.db, self.user_id)]
        return profile

    def _tool_save(self, args: dict[str, Any], result: AgentResult) -> dict[str, Any]:
        category = str(args.get("category", "fact"))
        content = str(args.get("content", ""))
        status, _ = memory.add_memory(self.db, self.user_id, category, content, source="agent")
        if status != "skipped":
            label = memory.CATEGORIES.get(category, memory.CATEGORIES["fact"])
            result.memories_saved.append(f"{label}: {content.strip()[:100]}")
        return {"status": status}

    def _dispatch(self, name: str, raw_args: str | None, result: AgentResult) -> dict[str, Any]:
        handlers = {
            "search_study_materials": self._tool_search,
            "get_learner_profile": self._tool_profile,
            "save_learner_memory": self._tool_save,
        }
        handler = handlers.get(name)
        if handler is None:
            return {"error": f"알 수 없는 도구: {name}"}
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                args = {}
        except json.JSONDecodeError:
            return {"error": "도구 인자가 올바른 JSON이 아님"}
        result.tools_used.append(name)
        try:
            return handler(args, result)
        except Exception as exc:  # 도구 실패는 LLM에게 알려 스스로 복구하게 한다
            return {"error": f"{exc.__class__.__name__}: {exc}"}

    # ---------- 실행 루프 ----------
    def run(self, user_message: str, history: list[dict[str, Any]] | None = None) -> AgentResult:
        result = AgentResult(answer="")
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt()}]
        for h in (history or [])[-HISTORY_TURNS:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": user_message})

        for _ in range(MAX_TOOL_STEPS):
            reply = self.llm.chat(messages, tools=TOOLS)
            tool_calls = getattr(reply, "tool_calls", None) or []
            if not tool_calls:
                result.answer = (reply.content or "").strip()
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": reply.content,
                    "tool_calls": [
                        {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                output = self._dispatch(tc.function.name, tc.function.arguments, result)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(output, ensure_ascii=False)})
        else:
            # 도구 호출이 계속되면 마지막에는 도구 없이 답하도록 강제
            reply = self.llm.chat(messages, tools=TOOLS, tool_choice="none")
            result.answer = (reply.content or "").strip()

        if not result.answer:
            result.answer = "죄송해요, 답변을 만들지 못했어요. 질문을 조금 바꿔서 다시 물어봐 주세요."
        # 답변에서 실제로 인용한 출처를 앞으로, 나머지는 뒤로
        for s in result.sources:
            s["cited"] = f"[{s['ref']}]" in result.answer
        result.sources.sort(key=lambda s: not s["cited"])  # 안정 정렬: 인용 순서 유지
        return result
