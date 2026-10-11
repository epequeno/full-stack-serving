# 0009 — L4 produces two findings: tracing overhead, and the span-to-client TTFT gap

- **Status:** accepted
- **Date:** 2026-10-09

## Context

L4's evidence was "a committed trace and a stability note". A trace is not a number, and
`AGENTS.md` says a level that produces no number is not done. L4 is also on the spine of the
project, so it cannot be the one level that escapes the rule.

What vLLM emits (read 2026-10-09 from `vllm/v1/engine/output_processor.py` and
`vllm/tracing/utils.py` on `main`; pin the version at L0, since this is a moving target):

- **One span per request, written when the request finishes.** `do_tracing` calls
  `instrument_manual("llm_request", start_time=arrival_time, kind=SERVER, …)` with all
  attributes set at that point. There are no per-token events and no spans while the request
  is in flight.
- **Attributes include** `gen_ai.latency.time_to_first_token`, `…e2e`, `…time_in_queue`,
  `…time_in_model_prefill`, `…time_in_model_decode`, token counts, and `gen_ai.request.id`.
- **The span's TTFT is measured inside the engine's output processor, from the request's
  arrival at the engine.** It does not include the client's send, the network, HTTP handling or
  SSE delivery. It will therefore **not** equal the client's TTFT, and it is not supposed to.
- vLLM extracts `traceparent` from the request headers, so a client can set the trace id.
- **Not verified:** whether the `gen_ai.latency.*` names are part of the OpenTelemetry GenAI
  semantic conventions or are vLLM's own. The source says its names are "copied from OTel
  semantic conventions", which does not settle it for the latency attributes. Check against the
  convention version in use at L4. The answer feeds the stability note.

> **2026-10-11 addendum (pinned v0.23.0, read from the image):** the pairing premise above is
> half wrong in the pinned version. `gen_ai.request.id` is the ENGINE's id (`chatcmpl-N`),
> not the client's `X-Request-Id` — that header is response-side only
> (`entrypoints/openai/engine/serving.py:392`). The client therefore records the engine id
> from the SSE chunks (`response_id`, same value, seen by both sides) and pairs **by trace
> id** — which v0.23.0 propagates from `traceparent` exactly as needed. Smoke 2026-10-11:
> 16/16 paired, 0 mismatches, 0 duplicate trace ids. The stability note also has its
> answer: the `gen_ai.latency.*` names are vLLM's own ("added until they are
> standardized"), NOT part of the OTel GenAI semconv package.

The original framing, "does span TTFT agree with client TTFT", is therefore the wrong question.
They measure different intervals. The useful question is **how large the difference is, and
what it is made of.**

## Decision

L4 produces two findings, declared before any data is taken, and judged by the ADR 0008 rule over
5 interleaved runs (ADR 0006).

**Finding 1 — tracing overhead.** Metrics: client-measured TTFT, TPOT and request throughput, same
fixture, same load model, with tracing **on** vs **off**.

- "On" means the engine exports spans to a collector that forwards to Phoenix, all running for
  real. "Off" means no exporter configured.
- `loadgen/` sends the same `traceparent` header in **both** arms, so the request bytes are
  identical and only the server's behaviour differs.
- The collector and Phoenix run on a separate core set from the serving process, so the
  cost measured is the engine's, not the backend's.
- The verdict `indistinguishable` is reported as **"no overhead detectable at this resolution"**,
  never "zero overhead". The effect size and margin are always printed with it.

**Finding 2 — the span-to-client TTFT gap.** Per request, `gap = client_ttft − span_ttft`,
paired by `gen_ai.request.id` (which `loadgen/` sets and records) and by trace id.

- Both are durations, so no clock synchronisation is involved.
- The prediction, **declared now**: `gap > 0` for every cell, because the client interval strictly
  contains the span's. The finding compares the two TTFT series by the ADR 0008 rule, which
  should yield `client > span`.
- The result that matters is the **size and the shape** of the gap: its per-run median, and
  how it changes with concurrency. If it grows with load, the unspanned part of the path (HTTP
  frontend, event loop, SSE) is queueing, and that is worth knowing.
- The gap is **not** decomposed further here. Splitting it needs more instrumentation
  (the eBPF project's territory) and is out of scope.

**Validity check — span coverage.** Coverage is the fraction of requests that have exactly one
matched span with a TTFT attribute. It is reported next to every L4 number.

- Pairing uses matched requests only. Unmatched requests are counted and listed.
- **Dropped spans are probably not random.** A batching exporter under load is most likely to
  drop the busiest, slowest requests, which biases the paired gap towards the easy cases. The
  results state coverage per cell, and a cell whose coverage is low is flagged, not hidden.
- The floor below which a cell is invalid is **not decided here**. It is declared in a committed
  file before L4 data is taken, for the same reason as in ADR 0007 and 0008.

**Stays prose, not a finding:** the stability assessment of the `gen_ai.*` conventions, and the
committed example trace. Both are kept. Neither can fail the gate.

## Consequences

- `loadgen/` must **send `traceparent` and a request id**, and record both per request. Added to
  `loadgen/README.md` now, so the schema does not need changing later.
- L4 depends on L0 and on the engine's tracing support in the pinned version. If that version does
  not emit these attributes, Finding 2 cannot be produced, and that is recorded as the result.
- **The overhead finding has the weakest resolution of the two.** Tracing overhead is usually small,
  and five runs can only detect a large effect. A `indistinguishable` result is likely, and the
  honest summary is then "we could not detect it", not "it is free". More runs per cell is the
  remedy, at the time budget's expense.
- vLLM emits one span at completion, so a trace shows total phase durations and not what
  happened in between. The committed trace will look sparse next to what the Phoenix demos
  show. That is a property of the engine's tracing, and the stability note says so.
- Per-request pairing needs the request id to survive the path. If an intermediary (L2's gateway)
  rewrites it, pairing breaks. L4 runs against L0/L1 first, and the L2 interaction is checked
  separately.
