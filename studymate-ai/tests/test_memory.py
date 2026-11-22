from studymate import memory


def test_add_memory_dedupes_similar_text(db, user):
    assert memory.add_memory(db, user.id, "weak_topic", "재귀 함수")[0] == "created"
    assert memory.add_memory(db, user.id, "weak_topic", "재귀함수")[0] == "updated"
    rows = memory.list_memories(db, user.id, "weak_topic")
    assert len(rows) == 1 and rows[0]["weight"] == 2


def test_invalid_category_and_empty(db, user):
    assert memory.add_memory(db, user.id, "unknown", "기타 정보")[0] == "created"
    assert memory.list_memories(db, user.id)[0]["category"] == "fact"
    assert memory.add_memory(db, user.id, "goal", "   ")[0] == "skipped"


def test_memory_cap(db, user):
    for i in range(memory.MAX_MEMORIES_PER_USER + 5):
        memory.add_memory(db, user.id, "fact", f"정보 {i}번")
    assert len(memory.list_memories(db, user.id)) == memory.MAX_MEMORIES_PER_USER


def test_quiz_updates_weak_then_strength(db, user):
    # 두 번 틀림 → 약점 가중치 2
    s1 = memory.record_quiz_attempt(db, user.id, "전체", [{"topic": "임베딩", "correct": False}, {"topic": "RAG", "correct": True}])
    assert s1["new_weak"] == ["임베딩"] and s1["num_correct"] == 1
    memory.record_quiz_attempt(db, user.id, "전체", [{"topic": "임베딩", "correct": False}])
    assert memory.list_memories(db, user.id, "weak_topic")[0]["weight"] == 2
    # 두 번 맞힘 → 약점 해제 + 강점 등록
    memory.record_quiz_attempt(db, user.id, "전체", [{"topic": "임베딩", "correct": True}])
    s4 = memory.record_quiz_attempt(db, user.id, "전체", [{"topic": "임베딩", "correct": True}])
    assert s4["recovered"] == ["임베딩"]
    assert memory.weak_topics(db, user.id) == []
    assert "임베딩" in memory.list_memories(db, user.id, "strength")[0]["content"]
    assert len(memory.recent_quizzes(db, user.id)) == 4


def test_memory_block_and_profile(db, user):
    assert "첫 대화" in memory.build_memory_block(db, user.id)
    memory.add_memory(db, user.id, "goal", "12월 정보처리기사 필기 합격")
    memory.add_memory(db, user.id, "preference", "예시 위주의 쉬운 설명")
    memory.record_quiz_attempt(db, user.id, "전체 자료", [{"topic": "정규화", "correct": False}])
    block = memory.build_memory_block(db, user.id)
    assert block.index("목표") < block.index("선호") < block.index("약점")
    assert "정규화 (오답 1회)" in block and "최근 퀴즈: 전체 자료 0/1" in block
    profile = memory.learner_profile(db, user.id)
    assert len(profile["memories"]) == 3 and profile["recent_quizzes"][0]["score"] == "0/1"


def test_messages_order_rating_and_clear(db, user):
    ids = [memory.save_message(db, user.id, role, f"{role}-{i}") for i, role in enumerate(["user", "assistant"] * 3)]
    loaded = memory.load_messages(db, user.id)
    assert [m["content"] for m in loaded] == [f"{r}-{i}" for i, r in enumerate(["user", "assistant"] * 3)]
    assert [m["content"] for m in memory.load_messages(db, user.id, limit=2)] == ["user-4", "assistant-5"]
    memory.rate_message(db, user.id, ids[1], 1)
    assert memory.load_messages(db, user.id)[1]["rating"] == 1
    memory.clear_messages(db, user.id)
    assert memory.load_messages(db, user.id) == []
