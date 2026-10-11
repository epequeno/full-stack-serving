# L1 — environment

Generated 2026-10-10T20:43:13-0500 by `python -m measure.measure_l1` (`make measure LEVEL=l1-k8s`).

| field | value |
|---|---|
| host | Linux-7.0.0-38-generic-x86_64-with-glibc2.43 |
| kernel | 7.0.0-38-generic |
| GPU | NVIDIA GeForce RTX 4070 Ti SUPER |
| driver | 595.99.02 · CUDA (max supported by driver) 13.2 |
| cluster | k3s version v1.37.1+k3s1 (356c0254) · node saturn · containerd://2.3.4-k3s1 |
| device plugin | nvcr.io/nvidia/k8s-device-plugin:v0.20.1 · allocatable nvidia.com/gpu=1 |
| runtime class | nvidia (k3s default stays runc; set per-pod — stack/l1-k8s/vllm.yaml) |
| network path | NodePort 30080: host client → kube-proxy → CNI → pod (ADR 0010: this path is part of what L1 measures) |
| server | vLLM 0.23.0 |
| image | vllm/vllm-openai:v0.23.0 · docker.io/vllm/vllm-openai@sha256:6d8429e38e3747723ca07ee1b17972e09bb9c51c4032b266f24fb1cc3b22ed8f |
| model | Qwen/Qwen2.5-1.5B-Instruct @ 989aa7980e4cf806f80c7fef2b1adb7bc71aa306 |
| quantization | none — bf16 weights as published (full-stack-serving ADR 0011) |
| CUDA graphs | enabled (server log mentions CUDA graphs) |
| page caches | no (not attempted) |
| client | python 3.13.16, aiohttp 3.14.4, loadgen (loadgen/README.md) |
| fixture | loadgen/fixtures/distinct.jsonl · seed 20261010 |

Raw runs in [runs/](runs/); the assembled matrix and findings in [l1.json](l1.json) and
[findings.json](findings.json).
