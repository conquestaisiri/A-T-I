# backend/infrastructure/secrets/sagax_loader.py
"""Secure multi-provider key loading for the Omega routing layer.

This loader is the *only* place that touches provider secrets. It never
hardcodes a key, never logs a full key, and never commits `api_keys.env`.

Sources (in priority order, first hit wins per-provider pool):
1. Explicit environment variables (`GROQ_API_KEY`, `OPENROUTER_API_KEY`,
   `GEMINI_API_KEY`, `CEREBRAS_API_KEY`, comma-separated for pools).
2. Sagax legacy file ``~/.config/ati/keys.env`` (portable, override via
   ``SAGAX_KEYS_PATH`` env var; gitignored, plaintext intentionally per
   Sagax README — read at runtime, not baked into any binary).
3. ``.env`` via ``pydantic-settings`` (handled upstream).

The Sagax file format is ``PROVIDER=value`` one per line, ``#`` comments,
key type detected by prefix (``gsk_``=Groq, ``sk-or-``=OpenRouter,
``AIza``=Gemini, ``csk-``=Cerebras, ``sk-``=AgentRouter). Duplicate
``PROVIDER=`` lines are kept as a pool for lightning rotation — this is
intentional, not a dup bug.

Security: keys live only in memory (``_KeyPool``), redacted in logs
(``...last4``), and never serialized. Rotation is per-request round-robin
plus instant skip on 429/auth failures.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

DEFAULT_SAGAX_PATH: Final[Path] = Path.home() / ".config" / "ati" / "keys.env"
ENV_SAGAX_PATH = "SAGAX_KEYS_PATH"

# Prefix -> canonical provider id (support both _ and - variants)
_PREFIX_MAP: Final[dict[str, str]] = {
    "gsk_": "groq",
    "sk-or-": "openrouter",
    "sk-or_": "openrouter",
    "AIza": "gemini",
    "csk-": "cerebras",
    "csk_": "cerebras",
}

# Env var pools (comma-separated)
_ENV_POOL_VARS: Final[dict[str, str]] = {
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "agentrouter": "AGENTROUTER_API_KEY",
    "zen": "OPENCODE_ZEN_API_KEY",
}


def _detect_provider(value: str) -> str | None:
    for prefix, provider in _PREFIX_MAP.items():
        if value.startswith(prefix):
            return provider
    if value.startswith("sk-") and not value.startswith("sk-or-"):
        return "agentrouter"
    return None


def _parse_sagax_file(path: Path) -> dict[str, list[str]]:
    pools: dict[str, list[str]] = {}
    if not path.exists():
        logger.debug("Sagax keys file not present at %s", path)
        return pools
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        logger.warning("Failed to read Sagax keys file %s: %s", path, exc)
        return pools
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            logger.debug("Skipping malformed Sagax line %d", lineno)
            continue
        _key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if not value:
            continue
        provider = _detect_provider(value)
        if provider is None:
            logger.debug("Unknown key prefix on Sagax line %d", lineno)
            continue
        pools.setdefault(provider, []).append(value)
    for provider, keys in pools.items():
        logger.info("Loaded %d %s key(s) from Sagax file", len(keys), provider)
    return pools


def _parse_env_pools() -> dict[str, list[str]]:
    pools: dict[str, list[str]] = {}
    for provider, env_name in _ENV_POOL_VARS.items():
        raw = os.getenv(env_name)
        if not raw:
            continue
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if keys:
            pools[provider] = keys
            logger.info("Loaded %d %s key(s) from env %s", len(keys), provider, env_name)
    # Also honour legacy comma-separated GROQ/OPENROUTER without _API_KEY suffix if present
    for legacy in ("GROQ", "OPENROUTER"):
        raw = os.getenv(legacy)
        if raw and legacy.lower() not in pools:
            keys = [k.strip() for k in raw.split(",") if k.strip()]
            # filter to matching prefix to avoid mixing
            filtered = [k for k in keys if _detect_provider(k) == legacy.lower()]
            if filtered:
                pools[legacy.lower()] = filtered
    return pools


def load_provider_keys(sagax_path: Path | str | None = None) -> dict[str, list[str]]:
    """Return ``{provider: [key, ...]}`` merged from env + Sagax file.

    Env pools win — if env provides a pool for a provider the file's pool
    for that provider is appended (not replaced) so you get the superset
    for rotation. Duplicate keys are de-duplicated preserving order.
    """
    env_pools = _parse_env_pools()
    file_path = (
        Path(sagax_path) if sagax_path else Path(os.getenv(ENV_SAGAX_PATH, str(DEFAULT_SAGAX_PATH)))
    )
    file_pools = _parse_sagax_file(file_path)

    merged: dict[str, list[str]] = {}
    for provider in set(env_pools) | set(file_pools):
        combined = []
        seen: set[str] = set()
        for key in (*env_pools.get(provider, []), *file_pools.get(provider, [])):
            if key not in seen:
                seen.add(key)
                combined.append(key)
        if combined:
            merged[provider] = combined
    return merged


def redact_key(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"
