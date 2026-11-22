import pytest

from studymate import analytics, memory
from studymate.auth import AuthError, login, register
from studymate.db import Database


def test_register_and_login(db):
    user = register(db, "공부왕", "123456", consent=True)
    assert login(db, "공부왕", "123456") == user
    row = db.query_one("SELECT pin_hash FROM users WHERE id = ?", (user.id,))
    assert row["pin_hash"] != "123456"  # 평문 저장 금지


@pytest.mark.parametrize(
    "nickname,pin,consent",
    [("a", "1234", True), ("정상닉네임", "12", True), ("정상닉네임", "abcd", True), ("정상닉네임", "1234", False), ("이메일@x", "1234", True)],
)
def test_register_validation(db, nickname, pin, consent):
    with pytest.raises(AuthError):
        register(db, nickname, pin, consent)


def test_duplicate_nickname_and_wrong_pin(db):
    register(db, "중복", "1111", consent=True)
    with pytest.raises(AuthError):
        register(db, "중복", "2222", consent=True)
    with pytest.raises(AuthError):
        login(db, "중복", "9999")
    with pytest.raises(AuthError):
        login(db, "없는사람", "1111")


def test_delete_user_data_removes_everything_but_anonymizes_feedback(db, user):
    memory.add_memory(db, user.id, "goal", "12월 시험 합격")
    memory.save_message(db, user.id, "user", "안녕")
    analytics.save_feedback(db, user.id, "1.0.0", satisfaction=5, usefulness=4, ease=5, recommend=9, liked="좋아요", improve="", tester_group="대학생")
    analytics.log_event(db, user.id, "chat")
    db.delete_user_data(user.id)
    for table in ("users", "memories", "messages", "events"):
        assert db.scalar(f"SELECT COUNT(*) FROM {table}") == 0
    fb = db.query("SELECT user_id, satisfaction FROM feedback")
    assert fb == [{"user_id": None, "satisfaction": 5}]


def test_postgres_placeholder_conversion():
    database = Database.__new__(Database)
    database.kind = "postgres"
    assert database._sql("SELECT * FROM t WHERE a = ? AND b = ?") == "SELECT * FROM t WHERE a = %s AND b = %s"


def test_invalid_database_url():
    with pytest.raises(ValueError):
        Database("mysql://x")
