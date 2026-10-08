"""Auth unit + contract tests: hashing, tokens, validation envelopes, routes.

DB-backed flows (register/login/isolation) live in
tests/integration/test_auth_db.py; everything here runs without PostgreSQL.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.api.v1.auth import normalize_email
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.main import app

# ---------------------------------------------------------------- passwords


def test_password_hash_verify_roundtrip() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert "$argon2" in hashed
    assert verify_password(hashed, "correct horse battery staple") is True


def test_verify_rejects_wrong_password() -> None:
    hashed = hash_password("the-real-password")
    assert verify_password(hashed, "the-wrong-password") is False


def test_verify_rejects_garbage_hash() -> None:
    assert verify_password("not-a-hash", "anything") is False


# ------------------------------------------------------------------- tokens


def test_token_create_decode_roundtrip() -> None:
    uid = uuid.uuid4()
    claims = decode_token(create_token(uid, "STUDENT"))
    assert claims is not None
    assert claims["sub"] == str(uid)
    assert claims["role"] == "STUDENT"


def test_expired_token_decodes_to_none() -> None:
    token = create_token(uuid.uuid4(), "STUDENT", ttl_seconds=-5)
    assert decode_token(token) is None


def test_tampered_token_decodes_to_none() -> None:
    token = create_token(uuid.uuid4(), "STUDENT")
    assert decode_token(token + "x") is None


# ----------------------------------------------------------- email/password


def test_email_normalization_trims_and_lowercases() -> None:
    assert normalize_email("  Ada.Lovelace@Example.COM ") == "ada.lovelace@example.com"


def _client() -> TestClient:
    return TestClient(app)


def test_register_rejects_weak_password_with_400_envelope() -> None:
    response = _client().post(
        "/api/v1/auth/register",
        json={"email": "weak-password@example.com", "password": "short"},
    )
    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert "8 characters" in body["message"]
    assert body["request_id"]


def test_register_rejects_invalid_email_with_400_envelope() -> None:
    response = _client().post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "password": "longenough"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_register_rejects_missing_email_with_400_envelope() -> None:
    response = _client().post("/api/v1/auth/register", json={"password": "longenough"})
    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert body["message"] == "Email is required"


def test_login_rejects_missing_password_with_400_envelope() -> None:
    response = _client().post("/api/v1/auth/login", json={"email": "someone@example.com"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------ contract


def test_auth_routes_exist_with_correct_methods() -> None:
    spec = _client().get("/api/v1/openapi.json").json()["paths"]
    assert "post" in spec["/api/v1/auth/register"]
    assert "post" in spec["/api/v1/auth/login"]
    assert "get" in spec["/api/v1/auth/me"]


def test_auth_register_declares_created_response() -> None:
    register = _client().get("/api/v1/openapi.json").json()["paths"]["/api/v1/auth/register"][
        "post"
    ]
    assert "201" in register["responses"]
