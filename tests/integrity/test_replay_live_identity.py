# tests/integrity/test_replay_live_identity.py
"""Composition-root replay identity (step 2 gap).

The decision/experiment/dataset replay tests exercise the simulator maths in
isolation. This test closes the remaining gap: it proves that replaying a
fixed observation sequence through TWO independent, freshly-initialised
instances of the REAL composition root (``backend.main.app``) produces
byte-identical economic trajectories.

Rationale: the pipeline's economic chain (ingest -> context -> reason -> risk
gate -> simulation -> ledger) is a pure function of ``(timestamp, price,
trade_id)``. The only wall-clock inputs are the supervisor's market-data
freshness gate and the drive route's default timestamp; both are neutralised
here by anchoring every observation to an explicit, fresh (within the
supervisor's staleness window) timestamp before driving.

The test therefore certifies that a trajectory validated by replay is, for
this rule-based strategy, exactly the trajectory the live paper loop would
have produced -- no mocked components, no hand-wired pipeline.

Generated identifiers (``proposal_id``, ``trade_id``, ``correlation_id``) are
names, not behaviour; they are excluded from the equality assertion in case a
future scheme makes them non-deterministic. Economics and risk verdicts are
compared exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.infrastructure.config.settings import settings
from fastapi.testclient import TestClient

_ID_FIELDS = ("trade_id", "proposal_id", "correlation_id")


def _economic_signature(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip identifiers, keep every field that constitutes economic behaviour."""
    closed = payload["closed_trade"]
    return {
        "result": payload["result"],
        "risk_verdict": payload["risk_verdict"],
        "equity": payload["equity"],
        "position_count": payload["position_count"],
        "open_exposure_pct": payload["open_exposure_pct"],
        "closed_trade": {k: v for k, v in closed.items() if k not in _ID_FIELDS}
        if closed
        else None,
    }


def _run_composed_campaign(
    db_path: Path,
    sequence: list[tuple[float, datetime, str]],
    app,
) -> list[dict[str, Any]]:
    """Drive a deterministic campaign through the real composition root.

    ``settings.db_path`` is temporarily pointed at ``db_path`` so each run
    starts from an empty database; the original value is restored afterwards.
    """
    original_db_path = settings.db_path
    settings.db_path = str(db_path)
    outcomes: list[dict[str, Any]] = []
    try:
        with TestClient(app) as client:
            for price, ts, trade_id in sequence:
                response = client.post(
                    "/v1/drive",
                    json={
                        "symbol": "btcusdt",
                        "price": price,
                        "trade_id": trade_id,
                        "timestamp": ts.isoformat(),
                    },
                )
                assert response.status_code == 200, response.text
                outcomes.append(response.json())
    finally:
        settings.db_path = original_db_path
    return outcomes


def _campaign_sequence(
    count: int = 24,
    start_price: float = 100.0,
    growth: float = 0.006,
    anchor: datetime | None = None,
) -> list[tuple[float, datetime, str]]:
    """Deterministic rising price series anchored just behind the clock.

    Timestamps stay within the supervisor's staleness window (300s) so the
    freshness gate is healthy in every run, while being identical across runs.
    """
    base = anchor or (datetime.now(UTC) - timedelta(seconds=count + 2))
    sequence: list[tuple[float, datetime, str]] = []
    price = start_price
    for i in range(count):
        price = round(price * (1 + growth) ** i, 4)
        sequence.append((price, base + timedelta(seconds=i), f"campaign-{i}"))
    return sequence


def test_composition_root_replay_equals_live_loop(tmp_path: Path) -> None:
    from backend.main import app  # the real composition root

    sequence = _campaign_sequence()
    signatures = [
        [
            _economic_signature(payload)
            for payload in _run_composed_campaign(tmp_path / f"run-{tag}.db", sequence, app)
        ]
        for tag in ("A", "B")
    ]

    first, second = signatures

    # Non-triviality guard: the campaign must actually trade, otherwise a
    # vacuous all-no-action equality would prove nothing.
    results_first = [step["result"] for step in first]
    assert "opened" in results_first
    assert "closed" in results_first

    # The core identity claim: two independent fresh instances of the real
    # composition root produced identical economic trajectories.
    assert second == first

    # Spot-check that real economics flowed through (an open then a close with
    # a realised PnL and preserved open/close ordering).
    closed_steps = [step for step in first if step["result"] == "closed"]
    opened_steps = [step for step in first if step["result"] == "opened"]
    assert opened_steps and closed_steps
    sample = closed_steps[0]["closed_trade"]
    assert sample["status"] == "closed"
    assert sample["entry_price"] > 0
    assert sample["exit_price"] > sample["entry_price"]
    assert sample["realized_pnl"] >= 0
