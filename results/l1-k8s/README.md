# L1 — results (the L0 workload on k3s)

Measured 2026-10-10 by `make measure LEVEL=l1-k8s` on saturn (the Linux GPU host) — **10
interleaved, seeded repeats** per cell (R=10; why below). Environment in [env.md](env.md);
assembled matrix and findings in [l1.json](l1.json) / [findings.json](findings.json); raw
per-run JSON in [runs/](runs/). The baseline is the committed L0 runs
([../l0-local/runs/](../l0-local/runs/)); the instrument's error envelope is
[../instrument/validation.json](../instrument/validation.json) (full-stack-serving ADR 0007).

## The matrix

2,560 requests per cell, 0 errors, no server restart between repeats. The pod is rolled
**fresh before every measure run** (verified: pod name change + "successfully rolled out"),
so the prefix cache starts cold exactly as L0's fresh container did — the fixture check below
is what proves it.

| c | TTFT p50 (ms) | TTFT p99 (ms) | ITL/event p50 (ms) | ITL/event p99 (ms) | TPOT p50 (ms) | tok/s, repeat min–max |
|---|---|---|---|---|---|---|
| 1 | 14.81 | 17.2 | 5.86 | 6.59 | 5.96 | 164.6–166.4 |
| 4 | 24.12 | 97.7 | 6.49 | 7.49 | 6.61 | 585–597 |
| 16 | 43.58 | 319.5 | 7.30 | 11.92 | 7.45 | 1260–2088 |
| 64 | 107.58 | 239.6 | 12.69 | 24.69 | 12.81 | 4631–4700 |

"ITL/event" is per-SSE-event, not per-token (full-stack-serving ADR 0005). The c=16 tok/s
floor (1260) is one repeat under desktop background load — kept, not discarded (ADR 0006);
the other nine sit at 2000–2090.

## The L1 vs L0 verdicts (full-stack-serving ADR 0008)

The comparison is **"Kubernetes deployment vs Docker deployment"** — never "Kubernetes
overhead": L0 ran under Docker, L1 under k3s's containerd with the `nvidia` runtime, a CNI and
kube-proxy Service networking in the path (ADR 0010). Every verdict, over ALL per-run medians
each side (this run R=10, the committed L0 baseline R=5), by the fixed sign rule:

| metric | c=1 | c=4 | c=16 | c=64 |
|---|---|---|---|---|
| TTFT p50 | indistinguishable (0.99×) | indistinguishable (0.98×) | indistinguishable (1.18×) | indistinguishable (1.01×) |
| ITL/event p50 | indistinguishable (1.00×) | indistinguishable (1.00×) | indistinguishable (1.00×) | indistinguishable (1.00×) |
| TPOT p50 | indistinguishable (1.00×) | indistinguishable (0.99×) | indistinguishable (0.99×) | indistinguishable (1.00×) |

