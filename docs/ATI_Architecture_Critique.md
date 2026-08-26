# ATI Architecture Critique — "Show me the evidence"

> **Standing instruction to any agent working in this repository: READ THIS
> FILE BEFORE DOING ANY MAJOR ARCHITECTURAL WORK.** It is the single most
> important external review of this project. When in doubt about whether to
> build a new subsystem, re-read §18 (next priorities) and §11 (too large).
>
> **Persisted 2026-08-13 by the chief-architect session, after an external
> reviewer read the ATI system diagram. This is the current truth about what
> the project lacks — the single biggest gap is evidence, not architecture.**

---

The review below is preserved verbatim. Its verdict, in one line:

> **"The architecture is much more mature than the evidence presented that the
> system can actually trade successfully and safely."**

---

## 1. The biggest problem: the architecture looks more autonomous than it actually is

The diagram contains **Research → Validation → Paper → Canary → Production →
Promotion → Rollback** — that sounds like a complete self-improving autonomous
trader. But the autonomy ladder is **planned wiring**, not wired into
`main.py`. There is a huge difference between:

- "The system contains the components necessary for autonomous progression."
- "The system autonomously discovers, validates, deploys, monitors and rolls back strategies."

The first is supported. The second is **not**.

**Verdict: Architecture strong. Current autonomy considerably lower than the
diagram initially makes it appear.**

## 2. Danger of building the "trading operating system" before proving the trading intelligence

Context pipeline, feature engine, observation enrichment, multiple reasoners,
risk gate, VPIN, simulator, fill engine, supervisor, reflection, reconciliation,
attribution, impact calibration, research harnesses, campaign state machine,
promotion engine, rollback system, autonomy audit, aggregation, 16 database
tables. A lot — and impressive. But:

> You can build an incredibly sophisticated machine that loses money extremely efficiently.

The architecture does not establish that the underlying trading edge is real.
**Having validation machinery is not the same as having validated alpha.**

Question to answer: *Where is the proof that the intelligence generating the
decisions has persistent positive expected value after fees, slippage, impact,
regime changes and realistic execution?*

## 3. Be skeptical of the AI reasoner as a serious trading edge

`RuleBasedSolver`, `AiOmniRouteReasoner`, `PydanticAIReasoner`. An LLM looking
at trend/volatility/liquidity/order-flow/regime/sentiment and saying "LONG"
does **not** automatically mean an exploitable statistical edge.

Wanted experiment (out of sample):
- A: Pure quantitative model
- B: Quant + AI
- C: AI alone
- D: Rule system
- E: Quant + AI + risk layer

> **AI should earn its place in the trading pipeline through measurable
> incremental value. Not because it sounds intelligent.**

If AI does not improve risk-adjusted performance consistently, remove it.

## 4. Risk controls exist — but want an explicit hierarchy of risk

`CircuitBreakerRiskGate` (impact veto, Kelly cap, circuit breaker violation,
toxicity flow, market statistics, edge estimates, VPIN) is one of the better
parts. But: **which risk rule wins when they disagree?**

```
AI: BUY
Kelly: position small
Liquidity: bad
VPIN: extreme toxicity
Impact: too expensive
Circuit breaker: triggered
```

Does `circuit breaker > toxicity > impact > Kelly > model confidence` exist as
an explicit hierarchy? Or are they evaluated independently? **Risk precedence
should be painfully explicit.**

## 5. "Reflection" does not automatically mean "learning"

```
Trade → Reflection → Memory
```
is not the same as:
```
Trade → System learned → Strategy improved
```

Memory is storage. Learning requires: identifying a pattern → testing it →
determining generalization → updating model/strategy → validating the change →
preventing regression.

**Memory ≠ Learning. Reflection ≠ Adaptation. Adaptation ≠ Improvement.**

## 6. The autonomy ladder is good, but the promotion problem is incredibly difficult

The design direction (Research → Validation → Paper → Canary → Scaling →
Promotion/Rollback) is correct. But: **what exactly makes a strategy worthy of
promotion?**

A strategy with Sharpe 2.1 / win 61% / maxDD 8% may still fail because it:
- only worked in one market regime;
- had 200 trades;
- most profits came from five trades;
- was trained on data overlapping the test period;
- transaction costs were underestimated;
- liquidity assumptions were unrealistic;
- stopped working two weeks later.

The promotion engine needs to understand far more than "Sharpe > X."
**Promotion logic is one of the most important pieces of the entire project.**

## 7. The simulator is one of the easiest places to fool yourself

`PaperFillEngine` models market/limit/partial fills, resting orders, sweeping,
impact, fees — excellent. But **a sophisticated simulator can still be wrong.**
You must prove simulated execution resembles actual execution, otherwise:
Simulator → beautiful results → promotion → live → reality → "what happened?"

This is why `ReconciliationService`, `ExecutionAttributionService`, and
`SquareRootImpactCalibrator` are promising. **Make simulator-vs-real-execution
calibration a first-class project objective.**

## 8. SQLite is excellent dev/paper infra, question it for production

Fine for research, local experimentation, paper trading, single-process
operation. For autonomous production trading, ask: concurrent writes? events
faster than persistence? process crash durability? migrations? backups? lock
contention? multiple services? recovery? Treat SQLite as excellent
development/paper-trading infrastructure, not automatically final production
infrastructure.

## 9. Want a much stronger failure model (second-biggest criticism)

What happens if: Binance disconnects? AI provider goes down? market data goes
stale? database write fails? **order submission succeeds but the response is
lost?**

