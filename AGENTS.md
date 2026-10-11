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

ADRs 0001, 0002 and 0005–0012 are accepted. **0003 is still `proposed`** — its cross-check has
never run (step 2 below). **0004 is `superseded by 0013`**: its open-loop level assignment no
longer holds, while its closed-loop assignment for L0/L1 carries forward and is restated in 0013.
**0013 is `proposed`.** The vendored `tools/host-inventory.sh` is canonically owned by the
sibling project `epequeno/ebpf-inference-observability` (public, and it runs its serving leg on
this repo's `make up LEVEL=l0-local`).

## Next action

**The spine is complete** — container → Kubernetes → measured observability (L0, L1, L4, all
committed and gated). **L2/L3/L5 are cut, not pending work.** L2/L3 need >1 replica and L5 needs
a second node plus metered GPU; there is one GPU and no second system is being provisioned.
`docs/design.md`'s risk mitigation already says to cut them in exactly this situation. The cut
order (L5 → L3 → L2) now applies to the whole set. One GPU, one owner at a time:
`make down LEVEL=l4-otel` (already down, verified).

What remains is single-machine, and it runs on **saturn** (the Linux GPU host). This repo is the
interface to that work, and this section is the runbook: one GPU, one owner at a time (`make
status`, then `make down LEVEL=<l>` when you stop), ordered so the repo becomes publishable
soonest. Nothing below needs a second machine, and nothing below is L2/L3/L5.

**A clone made before 2026-10-11 is stale** — history was rewritten that day (step 3). Delete it
and re-clone: `git clone git@github.com:epequeno/full-stack-serving.git`

Before deleting the old clone: check `git status` and `git log @{u}..` **on that machine** for
unpushed work, and keep `docs/PRIVATE-context.md` if it is there (it is gitignored, so a clone
does not carry it). Docker artifacts are unaffected — the compose projects use explicit `-p`
names and the Phoenix data volume is a named volume, not a bind mount into the clone.

### 1. Commit the gate evidence (`make gate-log`)

Every PASS in this repo is asserted in prose and written down nowhere, because `make verify` is
read-only by design (ADR 0008). `make gate-log` runs the same gate and commits its output, so the
central claim becomes checkable from a checkout instead of trusted (ADR 0014).

    make down LEVEL=l4-otel           # one GPU — confirm nothing else is on it first
    make up LEVEL=l1-k8s              # the L1 gate measures the live NodePort endpoint
    make gate-log LEVEL=l1-k8s        # rolls the pod, re-runs the L1 matrix into tmp/verify-l1-k8s
    make down LEVEL=l1-k8s            # release the GPU before the L4 arms
    make gate-log LEVEL=l4-otel       # self-contained — measure_l4 composes each arm itself
    git add results/l1-k8s/gates results/l4-otel/gates
    git commit -m "gate: commit the verify output for L1 and L4"

The L1 gate needs `make up`; the L4 gate does not. `measure_l1` measures the Service endpoint
and only rolls the pod; `measure_l4` runs `docker compose up -d` and `down` per arm itself.

**Accept:** each level has `results/<level>/gates/verify-<timestamp>.txt` whose last line is
`result: PASS` and whose header names the commit it ran against. **Cost:** a full re-run of that
level's matrix — budget it exactly like `make measure`; ADR 0006's time cap is the real limit.
**A FAIL is a result, not a mistake:** commit the log either way. ADR 0008's remedy is more runs
per cell or a restated finding — never a loosened rule.

### 2. The ADR 0003 cross-check (do this before publishing; ~half a day)

**full-stack-serving ADR 0003** is `proposed` and its one deliverable has never been run: the
custom client has never been compared against an independent instrument. Every number in this
repo comes from `loadgen/`, so this is the validation the rest of the repo rests on. It needs one
GPU, not a second machine. **It closes ADR 0003 to `accepted` when it has run and the
disagreement is reported.**

Protocol, exactly as ADR 0003 specifies it — do not tune the disagreement away:

- Both tools, the **same fixture** (`loadgen/fixtures/distinct.jsonl`), the **same server** (L0's
  endpoint), the **same concurrency** (1/4/16/64, matching the L0 matrix), the same `max_tokens`
  and `ignore_eos`. Seeds recorded.
- Report the **disagreement** in TTFT and ITL percentiles and throughput. Agreement is evidence
  the client is sound; a systematic gap is a finding about *where* `vllm bench serve`'s timing
  boundary costs accuracy — it timestamps after `json.loads()`, this client timestamps at the
  socket read. Both outcomes are results.
- `tokens_out` still comes from the server's `usage`, never from counting chunks (ADR 0003).

Evidence to commit:

- `results/instrument/crosscheck.json` — both tools' per-cell TTFT/ITL percentiles and throughput,
  the deltas, and the exact commands and versions. Same treatment as `validation.json`.
- Rendered in `results/l0-local/README.md` (which already cites `results/instrument/`) and listed
  in `measure/README.md`'s table, so the instrument's two validations sit together.
- Set ADR 0003 to `accepted`.

Known risk, flag it rather than papering over it: `vllm bench serve` expects its own dataset
format. Getting it to consume *our* fixture (custom dataset path, and matching the tokenizer and
`ignore_eos`) is the one real implementation unknown. If the fixture cannot be matched exactly,
say so in the result and report what was compared instead — a stated mismatch beats a silently
different workload.

### 3. Publish

The repo is **public** as of 2026-10-11 (`github.com/epequeno/full-stack-serving`). Everything
committed is world-readable from now on. The pre-flight, in order:

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

### 4. ADR 0013, open loop at L0 — only after steps 1–3

`adr/0013-open-loop-at-l0.md` (proposed) records the decision. The harness already implements open
loop (`loadgen/client.py --mode open`); no target runs it. It is single-machine and unblocked by
L2/L3. It is a **new experiment**, not part of wrapping up — do not start it before steps 1–3 are
done.

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
