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

ADRs 0005–0010 and 0012 are accepted (0003's cross-check and 0004's open-loop half stay
proposed). The sibling eBPF observability project has its live serving stack
(`make up LEVEL=l0-local`).

## Next action

**The spine is complete** — container → Kubernetes → measured observability (L0, L1, L4, all
committed and gated). **L2/L3/L5 are cut, not pending work.** L2/L3 need >1 replica and L5 needs
a second node plus metered GPU; there is one GPU and no second system is being provisioned.
`docs/design.md`'s risk mitigation already says to cut them in exactly this situation. The cut
order (L5 → L3 → L2) now applies to the whole set. One GPU, one owner at a time:
`make down LEVEL=l4-otel` (already down, verified).

What remains is single-machine. In order:

### 1. The ADR 0003 cross-check (do this first)

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

### 2. Fix the framing seams a review found

Four small doc fixes, all of them places where the prose is slightly stronger than the evidence:

- **The pitch line.** `README.md` opens *"A complete LLM serving stack — container → Kubernetes →
  inference-aware routing → autoscaling → observability"* while L2 and L3 are cut. The status
  line says so; the pitch line reads as a description of the artifact. Soften it so the unbuilt
  layers are not claimed in the first sentence a reviewer reads.
- **Declare the aggregation.** `results/l1-k8s/README.md`'s matrix is **pooled across repeats**,
  while `findings.json` and the verdicts use the **median of per-run medians**. At c=4 TTFT the
  two differ by 1.2 ms — more than the declared soft tolerance. Label the table and note where
  the methods diverge. Same for L4's gap column (12.37 pooled vs 19.67 median-of-run-medians at
  c=64).
- **L4's `client > span` sentence.** It says the prediction holds "everywhere" directly under a
  table that reads `indistinguishable` at c=4/16/64. The basis is the per-request minimum gap,
  which is sound — but the sentence reuses the verdict string. Say which of the two it means.
- **L4's arm order.** Base ran first, tracing second, and the one separated overhead verdict
  points the *wrong* way — so the two arms cannot separate tracing cost from drift, and the drift
  direction favours the tracing arm. State that as a limitation of the overhead finding, not a
  footnote.

### 3. Make the gate auditable

Every PASS (`make verify LEVEL=l1-k8s`, `LEVEL=l4-otel`) is asserted and never committed —
`verify` writes to scratch by design. Commit the verify output for the levels that have been
gated, so the repo's central claim is checkable from a checkout rather than trusted.

### 4. Then, if there is appetite — ADR 0013, open loop at L0

`adr/0013-open-loop-at-l0.md` (proposed) records the decision. The harness already implements
open loop; no target runs it. It is single-machine and unblocked by L2/L3. It is a **new
experiment**, not part of wrapping up — do not start it before the pre-public checklist below is
done.

### 5. Before publishing

Run the two checks in the rules above (both currently pass — verified 2026-10-11):
`git log --all -p -- docs/PRIVATE-context.md` and the `git grep` for `build seq` / `job-search`.
History was squashed on 2026-10-09 for this reason; re-verify before flipping visibility.

**Working-tree redactions (applied 2026-10-11).** Private IPv4 addresses and the wireless
interface name are redacted in `results/l0-local/host-inventory.md`, and `make inventory` now
redacts them at generation time — the vendored script is left byte-identical so
`tools/HOST-INVENTORY.source`'s sha256 still detects real drift. The absolute host path is gone
from `results/l1-k8s/l1.json` (`baseline.runs`, now repo-relative) and from
`tools/HOST-INVENTORY.source`. No MACs, keys, tokens or serials were found anywhere in the tree.

**Still in history, and the redactions do not fix it.** The host's LAN and gateway addresses
(commit `086634b`) and the absolute `/home/<user>` path (commits `086634b`, `6fe8112`,
`46616d5`) remain visible in `git log -p`.
Redacting the working tree does not remove them, and a public repo exposes them through history.
Two options:

- **Rewrite history** with `git filter-repo` before flipping visibility. Cheap here — no doc in
  this repo cites an internal commit SHA, so nothing breaks internally. Note that saturn's clone
  must be re-cloned rather than pulled.
- **Accept it.** RFC1918 addresses are not routable from the internet and the local username is
  already public elsewhere. This is a defensible call.

**Owner's decision, not a default.** Also note the sibling project (`ebpf-inference-observability`)
is named in `tools/host-inventory.sh` and `tools/HOST-INVENTORY.source`; publishing reveals that
connection, which is a separate choice.

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

**The repo is private** (`epequeno/full-stack-serving` on GitHub, pushed 2026-10-09). Do not make it
public without the owner saying so. `docs/PRIVATE-context.md` is gitignored planning material and
must never be committed. History was squashed on 2026-10-09 to remove earlier commits that
contained it. Before making the repo public, confirm with
`git log --all -p -- docs/PRIVATE-context.md` (expect nothing) and
`git grep -n -e 'build seq' -e 'job-search' $(git rev-list --all) -- . ':!AGENTS.md'` (expect
nothing).

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
