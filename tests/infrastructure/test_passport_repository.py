"""Tests for SQLite passport persistence (P5-003b).

The store must honour the corpus rules: passports are immutable facts (a
second save over the same id raises), lifecycle events append in order, and
snapshots round-trip exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.domain.research.passport import (
    EvidenceVerdict,
    PassportLifecycleEvent,
    PassportStatus,
    PassportVerdict,
    StrategyPassport,
)
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.passport_repository import (
    SqlitePassportRepository,
)


def passport(passport_id: str = "STRAT-1", status: PassportStatus = PassportStatus.CANDIDATE):
    return StrategyPassport(
        passport_id=passport_id,
        created_at=datetime(2026, 8, 13, tzinfo=UTC),
        hypothesis="h",
        dataset_id="btcusdt",
        dataset_version=1,
        features=("ofi",),
        model="RuleBasedSolver",
        trial_count=10,
        cost_model={"half_spread_pct": 0.0002},
        evidence={"pooled": {"n_folds": 8}},
        verdict=PassportVerdict(EvidenceVerdict.PROMOTE_TO_PAPER, ("passed",)),
        status=status,
        last_review=datetime(2026, 8, 13, tzinfo=UTC),
    )


class TestPassportRepository:
    def test_save_and_load_round_trip(self, tmp_path):
        repo = SqlitePassportRepository(Database(tmp_path / "p.db"))
        repo.save_passport(passport())
        loaded = repo.load_passport("STRAT-1")
        assert loaded is not None
        assert loaded == passport()
        assert loaded.verdict.verdict is EvidenceVerdict.PROMOTE_TO_PAPER

    def test_duplicate_save_is_refused(self, tmp_path):
        repo = SqlitePassportRepository(Database(tmp_path / "p.db"))
        repo.save_passport(passport())
        with pytest.raises(ValueError, match="already exists"):
            repo.save_passport(passport())

    def test_load_unknown_returns_none(self, tmp_path):
        repo = SqlitePassportRepository(Database(tmp_path / "p.db"))
        assert repo.load_passport("nope") is None

    def test_lifecycle_events_append_in_order(self, tmp_path):
        repo = SqlitePassportRepository(Database(tmp_path / "p.db"))
        repo.save_passport(passport(status=PassportStatus.CANDIDATE))
        repo.append_lifecycle_event(
            PassportLifecycleEvent(
                passport_id="STRAT-1",
                event_type="status_change",
                occurred_at=datetime(2026, 8, 14, tzinfo=UTC),
                from_status=PassportStatus.CANDIDATE,
                to_status=PassportStatus.PAPER,
                reason="paper campaign approved",
            )
        )
        repo.append_lifecycle_event(
            PassportLifecycleEvent(
                passport_id="STRAT-1",
                event_type="status_change",
                occurred_at=datetime(2026, 8, 15, tzinfo=UTC),
                from_status=PassportStatus.PAPER,
                to_status=PassportStatus.RETIRED,
                reason="edge decay detected",
            )
        )
        events = repo.lifecycle("STRAT-1")
        assert len(events) == 2
        assert [e.to_status for e in events] == [
            PassportStatus.PAPER,
            PassportStatus.RETIRED,
        ]

    def test_lifecycle_event_on_unknown_passport_is_refused(self, tmp_path):
        repo = SqlitePassportRepository(Database(tmp_path / "p.db"))
        with pytest.raises(ValueError, match="unknown"):
            repo.append_lifecycle_event(
                PassportLifecycleEvent(
                    passport_id="ghost",
                    event_type="status_change",
                    occurred_at=datetime(2026, 8, 14, tzinfo=UTC),
                    reason="x",
                )
            )

    def test_replace_requires_existing_and_updates_snapshot(self, tmp_path):
        repo = SqlitePassportRepository(Database(tmp_path / "p.db"))
        repo.save_passport(passport())
        repo.replace_passport(passport(status=PassportStatus.RETIRED))
        updated = repo.load_passport("STRAT-1")
        assert updated is not None
        assert updated.status is PassportStatus.RETIRED
        with pytest.raises(ValueError, match="unknown"):
            repo.replace_passport(passport(passport_id="ghost"))

    def test_all_passports_in_insertion_order(self, tmp_path):
        repo = SqlitePassportRepository(Database(tmp_path / "p.db"))
        repo.save_passport(passport("A"))
        repo.save_passport(passport("B"))
        ids = [p.passport_id for p in repo.all_passports()]
        assert ids == ["A", "B"]

    def test_persistence_across_instances(self, tmp_path):
        db = Database(tmp_path / "p.db")
        SqlitePassportRepository(db).save_passport(passport())
        reloaded = SqlitePassportRepository(db).load_passport("STRAT-1")
        assert reloaded == passport()

    def test_durable_across_connections(self, tmp_path):
        # A write must be committed, not just visible on the writing
        # connection: a second Database instance on the same file (the
        # registry/ladder reads) must see the passport. Regression test for
        # the missing-commit bug found while wiring T2-13-1.
        path = tmp_path / "p.db"
        SqlitePassportRepository(Database(path)).save_passport(passport())
        fresh = SqlitePassportRepository(Database(path))
        assert fresh.load_passport("STRAT-1") == passport()
        assert [p.passport_id for p in fresh.all_passports()] == ["STRAT-1"]
