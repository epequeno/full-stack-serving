# loadgen — the client, and the ground truth

**Status: implemented** — `uv run python -m loadgen.client` (dependencies pinned in `uv.lock`).
Validated against the logged-schedule mock server per full-stack-serving ADR 0007
(`measure/validate.py`); the committed validation lives in `results/instrument/`.

The load generator is shared by every level. It is not per-level, and it must not be forked:
applying the *same* client to each layer is what makes the levels comparable.

## Usage

```sh
uv run python -m loadgen.client \
  --endpoint http://localhost:8000/v1/chat/completions \
  --model Qwen/Qwen2.5-1.5B-Instruct \
  --fixture loadgen/fixtures/distinct.jsonl \
  --mode closed --concurrency 16 \
  --output run.json
```

**Closed loop** (L0/L1): one pass over the fixture, N requests in flight. **Open loop** (L2/L3):
`--mode open --rate <req/s> --duration <s> --inflight-cap <n> --seed <n>` — seeded Poisson
arrivals; a request that would exceed the cap is recorded `refused`, never queued.
`--absolute-times` adds raw `time.monotonic_ns` stamps per event (ADR 0007 comparison only —
not used in normal runs). `--extra-body '<json>'` merges engine-specific request options
(vLLM's per-chunk usage switch, ADR 0005) without hard-coding them into the client.
`--requests N` truncates the fixture (warmup, validation).

## Contract

Given a target endpoint and a load model (full-stack-serving ADR 0004), it must:

1. Issue requests with controlled prompt shapes: **closed loop** at fixed concurrency (L0/L1) or
   **open loop** at a seeded Poisson arrival rate (L2/L3). In open loop, record `scheduled_ms`
   and `sent_ms` per request so schedule lag is visible, and report the in-flight cap and any
   requests it refused.
2. Record, per request, with timestamps taken as early in user space as the language allows
   (full-stack-serving ADR 0003):
   - `events[]` — one `{t_ms, tokens}` per content-bearing SSE event (full-stack-serving
     ADR 0005); per-event ITL is derived from it and is **not** per-token. `tokens` is the
     delta of the server's cumulative per-chunk `completion_tokens`, `null` if the server
     cannot report it; it is never estimated.
   - `ttft_ms`, `e2e_ms`
   - `tokens_out` and `tokens_in`, from the server's `usage`, never from counting events
   - TPOT is derived: `(e2e − ttft) / (tokens_out − 1)`
   - `request_id` (the fixture index) and `trace_id`: the client sets both — a `traceparent`
     header is sent on every request, tracing on or off — so a request can be paired with the
     server's span by trace id (full-stack-serving ADR 0009). `response_id` is the engine's
     id (`chatcmpl-…`, from the SSE chunks): vLLM v0.23.0 puts THAT in `gen_ai.request.id`,
     not the client's `X-Request-Id` (which is response-side only), so the pair is
     cross-checked engine-id vs engine-id, seen by both sides.
3. Emit JSON to stdout (or a path), one record per request plus a run summary.
4. Record the run's configuration alongside the numbers.

## Two rules that matter

**Time as close to the wire as possible, and say where.** A library's streaming iterator batches
and buffers; timing there measures the library, not the server. Python cannot timestamp the
socket read itself, so the boundary is "as early in user space as the language allows", and
whatever that boundary is gets validated against a source of known timing rather than asserted.
Implemented boundary (stamped in every output's `env.boundary`): each event is stamped at the
return of aiohttp's `StreamReader.readany()`; `sent` immediately before the `session.post` call.
Events delivered in one read share a timestamp — that is the observation (ADR 0005), not a
defect.

**Same prompts across levels.** If L0 and L1 measure different workloads, the parity check is
meaningless. Prompt shapes live in committed fixtures ([`fixtures/`](fixtures/README.md)), so
every level replays the same thing.

## Output shape

`t_ms`/`ttft_ms`/`e2e_ms` are relative to the request's send; `sent_ms`/`scheduled_ms` are
relative to the run start. A percentile below ADR 0006's request-count floor prints
`{"n/a (n)": n}` instead of a number — pooling up to a reportable n is `measure/`'s job, never
an excuse to lower the floor.

```json
{
  "run": { "level": "l0-local", "load_model": "closed", "concurrency": 16,
           "endpoint": "...", "model": "...", "fixture": "...", "requests_planned": 256,
           "seed": 20261010, "extra_body": null },
  "env": { "python": "3.14.4", "aiohttp": "3.14.4", "client": "loadgen/1",
           "clock": "time.monotonic_ns",
           "boundary": "event t = return of StreamReader.readany(); sent = before session.post" },
  "wall_s": 61.2,
  "requests": [ { "id": 1, "fixture_id": "distinct-0000", "set": "distinct", "group": null,
                  "trace_id": "…", "scheduled_ms": null, "sent_ms": 0.0, "ttft_ms": 412.3,
                  "e2e_ms": 2210.5, "tokens_in": 1451, "tokens_out": 180, "status": "ok",
                  "events": [ {"t_ms": 412.3, "tokens": 1}, {"t_ms": 423.5, "tokens": 1} ] } ],
  "summary": { "requests_ok": 256, "requests_error": 0, "requests_refused": 0,
               "tokens_out_total": 46080, "tokens_per_s": 741.0,
               "ttft_ms":       { "p50": 0.0, "p99": 0.0 },
               "itl_event_ms":  { "p50": 0.0, "p99": 0.0 },
               "tpot_ms":       { "p50": 0.0, "p99": 0.0 } }
}
```

`env` is not decoration. A latency number without its environment is not reproducible, and the
whole point of `results/` is that a fresh run reproduces it.
