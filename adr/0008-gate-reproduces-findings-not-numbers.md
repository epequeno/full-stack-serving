# 0008 — The gate reproduces the findings; the numbers are a soft check

- **Status:** proposed
- **Date:** 2026-10-09

## Context

The gate said: *"A fresh run produces the numbers in the repo."* Latency numbers cannot satisfy
that. They vary run to run, and with thermals, clocks, driver state and the host. Read
literally the gate either fails every time or passes only because nobody ran it. Read loosely
it checks nothing.

The claims this repo makes are comparisons ("L1 is not slower than L0", "prefix-aware routing
beats round-robin", "scaling reduced queue depth"), not absolute figures. Absolute numbers are
environment-bound by design (`results/README.md`) and, under full-stack-serving ADR 0002, are
for a small model that does not transfer anyway.

Any tolerance on absolute numbers has a circularity trap, the same one as in full-stack-serving
ADR 0007: a tolerance chosen before data is arbitrary, and one chosen after seeing the fresh
run's deviation is made to pass.

## Decision

**Hard gate: every committed finding must reproduce.**

Each level that makes a comparison commits `results/<level>/findings.json`, produced by
`make measure`, never hand-written. A finding records the metric, the two conditions, and a
**verdict**. A verdict is computed by a fixed rule from the per-run values (ADR 0006), not
judged:

> For metric M (the per-run median) over conditions A and B, with five per-run values each:
> - `A<B` if `max(A) < min(B)`
> - `A>B` if `min(A) > max(B)`
> - `indistinguishable` otherwise

The gate passes only if every finding's verdict in the fresh run **equals** the committed
verdict. A missing, invalid or discarded finding is a failure.

**What the rule is and is not.**

- If runs were independent and A and B had the same distribution, the chance that the five and five
  values are fully separated in either direction is `2 / C(10,5)` = 2/252 ≈ **0.8%**. So a
  distinguishable verdict is a strong claim. That figure assumes independent runs.
  Drift between runs breaks the assumption, which is why ADR 0006 interleaves cells.
- It only detects large effects. **`indistinguishable` means "not separated by 5 runs", not
  "equal".** Parity verdicts are worded that way everywhere. L1's "parity" is "no regression
  detectable at this resolution".
- **A verdict near the boundary can flip on a fresh run.** That is a true result: the effect is
  at the instrument's resolution. The gate fails honestly. The remedy is more runs per cell
  (changing the declared R, then regenerating), or restating the finding. Loosening the rule to
  pass is not a remedy.
- Alongside each verdict the finding records the **effect size** (ratio of medians) and the
  **margin** (gap between the nearest values), so it is visible when a verdict is close.

**Soft check: absolute numbers, reported, never failing the gate.**

- The fresh run's per-cell median is compared with the committed median, and the ratio is
  printed.
- A tolerance is **declared in a committed file before the comparison is run**, derived from the
  across-run spread of the committed data. Its value is **not decided here**; L0 provides the
  spread.
- Exceeding it is reported as "numbers outside tolerance". It does not fail the gate. The
  report names which cells.
- If the environment differs materially from the committed one (GPU model, driver, or CUDA
  version, per `env`), the soft check is skipped and the report says why. The findings gate still
  applies, and a failure there is reported with the environment difference alongside it.

**The command.** `make verify LEVEL=<l>` runs `measure` into a scratch directory (never over
`results/`), compares against the committed `findings.json` and numbers, and exits non-zero only
on a findings mismatch. Regenerating committed evidence stays an explicit act:
`make measure`.

## Consequences

- The headline gate wording changes from "produces the numbers in the repo" to "reproduces the
  findings, and lands within a declared tolerance of the numbers". The README, `docs/design.md`
  and `measure/README.md` are updated to match.
- A weaker, honest claim: the repo shows its conclusions hold on a fresh run. It does not claim
  anyone will get the same milliseconds.
- **Findings must be declared before they can be checked.** Each level's design states its
  findings up front, so a finding cannot be chosen after the data to be one that reproduces.
  Findings that do not reproduce are kept and recorded, not dropped.
- L4's evidence (a trace and a stability note) is not a comparison, so this rule does not yet cover
  it. Giving L4 a checkable result is a separate decision.
- The verdict rule is coarse by design. If a finding needs finer resolution than five runs give,
  the cost is more runs, and the time budget for `make measure` (ADR 0006) is the real limit.
