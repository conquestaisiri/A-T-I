# backend/infrastructure/sqlite/autonomy_repository.py
"""SQLite implementation of the AutonomyStore port (workstream WS1).

Stores the durable autonomy outcome corpus: paper-campaign lifecycle records,
per-day outcomes, composed autonomy-program runs, promotion gate decisions and
automatic rollbacks. All five tables share the same discipline as the rest of
the persistence layer: immutable keys, append-only audit rows, forward-only
campaign transitions, and JSON payloads for full round-trips.
"""

from __future__ import annotations

import json
import sqlite3

from backend.application.interfaces.autonomy_store import AutonomyStore
from backend.domain.research.paper_campaign import PaperCampaignAction
from backend.domain.research.promotion import ModelEnvironment
from backend.domain.research.records import (
    CampaignRunRecord,
    CampaignStatus,
    DayOutcomeRecord,
    ProgramRunRecord,
    PromotionAction,
    PromotionDecisionRecord,
    RollbackRecord,
    StageSnapshot,
)
from backend.infrastructure.sqlite.database import Database

_TERMINAL = (
    CampaignStatus.COMPLETED,
    CampaignStatus.RETIRED,
    CampaignStatus.CANCELLED,
)


class SqliteAutonomyStore(AutonomyStore):
    """Persists the autonomy outcome corpus in SQLite."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._conn = database.connection

    # -- paper campaign lifecycle -------------------------------------------

    def save_campaign(self, record: CampaignRunRecord) -> None:
        try:
            with self._db.lock, self._conn:
                self._conn.execute(
                    """
                    INSERT INTO autonomy_campaigns
                        (campaign_id, candidate_id, status, action, target_days,
                         days_run, sharpe, drawdown_pct, reason, started_at,
                         completed_at, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _campaign_row(record),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"campaign {record.campaign_id} already exists (immutable records)"
            ) from exc

    def get_campaign(self, campaign_id: str) -> CampaignRunRecord | None:
        row = self._conn.execute(
            "SELECT * FROM autonomy_campaigns WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        return _row_to_campaign(row) if row is not None else None

    def list_campaigns(
        self,
        *,
        candidate_id: str | None = None,
        status: CampaignStatus | None = None,
    ) -> list[CampaignRunRecord]:
        query = "SELECT * FROM autonomy_campaigns"
        clauses: list[str] = []
        params: list[str] = []
        if candidate_id is not None:
            clauses.append("candidate_id = ?")
            params.append(candidate_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_campaign(row) for row in rows]

    def set_campaign_status(
        self,
        campaign_id: str,
        status: CampaignStatus,
        *,
        days_run: int | None = None,
        sharpe: float | None = None,
        drawdown_pct: float | None = None,
        action: str | None = None,
        reason: str = "",
        completed_at: str = "",
    ) -> CampaignRunRecord:
        row = self._conn.execute(
            "SELECT * FROM autonomy_campaigns WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown campaign {campaign_id}")
        current = CampaignStatus(row["status"])
        if current in _TERMINAL:
            raise ValueError(f"cannot transition terminal campaign {campaign_id} ({current.value})")
        if current is CampaignStatus.RUNNING and status is CampaignStatus.PENDING:
            raise ValueError(f"cannot reopen campaign {campaign_id} to pending")
        resolved_action = (
            PaperCampaignAction(action)
            if action is not None
            else (PaperCampaignAction(row["action"]) if row["action"] is not None else None)
        )
        stored = CampaignRunRecord(
            candidate_id=row["candidate_id"],
            campaign_id=row["campaign_id"],
            status=status,
            action=resolved_action,
            target_days=row["target_days"],
            days_run=row["days_run"] if days_run is None else days_run,
            sharpe=row["sharpe"] if sharpe is None else sharpe,
            drawdown_pct=row["drawdown_pct"] if drawdown_pct is None else drawdown_pct,
            reason=reason,
            started_at=row["started_at"],
            completed_at=completed_at,
        )
        with self._db.lock, self._conn:
            self._conn.execute(
                """
                UPDATE autonomy_campaigns
                SET status = ?, action = ?, days_run = ?, sharpe = ?,
                    drawdown_pct = ?, reason = ?, completed_at = ?, payload = ?
                WHERE campaign_id = ?
                """,
                (
                    stored.status.value,
                    stored.action.value if stored.action else None,
                    stored.days_run,
                    stored.sharpe,
                    stored.drawdown_pct,
                    stored.reason,
                    stored.completed_at,
                    json.dumps(stored.as_dict(), sort_keys=True),
                    campaign_id,
                ),
            )
        return stored

    # -- paper day outcomes --------------------------------------------------

    def save_day_outcome(self, record: DayOutcomeRecord) -> None:
        try:
            with self._db.lock, self._conn:
                self._conn.execute(
                    """
                    INSERT INTO autonomy_day_outcomes
                        (candidate_id, campaign_id, day, return_pct,
                         expected_return_pct, failed_orders, total_orders,
                         recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _day_row(record),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"day {record.day} of campaign {record.campaign_id} already recorded"
            ) from exc

    def list_day_outcomes(
        self,
        *,
        candidate_id: str | None = None,
        campaign_id: str | None = None,
    ) -> list[DayOutcomeRecord]:
        query = "SELECT * FROM autonomy_day_outcomes"
        clauses: list[str] = []
        params: list[str] = []
        if candidate_id is not None:
            clauses.append("candidate_id = ?")
            params.append(candidate_id)
        if campaign_id is not None:
            clauses.append("campaign_id = ?")
            params.append(campaign_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY campaign_id, day"
        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_day(row) for row in rows]

    # -- autonomy program runs ----------------------------------------------

    def save_program_run(self, record: ProgramRunRecord) -> None:
        try:
            with self._db.lock, self._conn:
                self._conn.execute(
                    """
                    INSERT INTO autonomy_program_runs
                        (program_id, candidate_id, final_environment, started_at,
                         completed_at, payload)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    _program_row(record),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"program run {record.program_id} already exists (immutable records)"
            ) from exc

    def get_program_run(self, program_id: str) -> ProgramRunRecord | None:
        row = self._conn.execute(
            "SELECT * FROM autonomy_program_runs WHERE program_id = ?", (program_id,)
        ).fetchone()
        return _row_to_program(row) if row is not None else None

    def list_program_runs(
        self,
        *,
        candidate_id: str | None = None,
        final_environment: str | None = None,
    ) -> list[ProgramRunRecord]:
        query = "SELECT * FROM autonomy_program_runs"
        clauses: list[str] = []
        params: list[str] = []
        if candidate_id is not None:
            clauses.append("candidate_id = ?")
            params.append(candidate_id)
        if final_environment is not None:
            clauses.append("final_environment = ?")
            params.append(final_environment)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_program(row) for row in rows]

    # -- promotion audit trail ----------------------------------------------

    def save_promotion_decision(self, record: PromotionDecisionRecord) -> None:
        with self._db.lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO autonomy_promotion_decisions
                    (candidate_id, action, environment, allowed, required,
                     satisfied, reasons, occurred_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _promotion_row(record),
            )

    def list_promotion_decisions(
        self,
        *,
        candidate_id: str | None = None,
    ) -> list[PromotionDecisionRecord]:
        query = "SELECT * FROM autonomy_promotion_decisions"
        params: list[str] = []
        if candidate_id is not None:
            query += " WHERE candidate_id = ?"
            params.append(candidate_id)
        query += " ORDER BY id DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_promotion(row) for row in rows]

    def save_rollback(self, record: RollbackRecord) -> None:
        with self._db.lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO autonomy_rollbacks
                    (candidate_id, from_environment, to_environment, reasons,
                     occurred_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                _rollback_row(record),
            )

    def list_rollbacks(
        self,
        *,
        candidate_id: str | None = None,
    ) -> list[RollbackRecord]:
        query = "SELECT * FROM autonomy_rollbacks"
        params: list[str] = []
        if candidate_id is not None:
            query += " WHERE candidate_id = ?"
            params.append(candidate_id)
        query += " ORDER BY id DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_rollback(row) for row in rows]


