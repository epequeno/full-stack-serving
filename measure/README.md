# measure — regenerating the evidence

**Status: implemented for L0** (`make measure LEVEL=l0-local`).

| piece | what |
|---|---|
| `measure/mock_sse.py` | the ADR 0007 reference: OpenAI-shaped mock SSE server on a configured schedule, logging the monotonic time of every actual write. Separate process from the client, same host. |
| `measure/validate.py` | orchestrates mock + client at each concurrency plus one CPU-loaded run; reports per-event error vs the mock's logged writes, the mock's own jitter, and count checks. `make validate-instrument` → `results/instrument/`. Pass criterion fixed from L0's numbers (ADR 0007): a finding is claimable only above 10 × the instrument's p99 error for its cell (5.3–53.9 ms, c=1→64). |
| `measure/measure_l0.py` | the L0 experiment: warmup pass (discarded, counted), the seeded interleaved 5-repeat × 1/4/16/64 matrix, the continuous-usage overhead pair (ADR 0005), the prefix-cache fixture check, GPU clock/temp and server-counter capture, env.md. → `results/l0-local/`. |

The raw JSON is the artifact; [`results/l0-local/README.md`](../results/l0-local/README.md)
renders the committed matrix as tables. Four cells don't warrant a chart — the JSON is the
data, and the tables are its view.
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
