# 0007 — Validate the load generator against a mock server with a logged schedule

- **Status:** accepted
- **Date:** 2026-10-09

## Context

full-stack-serving ADR 0003 makes the load generator an instrument we own and cross-checks it
against `vllm bench serve`. That cross-check cannot find a fault the two clients share, such as a
common event-loop effect or a common clock problem. Agreement between two instruments is not
accuracy. We need a reference whose timing is known independently of either.

The client's error is probably worst under load: many concurrent streams share one event loop,
and the loop's scheduling delay is added to every timestamp. A test at concurrency 1 would miss
the case that matters.

The reference is a computer too. A mock server written in Python sleeps imprecisely, so its
*intended* schedule is not the truth. Its *actual* write times are.

## Decision

Build a **mock SSE server** in `measure/`, and validate `loadgen/` against it before any
level's numbers are trusted.

**The mock:**

- Runs as a **separate process** from the client, on the same host. Same-process was rejected:
  it shares the event loop, so load on one corrupts the other and the client could never be shown
  to be at fault.
- Serves the same OpenAI-compatible streaming shape as the real engine, including the
  `usage` chunk.
- Emits events on a **configured schedule**: a fixed first-event delay, then event *k* at
  `t_first + k × Δ`. Δ, the number of events, and tokens per event are parameters.
- **Logs the monotonic timestamp of every actual write** (`CLOCK_MONOTONIC`, taken immediately
  before the write call), keyed by request id and event index.

**The comparison:**

- Error is `client_t − mock_actual_write_t` for every event, not `client_t − intended_t`.
  Both processes read the same host monotonic clock, so no clock-sync step is needed.
- **The mock's own scheduling jitter** (`actual − intended`) is reported separately, so a noisy
  mock is not mistaken for a noisy client.
- The error distribution is reported at concurrency 1/4/16/64, plus at least one run with
  deliberate extra CPU load on the client's core, since that is the failure we are testing for.
- It also verifies **counts**: events, `tokens`, and `tokens_out` must match the schedule
  exactly. A miscount is a failure, not an error margin.

**Pass criterion.** The measurement error must be small compared with the smallest effect any
level claims to detect. Concretely:

> p99 absolute error ≤ one tenth of the smallest between-level difference this repo
> reports as a finding.

**Fixed 2026-10-10**, after L0's numbers and the validation ran on the same host (saturn),
from [`results/instrument/validation.json`](../results/instrument/validation.json) and
[`results/l0-local/l0.json`](../results/l0-local/l0.json) — both committed before this
paragraph was written. The instrument's p99 error per cell, and therefore the smallest
between-level difference that cell can claim (10 × error):

| cell | client-vs-mock err p99 | smallest claimable finding |
|---|---|---|
| c=1 | 0.53 ms | 5.3 ms |
| c=4 | 1.37 ms | 13.7 ms |
| c=16 | 1.84 ms | 18.4 ms |
| c=64 | 5.39 ms | 53.9 ms |

The CPU-loaded run stayed inside the envelope (err p99 5.30 ms, p50 0.73 ms). The mock's own
jitter p99 reached 16.5 ms at c=64 — at that cell the reference was the noisier side, so the
client's envelope is the binding number.

Read against L0's committed medians: **TTFT findings are claimable at every cell** (bars
5.3–53.9 ms against medians 14.9–105.1 ms — effects of ≳36–51 % of TTFT clear their bar).
**ITL findings are claimable only at c=1** (bar 5.3 ms against a 5.86 ms median); at c≥4 the
bar exceeds the entire median, so between-level ITL differences below the bar are **not
claimed** with this instrument as-is. The 2026-10-10 validation therefore passes for
TTFT-scale findings at every cell, with the ITL limit stated rather than excused.

**When it runs:** before any level's numbers are trusted, and again whenever `loadgen/` or the
Python/aiohttp versions change. The result is committed under `results/instrument/` with the
environment it ran in.

## Consequences

- **Shared host clock, shared kernel.** This catches client-side and event-loop error. It does
  not catch error from the network stack or the kernel scheduler that both processes
  experience identically. That limit is stated in the result.
- **The mock does not behave like vLLM.** It has no batching, no GPU contention. It validates the
  client's timing path, not its behaviour against the real server. Agreement with
  `vllm bench serve` on the real server remains the second, separate check.
- If error is not small enough, the options are to reduce the harness error or to restrict the
  claims to effects larger than that error. Raising the threshold to pass is not one of them.
- The mock is itself new code that needs to be right. Its write log is the reference, so a bug
  in the logging would invalidate every result. It gets its own simple test: a run with Δ large
  enough that jitter is negligible next to it, compared against a wall-clock stopwatch.
- The pass threshold was fixed 2026-10-10 from L0's committed numbers (Decision, above);
  a validation run before that date could not have been called a pass.
