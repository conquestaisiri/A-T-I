# backend/application/research/evidence_engine.py
"""Evidence engine (task P5-003c): turns OOS reports into auditable passports.

The evidence engine is the single seam where an evaluated strategy becomes a
durable, auditable record: it takes the out-of-sample report produced by
``DecisionPipelineEvaluator`` (P1-009) plus its PBO/Deflated Sharpe context
(P5-001), applies the conservative evidence verdict (never promoted past
paper), and persists the resulting :class:`StrategyPassport` through the
``PassportStore`` (P5-003b).

The engine enforces the corpus rules: a passport id can only be issued once
(records are immutable facts); every later change is an append-only lifecycle
event on top of an immutable snapshot. This is the audit trail the Strategic
Review demands before any promotion or retirement.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from backend.application.interfaces.passport_store import PassportStore
from backend.application.research.decision_pipeline_evaluator import (
    OutOfSampleReport,
)
from backend.domain.research.canary import CanaryProgramResult
from backend.domain.research.oos_evaluation import PooledEvidence
from backend.domain.research.paper_campaign import PaperCampaignResult
from backend.domain.research.passport import (
    EvidenceVerdict,
    PassportLifecycleEvent,
    PassportStatus,
    StrategyPassport,
    verdict_for_evidence,
)
from backend.domain.research.pbo import PboResult
from backend.domain.research.promotion import RollbackDecision


class EvidenceEngine:
    """Issue and maintain auditable strategy passports from OOS evidence.

    Parameters
    ----------
    store: PassportStore
        The durable passport ledger (SQLite repository).
    max_pbo: float
        PBO gate for the evidence verdict (default 0.5).
    """

    def __init__(self, store: PassportStore, *, max_pbo: float = 0.5) -> None:
        self._store = store
        self._max_pbo = max_pbo

    def issue_passport(
        self,
        *,
        passport_id: str,
        hypothesis: str,
        dataset_id: str,
        dataset_version: int,
        features: tuple[str, ...],
        model: str,
        trial_count: int,
        report: OutOfSampleReport,
        pbo: PboResult | None = None,
        regime_evidence: Mapping[str, Any] | None = None,
        attribution_evidence: Mapping[str, Any] | None = None,
        robustness_evidence: Mapping[str, Any] | None = None,
        experiment_id: str | None = None,
        train_period: tuple[str, str] | None = None,
        validation_period: tuple[str, str] | None = None,
        test_period: tuple[str, str] | None = None,
        now: datetime | None = None,
    ) -> StrategyPassport:
        """Compose the OOS report into a passport, verdict it, and persist it.

        The verdict comes from :func:`verdict_for_evidence` on the pooled
        fold evidence (with the PBO family when variants were evaluated). The
        issued status is ``CANDIDATE`` when the verdict is not REJECT
        (evidence gathered), else ``RETIRED`` (dead on arrival). Raises
        ``ValueError`` when the passport id already exists (immutability).
        """
        verdict = verdict_for_evidence(report.pooled, pbo=pbo, max_pbo=self._max_pbo)
        issued_at = now or datetime.now(UTC)

        passport = StrategyPassport(
            passport_id=passport_id,
            created_at=issued_at,
            hypothesis=hypothesis,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            features=features,
            model=model,
            trial_count=trial_count,
            train_period=train_period,
            validation_period=validation_period,
            test_period=test_period,
            cost_model={
                "half_spread_pct": report.costs.half_spread_pct,
                "taker_fee_pct": report.costs.taker_fee_pct,
            },
            evidence=_evidence_payload(
                report, pbo, regime_evidence, attribution_evidence, robustness_evidence
            ),
            verdict=verdict,
            status=(
                PassportStatus.CANDIDATE
                if verdict.verdict is not EvidenceVerdict.REJECT
                else PassportStatus.RETIRED
            ),
            experiment_id=experiment_id,
            promotion_requirements=_promotion_requirements(),
            rollback_requirements=_rollback_requirements(),
            last_review=issued_at,
        )
        self._store.save_passport(passport)
        return passport

    def transition(
        self,
        passport_id: str,
        to_status: PassportStatus,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> StrategyPassport:
        """Move a passport's lifecycle status, recording the audit event.

        The change is recorded as an append-only lifecycle event and the
        snapshot is replaced (the passport id itself is immutable).

        T3-28-1 (terminality): RETIRED is the terminal death state — a
        retired passport can never be transitioned again (no resurrection,
        no double death). Attempts raise ``ValueError``.
        """
        passport = self._require_alive(passport_id)
        occurred_at = now or datetime.now(UTC)
        event = PassportLifecycleEvent(
            passport_id=passport_id,
            event_type="status_change",
            occurred_at=occurred_at,
            from_status=passport.status,
            to_status=to_status,
            reason=reason,
        )
        self._store.append_lifecycle_event(event)
        updated = replace(
            passport,
            status=to_status,
            last_review=occurred_at,
        )
        self._store.replace_passport(updated)
        return updated

    def rerecord_evidence(
        self,
        passport_id: str,
        *,
        report: OutOfSampleReport,
        pbo: PboResult | None = None,
        regime_evidence: Mapping[str, Any] | None = None,
        attribution_evidence: Mapping[str, Any] | None = None,
        robustness_evidence: Mapping[str, Any] | None = None,
        reason: str,
        now: datetime | None = None,
    ) -> StrategyPassport:
        """Replace a passport's evidence (new evaluation round) and re-verdict.

        Recorded as an ``evidence_update`` lifecycle event; the new snapshot
        keeps its lifecycle status unless the verdict is REJECT, in which
        case the passport is retired with the rejection reason (death
        system: strategy death is a feature).

        T3-28-1 (terminality): a retired passport cannot be re-evaluated —
        the dead hypothesis must be revised and re-issued as a *new*
        passport. Attempts raise ``ValueError``.
        """
        passport = self._require_alive(passport_id)
        verdict = verdict_for_evidence(report.pooled, pbo=pbo, max_pbo=self._max_pbo)
        occurred_at = now or datetime.now(UTC)
        new_status = (
            PassportStatus.RETIRED if verdict.verdict is EvidenceVerdict.REJECT else passport.status
        )
        event = PassportLifecycleEvent(
            passport_id=passport_id,
            event_type="evidence_update",
            occurred_at=occurred_at,
            from_status=passport.status,
            to_status=new_status,
            reason=reason,
        )
        self._store.append_lifecycle_event(event)
        updated = replace(
            passport,
            status=new_status,
            evidence=_evidence_payload(
                report, pbo, regime_evidence, attribution_evidence, robustness_evidence
            ),
            cost_model={
                "half_spread_pct": report.costs.half_spread_pct,
                "taker_fee_pct": report.costs.taker_fee_pct,
            },
            verdict=verdict,
            last_review=occurred_at,
        )
        self._store.replace_passport(updated)
        return updated

    def record_paper_campaign(
        self,
        passport_id: str,
        *,
        result: PaperCampaignResult | Mapping[str, Any],
        reason: str,
        now: datetime | None = None,
    ) -> StrategyPassport:
        """Attach a paper campaign outcome snapshot to a passport.

        T3-24-1 seam: the campaign service produces
        ``PaperCampaignResult`` (verdict, days run, sharpe, drawdown,
        promotion evidence, per-day periods); this method appends it to
        the passport's ``paper_evidence`` as an append-only
        ``paper_campaign_update`` lifecycle event without touching the
        lifecycle status — campaign outcomes are on the audit trail
        before any canary or demotion decision may use them, and the
        evidence section of the passport is a faithful reproduction of
        what the campaign actually earned.
        """
        payload = result.as_dict() if isinstance(result, PaperCampaignResult) else dict(result)
        passport = self._require_alive(passport_id)
        occurred_at = now or datetime.now(UTC)
        event = PassportLifecycleEvent(
            passport_id=passport_id,
            event_type="paper_campaign_update",
            occurred_at=occurred_at,
            from_status=passport.status,
            to_status=passport.status,
            reason=reason,
        )
        self._store.append_lifecycle_event(event)
        updated = replace(
            passport,
            paper_evidence={"paper_campaign": payload},
            last_review=occurred_at,
        )
        self._store.replace_passport(updated)
        return updated

    def record_rollback(
        self,
        passport_id: str,
        *,
        decision: RollbackDecision | Mapping[str, Any],
        reason: str,
        now: datetime | None = None,
    ) -> StrategyPassport:
        """Attach a rollback decision record to a passport.

        T3-27-1 seam: the death system produces ``RollbackDecision``
        (rollback flag, demotion target, reasons); this method appends it
        to the passport's ``live_evidence`` as an append-only
        ``rollback_update`` lifecycle event without touching the lifecycle
        status — the rollback decision is on the audit trail, and the
        passport keeps the reasoned record of every automatic demotion.

        Deliberately NOT blocked on retired passports (T3-28-1): the death
        system records the rollback right after the RETIRE transition, so
        this method is how the death record closes. The audit trail may
        always append the reason a strategy died.
        """
        payload = decision.as_dict() if isinstance(decision, RollbackDecision) else dict(decision)
        passport = self._require(passport_id)
        occurred_at = now or datetime.now(UTC)
        event = PassportLifecycleEvent(
            passport_id=passport_id,
            event_type="rollback_update",
            occurred_at=occurred_at,
            from_status=passport.status,
            to_status=passport.status,
            reason=reason,
        )
        self._store.append_lifecycle_event(event)
        updated = replace(
            passport,
            live_evidence={"rollback": payload},
            last_review=occurred_at,
        )
        self._store.replace_passport(updated)
        return updated

    def record_canary(
        self,
        passport_id: str,
        *,
        result: CanaryProgramResult | Mapping[str, Any],
        reason: str,
        now: datetime | None = None,
    ) -> StrategyPassport:
        """Attach a canary campaign outcome snapshot to a passport.

        T3-25-1 seam: the harness produces ``CanaryProgramResult``
        (verdict, days run, per-period actions, reasons) from the
        bounded, operator-authorized live-touch campaign; this method
        appends it to the passport's ``live_evidence`` as an append-only
        ``canary_update`` lifecycle event without touching the lifecycle
        status — canary outcomes are on the audit trail before any
        production or demotion decision may use them, and the live
        evidence section of the passport is a faithful reproduction of
        what the canary actually earned.
        """
        payload = result.as_dict() if isinstance(result, CanaryProgramResult) else dict(result)
        passport = self._require_alive(passport_id)
        occurred_at = now or datetime.now(UTC)
        event = PassportLifecycleEvent(
            passport_id=passport_id,
            event_type="canary_update",
            occurred_at=occurred_at,
            from_status=passport.status,
            to_status=passport.status,
            reason=reason,
        )
        self._store.append_lifecycle_event(event)
        updated = replace(
            passport,
            live_evidence={"canary": payload},
            last_review=occurred_at,
        )
        self._store.replace_passport(updated)
        return updated

    def record_calibration(
        self,
        passport_id: str,
        *,
        report: Mapping[str, Any],
        reason: str,
        now: datetime | None = None,
    ) -> StrategyPassport:
        """Attach a live-vs-paper calibration snapshot to a passport.

        P5-004 seam: the calibration harness produces a
        ``CalibrationReport.as_dict()``; this method appends it to the
        passport's ``live_evidence`` as an append-only ``calibration_update``
        lifecycle event without touching the lifecycle status — so
        live-vs-paper drift is on the audit trail before any rollback or
        promotion decision may use it.
        """
        passport = self._require_alive(passport_id)
        occurred_at = now or datetime.now(UTC)
        event = PassportLifecycleEvent(
            passport_id=passport_id,
            event_type="calibration_update",
            occurred_at=occurred_at,
            from_status=passport.status,
            to_status=passport.status,
            reason=reason,
        )
        self._store.append_lifecycle_event(event)
        updated = replace(
            passport,
            live_evidence={"calibration": dict(report)},
            last_review=occurred_at,
        )
        self._store.replace_passport(updated)
        return updated

    def passport(self, passport_id: str) -> StrategyPassport | None:
        """Latest passport snapshot by id."""
        return self._store.load_passport(passport_id)

    def lifecycle(self, passport_id: str) -> tuple[PassportLifecycleEvent, ...]:
        """The passport's append-only lifecycle ledger, oldest first."""
        return self._store.lifecycle(passport_id)

    def all_passports(self) -> tuple[StrategyPassport, ...]:
        """Every issued passport (strategy population view, T2-12)."""
        return self._store.all_passports()

    def _require(self, passport_id: str) -> StrategyPassport:
        passport = self._store.load_passport(passport_id)
        if passport is None:
            raise ValueError(f"unknown passport {passport_id!r}")
        return passport

    def _require_alive(self, passport_id: str) -> StrategyPassport:
        """Require the passport to exist and not be dead (T3-28-1).

        RETIRED is terminal: only the audit trail may touch a retired
        passport (``record_rollback`` closes the death record). Everything
        else refuses with a ValueError naming the tombstone state.
        """
        passport = self._require(passport_id)
        if passport.status is PassportStatus.RETIRED:
            raise ValueError(
                f"passport {passport_id!r} is retired (terminal): "
                "dead strategies cannot be transitioned, re-evaluated, "
                "re-calibrated or start new campaigns"
            )
        return passport


