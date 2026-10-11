# Agent instructions

## What this project is

A measured LLM serving stack: container → Kubernetes → inference-aware routing → autoscaling →
observability, where each level must produce evidence rather than an assertion. It exists to
demonstrate ecosystem and deployment fluency, not to be novel.

Read [`README.md`](README.md) for the level map, then [`docs/design.md`](docs/design.md) for the
reasoning. Decisions the stack forced live in `adr/`.

## Status

**L0, L1 and L4 are measured.** On saturn (the Linux GPU host):

- **L0** (`results/l0-local/`, 2026-10-10): TTFT/ITL/TPOT at 1/4/16/64, 5 repeats, 0 errors;
  instrument validation in `results/instrument/` (ADR 0007; 0 count failures, bars fixed).
- **L1** (`results/l1-k8s/`, 2026-10-10): the same workload on `k3s` v1.37.1+k3s1 (ADR 0010),
  R=10. 12/12 verdicts vs the committed L0 baseline `indistinguishable`, **no directional
  finding claimable** — parity at the instrument's resolution; the gate failed honestly once
  (c=1 TTFT flip at a 0.012 ms margin), then the ADR 0007/0008 claimability restatement made
  it PASS on a fresh run.
- **L4** (`results/l4-otel/`, 2026-10-11): the two ADR 0009 findings over the identical
  engine, two compose arms run sequentially (base = control, then tracing → collector →
  Phoenix, all real). **Overhead:** none detectable at this resolution (every effect under
  its ADR 0007 bar; one sub-bar `tracing<base` flip recorded as info). **Gap:** prediction
  holds — `client > span` on every request (100% span coverage at every cell, 98% floor);
  the gap **grows ~an order of magnitude from c=1 to c=64** (pooled p50 1.05 → 12.37 ms),
  so the unspanned path (HTTP frontend, event loop, SSE) queues under load — recorded,
  none of it claimable (all sub-bar). Committed pair in `results/l4-otel/trace-example.json`.

