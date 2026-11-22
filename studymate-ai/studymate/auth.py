"""닉네임 + PIN 기반의 가벼운 사용자 식별.

실명·이메일·전화번호를 받지 않는다(개인정보 최소 수집). PIN은 PBKDF2로 해시해 저장한다.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from .db import Database, new_id, utcnow

NICKNAME_RE = re.compile(r"^[0-9A-Za-z가-힣_]{2,20}$")
PIN_RE = re.compile(r"^\d{4,6}$")
PBKDF2_ITERATIONS = 200_000


class AuthError(Exception):
    """사용자에게 그대로 보여줄 수 있는 인증 오류."""


@dataclass(frozen=True)
class User:
    id: str
    nickname: str


def hash_pin(pin: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return digest.hex(), salt


def validate(nickname: str, pin: str) -> tuple[str, str]:
    nickname = (nickname or "").strip()
    pin = (pin or "").strip()
    if not NICKNAME_RE.match(nickname):
        raise AuthError("닉네임은 한글·영문·숫자·밑줄(_) 2~20자로 입력해 주세요. (실명은 쓰지 마세요)")
    if not PIN_RE.match(pin):
        raise AuthError("PIN은 숫자 4~6자리로 입력해 주세요.")
    return nickname, pin


def register(db: Database, nickname: str, pin: str, consent: bool) -> User:
    nickname, pin = validate(nickname, pin)
    if not consent:
        raise AuthError("서비스 이용을 위해 개인정보 수집·이용에 동의해 주세요.")
    if db.query_one("SELECT id FROM users WHERE nickname = ?", (nickname,)):
        raise AuthError("이미 사용 중인 닉네임이에요. 다른 닉네임을 쓰거나 '다시 왔어요'에서 로그인해 주세요.")
    pin_hash, salt = hash_pin(pin)
    user_id = new_id()
    now = utcnow()
    db.execute(
        "INSERT INTO users (id, nickname, pin_hash, pin_salt, consent_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, nickname, pin_hash, salt, now, now),
    )
    return User(user_id, nickname)


def login(db: Database, nickname: str, pin: str) -> User:
    nickname, pin = validate(nickname, pin)
    row = db.query_one("SELECT id, nickname, pin_hash, pin_salt FROM users WHERE nickname = ?", (nickname,))
    if not row:
        raise AuthError("닉네임 또는 PIN이 맞지 않아요.")
    expected, _ = hash_pin(pin, row["pin_salt"])
    if not hmac.compare_digest(expected, row["pin_hash"]):
        raise AuthError("닉네임 또는 PIN이 맞지 않아요.")
    return User(row["id"], row["nickname"])
