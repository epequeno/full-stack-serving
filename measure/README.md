# measure — regenerating the evidence

**Status: not implemented.**

`make measure LEVEL=<l>` regenerates everything under `results/<l>/` from scratch. Nothing in
`results/` should be hand-edited, and nothing in it should be unreproducible.

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
  Log it rather than quietly re-running until the numbers look good.
- **Measure the instrument.** Repeat a baseline run with the harness detached and report the
  overhead. A measurement whose own cost is unmeasured is a confound.
- **Validate the instrument against a known schedule** (full-stack-serving ADR 0007): a
  separate-process mock SSE server logs its own actual write times, and `loadgen/` is compared
  against that log under load. This runs before any level's numbers are trusted and after any
  `loadgen/` change.
- **One directory per level**, named exactly as the level.
- `make measure` must be safe to re-run. It overwrites its own outputs and touches nothing else.
- **`make verify` never writes to `results/`.** It measures into scratch and compares against the
  committed `findings.json`; it fails only if a finding's verdict changes (full-stack-serving
  ADR 0008). Regenerating evidence stays an explicit `make measure`.

## Sampling (full-stack-serving ADR 0006)

- **Every cell runs 5 times**, interleaved in a recorded, seeded order. Warmup is discarded and counted.
- **A percentile is reported only if its request count supports it:** p50 ≥ 30, p90 ≥ 100,
  p99 ≥ 1000. Otherwise the cell shows `n/a (n=…)`. `n` is requests, never ITL events.
- **Report min / median / max across the 5 runs**, with the pooled figure beside it. No standard
  deviation or confidence interval from five points.
- Record GPU clocks and temperature at run start and end, and whether the server restarted
  between repeats.
