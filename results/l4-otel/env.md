# L4 — environment

Generated 2026-10-11T08:36:03-0500 by `python -m measure.measure_l4` (`make measure LEVEL=l4-otel`).

| field | value |
|---|---|
| host | saturn |
| kernel | 7.0.0-38-generic |
| GPU | NVIDIA GeForce RTX 4070 Ti SUPER · driver 595.99.02 |
| server | vLLM 0.23.0 (vllm/vllm-openai:v0.23.0) |
| tracing | vLLM --otlp-traces-endpoint → otel collector (grpc 4317) → Phoenix (grpc 4317) + scratch spans.json (the pairing source) |
| phoenix | arizephoenix/phoenix:latest @ sha256:3a2e5a049f706f29c7b13bd82bcd94101bb10701874ce134ec9eb7f0299741aa |
| collector | otel/opentelemetry-collector-contrib:latest @ sha256:39923a8e431bd1f57be82411999d389fcfe40857492e4365456d97a4c1f74be6 |
| core pinning | engine cpuset 0-15, phoenix cpuset 16-23, collector cpuset 24-25 |
| model | Qwen/Qwen2.5-1.5B-Instruct @ 989aa7980e4cf806f80c7fef2b1adb7bc71aa306 |
| fixture | loadgen/fixtures/distinct.jsonl · seed 20261010 · R=5 |
| arm order | base → tracing (one GPU; the order is a recorded confound) |

Raw runs in [runs/](runs/) (per arm); the assembled matrix, findings and example trace
in [l4.json](l4.json), [findings.json](findings.json) and [trace-example.json](trace-example.json).
