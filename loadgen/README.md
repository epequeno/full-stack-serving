# loadgen — the client, and the ground truth

**Status: not implemented.**

The load generator is shared by every level. It is not per-level, and it must not be forked:
applying the *same* client to each layer is what makes the levels comparable.

## Contract

Given a target endpoint and a load model (full-stack-serving ADR 0004), it must:

1. Issue requests with controlled prompt shapes: **closed loop** at fixed concurrency (L0/L1) or
   **open loop** at a seeded Poisson arrival rate (L2/L3). In open loop, record `scheduled_ms`
   and `sent_ms` per request so schedule lag is visible, and report the in-flight cap and any
   requests it refused.
2. Record, per request, with timestamps taken as early in user space as the language allows
   (full-stack-serving ADR 0003):
   - `events[]` — one `{t_ms, tokens}` per content-bearing SSE event (full-stack-serving
     ADR 0005); per-event ITL is derived from it and is **not** per-token. `tokens` is `null`
     if the server cannot report it; it is never estimated.
   - `ttft_ms`, `e2e_ms`
   - `tokens_out` and `tokens_in`, from the server's `usage`, never from counting events
   - TPOT is derived: `(e2e − ttft) / (tokens_out − 1)`
   - `request_id` and `trace_id`: the client sets both (a `traceparent` header is sent on every
     request, tracing on or off) so a request can be paired with the server's span
     (full-stack-serving ADR 0009)
3. Emit JSON to stdout (or a path), one record per request plus a run summary.
4. Record the run's configuration alongside the numbers.

## Two rules that matter

**Time as close to the wire as possible, and say where.** A library's streaming iterator batches
and buffers; timing there measures the library, not the server. Python cannot timestamp the
socket read itself, so the boundary is "as early in user space as the language allows", and
whatever that boundary is gets validated against a source of known timing rather than asserted.

**Same prompts across levels.** If L0 and L1 measure different workloads, the parity check is
meaningless. Prompt shapes live in committed fixtures ([`fixtures/`](fixtures/README.md)), so
every level replays the same thing.

## Output shape (proposed)

```json
{
  "run": { "level": "l0-local", "load_model": "closed", "concurrency": 16, "duration_s": 60,
           "endpoint": "...", "model": "...", "fixture": "..." },
  "env": { "kernel": "...", "driver": "...", "cuda": "...", "server_version": "...",
           "caches_dropped": true, "cuda_graphs": false },
  "requests": [ { "id": 1, "scheduled_ms": null, "sent_ms": 0.0, "ttft_ms": 412.3,
                  "events": [ {"t_ms": 412.3, "tokens": 1}, {"t_ms": 423.5, "tokens": 1} ],
                  "e2e_ms": 2210.5, "tokens_out": 180 } ],
  "summary": { "ttft_p50_ms": 0.0, "ttft_p99_ms": 0.0,
               "itl_event_p50_ms": 0.0, "itl_event_p99_ms": 0.0,
               "tpot_p50_ms": 0.0, "tpot_p99_ms": 0.0, "tokens_per_s": 0.0 }
}
```

`env` is not decoration. A latency number without its environment is not reproducible, and the
whole point of `results/` is that a fresh run reproduces it.
