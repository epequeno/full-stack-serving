# 0003 — Build the load generator, and cross-check it against `vllm bench serve`

- **Status:** proposed
- **Date:** 2026-10-09

## Context

Every claim in this repo is a latency number produced by a client, so the client is the
instrument. Mature tools exist: `vllm bench serve`, GuideLLM, genai-perf.

`loadgen/README.md` requires per-request timing at the **socket read**, not at an HTTP
library's iterator, because an iterator measures the library's buffering as well as the server.

**What `vllm bench serve` does** (read 2026-10-09 from `vllm/benchmarks/lib/endpoint_request_func.py`
on `main`; pin the version when L0 is built, since this is a moving target):

- It iterates `response.content.iter_any()` (aiohttp's stream layer), feeds the bytes to an SSE
  buffer, and calls `time.perf_counter()` **after** each message is parsed with `json.loads`.
- So the timestamp includes event-loop scheduling, SSE parsing and JSON decoding, and several
  SSE messages that arrive in one read get near-identical timestamps.
- It requests `stream_options.include_usage` and takes `output_tokens` from the server's
  `usage` rather than counting chunks, and supports `ignore_eos`.

This is a defensible boundary for a general benchmark. It is not the one this repo says it
needs, and the difference is small enough that it may or may not matter. Neither of us knows,
and **that is the case for measuring it, not for assuming it.**

Not examined: GuideLLM and genai-perf. No claim is made about where they time.

Two further considerations:

- `vllm bench serve` is specific to vLLM. If the engine choice changes, the instrument
  would change with it and the levels would stop being comparable.
- Running upstream's benchmark and reporting its output fails the anti-tutorial test.

## Decision

Build `loadgen/` as a custom asyncio client with timestamps taken as early in user space as
Python allows. **Cross-check it against `vllm bench serve`** as part of the harness-overhead
measurement required by `measure/README.md`:

- Both tools run the same fixture against the same server, same concurrency, same
  `max_tokens` and `ignore_eos`.
- The disagreement in TTFT, ITL percentiles and throughput is **reported with the results**.
  It is not tuned away.
- `tokens_out` comes from the server's `usage` field, never from counting chunks.

The cross-check is what justifies owning the instrument. Agreement is evidence the client is
sound. A systematic gap is a finding about *where* `vllm bench serve`'s boundary costs
accuracy, which is itself the kind of result this repo exists to produce.

## Consequences

- **A new instrument that can be wrong.** It must be validated against a source of known
  timing, not only against another tool (separate decision, `loadgen/` validation).
  Cross-checking two tools that share a bug proves nothing.
- Real implementation cost, and the cost recurs whenever the client changes.
- The same timestamps can later be compared with the kernel-level view from the eBPF project.
  Only an instrument we own can be aligned that way.
- **Fallback:** if the validation decision shows the custom client cannot reach a better
  boundary than aiohttp's, then the justification for owning it weakens to "engine-agnostic,
  fixture-controlled, reproducible". Re-evaluate then. Do not carry on as though this ADR still
  held on its original grounds.
- Pin the `vllm` version used for the cross-check in every result's `env`.
