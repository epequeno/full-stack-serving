# 0006 — Report a percentile only when the sample supports it, and repeat every cell

- **Status:** proposed
- **Date:** 2026-10-09

## Context

A cell is one (level, load model, concurrency or rate) combination. Its tail percentiles depend on
how many requests it completed. A p99 from fewer than about 100 requests is the single worst
request, so it is noise. At concurrency 1 a fixed-duration run completes few requests, which makes
this worst in exactly the cell that is cheapest to over-trust.

A single run also gives no evidence about reproducibility. The repo's gate says a fresh run
reproduces the committed numbers. That claim has no meaning without a measured run-to-run spread.

Sample counts are not what they seem:

- **ITL events within one request are correlated.** Many events from 200 requests are not many
  independent samples, so the sample size for any latency figure is the number of **requests**,
  not events.
- **Runs drift.** GPU thermals, clocks and background load change over a session. Repeats run
  back to back can all share one drift.

## Decision

**1. Floors.** A percentile is reported only if the number of requests behind it meets the floor:

| Percentile | Minimum requests |
|---|---|
| p50 | 30 |
| p90 | 100 |
| p99 | 1000 |

Below the floor the cell shows `n/a (n=…)` with the count. It is not shown as a number with a
caveat. The floors are a rule of thumb that keeps the tail from being a single observation. They
are not a statistical guarantee. They are an explicit choice, and they are open to revision.

**2. Repeats.** Every cell is run **R = 5** times. The unit that gets a floor check is whatever
the number is computed from:

- **Per-run** percentiles use that run's request count.
- **Pooled** percentiles (all repeats' requests together) use the pooled count. Pooling can clear a
  floor that a single run cannot, such as p99 from 5 × 200 requests. It also hides run-to-run
  variation, so a pooled figure is always printed next to the per-run spread.

**3. Reported spread.** For each per-run statistic, report **min / median / max across the 5 runs**.
Do not report a standard deviation or confidence interval. With five points those would be
false precision.

**4. Order.** Repeats of different cells are **interleaved** in a recorded, seeded order, not run
as five consecutive blocks per cell, so drift lands across cells instead of in one. Warmup
requests are discarded and counted. Each run records GPU clocks and temperature at start and end
where the driver reports them.

**Not decided here:** the request count per run. It follows from measured throughput and a time
budget for `make measure`, and L0 supplies that. The floors set the *minimum*, not the amount.

## Consequences

- **`make measure` is about five times slower** than a single pass, and the concurrency-1 cell
  is the slowest. If the budget does not allow p99 at concurrency 1, then p99 is `n/a` there. That
  is the intended outcome, not a failure.
- The across-run spread is the **reproducibility band** the gate needs. How "reproduces" is
  defined in terms of that band is fix 4, not decided here.
- Five runs bound run-to-run noise only coarsely. Systematic differences, such as a
  different server restart state, are not shown by repeating within one server session. The
  results state whether the server was restarted between repeats. Whether to restart is
  recorded in `env`.
- Headline numbers are weaker than a single fast run would suggest. That is the cost of not
  claiming more than was observed.
