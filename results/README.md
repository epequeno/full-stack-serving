# results — the evidence

**Status: L0, L1 and L4 measured and committed; the instrument is twice-validated
(ADR 0007, ADR 0003). L2/L3/L5 are cut, not pending.**

This directory is the reason the project exists. Everything else is setup; this is the output.

## Layout

```
results/
  l0-local/        TTFT/ITL/TPOT at 1/4/16/64, closed loop; env.md; host-inventory.md
  l1-k8s/          the same matrix on k3s; findings.json (parity vs L0); gates/
  l4-otel/         two tracing arms over the identical engine; findings.json; gates/
  instrument/      the instrument's two validations: ADR 0007 vs the mock server,
                   ADR 0003 vs vllm bench serve; env.md; README.md renders both
  l2-routing/      cut (needs >1 replica; one GPU)
  l3-autoscaling/  cut (needs >1 replica; one GPU)
  l5-multinode/    cut (needs a second node + metered GPU; one GPU)
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
