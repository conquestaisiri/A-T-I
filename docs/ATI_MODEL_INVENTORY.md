# ATI Model Inventory — Omega Provider Pool

> Evidence-based ranking from live benchmarks (2026-08-23) against the REAL
> production prompt (`prompt_builder` payload, byte-identical to the live path).
> Re-benchmark before reordering: `Temp/opencode/bench_providers.py`.

## Active chain (priority order)

| # | Provider | Model | Auth | Benchmark verdict |
|---|---|---|---|---|
| 1 | zen | router picks | key (401 now) | DEAD until operator sets OPENCODE_ZEN_API_KEY |
| 2 | groq | llama-3.3-70b | 4 keys | 403 all keys expired — refresh at console.groq.com |
| 3 | openrouter | gpt-4o-mini | keys | untested live (keys may work) |
| 4-6 | cerebras/gemini/agentrouter | — | keys | configured, no keys present |
| **7** | **ovh** | **Qwen3-Coder-30B-A3B-Instruct** | **none** | ✅ **BEST**: exact strict-JSON trading decisions, fast (~7.5s), zero-retention. Limit: 2 req/min/IP (429 under bursts — circuit breaker handles) |
| **8** | **kilo** | **tencent/hy3:free** | **none** | ✅ **WORKING** after max_tokens=4000 fix (reasoning field eats 1600+ chars before JSON). Sensible conservative decisions. Slower (~30-60s) |
| 9 | llm7 | DeepSeek-V4-Flash-0731 | none | ⚠️ LAST RESORT: 3/3 replies were `"hold"` — systematic refusal to pick a trading action on this prompt. HTTP-reliable, prompt-adherence poor |

## Benchmark evidence (2026-08-23, 3 rounds x production prompt)

| Provider | HTTP ok | Valid JSON | Valid action | Avg latency | Decisions seen |
|---|---|---|---|---|---|
| llm7 | 3/3 | 3/3 | **0/3** | 11.5s | hold/0.7 x3 |
| kilo-hy3 | 0/3 @600tok | — | — | — | content=None (reasoning ate budget) |
| kilo-nemotron | 1/3 | 1/3 | 1/3 | 8.4s | enter_long/0.55 (+truncation errors) |
| ovh-qwen3 | 1/3* | 1/3 | 1/3 | 7.5s | enter_long/0.85 (*429s under burst) |

Post-fix live runs: kilo-hy3 @4000 tok = full stop-finish JSON; ovh alternates
with 429s per its 2rpm cap.

## Transport notes

- Per-provider `max_tokens` override injects at the deepcopy sites only —
  prompt messages stay byte-identical (continuity Rule 3 allows transport-param
  differences: model/temperature/max_tokens).
- hy3 response shape: `choices[0].message.reasoning` (CoT) + `.content` (JSON).
  Parser only reads `.content` — correct.
- llm7 quirk: occasional upstream_rate_limited under rapid sequential calls
  (shared upstream capacity). Fine at normal pacing.
- Pollinations: HTTP 402 on tool calls — excluded from chain entirely.

## Refresh / re-rank procedure

1. `py -3 Temp\opencode\bench_providers.py` (3 rounds, production prompt)
2. Rank by action_valid desc, latency asc
3. Edit `_DEFAULT_PRIORITY` tail order in
   `backend/infrastructure/ai/smart_fallback_reasoner.py`
4. Run `pytest tests/application/test_smart_fallback_reasoner.py
   tests/application/test_prompt_determinism.py -q`