ADRs 0001–0012 are accepted. **0003 was accepted 2026-10-11** — its cross-check ran (step 2,
done): `loadgen/` vs `vllm bench serve` v0.23.0 over the same fixture/server/concurrency,
both committed in `results/instrument/` (`crosscheck.json`, rendered in its README). Per-event
ITL and throughput agree at every cell (|Δ| ≤ 0.03 ms, ≤ 0.1 req/s); TTFT is bench-higher
directionally, clearing the ADR 0007 bar at c=16 (−50 ms, −2.7 × bar) — the reported
disagreement, not tuned away. **0004 is `superseded by 0013`**: its open-loop level assignment no
longer holds, while its closed-loop assignment for L0/L1 carries forward and is restated in 0013.
**0013 was accepted 2026-10-11** — the open-loop sweep ran at L0's endpoint (step 4, done):
seeded Poisson arrivals at λ = 2/4/8/16/24/32 req/s, R=5 interleaved, 30/30 runs valid under
the pre-declared lag rule (0 refused hidden numbers: refusals 0 → 780 as λ crosses the
server's ≈ 26 ok/s ceiling; TTFT p99 swells 127 → 395 ms, the tail ADR 0004 said closed loop
understates). `results/l0-local/open/`, rendered in the L0 README. The vendored
`tools/host-inventory.sh` is canonically owned by the
sibling project `epequeno/ebpf-inference-observability` (public, and it runs its serving leg on
this repo's `make up LEVEL=l0-local`).

## Next action

**The spine is complete and its evidence is committed** — container → Kubernetes → measured
observability (L0, L1, L4), gate logs committed (step 1, done), and the instrument
twice-validated (step 2, done: ADR 0007 against the mock server, ADR 0003 against
`vllm bench serve`, both in `results/instrument/`). **L2/L3/L5 are cut, not pending work.**
L2/L3 need >1 replica and L5 needs a second node plus metered GPU; there is one GPU and no
second system is being provisioned. `docs/design.md`'s risk mitigation already says to cut
them in exactly this situation. The cut order (L5 → L3 → L2) now applies to the whole set.

What remains is single-machine, and it runs on **saturn** (the Linux GPU host). This repo is
the interface to that work, and this section is the runbook: one GPU, one owner at a time
(`make status`, then `make down LEVEL=<l>` when you stop). **All four steps are done** —
records below; step 3's checks are standing and re-run after every push. The repo is
public, gated, twice-validated and fully swept: no actionable work remains on the current
single-GPU plan. New work starts from a new decision, not from this runbook.

**A clone made before 2026-10-11 is stale** — history was rewritten that day (step 3). Delete it
and re-clone: `git clone git@github.com:epequeno/full-stack-serving.git`

Before deleting the old clone: check `git status` and `git log @{u}..` **on that machine** for
unpushed work, and keep `docs/PRIVATE-context.md` if it is there (it is gitignored, so a clone
does not carry it). Docker artifacts are unaffected — the compose projects use explicit `-p`
names and the Phoenix data volume is a named volume, not a bind mount into the clone.

### 1. Commit the gate evidence (`make gate-log`) — DONE 2026-10-11

`results/l1-k8s/gates/verify-2026-10-11T1802Z.txt` (L1, needs `make up`) and
`results/l4-otel/gates/verify-2026-10-11T1943Z.txt` (L4, self-contained) — last line
`result: PASS`, headers naming the commit each ran against (ADR 0014). Two earlier L4 logs
are committed as results, not mistakes: an invocation FAIL (the `ENDPOINT` Makefile default
lost in the L1 rename) and an infra FAIL (a docker bind-mount auto-created root-owned spans
scratch the collector could not write into). Both fixed, L4 gate re-run, PASS.
`tools/gate-log.sh` now redacts host-identifying literals at write time — the same policy as
`make inventory`, because the first teed logs put the absolute home path back into history
(caught by step 3's check, post-generation redaction applied, nothing pushed with it).

### 2. The ADR 0003 cross-check — DONE 2026-10-11, ADR 0003 accepted

`make crosscheck` (new) drives both tools against the same L0 endpoint, one cell at a time
(c=1/4/16/64, same fixture, seed 20261010, `max_tokens` 128, `ignore_eos`), and commits
`results/instrument/crosscheck.json`; `results/instrument/README.md` renders the
instrument's two validations together (ADR 0007 and ADR 0003). The reading, reported not
tuned away: per-event ITL and throughput agree at every cell (|Δ| ≤ 0.03 ms, ≤ 0.1 req/s);
TTFT is bench-higher directionally, clearing the ADR 0007 bar at c=16 (−50 ms, −2.7 × bar) —
the expected signature of bench's later timing boundary (after `json.loads()`, inside
aiohttp's stream iterator) vs loadgen's socket read. The one implementation unknown
resolved as ADR 0003 allowed: the pinned image needs the `bench` extra (`pandas`) to load a
custom dataset, so the cross-check uses a derived image (`crosscheck-bench:v0.23.0-pandas` —
pinned base digest + `pip install pandas 3.0.6`), recorded in the JSON's `env` with both
tools' timing boundaries and the exact per-cell commands.

### 3. Publish

The repo is **public** as of 2026-10-11 (`github.com/epequeno/full-stack-serving`). Everything
committed is world-readable. The pre-flight checks are **standing** — re-run after every push
(they caught a real leak on 2026-10-11: the first teed gate logs carried the absolute home
path; fixed at write time in `tools/gate-log.sh`, see step 1). In order:

1. **History carries no private literals.** History was rewritten on 2026-10-11
   (`git filter-repo --replace-text`, plus a corrected commit message) to strip the host's LAN and
   gateway addresses, the wireless interface name, and the absolute home paths that earlier commits
   had committed. Re-verify after any further commit:

       git log --all -p | grep -nE '(^|[^0-9])(192\.168\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}|10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})'
       git log --all -p | grep -nE '/home/[a-z]+|/Users/[a-z]+|dev wl[a-z0-9]+'

   Expect nothing. This is also why `make inventory` redacts at generation time (section *Rules*):
   a regenerated report must not put the values back. The vendored script itself stays
   byte-identical, so `tools/HOST-INVENTORY.source`'s sha256 still detects real drift.
2. **Planning context was never committed.** `docs/PRIVATE-context.md` is gitignored, and the
   2026-10-09 squash removed the commits that had contained it:

       git log --all -p -- docs/PRIVATE-context.md                            # expect nothing
       git grep -n -e 'build seq' -e 'job-search' $(git rev-list --all) -- . ':!AGENTS.md'

3. **The sibling project is public too**, so naming it is not a disclosure. `Makefile` and
   `tools/HOST-INVENTORY.source` name `ebpf-inference-observability`, which is public
   (`github.com/epequeno/ebpf-inference-observability`) and already cross-references this repo.

**What the rewrite cost.** Every commit SHA changed, so any existing clone — saturn's included —
must be re-cloned rather than pulled. No document in this repo cites an internal SHA, so nothing
internal broke. A pre-rewrite bundle is kept outside this repo, locally, and was never pushed.

### 4. ADR 0013, open loop at L0 — DONE 2026-10-11, ADR 0013 accepted

`make measure-open-l0` (new; `measure/measure_open.py`) ran the sweep at L0's endpoint:
seeded Poisson arrivals (seed 20261010, cap 64 in flight) at λ = 2/4/8/16/24/32 req/s,
R=5 interleaved, warmup discarded and counted, GPU/counters captured per run. **30/30
runs valid** under the lag rule pre-declared in `open.json`'s protocol block (lag p50
0.61–0.67 ms, 50–80× under its bars — saturation showed as refusals at the cap, never
schedule lag, so the λ numbers are server behaviour). The consequence ADR 0004 left
unmeasured is measured: the server's open-loop ceiling is ≈ 26 ok/s (offered 32, refused
16.6% at the cap), TTFT p50 rises 22.8 → 52.0 ms while ITL p50 only 6.2 → 12.1, and the
pooled TTFT p99 swells 127 → 395 ms — the tail-under-offered-load that closed loop
structurally understates, and the second view ADR 0013 wanted for L4's span-gap finding.
`results/l0-local/open/` (data + raw runs), rendered in the L0 README; never substituted
into the closed-loop baseline.

