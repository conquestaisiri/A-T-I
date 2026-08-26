# backend/infrastructure/sqlite/database.py
"""SQLite connection management and schema initialization.

Owns the single writer connection and ensures the schema exists before any
repository uses it. Schema creation is idempotent.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS observation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_time TEXT NOT NULL,
    payload TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observation_events_symbol_time
    ON observation_events (symbol, event_time);

CREATE TABLE IF NOT EXISTS market_contexts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    created_at TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    features_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_contexts_symbol_created
    ON market_contexts (symbol, created_at);

CREATE TABLE IF NOT EXISTS decision_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL UNIQUE,
    correlation_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    created_at TEXT NOT NULL,
    confidence REAL NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decision_proposals_symbol_created
    ON decision_proposals (symbol, created_at);

CREATE TABLE IF NOT EXISTS trade_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL UNIQUE,
    proposal_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    status TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    realized_pnl REAL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trade_ledger_symbol_opened
    ON trade_ledger (symbol, opened_at);

CREATE TABLE IF NOT EXISTS memory_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id TEXT NOT NULL UNIQUE,
    correlation_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    created_at TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    outcome TEXT NOT NULL,
    realized_pnl REAL,
    summary TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_episodes_symbol_created
    ON memory_episodes (symbol, created_at);

CREATE TABLE IF NOT EXISTS reconciliation_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    reconciled_at TEXT NOT NULL,
    consistent INTEGER NOT NULL,
    discrepancy_count INTEGER NOT NULL,
    venue_signed REAL,
    internal_signed REAL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reconciliation_symbol_time
    ON reconciliation_reports (symbol, reconciled_at);

CREATE TABLE IF NOT EXISTS dataset_versions (
    dataset_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    kind TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    record_count INTEGER NOT NULL,
    source_start TEXT NOT NULL,
    source_end TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata TEXT NOT NULL,
    PRIMARY KEY (dataset_id, version)
);
CREATE INDEX IF NOT EXISTS idx_dataset_versions_dataset
    ON dataset_versions (dataset_id, created_at DESC);

CREATE TABLE IF NOT EXISTS dataset_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    kind TEXT NOT NULL,
    source_time TEXT NOT NULL,
    available_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE (dataset_id, version, id)
);
CREATE INDEX IF NOT EXISTS idx_dataset_records_lookup
    ON dataset_records (dataset_id, version, source_time);

CREATE TABLE IF NOT EXISTS test_period_locks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    claimed_by TEXT NOT NULL,
    claimed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_test_period_locks_dataset
    ON test_period_locks (dataset_id, start_time);

CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    dataset_version INTEGER NOT NULL,
    group_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    scorer_name TEXT NOT NULL,
    features TEXT NOT NULL,
    label_definition TEXT NOT NULL,
    cost_model TEXT NOT NULL,
    metrics TEXT NOT NULL,
    parent_experiment_id TEXT,
    failure_reason TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experiments_group_status
    ON experiments (group_kind, status);
CREATE INDEX IF NOT EXISTS idx_experiments_dataset
    ON experiments (dataset_id, dataset_version);

CREATE TABLE IF NOT EXISTS final_test_claims (
    dataset_id TEXT PRIMARY KEY,
    claimed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alt_data_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,
    event_time TEXT NOT NULL,
    published_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alt_data_published_lookup
    ON alt_data_events (published_at, symbol, kind);

CREATE TABLE IF NOT EXISTS autonomy_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL UNIQUE,
    candidate_id TEXT NOT NULL,
    status TEXT NOT NULL,
    action TEXT,
    target_days INTEGER NOT NULL,
    days_run INTEGER NOT NULL,
    sharpe REAL,
    drawdown_pct REAL,
    reason TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_autonomy_campaigns_candidate
    ON autonomy_campaigns (candidate_id, id DESC);

CREATE TABLE IF NOT EXISTS autonomy_day_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    day INTEGER NOT NULL,
    return_pct REAL NOT NULL,
    expected_return_pct REAL NOT NULL,
    failed_orders INTEGER NOT NULL,
    total_orders INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (campaign_id, day)
);
CREATE INDEX IF NOT EXISTS idx_autonomy_days_lookup
    ON autonomy_day_outcomes (candidate_id, campaign_id, day);

CREATE TABLE IF NOT EXISTS autonomy_program_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id TEXT NOT NULL UNIQUE,
    candidate_id TEXT NOT NULL,
    final_environment TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_autonomy_program_candidate
    ON autonomy_program_runs (candidate_id, id DESC);

CREATE TABLE IF NOT EXISTS autonomy_promotion_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    action TEXT NOT NULL,
    environment TEXT NOT NULL,
    allowed INTEGER NOT NULL,
    required TEXT NOT NULL,
    satisfied TEXT NOT NULL,
    reasons TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_autonomy_promotions_candidate
    ON autonomy_promotion_decisions (candidate_id, id DESC);

CREATE TABLE IF NOT EXISTS autonomy_rollbacks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    from_environment TEXT NOT NULL,
    to_environment TEXT,
    reasons TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_autonomy_rollbacks_candidate
    ON autonomy_rollbacks (candidate_id, id DESC);

CREATE TABLE IF NOT EXISTS strategy_passports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    passport_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    dataset_version INTEGER NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    verdict TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strategy_passports_status
    ON strategy_passports (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_passports_dataset
    ON strategy_passports (dataset_id, dataset_version);

CREATE TABLE IF NOT EXISTS passport_lifecycle_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    passport_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    reason TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_passport_lifecycle_passport
    ON passport_lifecycle_events (passport_id, id ASC);
"""


class Database:
    """Thin wrapper around a single SQLite connection.

    One connection per process is the V1 model: SQLite serialises writes and
    the observation pipeline is single-consumer. A threading lock ensures
    thread-safe access from web-server worker threads.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        if str(path) != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self.initialize()

    def initialize(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self._conn:
            self._conn.executescript(SCHEMA)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    @property
    def lock(self) -> threading.Lock:
        """Thread-safe lock for database access."""
        return self._lock

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()
