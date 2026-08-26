# backend/application/research/evidence_report.py
"""Operator-readable evidence report writer (task P5-003f).

Every passport plus its lifecycle ledger is archived to an append-only JSON
report under a reports directory, so the operator can review exactly how a
strategy earned (or failed) each rung — and P5-005's real-data runs can
produce the first honest evidence report from a single command.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.domain.research.passport import (
    PassportLifecycleEvent,
    StrategyPassport,
)


class EvidenceReportWriter:
    """Write append-only, operator-readable evidence reports to disk.

    Parameters
    ----------
    reports_dir: str | Path
        Root directory for evidence reports (default ``reports/evidence``).
    """

    def __init__(self, reports_dir: str | Path = "reports/evidence") -> None:
        self._reports_dir = Path(reports_dir)

    def write(
        self,
        passport: StrategyPassport,
        lifecycle: tuple[PassportLifecycleEvent, ...] = (),
        *,
        extra: dict[str, Any] | None = None,
        generated_at: datetime | None = None,
    ) -> Path:
        """Write one report file; refuses to overwrite an existing report.

        The report carries the full passport provenance plus its lifecycle
        ledger and the generation timestamp. Returns the written path.
        """
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        path = self._reports_dir / f"{passport.passport_id}.json"
        if path.exists():
            raise ValueError(
                f"evidence report {path} already exists: the archive is "
                "append-only; use a new passport id or a different directory"
            )
        payload: dict[str, Any] = {
            "report_type": "strategy_passport",
            "generated_at": (generated_at or datetime.now(UTC)).isoformat(timespec="milliseconds"),
            "passport": passport.as_dict(),
            "lifecycle": [event.as_dict() for event in lifecycle],
        }
        if extra:
            payload["extra"] = extra
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return path
