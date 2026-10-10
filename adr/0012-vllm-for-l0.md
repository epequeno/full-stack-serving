# 0012 — vLLM is the engine

- **Status:** accepted
- **Date:** 2026-10-10

## Context

L0 needs an OpenAI-compatible streaming engine in a container; `docs/design.md` left it at
"vLLM (or llama.cpp)". The engine is a fixed input to every experiment, so the choice is judged
on what the levels must *observe*, not on throughput leadership (a benchmark is a non-goal):

- **L4** needs `gen_ai.*` spans from the engine; **L2/L3** need prefix-cache and queue-depth
  signals plus working automatic prefix caching.
- **L2** targets an llm-d-style inference pool, and vLLM is that ecosystem's reference backend —
  measuring it means measuring the real thing, not a portability exception.
- **ADR 0011** serves bf16 safetensors, unquantized; the engine must run them without a
  conversion step.

Checked 2026-10-10: vLLM v0.23.0 is the current release (vllm-project/vllm releases page); the
official `vllm/vllm-openai:v0.23.0` image exists on Docker Hub (linux/amd64); vllm.ai states the
stable line requires Python 3.10+ and is compatible with CUDA 13.x — saturn's driver reports
CUDA 13.2 (`results/l0-local/host-inventory.md`), so the host is not the constraint.

## Decision

Serve with **vLLM**, from the official `vllm/vllm-openai` image, tag pinned in
`stack/l0-local/docker-compose.yaml`; the resolved digest is recorded in
`results/l0-local/env.md` at bring-up.

Why not the others:

- **llama.cpp / llama-server** — quantization-centric: ADR 0011's bf16 safetensors would need a
  GGUF conversion, and its metrics and OTel surface is thinner than what L2–L4 must read. It
  would be the fallback on a CPU-only box; the inventoried host is not one.
- **SGLang** — capable and also llm-d-compatible, but a second engine doubles the
  version-pinning and ADR 0007 validation surface for no observable the levels need that vLLM
  lacks.
- **TensorRT-LLM / TGI** — heavier bring-up and narrower community documentation for the
  k3s + device-plugin path L1 takes, for no observable the levels need.

The engine is a swappable parameter exactly like the model: everything behind the
OpenAI-compatible endpoint belongs to `stack/l0-local/`; `loadgen/` and `measure/` must not
know it (ADR 0002's rule, applied to the engine).

## Consequences

- Version bumps are explicit: a newer engine feature means editing the pinned tag here and in
  the compose file — and re-running `make validate-instrument`, which ADR 0007 requires
  whenever the serving stack's versions change.
- The per-chunk `usage` option (ADR 0005) is a vLLM detail: its exact request-option name is
  confirmed against the pinned version at bring-up (`stack/l0-local/README.md`).
- Claims stay engine-relative: nothing here says vLLM is fastest, only that it is the
  observable engine for these levels. The L0 numbers describe *this* engine at *this* version —
  which is why both are pinned and recorded in every result's `env`.
