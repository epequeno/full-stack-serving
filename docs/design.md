# Design — `full-stack-serving`

Status: **L0, L1 and L4 measured** (L0 2026-10-10 `results/l0-local/`; L1 2026-10-10
`results/l1-k8s/` — parity, nothing claimable; L4 2026-10-11 `results/l4-otel/` — no
detectable overhead, the span gap grows with load, nothing claimable). L2/L3/L5 are
design only. Last revised 2026-10-11.

The [`README`](../README.md) owns the pitch, the anti-tutorial test, the level table, the gate and
the constraints. This document owns the reasoning behind each level, the non-goals, the risks and
the cut order. Decisions the stack forced are in [`adr/`](../adr/); they are named below as
*full-stack-serving ADR NNNN*.

---

## Levels

Each level is independently demonstrable and produces its own evidence. Stop anywhere; the repo
is honest about where it stopped. Every level that makes a comparison commits it as a declared
finding, judged by a fixed rule (full-stack-serving ADR 0008).

### L0 — Bare metal to container

**What:** vLLM (full-stack-serving ADR 0012) in a container on the Linux box, one open-weights model,
OpenAI-compatible endpoint. A load generator that records TTFT, per-event ITL and TPOT as ground
truth.

**Proves:** you can go from an HF model id to a measured endpoint without a platform.

**Evidence:** TTFT, per-event ITL, TPOT and tokens-per-second at 1, 4, 16 and 64 concurrent
requests (closed loop), committed as JSON. Model choice and quantization documented with the VRAM
justification, following full-stack-serving ADR 0002 (the smallest model that still makes the
behaviour under test visible).

**Note:** model choice was gated on the host inventory. The inventory is committed
(`results/l0-local/host-inventory.md`); the choice it produced is full-stack-serving ADR 0011:
Qwen2.5-1.5B-Instruct, bf16.

### L1 — Single-node Kubernetes

**What:** the same serving workload as a k8s Deployment/StatefulSet on `k3s` (full-stack-serving
ADR 0010), GPU visible via the device plugin, brought up by Helm or plain manifests.

**Proves:** container → orchestrator, GPU scheduling, declarative config.

**Evidence:** the L0 numbers re-measured *inside* the cluster, and an explicit **parity or
regression** verdict versus L0. The comparison is a *Kubernetes deployment vs Docker deployment*,
not "Kubernetes overhead": the container runtime, CNI and Service networking all differ. A latency
penalty from the k8s networking layer is a finding, not a failure — report it.

### L2 — Inference-aware routing

**What:** Gateway API Inference Extension (and/or llm-d) in front of the serving pool.

**Proves:** the CNCF serving primitive — inference pools, prefix-cache-aware routing.

**Evidence:** the routing decision made visible — hit rate under prefix-cache-aware routing versus
round-robin, on a workload with deliberately shared prefixes, under open-loop load
(full-stack-serving ADR 0004). This is the level that distinguishes *installing* llm-d from
*understanding* it.

**Open dependency:** routing needs at least two replicas, and the host has one GPU. How to get
them (fractional-GPU replicas, N copies of a smaller model, or the L5 node) depends on the
inventoried VRAM and is **not decided**. If the GPU cannot support it, L2 is cut rather than faked.
The GA status of the Gateway API Inference Extension is also unresolved; see the README's
constraints.

### L3 — Autoscaling

**What:** KEDA (or inference-pool autoscaling) scaling replicas on queue depth rather than CPU.

**Proves:** load-driven scaling — the thing GPU serving actually needs, since CPU metrics are
useless for a GPU-bound workload.

**Evidence:** a replica-count-vs-queue-depth trace over an open-loop load ramp, with scale-up and
scale-down latency recorded. Autoscaling without cold-start cost measured is half an experiment,
and with a small model (full-stack-serving ADR 0002) cold start is optimistic, so it is reported
with the model's size. The same single-GPU replica question as L2 applies.

### L4 — Observability and eval

**What:** OpenTelemetry `gen_ai.*` spans emitted from the stack into Arize Phoenix (or Langfuse),
fed by the same load generator.

**Proves:** that the serving stack can be observed with standard GenAI telemetry, and what that
costs and how far it can be trusted.

