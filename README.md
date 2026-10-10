# full-stack-serving

**Most serving-stack demos are screenshots. This one is measurements.**

A complete LLM serving stack — container → Kubernetes → inference-aware routing → autoscaling →
observability — in which every layer must produce a **number** rather than an assertion.

**Status: L0 is measured.** Container + vLLM + load generator ran on the GPU host on 2026-10-10:
[`results/l0-local/`](results/l0-local/README.md) has TTFT/ITL/TPOT at 1/4/16/64 concurrency
(zero errors), and [`results/instrument/`](results/instrument/) the ADR 0007 instrument
validation. L1–L5 are not started. Reasoning per level: [`docs/design.md`](docs/design.md).
Decisions: [`adr/`](adr/).

## Why this exists

Anyone can follow llm-d's quickstart and post a `kubectl get pods`. That shows you can follow
instructions. It does not show you know *why* the stack is shaped that way, what it costs, or what
each layer changes when you turn it on.

This repo is the same stack, instrumented so every level yields evidence.

## The anti-tutorial test

The failure mode is a re-typed quickstart. Three pass conditions:

1. **It produces numbers upstream docs don't.** Upstream tells you how to install. It does not
   tell you what TTFT looked like on *your* hardware at *your* concurrency levels.
2. **It makes decisions upstream deliberately doesn't.** llm-d supports vLLM *and* SGLang;
   something has to choose, and the reasoning is the artifact.
3. **It documents constraints upstream doesn't have.** A single GPU on a home LAN is nobody's
   target environment.

If a section here could be replaced by a link to upstream docs, it should be.

## Levels

Each level is independently demonstrable and produces its own evidence. Stop anywhere — the repo
stays honest about where it stopped.

| Level | What | Evidence it must produce | Status |
|---|---|---|---|
| **L0** | Container + vLLM + load generator | TTFT/ITL at 1/4/16/64 concurrency, committed as JSON | **measured 2026-10-10** — `results/l0-local/` |
| **L1** | The same on `k3s`, GPU via device plugin | in-cluster numbers + an explicit **parity or regression** verdict vs L0 | not started |
| **L2** | Gateway API Inference Extension / llm-d routing | cache hit rate, prefix-aware vs round-robin | not started |
| **L3** | KEDA autoscaling on queue depth | replica-vs-depth trace, scale latency | not started |
| **L4** | OTel `gen_ai.*` → Arize Phoenix | tracing overhead (on vs off), the span-to-client TTFT gap, and a committed trace | not started |
| **L5** | Multi-node / P-D disaggregation | co-located vs disaggregated, plus **measured cost** | not started |

**Cut order if time runs short:** L5 → L3 → L2. **Keep L0, L1, L4.** Container → Kubernetes →
measured observability is the spine; if only that ships, the project still works.

## Gate

`make up` on a clean box brings the stack to a working state, `make measure` regenerates the
committed evidence in `results/`, and the README explains every number. **`make verify` on a fresh
run reproduces every committed finding** — the sign of each comparison, by a fixed rule over five
repeats — and reports how far the absolute numbers moved. Numbers are environment-bound; the
conclusions are what must hold (full-stack-serving ADR 0008).

## Layout

```
docs/design.md   the reasoning per level, methodology index, non-goals, risks, cut list
adr/             decisions the stack forced
stack/           per-level config, manifests and Helm values
loadgen/         the client; records ground-truth TTFT/ITL
measure/         the harness that regenerates results/
results/         committed evidence, one directory per level
```

## Prerequisites

Inventoried 2026-10-10 — see
[`results/l0-local/host-inventory.md`](results/l0-local/host-inventory.md): **saturn**, RTX
4070 Ti SUPER (16 376 MiB, compute 8.9), driver 595.99.02 / CUDA 13.2, Docker 29.1.3 with the
NVIDIA container toolkit (GPU-in-container verified), Python 3.14.4. Model:
**Qwen2.5-1.5B-Instruct, bf16** — [ADR 0011](adr/0011-qwen2.5-1.5b-instruct-bf16.md). Absent and
needed later: `k3s` + `kubectl` (L1), `helm` (L2/L3), an IaC tool (L5).

`make doctor` prints what the current host has. On the Linux GPU host, `sudo make inventory`
rewrites that report (`PROBE_IMAGE=<local image>` adds the GPU-in-container check). It is
regenerated there, never hand-edited.

## Constraints worth knowing before you start

- **The inference node cannot be AWS Fargate.** Fargate does not support GPU workloads. The
  [Fargate FAQ](https://aws.amazon.com/fargate/faqs/) says: "Use Amazon EC2 for GPU workloads,
  which are not supported on AWS Fargate today." (read 2026-10-09). Fargate may host CPU-side
  pieces only.
- **L5's second node is assumed, not confirmed.**
- **Gateway API Inference Extension's GA status is unresolved** — its README says it is "building
  towards a GA release" while its module page says it is GA'd. Re-check before L2.
- **Cloud GPU time is metered.** Any L5 instance is ephemeral: bring up, measure, tear down,
  record the cost.

## License

Apache-2.0, see [`LICENSE`](LICENSE). `tools/host-inventory.sh` is vendored from the sibling
[`ebpf-inference-observability`](https://github.com/epequeno/ebpf-inference-observability)
project, which is Apache-2.0 as well.
