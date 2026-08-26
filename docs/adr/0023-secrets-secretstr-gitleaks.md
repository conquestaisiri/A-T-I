# ADR 0023: Secrets SecretStr + gitleaks

**Status:** Accepted (Tier-4 deferred, T4-34)
**Date:** 2026-08-22
**Context:** `sagax_loader.py:35` now `Path.home()/.config/ati/keys.env` portable, `settings.py:9` `Field(repr=False)` hides `api_key/ccxt_*`, `.dockerignore` ignores `.env/api_keys.env/data/*.db`.
**Decision:** Migrate remaining `api_key` fields to `SecretStr` + `repr=False` (log-safe), add `gitleaks`/`detect-secrets` pre-commit hook via `pyproject.toml` `[[tool.gitleaks]]`, `docker-compose.yml` `POSTGRES_PASSWORD` `:-` → `:?` required (fail-closed), `httpx` logs sanitize `Authorization` header.
**Consequences:** No hardcoded keys, `redact_key` `...last4` in logs, `SAGAX_KEYS_PATH` env override, ACL `USER+SYSTEM` for `keys.env`.
