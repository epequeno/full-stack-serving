# results — the evidence

**Status: empty. No level has been built.**

This directory is the reason the project exists. Everything else is setup; this is the output.

## Layout

```
results/
  l0-local/        TTFT/ITL at 1/4/16/64 concurrency; env.md; host-inventory.md
  l1-k8s/          the same, in-cluster; env.md; parity.md (verdict vs L0)
  l2-routing/      cache hit rate, prefix-aware vs round-robin; env.md
  l3-autoscaling/  replica-vs-queue-depth trace, scale latency; env.md
  l4-observability/ a committed gen_ai.* trace; notes on convention stability
  l5-multinode/    co-located vs disaggregated; cost.md
  instrument/      loadgen validation vs the mock server; env.md
```

Each level's output is produced by `make measure LEVEL=<l>`. **Nothing here is hand-edited.**

## What every result must carry

- The raw numbers, as data.
- The environment: kernel, driver, CUDA, server version, model, quantization.
- A `findings.json` for any level that makes a comparison: metric, conditions, verdict, effect
  size, margin. Produced by `make measure`, checked by `make verify`.
- The run configuration: concurrency, duration, fixture, warmup.
- **Confound state**: whether page caches were dropped, whether CUDA graphs were enabled.

A number without these is not reproducible, and an unreproducible number is an anecdote.

## What must never be here

- Screenshots standing in for data.
- Anything not regenerable by `make measure`.
- Cherry-picked runs. If a run was discarded, `measure/` logs it and the discard reason.
- Secrets, endpoints, account ids, or credentials. L5 touches cloud; scrub before committing.
