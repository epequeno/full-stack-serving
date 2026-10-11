# L4 — observability, measured (full-stack-serving ADR 0009)

Two arms over the **identical** engine — same vLLM v0.23.0 image, model, revision and flags
as L0/L1 — run sequentially on the one GPU (base first; the order is a recorded confound):

| arm | what | requests |
|---|---|---|
| `base` | no exporter configured — the control | 5,120 matrix + 32 warmup, 0 errors |
| `tracing` | `--otlp-traces-endpoint` → otel collector → Phoenix, all running for real | 5,120 matrix + 32 warmup, 0 errors |

Each arm is the L0 protocol: same fixture, seed 20261010, R=5 interleaved repeats over
c = 1/4/16/64, warmup discarded but counted. `loadgen/` sends `traceparent` in **both**
arms, so the request bytes differ only by the fresh per-request trace id. Environment
(digests, core pinning, versions): [env.md](env.md); raw runs in [runs/](runs/).

## Finding 1 — tracing overhead (base arm = the control)

Pooled p50, ms (base → tracing): TTFT 14.4→14.2, 22.8→24.6, 39.1→44.5, 112.3→106.2;
ITL 5.8→5.8, 6.5→6.5, 7.2→7.2, 12.5→12.5; TPOT 5.9→5.8, 6.5→6.5, 7.3→7.3, 12.7→12.6.

| verdict (ADR 0008 sign rule) | effect (median) | bar (ADR 0007) | claimable |
|---|---|---|---|
| 15/16 cells `indistinguishable`; c=1 TTFT `tracing<base` (−0.367 ms, sub-bar flip recorded as info) | −10.2 … +6.9 ms | 5.3 / 13.7 / 18.4 / 53.9 ms | **none** |

**No overhead detectable at this resolution.** Not "zero overhead": every effect sits below
its cell's claimability bar (10× the committed instrument p99 error). Request throughput:
`indistinguishable` at every cell (+0.01…+0.4 req/s), recorded as information — the
instrument's bars are latency ms, so throughput has no bar to clear.

**Limitation, stated rather than footnoted: the arm order is a confound, and it points one
way.** `base` ran first and `tracing` second, so any thermal or time-of-day drift is inside
every delta, and the two arms cannot be interleaved on one GPU — either arm saturates the card
(ADR 0009's own consequence note accepts the sequential order). A warming card *favours the
later arm*, and the one separated verdict (c=1 TTFT `tracing<base`, −0.367 ms, sub-bar, and
`tracing>base` on the gate re-run) is exactly that shape. So this finding supports **"no
tracing cost above the claimability bars"**, not **"tracing is free"**.

## Finding 2 — the span-to-client TTFT gap (tracing arm)

Per request, `gap = client_ttft − span_ttft` (`gen_ai.latency.time_to_first_token`),
paired by **trace id** (join key); the cross-check is the engine's id — `gen_ai.request.id`
= the client-recorded `response_id` (`chatcmpl-N`), 0 mismatches, 0 duplicate trace ids.
Both are durations, so no clock sync is involved. **Coverage 100.0% (1280/1280) at every
cell** against the committed 98% floor — no dropped spans anywhere.

| c | verdict | gap p50 (pooled) | gap median-of-run-medians | gap min→max | bar |
|---|---|---|---|---|---|
| 1 | `client>span` | 1.05 ms | 1.05 ms | 0.92 → 3.03 ms | 5.3 ms |
| 4 | `indistinguishable` | 1.57 ms | 1.71 ms | 0.86 → 4.56 ms | 13.7 ms |
| 16 | `indistinguishable` | 4.88 ms | 4.87 ms | 1.00 → 11.26 ms | 18.4 ms |
| 64 | `indistinguishable` | 12.37 ms | 19.67 ms | 1.54 → 46.62 ms | 53.9 ms |

**The declared prediction holds per request; the verdict string in the table above is a
different claim.** ADR 0009 predicted `gap > 0` for every request, and that holds: all 1,280
paired requests have a positive gap (minimum 0.86 ms), because the client's interval strictly
contains the span's. The table's verdict column is the ADR 0008 rule over the five per-run
medians — a different test of a different thing — and it reads `indistinguishable` at
c=4/16/64: the repeats do not separate there. Both statements are true at once. The gap's
**sign** is guaranteed by the two intervals; its **size** is not resolvable above the bars at
this sample size. ADR 0009 also expected the verdict rule itself to yield `client>span` at
every cell; at c≥4 it did not, and that expectation is recorded here as not met rather than
restated.

The result that matters is the **shape**: the gap grows ~an order of magnitude from c=1 to
c=64 (pooled p50 1.05 → 12.37 ms), so the unspanned part of the path — HTTP frontend,
event loop, SSE delivery — **queues under load**. That is what ADR 0009 wanted to know.
## The gate (ADR 0008, restricted by ADR 0007)

`make verify LEVEL=l4-otel` re-runs **both arms** into scratch (`tmp/verify-l4-otel`,
never `results/`) with the committed protocol and gates on the claimable set.

**2026-10-11: PASS.** Fresh run (40/40 ok, 0 failures): the claimable set is empty and
stayed empty; span coverage 100% at every cell again; the gap's shape reproduced
(`client>span` at c=1, 1.18 ms; growing to 17.85 ms at c=64). One sub-bar flip was
recorded as information: c=1 TTFT overhead read `tracing<base` (−0.37 ms) on the
committed run and `tracing>base` (+0.20 ms) on the fresh run — both under the 5.3 ms
bar, opposite directions; that is the ADR 0007/0008 rule working, not a failure. Soft
check: 23/24 within the declared tolerance (base c=4 TPOT −0.11 vs ±0.10, tracing c=1
TTFT +0.37 vs ±0.35 — reported, never fatal).