def _campaign_row(record: CampaignRunRecord) -> tuple[object, ...]:
    return (
        record.campaign_id,
        record.candidate_id,
        record.status.value,
        record.action.value if record.action else None,
        record.target_days,
        record.days_run,
        record.sharpe,
        record.drawdown_pct,
        record.reason,
        record.started_at,
        record.completed_at,
        json.dumps(record.as_dict(), sort_keys=True),
    )


def _row_to_campaign(row: sqlite3.Row) -> CampaignRunRecord:
    return CampaignRunRecord(
        candidate_id=row["candidate_id"],
        campaign_id=row["campaign_id"],
        status=CampaignStatus(row["status"]),
        action=row["action"],
        target_days=row["target_days"],
        days_run=row["days_run"],
        sharpe=row["sharpe"],
        drawdown_pct=row["drawdown_pct"],
        reason=row["reason"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _day_row(record: DayOutcomeRecord) -> tuple[object, ...]:
    return (
        record.candidate_id,
        record.campaign_id,
        record.day,
        record.return_pct,
        record.expected_return_pct,
        record.failed_orders,
        record.total_orders,
        record.recorded_at,
    )


def _row_to_day(row: sqlite3.Row) -> DayOutcomeRecord:
    return DayOutcomeRecord(
        candidate_id=row["candidate_id"],
        campaign_id=row["campaign_id"],
        day=row["day"],
        return_pct=row["return_pct"],
        expected_return_pct=row["expected_return_pct"],
        failed_orders=row["failed_orders"],
        total_orders=row["total_orders"],
        recorded_at=row["recorded_at"],
    )


def _program_row(record: ProgramRunRecord) -> tuple[object, ...]:
    return (
        record.program_id,
        record.candidate_id,
        record.final_environment,
        record.started_at,
        record.completed_at,
        json.dumps(record.as_dict(), sort_keys=True),
    )


def _row_to_program(row: sqlite3.Row) -> ProgramRunRecord:
    payload = json.loads(row["payload"])
    return ProgramRunRecord(
        program_id=payload["program_id"],
        candidate_id=row["candidate_id"],
        final_environment=row["final_environment"],
        earned=tuple(payload["earned"]),
        stages=tuple(
            StageSnapshot(
                stage=s["stage"],
                verdict=s["verdict"],
                reason=s.get("reason", ""),
                evidence=s.get("evidence"),
            )
            for s in payload.get("stages", [])
        ),
        notes=tuple(payload.get("notes", [])),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _promotion_row(record: PromotionDecisionRecord) -> tuple[object, ...]:
    return (
        record.candidate_id,
        record.action.value,
        record.environment.value,
        int(record.allowed),
        json.dumps(list(record.required), sort_keys=True),
        json.dumps(list(record.satisfied), sort_keys=True),
        json.dumps(list(record.reasons), sort_keys=True),
        record.occurred_at,
        json.dumps(record.as_dict(), sort_keys=True),
    )


def _row_to_promotion(row: sqlite3.Row) -> PromotionDecisionRecord:
    return PromotionDecisionRecord(
        candidate_id=row["candidate_id"],
        action=PromotionAction(row["action"]),
        environment=ModelEnvironment(row["environment"]),
        allowed=bool(row["allowed"]),
        required=tuple(json.loads(row["required"])),
        satisfied=tuple(json.loads(row["satisfied"])),
        reasons=tuple(json.loads(row["reasons"])),
        occurred_at=row["occurred_at"],
    )


def _rollback_row(record: RollbackRecord) -> tuple[object, ...]:
    return (
        record.candidate_id,
        record.from_environment.value,
        record.to_environment.value if record.to_environment else None,
        json.dumps(list(record.reasons), sort_keys=True),
        record.occurred_at,
        json.dumps(record.as_dict(), sort_keys=True),
    )


def _row_to_rollback(row: sqlite3.Row) -> RollbackRecord:
    return RollbackRecord(
        candidate_id=row["candidate_id"],
        from_environment=ModelEnvironment(row["from_environment"]),
        to_environment=(
            ModelEnvironment(row["to_environment"]) if row["to_environment"] is not None else None
        ),
        reasons=tuple(json.loads(row["reasons"])),
        occurred_at=row["occurred_at"],
    )