**What may be claimed: nothing directional, at any cell.** ADR 0007 (fixed before L1)
restricts a between-level finding to |effect| ≥ 10× the cell's committed instrument p99
error — bars 5.3/13.7/18.4/53.9 ms at c=1/4/16/64. Every measured effect is below its
bar (largest: c=16 TTFT, 6.8 ms vs 18.4). The claimable finding is the aggregate:
**no claimable between-level difference — parity at the instrument's resolution**.
The 12 mechanical verdicts are recorded in [findings.json](findings.json) (never
dropped); at sub-bar margins they flip between fresh runs by design — observed
2026-10-10: c=1 TTFT read `l1<l0` (margin 0.012 ms) on the first gate run and
`indistinguishable` on the committed run. The gate checks the claimable set and
reports sub-bar flips as information (ADR 0007/0008: restricting claims to effects
above the instrument's error is the sanctioned remedy; loosening the rule is not).

**Why R=10.** At R=5 two cells sat on a knife-edge (c=1 TTFT and c=16 ITL, margins
0.003–0.015 ms) and their verdicts **flipped between identical-protocol runs**. ADR
0008 prescribes more runs per cell, then regenerate — R=10 moved every cell into the
stable regime (the R=5 history is kept here, not quietly dropped). The knife-edge then
reappeared at c=1 TTFT under R=10, which is what forced the claimability restatement
above: more runs sharpen a sub-resolution effect, they do not make it claimable.

## The overhead pair (ADR 0005)

One extra pair at c=16, per-chunk `usage` option on vs off (`stream_options.continuous_usage_stats`):

| | TTFT p50 (ms) | ITL/event p50 (ms) | TPOT p50 (ms) |
|---|---|---|---|
| with continuous usage | 45.69 | 7.31 | 7.45 |
| without | 38.76 | 7.29 | 7.44 |

Deltas: TTFT +6.9 ms — inside the c=16 TTFT repeat band (33.5–45 ms in this run), so it is
run-to-run spread, not a measured cost; ITL +0.02 ms. Consistent with L0's pair (≈free at
p50): the option stays viable as the per-event token source.

## Prefix-cache check (fixtures "Not verified")

One c=1 pass over `distinct`, run FIRST on the freshly rolled pod — cold prefix cache, with
`vllm:*prefix_cache*` counters scraped around it: **hits 4,080 / queries 340,614 = 1.2%**, the
chat-template prefix every request shares — matching L0's cold figure and the fixture's
2-word maximum shared prompt prefix. The check is run-first for a measured reason: on
2026-10-10 a warm-cache pass read **98.7%** (replay self-hits), and the first L1 measure —
before the roll was verified — read the same. `distinct` defeats cross-prompt prefix
caching in-cluster: **verified**.

## The gate (ADR 0008, restricted by ADR 0007)

`make verify LEVEL=l1-k8s` re-runs the whole experiment into scratch (never `results/`) with
the committed protocol and compares against `findings.json`.

**2026-10-10: PASS.** Fresh R=10 matrix (40/40 ok, roll verified, cold check 1.2%): every
**claimable** finding reproduced — the claimable set is empty, and that is the result. One
sub-bar flip was recorded as information: c=1 TTFT read `l1<l0` (margin 0.016 ms) vs the
committed `indistinguishable` — at the instrument's resolution, ungated by the claimability
rule. The *first* gate run (before the restatement) failed honestly on the same flip:
the gate did its job twice. Soft check: 11/12 absolute medians within the declared
tolerance; c=4 TTFT moved −0.44 ms against ±0.381 — reported, never fatal, per ADR 0008.

## Harness vs GPU (ADR 0011 criterion 3)

Same conclusion as L0: ITL/event ≈ 5.9 ms at c=1 against a client timing error of 0.4 ms
p50, and the NodePort/CNI path the verdicts ran over costs nothing detectable at 10 runs.
The harness is not the bottleneck.

## Raw-run provenance (known defect in the committed runs)

Every file in `runs/` (44/44) records `run.level: "l0-local"` in its `run` block. The runs are
L1; only the label is wrong. Cause: `loadgen.client --level` defaults to `l0-local`, and
`measure_l1` reused `measure_l0.run_loadgen`, which hardcoded `--level l0-local`. Fixed by
threading an explicit `level` parameter through that helper (`measure_l0.py`, L0 keeps the
default) and passing `level="l1-k8s"` at all four L1 call sites (`measure_l1.py`).

What this does and does not affect. **Not affected:** every number, verdict and claim here —
the matrix is assembled from the in-memory run objects and the baseline is read from an
explicit path, so nothing keys off `run.level`. **Affected:** re-deriving findings from
`runs/` alone, which would compare L0 against L0. The committed raw files were left as
measured rather than edited in place; a fresh `make measure LEVEL=l1-k8s` writes the correct
label. The `l1.json`/`findings.json` top-level `level` fields are correct (`l1-k8s`).