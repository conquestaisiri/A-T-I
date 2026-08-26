# ADR 0022: DB Postgres DSN

**Status:** Accepted (Tier-4 deferred, T4-33)
**Date:** 2026-08-22
**Context:** `database.py:292` `sqlite3` single-writer is excellent dev/paper infra (`ARCHITECTURE_REVIEW.md:45`). `docker-compose.yml:51` already has `postgres:16-alpine` + `ati-api` `DB_PATH=postgresql://ati:${POSTGRES_PASSWORD}@postgres:5432/...` DSN interpolation (fixed). No migration until real constraint appears.
**Decision:** Keep `Database` SQLite for paper/dev, `Postgres` for `docker-compose` prod via `DATABASE_URL` single source. No `Database` interface change until `SQLite constraint` proven (critique §8). `data/trading_intelligence.db` remains gitignored, `WAL` + `busy_timeout` sufficient.
**Consequences:** No migration code now, `psycopg2` optional via `requirements-venue.txt`, `Database` stays V1.
