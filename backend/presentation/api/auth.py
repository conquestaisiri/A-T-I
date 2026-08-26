# backend/presentation/api/auth.py
"""API key authentication."""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader
from pydantic import SecretStr

from backend.infrastructure.config.settings import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Verify the API key for protected endpoints. Fail-closed outside development."""
    _expected_secret = settings.api_key
    if _expected_secret is None:
        _expected = ""
    elif isinstance(_expected_secret, SecretStr):
        _expected = _expected_secret.get_secret_value()
    else:
        # Tests may monkeypatch a plain str
        _expected = str(_expected_secret)
    # Treat empty string as missing; allow only when explicitly in development env
    if not _expected or not _expected.strip():
        if settings.api_env == "development":
            return "development"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API_KEY not configured",
        )
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )
    if not hmac.compare_digest(api_key, _expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
    return api_key
