# 0005 — Record ITL per SSE event, and report TPOT separately

- **Status:** proposed
- **Date:** 2026-10-09

## Context

"Inter-token latency" is not directly observable from a streaming response. What a client observes
is **SSE events**, and one event may carry zero, one, or several tokens:

- Speculative decoding can emit several tokens per step.
- A slow server event loop can batch several tokens into one event.
- TCP can deliver several events in one read, so they get near-identical client timestamps.

`vllm bench serve` (see full-stack-serving ADR 0003) appends one ITL entry per parsed event with no
token count, so an event carrying three tokens contributes one entry, and "ITL" quietly changes
meaning under load. Tools that divide by tokens report a different number again. A figure
labelled "ITL" is therefore not comparable across tools unless its definition is stated.

## Decision

Record what was observed and never invent per-token timing.

**Per request**, `loadgen/` records, for each SSE event that carries a content delta:

- `t_ms`: the client timestamp of the event, relative to the request start.
- `tokens`: the number of tokens it carried.

Two derived quantities, both reported and both labelled:

1. **Per-event ITL**: the gap between consecutive content events. Reported as
   *"ITL (per event)"*. Never labelled bare "ITL".
2. **TPOT**: `(e2e_ms − ttft_ms) / (tokens_out − 1)`, with `tokens_out` from the server's
   `usage` field. This is the number to use when comparing with token-normalised figures from other tools.

**Token counts per event.** The preferred source is the server's cumulative per-chunk
`completion_tokens`, whose difference between consecutive events is `tokens`. As of 2026-10-09,
vLLM's serving code contains an `include_continuous_usage` path that attaches cumulative
`completion_tokens` to every streamed chunk; **the client-facing request option that
enables it has not been verified.** Confirm the exact field against the pinned vLLM version when
L0 is built. If it is unavailable, `tokens` is unknown per event. In that case the harness
records `tokens: null`, reports only per-event ITL and TPOT, and the results state that
token-per-event data was not available. It must not be estimated.

`tokens_out` always comes from the final `usage`, never from counting events.

## Consequences

- **Per-event ITL is not per-token ITL.** Under speculative decoding or event batching it
  understates per-token latency. Both are reported so the gap itself is visible.
- Asking for continuous usage adds a `usage` object to every chunk, which makes events larger.
  That is a small, real change to what is being measured, and it belongs in the
  harness-overhead measurement (with and without it).
- The loadgen schema changes: `itl_ms[]` becomes an `events[]` list of `{t_ms, tokens}`, and the
  summary carries `itl_event_*` and `tpot_*` separately. `loadgen/README.md` is updated to match.
- Comparisons against other tools use TPOT. Per-event ITL is a property of this stack's streaming
  behaviour, not a portable metric.