## Rules

**A level that produces no number is not done.** Screenshots are not evidence. Every level's
result goes in `results/<level>/` as data (JSON/CSV) plus whatever chart it warrants.

**Record the environment with every measurement.** Kernel, driver, CUDA, server version, model,
quantization, and whether page caches were dropped or CUDA graphs were enabled. A number without
its environment is not reproducible.

**Measure the measurement.** The harness's own overhead is a confound. Run the workload with and
without instrumentation and report the delta.

**Never claim a capability the artifact doesn't demonstrate.** Until L1 runs on `k3s`, do not
claim Kubernetes; until L5 provisions, do not claim Terraform. The whole point is that the
artifact is the evidence.

**Cloud is ephemeral.** Any GPU instance is brought up, measured, torn down, and its cost
recorded in `results/l5-multinode/cost.md`. No instance idles.

**`results/` is committed. Scratch is not.** Anything that cannot be regenerated by
`make measure` should not be in `results/`.

**The repo is public** (`epequeno/full-stack-serving`, public since 2026-10-11). Treat every
commit as published. `docs/PRIVATE-context.md` is gitignored planning material and must never be
committed; history was squashed on 2026-10-09 to remove the commits that had contained it, and
rewritten on 2026-10-11 to remove host-identifying literals earlier commits had committed. The
checks that verify both are in the next-action runbook, step 3.

## Conventions

- One directory per level under `stack/`. The shared `loadgen/` and `measure/` are used by *every*
  level — that is what makes the levels comparable, so do not fork them per level.
- ADR numbering is **per-project**. In this repo, `adr/0001`. Anywhere else, always qualify:
  *"full-stack-serving ADR 0001"*, never a bare "ADR 0001".
- Prefer Helm values or plain manifests committed as-is over generated-in-place by a tool, so a
  reviewer can read the actual object.

## Out of scope

- Benchmarking models or hardware for a leaderboard.
- Production concerns: HA, backups, multi-tenancy, SLAs.
- Model training or fine-tuning.
- Custom kernels or a from-scratch inference server — a different project with the opposite
  goal.