**Evidence:** two findings (full-stack-serving ADR 0009): **tracing overhead** (client-measured
latency with tracing on vs off) and the **span-to-client TTFT gap** (the engine's span TTFT against
the load generator's, paired per request). Plus a committed trace showing `gen_ai.*` spans for a
single request, and a note on which conventions were usable and which were still in flux —
Langfuse states the GenAI semantic conventions are *"still evolving"*, so a stability assessment
is itself a contribution.

**This is the level to prioritise if time is short.** It is the least copyable from upstream.

### L5 — Multi-node and cloud provisioning

**What:** LeaderWorkerSet and/or prefill/decode disaggregation across two nodes, where the second
node is either a second LAN machine or a cloud instance (EC2 GPU — see the README's Fargate
constraint).

**Proves:** the headline llm-d capability, that disaggregation's purpose is understood, and that a
node can be provisioned and joined rather than only configured.

**Evidence:**

- TTFT/ITL and throughput, co-located versus disaggregated.
- A committed IaC definition for the second node (Terraform/Pulumi/OpenTofu), with bring-up and
  tear-down times.
- **Cost, measured.** Hourly burn and total spend for the L5 experiments, committed. This is a
  demo of judgement, not just capability: "disaggregation cost $X for the experiment and the
  numbers justified it" is the artifact.

**Hard rule:** the cloud node is ephemeral. Bring up, measure, tear down, record the cost. No
long-lived GPU instance idle over a weekend.

---

## Methodology

The measurement choices are decided in ADRs. L0 tested their assumptions, L1 ran the gate,
L4 measured the observability layer, and the instrument is twice-validated (0003's
`vllm bench serve` cross-check ran 2026-10-11) — so every ADR through 0013 is `accepted`
except where marked below, and 0004 is `superseded by 0013`:

| ADR | Decision |
|---|---|
| 0002 | Size the workload for observability, not capability (accepted) |
| 0003 | Build the load generator; cross-check it against `vllm bench serve` (accepted — cross-check ran 2026-10-11) |
| 0004 | Closed loop for L0/L1, open loop for L2/L3 (superseded by 0013) |
| 0005 | Record ITL per SSE event, report TPOT separately (accepted) |
| 0006 | Percentile floors on request count; interleaved repeats per cell (accepted) |
| 0007 | Validate the load generator against a mock server with a logged schedule (accepted — claimability bars fixed) |
| 0008 | The gate reproduces findings; absolute numbers are a soft check (accepted — gated claimable findings) |
| 0009 | L4 findings: tracing overhead and the span-to-client TTFT gap (accepted) |
| 0010 | k3s for L1 (accepted) |
| 0011 | Model: Qwen2.5-1.5B-Instruct, bf16, unquantized (accepted) |
| 0012 | Engine: vLLM (official image, tag pinned) (accepted) |
| 0013 | Run the open-loop half at L0 as a supplementary experiment, not a level (accepted — swept 2026-10-11) |
| 0014 | Commit the gate's output to results/<level>/gates/ (accepted — logs committed 2026-10-11) |

`loadgen/` and `measure/` are shared across levels by design: the same measurement applied to each
layer is what makes the levels comparable. The per-level directory layout is in
[`stack/README.md`](../stack/README.md).

---

## Explicit non-goals

- Not a benchmark of models or hardware against a leaderboard.
- Not a production deployment. No HA, no backups, no multi-tenancy, no SLA.
- Not model training or fine-tuning. A fine-tune is a *possible later addition* (Unsloth → hub →
  serve the tuned model), which would close the HF layer from the bottom, but it is not this
  design.
- Not custom kernels, custom servers, or novel inference optimisations.
- Not a from-scratch agent application.

---

## Relationship to `ebpf-inference-observability`

The sibling eBPF project needs a live serving stack to attach probes to. **L0 is that stack** and
is built first for both. The eBPF project does not need L1–L5. Run the host inventory once: it is
a shared prerequisite, and `tools/host-inventory.sh` is vendored from that project's canonical copy
so the two cannot disagree about the same machine.

---

## Risks

| Risk | Mitigation |
|---|---|
| **It becomes a tutorial.** The single greatest risk. | The anti-tutorial test (README); every level must produce a number or it doesn't count as done. |
| **`make up` works on the author's box and nowhere else.** | Prerequisites documented; reproducibility on a *clean* box is the gate, not portability. |
| **k3s + GPU + CRDs is a large surface first time out.** | Levels are ordered so each is independently useful; L0 and L1 carry most of the deploy signal. |
| **L2 and L3 have fiddly failure modes and need more than one replica.** | Timebox them; cut if the GPU cannot host replicas (see L2). L0, L1, L4 are the ones that pay. |
| **Cloud cost drift.** L5 introduces metered GPU instances that can idle. | Hard rule: ephemeral only, cost recorded per run. |
| **Tool versions move fast** (llm-d, Gateway API Inference Extension, vLLM tracing). | Pin versions at L0 and record them in every result's `env`; re-verify the Inference Extension's GA status before L2. |

---

## Cut list (if time runs short, drop in this order)

1. L5 — multi-node and cloud. Last in, first out; also the only level that costs money.
2. L3 — autoscaling. Demonstrable but the numbers are the least transferable.
3. L2 — routing. Painful to cut, since it is the llm-d hook, but L1 still proves k8s.
4. **Keep L0, L1, L4.** Container → k8s → measured observability is the spine. If only that
   ships, the project still works.
