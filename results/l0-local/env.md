# L0 — environment

Generated 2026-10-10T10:18:59-0500 by `python -m measure.measure_l0` (`make measure LEVEL=l0-local`).

| field | value |
|---|---|
| host | Linux-7.0.0-38-generic-x86_64-with-glibc2.43 |
| kernel | 7.0.0-38-generic |
| GPU | NVIDIA GeForce RTX 4070 Ti SUPER |
| driver | 595.99.02 · CUDA (max supported by driver) 13.2 |
| server | vLLM 0.23.0 |
| image | vllm/vllm-openai:v0.23.0 · digest vllm/vllm-openai@sha256:6d8429e38e3747723ca07ee1b17972e09bb9c51c4032b266f24fb1cc3b22ed8f |
| model | Qwen/Qwen2.5-1.5B-Instruct @ 989aa7980e4cf806f80c7fef2b1adb7bc71aa306 |
| quantization | none — bf16 weights as published (full-stack-serving ADR 0011) |
| CUDA graphs | enabled (server log mentions CUDA graphs) |
| page caches | no (not attempted) |
| client | python 3.13.16, aiohttp 3.14.4, loadgen (loadgen/README.md) |
| fixture | loadgen/fixtures/distinct.jsonl · seed 20261010 |

Raw runs in [runs/](runs/); the assembled matrix in [l0.json](l0.json).
