import json
import random

import pytest

from studymate import memory, rag
from studymate.quiz import QuizError, generate_quiz, grade, parse_quiz

from .conftest import SAMPLE_TEXT, FakeLLM, reply

VALID = {
    "questions": [
        {"question": "임베딩이란?", "options": ["의미를 벡터로 바꾼 것", "이미지 압축", "암호화", "정렬"], "answer_index": 0,
         "explanation": "자료에 정의가 있다.", "topic": "임베딩", "source": "S1"},
        {"question": "RAG의 효과는?", "options": ["환각 감소", "속도 저하", "학습 필요", "비용 0"], "answer_index": 0,
         "explanation": "근거를 넣기 때문", "topic": "RAG", "source": "S2"},
    ]
}


def test_parse_quiz_shuffles_but_keeps_correct_answer():
    for seed in range(10):
        qs = parse_quiz(json.dumps(VALID, ensure_ascii=False), random.Random(seed))
        assert qs[0].options[qs[0].answer_index] == "의미를 벡터로 바꾼 것"
        assert qs[1].options[qs[1].answer_index] == "환각 감소"
    positions = {parse_quiz(json.dumps(VALID), random.Random(s))[0].answer_index for s in range(30)}
    assert len(positions) > 1  # 정답이 항상 1번에 몰리지 않음


def test_parse_quiz_accepts_code_fence_and_drops_invalid():
    bad_items = VALID["questions"] + [
        {"question": "보기 3개", "options": ["a", "b", "c"], "answer_index": 0},
        {"question": "정답 범위 밖", "options": ["a", "b", "c", "d"], "answer_index": 7},
        {"question": "중복 보기", "options": ["a", "a", "c", "d"], "answer_index": 0},
        {"question": "임베딩이란?", "options": ["1", "2", "3", "4"], "answer_index": 1},
        "문자열",
    ]
    text = "```json\n" + json.dumps({"questions": bad_items}, ensure_ascii=False) + "\n```"
    assert len(parse_quiz(text)) == 2


def test_parse_quiz_errors():
    with pytest.raises(QuizError):
        parse_quiz("이건 JSON이 아님")
    with pytest.raises(QuizError):
        parse_quiz(json.dumps({"questions": []}))


def test_generate_quiz_uses_context_and_retries(db, user):
    llm = FakeLLM([reply("망가진 출력"), reply(json.dumps(VALID, ensure_ascii=False))])
    rag.ingest_document(db, llm, user.id, "notes.txt", SAMPLE_TEXT.encode())
    memory.add_memory(db, user.id, "weak_topic", "임베딩", source="quiz")
    questions, hits, scope = generate_quiz(db, llm, user.id, num_questions=2, focus_weak=True)
    assert len(questions) == 2 and hits and scope == "임베딩"
    prompt = llm.calls[-1]["messages"][1]["content"]
    assert "[S1]" in prompt and "임베딩" in prompt and llm.calls[-1]["json_mode"] is True
    assert len(llm.calls) == 2  # 1회 재시도


def test_generate_quiz_without_documents(db, user):
    with pytest.raises(QuizError):
        generate_quiz(db, FakeLLM(), user.id)


def test_generate_quiz_fails_after_two_bad_outputs(db, user):
    llm = FakeLLM([reply("x"), reply("y")])
    rag.ingest_document(db, llm, user.id, "notes.txt", SAMPLE_TEXT.encode())
    with pytest.raises(QuizError):
        generate_quiz(db, llm, user.id)


def test_grade():
    qs = parse_quiz(json.dumps(VALID), random.Random(1))
    results = grade(qs, [qs[0].answer_index, (qs[1].answer_index + 1) % 4])
    assert [r["correct"] for r in results] == [True, False]
    assert results[1]["topic"] == "RAG"