def _evidence_payload(
    report: OutOfSampleReport,
    pbo: PboResult | None,
    regime_evidence: Mapping[str, Any] | None,
    attribution_evidence: Mapping[str, Any] | None = None,
    robustness_evidence: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """The auditable evidence block: pooled scorecard + PBO + CV spec.

    When ``regime_evidence`` is supplied (T2-11-1, the book's regime-
    performance evidence builder) it is embedded verbatim so the passport's
    robustness surface is reproducible from the exact report that produced it.
    When ``attribution_evidence`` is supplied (T2-16-1, the feature
    attribution report) it is embedded verbatim the same way, so the
    passport carries the measured contribution of each feature to the
    evaluated model — reproducible, never summarised away. When
    ``robustness_evidence`` is supplied (T3-23-1, the perturbation /
    expense-stress / selection-bias bundle) it is embedded verbatim the
    same way, so the passport's robustness surface is reproducible from
    the exact reports that produced it.
    """
    pooled: PooledEvidence = report.pooled
    payload: dict[str, Any] = {
        "pooled": pooled.as_dict(),
        "symbol": report.symbol,
        "cv_spec": dict(report.cv_spec),
    }
    if pbo is not None:
        payload["pbo"] = pbo.as_dict()
    if regime_evidence is not None:
        payload["regime_evidence"] = dict(regime_evidence)
    if attribution_evidence is not None:
        payload["attribution_evidence"] = dict(attribution_evidence)
    if robustness_evidence is not None:
        payload["robustness_evidence"] = dict(robustness_evidence)
    return payload


def _promotion_requirements() -> tuple[str, ...]:
    return (
        "statistical evidence: positive deflated Sharpe (P5-001)",
        "stability evidence: positive-fold rate >= 0.5, beats buy-and-hold >= 0.5",
        "execution evidence: results hold under the shared cost ruler",
        "risk evidence: mean max drawdown within limits",
        "paper evidence: paper campaign outcomes (T3-24)",
        "canary evidence: canary performance (T3-25)",
    )


def _rollback_requirements() -> tuple[str, ...]:
    return (
        "edge decay: drift/ADWIN triggers on rolling OOS or paper returns",
        "regime shift: strategy performance breaks regime robustness bounds",
        "execution failure: live-vs-paper calibration drift beyond bounds",
    )
