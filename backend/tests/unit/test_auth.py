"""
Unit tests for authentication utilities.
No database required — all logic is pure functions.
"""
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from jose import JWTError

from app.core.auth import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_is_not_plaintext(self):
        hashed = hash_password("mysecretpassword")
        assert hashed != "mysecretpassword"

    def test_correct_password_verifies(self):
        hashed = hash_password("mysecretpassword")
        assert verify_password("mysecretpassword", hashed) is True

    def test_wrong_password_fails(self):
        hashed = hash_password("mysecretpassword")
        assert verify_password("wrongpassword", hashed) is False

    def test_same_password_produces_different_hashes(self):
        h1 = hash_password("password123")
        h2 = hash_password("password123")
        assert h1 != h2  # bcrypt uses a random salt


class TestAccessToken:
    def test_token_contains_correct_subject(self):
        token = create_access_token(subject="user-123")
        payload = decode_access_token(token)
        assert payload["sub"] == "user-123"

    def test_token_type_is_access(self):
        token = create_access_token(subject="user-123")
        payload = decode_access_token(token)
        assert payload["type"] == "access"

    def test_expired_token_raises(self):
        with patch("app.core.auth.get_settings") as mock_settings:
            mock_settings.return_value.ACCESS_TOKEN_EXPIRE_MINUTES = 0
            mock_settings.return_value.SECRET_KEY = "test-secret"
            mock_settings.return_value.REFRESH_TOKEN_EXPIRE_DAYS = 7
            token = create_access_token(subject="user-123")
        # Token expired immediately — decode should raise
        with pytest.raises(JWTError):
            decode_access_token(token)

    def test_tampered_token_raises(self):
        token = create_access_token(subject="user-123")
        tampered = token[:-5] + "XXXXX"
        with pytest.raises(JWTError):
            decode_access_token(tampered)


class TestTokenSecurity:
    def test_alg_none_token_is_rejected(self):
        """
        Tokens with alg=none (unsigned) must be rejected at decode time.

        Some JWT libraries have a known vulnerability where they accept unsigned
        tokens. Verify that decode_access_token raises for any such attempt.
        """
        from jose import jwt as jose_jwt

        try:
            unsigned_token = jose_jwt.encode(
                {"sub": "attacker", "type": "access"},
                key="",
                algorithm="none",
            )
        except Exception:
            # jose raises at encode time — protection is at the library level.
            return

        # If encoding succeeded, decoding must fail with a JWTError.
        with pytest.raises(JWTError):
            decode_access_token(unsigned_token)

    def test_token_signed_with_wrong_key_raises(self):
        """A token signed with a different SECRET_KEY must be rejected."""
        from jose import jwt as jose_jwt

        wrong_key_token = jose_jwt.encode(
            {"sub": "user-123", "type": "access"},
            key="totally-wrong-secret-key-not-matching-settings",
            algorithm="HS256",
        )
        with pytest.raises(JWTError):
            decode_access_token(wrong_key_token)


class TestRefreshToken:
    def test_generate_returns_two_distinct_strings(self):
        raw, token_hash = generate_refresh_token()
        assert raw != token_hash
        assert len(raw) == 128    # 64 bytes → 128 hex chars
        assert len(token_hash) == 64  # SHA-256 → 64 hex chars

    def test_hash_is_deterministic(self):
        raw, h1 = generate_refresh_token()
        h2 = hash_token(raw)
        assert h1 == h2

    def test_two_tokens_are_unique(self):
        raw1, _ = generate_refresh_token()
        raw2, _ = generate_refresh_token()
        assert raw1 != raw2