```
ATI → exchange: BUY 1 BTC
Exchange: "Order accepted."
ATI: does not receive response (network died)
ATI thinks: "No position."  Binance thinks: "Position = +1 BTC."
```

The architecture contains reconciliation (good), but **failure/recovery
semantics must be documented much more explicitly.**

## 10. Operator lock is good, but concurrency needs serious attention

`/drive` runs `ingest.handle → set_mark_price → pipeline.process` under
`operator_lock`. Good. But: MarketLoopService and operator /drive
simultaneously? Two market events near-simultaneously? A fill update during
decision processing? Risk state changing between decision creation and
execution? These matter as you approach live trading.

## 11. The architecture is probably too large for the current stage

Trading system + research platform + autonomy platform + experimentation
platform + execution simulator + memory system + governance system — enormous.
Every subsystem adds code + interactions + states + failure modes + tests +
maintenance. Risk: **spend more time maintaining the machine than proving
whether the machine is useful.** Build in stages.

## 12. Ruthlessly prioritize the actual trading loop

First milestone: Market Data → Context → Decision → Risk → Execution
Simulation → Performance Measurement. Ask: **does this thing actually produce
statistically meaningful positive expectancy?** Everything else second.

Phased order:
1. Reliable market → decision → simulation
2. Reliable risk + reconciliation
3. Research automation
4. Paper campaigns
5. Canary
6. Controlled live deployment
7. Autonomous promotion

## 13. Feature quantity vs feature quality

12 features (trend, momentum, volatility, volume, liquidity, micro-price, order
flow, book imbalance, Kyle lambda, regime, sentiment, insider) is a strong
universe. But which actually contribute predictive power? 100 noisy features can
overfit; 5 strong ones can win. **Make feature attribution and ablation testing
a major part of the research system** — the existing `AblationRunner` under
`feature_attribution` is a very good sign; lean into it.

## 14. Biggest missing thing from the visual: measurable evidence

The diagram says *what components exist*. Now want another document/dashboard
that says *what evidence each component has produced*:
- Decision engine: total decisions, LONG/SHORT/STAND_ASIDE counts
- Performance: net return, Sharpe, Sortino, max drawdown, profit factor,
  expectancy, turnover, fees, slippage, impact
- Robustness: by regime (bull/bear/high-vol/low-vol/high-liq/low-liq)
- Execution: expected price vs actual simulated fill, slippage, impact, fees
- AI contribution: quant-only vs quant+AI

## 15. Be careful with the word "autonomous"

More defensible description today: **"An autonomous trading intelligence
framework with a currently operational market-decision/simulation pipeline and
a planned autonomy/promotion layer."** Only once the ladder is connected and
demonstrated end-to-end can the system honestly be called autonomous.

## 16. What the reviewer LIKES

1. Separation of concerns (Presentation/Application/Domain/Infrastructure;
   domain independent). Very good.
2. Risk is not delegated entirely to the AI — dedicated risk gate. Very good.
3. Execution separated from decision-making. Very good.
4. Reconciliation exists (most toy bots ignore it). Very good.
5. Promotion and rollback are explicit concepts (campaign state, promotion
   decisions, rollback records). Very good direction.
6. The system records research artifacts (datasets, experiments, alt data,
   final-test claims) — important for reproducibility.

## 17. Scorecard

| Area | Score |
| --- | --- |
| Architecture | 9/10 |
| Separation of concerns | 9/10 |
| Risk architecture | 8/10 |
| Execution simulation design | 8.5/10 |
| Research infrastructure | 8/10 |
| Observability/API structure | 8/10 |
| Autonomy design | 8.5/10 |
| **Evidence of actual autonomy** | **4/10** |
| Evidence of profitable alpha | Unknown |
| Production readiness | Not established |
| Overall engineering ambition | 9/10 |
| Overall trading-system maturity | ~6.5–7/10 until proven in realistic testing |

## 18. If this were my project — the next priorities

Do **not** immediately add more AI, more features, or more autonomy components.
Do these instead:

1. **Prove the decision pipeline** — huge historical/out-of-sample evaluation.
2. **Prove the simulator** — compare simulated execution against realistic
   historical execution assumptions.
3. **Quantify the AI's contribution** — rules-only vs AI-only vs rules+AI vs
   quant+AI; keep what measurably improves.
4. **Stress the risk system** — flash crash, extreme volatility, liquidity
   collapse, stale data, exchange disconnect, AI failure, database failure,
   duplicate/delayed/missing events, partial fill, rejected order, unknown
   order status. See whether it behaves correctly.
5. **Build the evidence layer** — every strategy carries a complete record:
   WHY created, WHAT data trained/tested it, WHAT assumptions, HOW it
   performed, WHERE it failed, WHAT regimes it works in, WHY promoted, WHEN to
   roll back.
6. **Only then wire the autonomy ladder** — otherwise you risk creating *an
   autonomous machine that autonomously promotes bad strategies.* That is the
   exact thing you do not want.

## Overall verdict

The strongest part is that it does not treat trading as "market data → AI →
buy/sell"; it recognizes data → context → reasoning → supervision → risk →
execution → reconciliation → memory → research → controlled promotion. That is
the right mental model.

The biggest warning: **do not confuse architectural completeness with trading
intelligence.** The autonomy ladder is still planned rather than fully wired,
and there is no evidence the strategies have a durable live-market edge.

**The thing to attack relentlessly now:**
> "Show me the evidence that this machine can make money without fooling itself."
