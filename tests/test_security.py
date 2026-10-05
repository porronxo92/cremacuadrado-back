"""Hashing y JWT tras migrar de passlib/python-jose a bcrypt/PyJWT."""
from app.utils.security import (
    create_access_token, create_refresh_token, decode_token, get_password_hash, verify_password,
)

# Hash generado por passlib (bcrypt, $2b$12$) para "Cliente123!" — debe seguir validando
PASSLIB_HASH = "$2b$12$Exy9gbsFzOPWlyouOUE/Juir6JsPfgbrJ2sOowW7GQLdLfWi.EpSi"


def test_hash_and_verify_roundtrip():
    hashed = get_password_hash("Cliente123!")
    assert hashed.startswith("$2b$12$")
    assert verify_password("Cliente123!", hashed)
    assert not verify_password("otra", hashed)


def test_existing_passlib_hashes_still_verify():
    assert verify_password("Cliente123!", PASSLIB_HASH)
    assert not verify_password("Cliente124!", PASSLIB_HASH)


def test_verify_handles_empty_or_malformed_hash():
    assert not verify_password("x", "")
    assert not verify_password("x", "not-a-bcrypt-hash")


def test_long_passwords_truncate_like_passlib():
    long_pw = "a" * 100
    hashed = get_password_hash(long_pw)
    assert verify_password("a" * 72, hashed)


def test_tokens_roundtrip():
    access = decode_token(create_access_token(7, token_version=3))
    refresh = decode_token(create_refresh_token(7))
    assert access["sub"] == "7" and access["type"] == "access" and access["ver"] == 3
    assert refresh["type"] == "refresh"
    assert decode_token("garbage") is None
