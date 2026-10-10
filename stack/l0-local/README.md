# L0 — container + vLLM

What lives here: the container definition of the serving engine, and nothing else. The model
and its flags are the only level-specific decisions (ADRs 0011 and 0012); the shared client is
[`loadgen/`](../../loadgen/README.md) and the experiment is
[`measure/`](../../measure/README.md). Neither knows which engine or model is behind the
endpoint — that is what makes the levels comparable.

## Bring-up (Linux GPU host — saturn)

```
make up LEVEL=l0-local         # docker compose up; prints the resolved image digest
make validate-instrument       # ADR 0007: validate loadgen vs the mock → results/instrument/
make measure LEVEL=l0-local    # the 1/4/16/64 matrix → results/l0-local/
make down LEVEL=l0-local
```

First `up` downloads ~2.9 GiB of weights (ADR 0011's model, at the pinned revision) into the
`hf-cache` volume; they survive `down`. The server is ready when
`docker logs l0-vllm` shows the OpenAI API startup line — model load is seconds on this
hardware, but the first image pull is large.

## Flags and why

| flag | value | why |
|---|---|---|
| `--model` | `Qwen/Qwen2.5-1.5B-Instruct` | full-stack-serving ADR 0011: smallest model that keeps the behaviours under test visible; two replicas fit for L2/L3. |
| `--revision` | `989aa…0306` (pinned) | the model is an interchangeable, pinned parameter; a fresh clone must run the same weights. |
| `--dtype` | `bfloat16` | ADR 0011: weights as published, no conversion. |
| `--max-model-len` | `4096` | fixture prompts are ≲1.5K tokens + 128 out (the fixtures README's bound); 32K would only preallocate KV nobody uses. Bounding it keeps startup short and memory predictable. |
| `--gpu-memory-utilization` | `0.90` | one replica owns the GPU at L0. L2/L3 split this for two replicas — that split is their ADR, using ADR 0011's fit table. |
| prefix caching | not flagged | default-on in the pinned vLLM; the CLI flag has churned between versions, so status is confirmed from the server log at bring-up, and L0's fixture check reads the hit counters (fixtures README, "Not verified"). |

## What bring-up must confirm (AGENTS.md, "deferred to L0")

- **The per-chunk `usage` request option** (ADR 0005). The working assumption is
  `include_continuous_usage`, wired via `make measure`'s `CONTINUOUS_USAGE_BODY`. A wrong name
  makes the with-continual-usage overhead run fail loudly (the server rejects the request) —
  it can never fail silently. If the pinned version names it differently, fix the Makefile
  variable here and re-run.
- **Image digest**: printed by `make up`, recorded by `measure_l0` in
  `results/l0-local/env.md`.
- **CUDA graphs**: recorded from the server log into env.md (enabled/unknown, never asserted).

Swapping the model or engine edits this directory only.
