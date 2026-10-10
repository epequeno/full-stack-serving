# stack — per-level configuration

One directory per level, named **exactly** as the level appears in the `Makefile` and in
`results/`. The names are load-bearing: `make measure LEVEL=l1-k8s` maps to `stack/l1-k8s/` and
writes to `results/l1-k8s/`.

| Directory | Level | What belongs here |
|---|---|---|
| `l0-local/` | L0 | Container/compose definitions, vLLM config, model choice |
| `l1-k8s/` | L1 | Manifests or Helm values, GPU device-plugin config |
| `l2-routing/` | L2 | Gateway + inference-pool resources, routing policy |
| `l3-autoscaling/` | L3 | KEDA `ScaledObject`, the load ramp definition |
| `l4-observability/` | L4 | OTel collector config, Phoenix wiring, span assertions |
| `l5-multinode/` | L5 | IaC for the second node, LWS / disaggregation config |

Nothing here yet — no level is built. See [`../docs/design.md`](../docs/design.md) for what each
level must prove.

## Conventions

- **Committed as-is and readable.** Prefer Helm values over manifests generated in place by a
  tool, so a reviewer reads the actual object rather than a template.
- The **shared** `loadgen/` and `measure/` deliberately live outside `stack/`. A per-level fork of
  either would make the levels incomparable.
- A level's directory holds only what is *specific* to that level; anything reused is promoted.
