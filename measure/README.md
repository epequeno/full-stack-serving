# measure — regenerating the evidence

**Status: implemented for L0 and L1** (`make measure LEVEL=l0-local|l1-k8s`; the L1 gate:
`make verify LEVEL=l1-k8s`).

| piece | what |
|---|---|
| `measure/mock_sse.py` | the ADR 0007 reference: OpenAI-shaped mock SSE server on a configured schedule, logging the monotonic time of every actual write. Separate process from the client, same host. |
| `measure/validate.py` | orchestrates mock + client at each concurrency plus one CPU-loaded run; reports per-event error vs the mock's logged writes, the mock's own jitter, and count checks. `make validate-instrument` → `results/instrument/`. Pass criterion fixed from L0's numbers (ADR 0007): a finding is claimable only above 10 × the instrument's p99 error for its cell (5.3–53.9 ms, c=1→64). |
| `measure/measure_l0.py` | the L0 experiment: warmup pass (discarded, counted), the seeded interleaved 5-repeat × 1/4/16/64 matrix, the continuous-usage overhead pair (ADR 0005), the prefix-cache fixture check, GPU clock/temp and server-counter capture, env.md. → `results/l0-local/`. |
| `measure/measure_l1.py` | the L1 experiment: **the same protocol as L0** (same fixture, seed, interleaved order, overhead pair, cold-cache prefix check), reusing `measure_l0`'s helpers, plus a k8s env snapshot (runtime class, device plugin, allocatable GPU, network path) and the pod rolled fresh — **verified**, not assumed — so the matrix starts cold. Writes `findings.json` from the committed L0 per-run medians vs this run's, with ADR 0007 claimability (bar = 10× the cell's committed instrument p99 error) computed per finding. → `results/l1-k8s/`. |
| `measure/verify_l1.py` | the ADR 0008 gate for L1: re-runs `measure_l1.run_level` into scratch (never `results/`), using the *committed* protocol from `l1.json`. HARD: every **claimable** finding (ADR 0007 bar cleared) must reproduce — a missing, changed or newly-claimable verdict fails; sub-bar verdicts sit at the instrument's resolution and flip between fresh runs by design, so they are reported as information, never gated. SOFT: absolute-number deltas vs the tolerance declared at measure time (skipped on environment drift). |
| `measure/measure_l4.py` | the L4 experiment: **two arms over the identical engine** — `base` (no exporter, the control) and `tracing` (`--otlp-traces-endpoint` → collector → Phoenix, all real) — run sequentially (one GPU; base first, the order is a recorded confound), each arm the L0 protocol (same fixture/seed/interleaved order, R=5). Finding 1: tracing-overhead verdicts (ADR 0008 sign rule) with ADR 0007 claimability bars; Finding 2: the span-to-client TTFT gap, paired by trace id (loadgen's `trace_id`) with the engine's `gen_ai.request.id` == the client-recorded `response_id` as the cross-check — v0.23.0 does NOT echo `X-Request-Id` into the span. Coverage per cell vs the committed 98% floor (ADR 0009). → `results/l4-otel/`. |
| `measure/verify_l4.py` | the ADR 0008 gate for L4: re-runs `measure_l4.run_level` into scratch (never `results/`) with the committed protocol; HARD on claimable findings (ADR 0007 bar), sub-bar flips reported as information, low-coverage gap cells flagged INVALID (never gated), SOFT on absolute medians vs the tolerance declared at measure time, skipped on image-digest or GPU drift (`latest`-tagged Phoenix/collector make digest drift a live possibility). |

The raw JSON is the artifact; [`results/l0-local/README.md`](../results/l0-local/README.md)
and [`results/l1-k8s/README.md`](../results/l1-k8s/README.md) render the committed data as
tables. Four cells per level don't warrant a chart — the JSON is the data, and the tables
are its view.
`make verify` (ADR 0008) starts at L1 — L0 is the baseline and commits no findings.

`make measure LEVEL=<l>` regenerates everything under `results/<l>/` from scratch. Nothing in
`results/` should be hand-edited, and nothing in it should be unreproducible. `measure_l0`
overwrites only its own outputs (`l0.json`, `env.md`, `runs/`); it never touches
`host-inventory.md`.

## Contract

1. Bring the level to a known state (or assert it already is).
2. Run `loadgen/` at each configured concurrency level.
3. Emit raw JSON to `results/<l>/`.
4. Render charts from the raw JSON — never the reverse. The data is the artifact; the chart is a
   view of it.
5. Write `results/<l>/env.md` recording the environment the run happened in.

## Why this exists separately from loadgen

`loadgen/` is an instrument. `measure/` is the experiment: which concurrency levels, how long,
in what order, with what warmup, and what is considered a valid run.

Keeping them apart means the same instrument is applied to every layer, which is the only way
"L1 is 8% slower than L0" is a comparable statement.

## Rules

- **No screenshots.** If a claim needs a picture, the underlying data must also be committed.
- **Record failed runs.** A run that was discarded because the machine was busy is information.
  Log it rather than quietly re-running until the numbers look good. (`measure_l0` records failed
  runs in `l0.json` and exits non-zero; it never silently retries.)
- **Measure the instrument.** Repeat a baseline run with the harness detached and report the
  overhead. A measurement whose own cost is unmeasured is a confound. (Two instruments for this:
  the ADR 0007 error distribution, and the continuous-usage on/off pair ADR 0005 requires.)
- **Validate the instrument against a known schedule** (full-stack-serving ADR 0007): a
  separate-process mock SSE server logs its own actual write times, and `loadgen/` is compared
  against that log under load. This runs before any level's numbers are trusted and after any
  `loadgen/` or Python/aiohttp version change — i.e. on the GPU host, whose numbers get
  committed.
- **One directory per level**, named exactly as the level.
- `make measure` must be safe to re-run. It overwrites its own outputs and touches nothing else.
- **`make verify` never writes to `results/`.** It measures into scratch and compares against the
  committed `findings.json`; it fails only if a finding's verdict changes (full-stack-serving
  ADR 0008). Regenerating evidence stays an explicit `make measure`.

## Sampling (full-stack-serving ADR 0006)

- **Every cell runs 5 times**, interleaved in a recorded, seeded order. Warmup is discarded and counted.
- **A percentile is reported only if its request count supports it:** p50 ≥ 30, p90 ≥ 100,
  p99 ≥ 1000. Otherwise the cell shows `{"n/a (n)": n}`. `n` is requests, never ITL events.
- **Report min / median / max across the 5 runs**, with the pooled figure beside it. No standard
  deviation or confidence interval from five points.
- Record GPU clocks and temperature at run start and end, and whether the server restarted
  between repeats (detected from the request counter going backwards, not assumed).
