# 0004 — Closed-loop load for L0/L1, open-loop load for L2/L3

- **Status:** superseded by [0013](0013-open-loop-at-l0.md)
- **Date:** 2026-10-09

## Context

A load generator has to decide when the next request is sent. There are two models:

- **Closed loop.** N workers each send a request, wait for the full response, then send the
  next. Concurrency is exactly N. Arrival rate is whatever the server allows.
- **Open loop.** Requests arrive at a target rate whatever the server is doing.

They answer different questions, and they fail differently:

- Closed loop suffers **coordinated omission**. When the server slows down, the client slows
  down with it, so the slow period contributes fewer samples and the tail looks better than a
  real user population would see. It also cannot build a queue deeper than N.
- Open loop exposes queueing, which is the behaviour L2 (routing) and L3 (queue-depth
  autoscaling) exist to react to. But if the *client* cannot keep up with its own schedule, the
  measured latency includes client-side delay, and the run silently measures the harness.

The README promises "TTFT/ITL at 1/4/16/64 concurrency", which is a closed-loop framing. L3
cannot be closed-loop: KEDA scales on queue depth, and a closed loop caps the queue at N.

## Decision

The harness supports both models. Each level uses the one matching its question, and every result
states which.

| Level | Model | Why |
|---|---|---|
| L0, L1 | **Closed loop**, concurrency 1/4/16/64 | The question is "what does the server do at N in-flight requests". Fixed N makes L1 vs L0 a clean comparison. |
| L2, L3 | **Open loop**, target arrival rate | The question is "what happens as demand varies". Arrival rate is the independent variable, and queueing has to be able to build. |

**Closed loop:**
- N workers, each sending its next request immediately on completion (no think time).
- Warmup requests are discarded and counted, not silently dropped.
- Results are reported as "concurrency N", never as "load", and not read as tail behaviour under
  overload.

**Open loop:**
- Poisson arrivals at rate λ, from a **seeded** RNG so the arrival schedule is reproducible.
- Every request records `scheduled_ms` and `sent_ms`. The difference is **schedule lag**.
- The harness reports schedule lag percentiles. A run whose lag exceeds a threshold is
  **invalid** and is logged as a discarded run with the reason (per `measure/README.md`). It
  is not reported as a slow server.
- In-flight requests are capped by an explicit, recorded limit. Requests that would exceed it
  are counted and reported, never silently skipped.

## Consequences

- **Closed-loop tails at saturation are understated.** The ADR does not fix this. It limits the
  claim, and L0/L1 results must not be presented as overload behaviour.
- **Two modes means more harness code and more ways to be wrong.** Both need validation against
  a source of known timing (separate decision).
- **Open loop needs a rate sweep, and the rate levels depend on measured capacity.** The
  specific λ values, and the schedule-lag threshold, are **not decided here**. They are set from
  an L0 capacity measurement, not guessed.
- L0/L1 numbers and L2/L3 numbers are **not comparable with each other**, since they were produced
  under different load models. That is acceptable only while it is stated on the results.
- `loadgen/` output gains `scheduled_ms`, `sent_ms`, `load_model`, and the cap fields.
