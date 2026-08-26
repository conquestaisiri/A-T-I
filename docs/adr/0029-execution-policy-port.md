# ADR 0029: ExecutionPolicy Port (Hummingbot-Inspired)

**Status:** Proposed
**Date:** 2026-08-24
**Context:** ATI's PaperFillEngine only supports market orders. Real profitability depends on execution efficiency: passive limit fills save the taker fee AND capture spread. Hummingbot's executor lifecycle proves that execution intelligence can improve PnL without improving prediction.
**License:** Apache-2.0 (Hummingbot) — concepts only, zero code copied.

## Problem

ATI currently:
1. Submits market orders → always pays taker fee (0.04%)
2. Fills at synthetic spread (~1 bps slippage each side)
3. Never considers whether passive execution would be cheaper
4. Never cancels/re-prices based on signal decay

On a strategy with 0.1% expected edge per trade, paying 4 bps × 2 sides = 8 bps in fees + ~2 bps slippage = 10 bps total cost consumes 10% of the edge. If half those trades could fill passively at maker rate (0.02%), cost drops to ~6 bps, saving 40% of execution cost.

## Proposed Design

```
AI Decision Engine
       ↓
  Risk Governor (veto authority, unchanged)
       ↓
ExecutionPolicy (NEW PORT)
       ↓
OrderGateway (PaperFillEngine / CcxtOrderGateway)
       ↓
Venue
```

### ExecutionPolicy Port

```python
class ExecutionPolicy(Protocol):
    """Decides HOW to execute a risk-approved action."""

    def plan_execution(
        self,
        proposal: DecisionProposal,
        market_state: MarketStateSnapshot,
    ) -> ExecutionPlan: ...

class ExecutionPlan:
    order_type: OrderType          # MARKET or LIMIT
    slices: list[OrderSlice]       # time/quantity slicing for large orders
    post_only: bool                # maker-only if True
    cancel_after_seconds: float    # signal-decay timeout
    reprice_on_spread_widen: bool  # cancel if spread exceeds threshold
```

### Policy implementations (phase-in order)

| Phase | Policy | When used | Expected saving |
|---|---|---|---|
| P0 | AlwaysMarket (current behavior) | default, no change | baseline |
| P1 | PassiveIfSpreadTight | spread < 2 bps → post-only limit at mid | 2 bps fee saved on filled orders |
| P2 | SignalDecayCancel | cancel passive if momentum feature drops >50% | avoids stale fills |
| P3 | SlicedTWAP | size > X% of ADV → slice into N child orders | reduces impact |

### Integration points

- `DecisionPipelineService._execute_approved()` calls `policy.plan_execution()` after risk approval
- PaperFillEngine gains `submit_limit(post_only=True)` support
- CalibrationHarness already measures live-vs-paper; extend to compare policy-vs-always-market

### What we do NOT build

- No full Hummingbot integration (too heavy, different architecture)
- No cross-exchange arbitrage (out of scope)
- No liquidity mining / market-making (different strategy class)
- No PMM (pure market making) — that's a strategy, not an execution policy

## Evidence needed before adoption

1. Simulate PassiveIfSpreadTight on btcusdt-1h v1: what % of limit orders fill?
2. What is the opportunity cost of missed fills vs fee savings?
3. Does signal-decay-cancel improve or hurt net PnL?

## Verdict

ADOPT as design — implement `ExecutionPolicy` port + `AlwaysMarket` default (zero-risk), then phase in `PassiveIfSpreadTight` behind evidence gate. The port itself costs nothing and makes execution pluggable.
