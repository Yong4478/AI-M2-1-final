import json

from studymate import memory, rag
from studymate.agent import MAX_TOOL_STEPS, TOOLS, StudyAgent

from .conftest import SAMPLE_TEXT, FakeLLM, reply, tool_call


def make_agent(db, user, llm):
    return StudyAgent(db, llm, user.id, user.nickname, top_k=3, min_score=0.0)


def test_agent_searches_then_answers_with_citation(db, user):
    llm = FakeLLM([
        reply(tool_calls=[tool_call("search_study_materials", {"query": "코사인 유사도"})]),
        reply("코사인 유사도는 두 벡터가 얼마나 비슷한지 계산하는 방법이에요 [S1]."),
    ])
    rag.ingest_document(db, llm, user.id, "notes.txt", SAMPLE_TEXT.encode())
    result = make_agent(db, user, llm).run("코사인 유사도가 뭐야?")
    assert result.tools_used == ["search_study_materials"]
    assert result.sources[0]["ref"] == "S1" and result.sources[0]["cited"] is True
    # 두 번째 LLM 호출에는 tool 결과 메시지가 들어 있어야 한다
    second = llm.calls[1]["messages"]
    tool_msg = next(m for m in second if m["role"] == "tool")
    assert json.loads(tool_msg["content"])["results"][0]["ref"] == "S1"
    assert second[-2]["tool_calls"][0]["function"]["name"] == "search_study_materials"


def test_agent_saves_memory_and_profile_is_injected_next_turn(db, user):
    llm = FakeLLM([
        reply(tool_calls=[tool_call("save_learner_memory", {"category": "goal", "content": "12월 14일 정보처리기사 필기"})]),
        reply("시험 일정을 기억해 둘게요!"),
    ])
    result = make_agent(db, user, llm).run("나 12월 14일에 정보처리기사 필기 봐")
    assert result.memories_saved and "정보처리기사" in result.memories_saved[0]
    # 다음 대화 시스템 프롬프트에 장기 기억이 자동 주입된다
    llm2 = FakeLLM([reply("네")])
    make_agent(db, user, llm2).run("안녕")
    assert "12월 14일 정보처리기사 필기" in llm2.calls[0]["messages"][0]["content"]


def test_agent_profile_tool(db, user):
    memory.record_quiz_attempt(db, user.id, "전체", [{"topic": "임베딩", "correct": False}])
    llm = FakeLLM([reply(tool_calls=[tool_call("get_learner_profile", {})]), reply("임베딩이 약점이에요")])
    result = make_agent(db, user, llm).run("내 약점이 뭐야?")
    tool_msg = next(m for m in llm.calls[1]["messages"] if m["role"] == "tool")
    profile = json.loads(tool_msg["content"])
    assert profile["memories"][0]["content"] == "임베딩" and profile["recent_quizzes"]
    assert result.tools_used == ["get_learner_profile"]


def test_agent_handles_bad_tool_calls(db, user):
    llm = FakeLLM([
        reply(tool_calls=[tool_call("unknown_tool", {}, "c1")]),
        reply(tool_calls=[type(tool_call("x", {}))(id="c2", type="function", function=type(tool_call("x", {}).function)(name="search_study_materials", arguments="{not json"))]),
        reply("괜찮아요"),
    ])
    result = make_agent(db, user, llm).run("테스트")
    assert result.answer == "괜찮아요"
    outputs = [json.loads(m["content"]) for m in llm.calls[2]["messages"] if m["role"] == "tool"]
    assert all("error" in o for o in outputs)


def test_agent_stops_after_max_steps(db, user):
    looping = [reply(tool_calls=[tool_call("get_learner_profile", {}, f"c{i}")]) for i in range(MAX_TOOL_STEPS)]
    llm = FakeLLM(looping + [reply("최종 답변")])
    result = make_agent(db, user, llm).run("계속 도구만 부르는 경우")
    assert result.answer == "최종 답변"
    assert llm.calls[-1]["tool_choice"] == "none"
    assert len(llm.calls) == MAX_TOOL_STEPS + 1


def test_agent_no_results_note_and_history(db, user):
    llm = FakeLLM([
        reply(tool_calls=[tool_call("search_study_materials", {"query": "양자역학"})]),
        reply("올려주신 자료에서는 찾지 못했어요."),
    ])
    history = [{"role": "user", "content": "이전 질문"}, {"role": "assistant", "content": "이전 답"}]
    result = make_agent(db, user, llm).run("양자역학 알려줘", history)
    first = llm.calls[0]["messages"]
    assert [m["role"] for m in first] == ["system", "user", "assistant", "user"]
    tool_msg = next(m for m in llm.calls[1]["messages"] if m["role"] == "tool")
    assert json.loads(tool_msg["content"])["results"] == []
    assert result.sources == []


def test_empty_answer_fallback(db, user):
    result = make_agent(db, user, FakeLLM([reply("")])).run("?")
    assert "다시" in result.answer


def test_tool_schemas_are_valid():
    names = {t["function"]["name"] for t in TOOLS}
    assert names == {"search_study_materials", "get_learner_profile", "save_learner_memory"}
    for t in TOOLS:
        assert t["type"] == "function" and t["function"]["parameters"]["type"] == "object"
