# Trading Intelligence Engineering Constitution

The authoritative engineering governance handbook for the Autonomous Trading Intelligence repository.

Every AI agent, engineer, reviewer, and contributor must read the full Constitution before modifying this repository. The Constitution outranks all other documents; when any document contradicts it, the Constitution wins.

## Reading Order

- [00 — Master Index](00-Master-Index.md)
- [01 — Chief Architect Charter](01-Chief-Architect-Charter.md)
- [02 — Product Constitution](02-Product-Constitution.md)
- [03 — Architecture Constitution](03-Architecture-Constitution.md)
- [04 — Engineering Standards & Code Quality](04-Engineering-Standards-and-Code-Quality.md)
- [05 — AI & Decision Systems Constitution](05-AI-and-Decision-Systems-Constitution.md)
- [06 — Integration Constitution](06-Integration-Constitution.md)
- [07 — Repository Review Framework](07-Repository-Review-Framework.md)
- [08 — Implementation Strategy](08-Implementation-Strategy.md)
- [09 — Long-Term Evolution Strategy](09-Long-Term-Evolution-Strategy.md)
- [10 — Chief Architect Operating Manual](10-Chief-Architect-Operating-Manual.md)

Order is mandatory. Skimming is forbidden.

## Invariants (abbreviated)

1. The AI is the trader; deterministic software is the workhorse; rules are safety constraints.
2. The system must be runnable before it is ambitious.
3. Everything inside the deterministic core is deterministic; everything nondeterministic is an external capability.
4. Risk is a decoupled service with veto authority over every order.
5. The learning loop is sandboxed; it never alters risk parameters without human approval.
6. Nothing is stored, nothing is learned.
7. Every external system is replaceable behind a stable interface.
8. No provider, model, or venue is ever permanent; live trading never depends on free AI tiers.
9. No subsystem enters the core without its own ADR.
10. Knowledge is organized; execution is delegated.
11. Every subsystem justifies its existence or is removed.
12. The operator never touches internal machinery.

## Amendment

Any engineer or AI agent may propose an amendment. Every amendment must state what changed, why, what it affects, what it replaces, and cite evidence. No amendment may weaken determinism, risk-veto, the learning sandbox, or replaceability. See [00-Master-Index.md](00-Master-Index.md).
