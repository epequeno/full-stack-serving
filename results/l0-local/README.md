# L0 — results

Measured 2026-10-10 by `make measure LEVEL=l0-local` on saturn (the Linux GPU host).
Environment in [env.md](env.md); assembled matrix in [l0.json](l0.json); raw per-run JSON in
[runs/](runs/). The instrument's error envelope is in
[../instrument/validation.json](../instrument/validation.json) and its cross-check against
`vllm bench serve` in [../instrument/crosscheck.json](../instrument/crosscheck.json)
(full-stack-serving ADRs 0007 and 0003).
Regenerate with `make measure LEVEL=l0-local` (server first: `make up LEVEL=l0-local`): the
data is regenerated, never hand-edited — this page only reads it.

## The matrix

Pooled percentiles per cell: 5 interleaved, seeded repeats × 256 requests = **1,280 requests per
cell**, 0 errors, no server restart between repeats. ADR 0006's floors (p50 ≥ 30, p99 ≥ 1000
requests) are met at every cell, so pooled p99 is reported everywhere.

| c | TTFT p50 (ms) | TTFT p99 (ms) | ITL/event p50 (ms) | ITL/event p99 (ms) | TPOT p50 (ms) | tok/s, repeat min–max |
|---|---|---|---|---|---|---|
| 1 | 14.9 | 57.6 | 5.86 | 6.62 | 5.97 | 164.7–166.1 |
| 4 | 25.7 | 50.9 | 6.50 | 7.55 | 6.63 | 585–595 |
| 16 | 37.0 | 190.8 | 7.33 | 10.18 | 7.49 | 2007–2091 |
| 64 | 105.1 | 1493 | 12.69 | 92.9 | 12.83 | 2772–4728 |

"ITL/event" is per-SSE-event, not per-token (full-stack-serving ADR 0005); TPOT is the
token-normalised figure. `ignore_eos` fixed output at 128 tokens on every request (verified),
so TPOT comparisons are length-honest.

### Reading the matrix

- **Absolute numbers are environment-bound** — one RTX 4070 Ti SUPER, one bf16 1.5B model, vLLM
  v0.23.0, this driver. They transfer nowhere (ADR 0008); the *shape* is the result:
  TTFT p50 rises 7× from c=1 to c=64 while ITL/event p50 only doubles, i.e. queueing dominates
  decode cost, and the engine still streams (no stalled events) at 64 concurrent.
- **Run-to-run spread is the reproducibility band.** Per-repeat TTFT p50: c=1 14.9–15.0,
  c=16 33.0–45.1, c=64 96–283 ms. The c=16/c=64 TTFT bands are wide because one repeat per cell
  (the first run after warmup in the seeded interleaved order) is slower — visible in
  `across_repeats` and in the c=64 tok/s floor of 2772 vs median 4695. That is the cold-start
  cost of the cell's first scheduling, reported, not discarded (ADR 0006: min/median/max, never
  silent re-runs).
- **What a finding may claim here (ADR 0007, fixed from this run):** instrument p99 error is
  0.53/1.37/1.84/5.39 ms at c=1/4/16/64, so between-level findings are claimable above
  5.3/13.7/18.4/53.9 ms respectively. TTFT effects clear their bar at every cell; ITL/event
  effects clear it only at c=1. Anything smaller than the bar is not claimed.

## The overhead pair (ADR 0005)

One extra pair of runs at c=16, per-chunk `usage` request option on vs off
(`stream_options.continuous_usage_stats`, confirmed against the pinned server):

| | TTFT p50 (ms) | ITL/event p50 (ms) | TPOT p50 (ms) |
|---|---|---|---|
| with continuous usage | 47.5 | 7.30 | 7.40 |
| without | 49.0 | 7.32 | 7.44 |

Deltas: TTFT −1.5 ms, ITL −0.02 ms — both inside the c=16 TTFT repeat band (33–45 ms), so
**per-chunk usage is free at p50 on this stack** — the option is viable as the per-event
token source everywhere.

## Harness vs GPU (ADR 0011 criterion 3)

The model must stay big enough that GPU time dominates. It does: ITL/event ≈ 5.9 ms at c=1
against a client timing error of 0.4 ms p50 / 0.5 ms p99 at that cell, and the overhead pair
above costs ≈0 at p50. The harness is not the bottleneck; **the model choice stands.**

## Prefix-cache check (fixtures "Not verified")

One c=1 pass over `distinct`, run FIRST on a cold cache, with
`vllm:*prefix_cache*` counters scraped around it: **hits 4,080 / queries 340,614 = 1.2%**, and
the hits are the chat-template prefix every request shares — consistent with the fixture's
2-word maximum shared prompt prefix. `distinct` defeats cross-prompt prefix caching:
**verified** (`loadgen/fixtures/README.md`). A warm cache reads 98.7% (replay self-hits) —
which is why the check runs first.