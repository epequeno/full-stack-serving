# L1 — the L0 workload on k3s

The same pinned image, model, revision and flags as [`../l0-local/`](../l0-local/README.md) —
the only deltas are the orchestrator and the network path. The comparison this level commits
is worded **"Kubernetes deployment vs Docker deployment"**, never "Kubernetes overhead":
L0 runs under Docker's runtime, L1 under k3s's containerd with the `nvidia` runtime, a CNI and
kube-proxy Service networking in the path (full-stack-serving ADR 0010). k3s itself:
full-stack-serving ADR 0010, installed pinned (`v1.37.1+k3s1`).

## Files

| file | what |
|---|---|
| `nvidia-device-plugin.yml` | the NVIDIA device plugin DaemonSet, vendored from `NVIDIA/k8s-device-plugin` **v0.20.1** (2026-10-10, latest stable then), pinned by tag. Local patch, recorded in the file header: `runtimeClassName: nvidia`. |
| `vllm.yaml` | the serving Deployment + a NodePort Service. Args are byte-identical to `../l0-local/docker-compose.yaml` (ADR 0012 engine, ADR 0011 model). |

## Decisions (ADR 0010 left these open — they live here, not in the ADR)

- **Device plugin, not GPU Operator.** The operator is Helm charts + CRDs + a controller
  loop; the plugin is one static DaemonSet applied by `make up`, readable as committed YAML —
  the repo's own convention (`../README.md`). One node, one GPU, no upgrade surface: the
  operator buys nothing L1 measures.
- **`runtimeClassName: nvidia` on every GPU pod.** k3s detects the `nvidia` runtime at
  startup (toolkit 1.20.1 is installed on the host) but keeps `runc` as the default runtime;
  the documented check is `grep nvidia /var/lib/rancher/k3s/agent/etc/containerd/config.toml`
  (full-stack-serving ADR 0010). The plugin pod also names the runtime: NVML needs the
  driver libs the NVIDIA runtime injects.
- **`strategy: Recreate` (not RollingUpdate).** One GPU, one replica: RollingUpdate's
  surge pod can never schedule (the GPU is held by the pod it is meant to replace), the
  old pod never dies, `rollout restart` times out silently — caught live at the first
  measure (2026-10-10: a 43-minute Pending surge pod while the "fresh" matrix ran on the
  warm old one). Recreate kills the old pod first; `measure_l1` additionally *verifies*
  the roll (pod name changed + "successfully rolled out") and refuses to measure a warm pod.
- **Weights via hostPath, not a second in-image download per pod.** `hostPath:
  /var/cache/full-stack-serving/huggingface` (`type: DirectoryOrCreate`): the first pod
  bootstraps the directory, every later pod — restart, re-run, re-measure — reuses the
  download. ADR 0010's "two image stores" note applies to the *image* (containerd re-pulls
  `vllm/vllm-openai:v0.23.0` once); weights are not pulled per-pod.
- **Load generator on the host, NodePort network path.** `loadgen/` runs on saturn and hits
  `http://localhost:30080` — kube-proxy → CNI → pod. That path is *part of what L1 measures*
  (a latency penalty from it is a finding, not a failure — ADR 0010), and it is recorded in
  every result's `env`. hostNetwork would have hidden it; an in-pod client would have moved
  the instrument, breaking comparability with L0.

## Bring-up

```
make up   LEVEL=l1-k8s     # apply plugin + vllm, wait for Ready, print NodePort
make measure LEVEL=l1-k8s # the 1/4/16/64 matrix → results/l1-k8s/ (+ findings.json)
make verify LEVEL=l1-k8s  # gate: fresh verdicts must equal committed (ADR 0008)
make down LEVEL=l1-k8s    # delete both
```

First bootstrap on a fresh node is slow by construction: containerd re-pulls the whole
~3.9 GB image (ADR 0010: "two image stores" — Docker and containerd share nothing) and the
first pod downloads ~2.9 GB of weights into the hostPath; later rollouts reuse both. The
deployment therefore sets `progressDeadlineSeconds: 3600` — the default 600 s fires mid-pull
and leaves a `Progressing=False` that a plain re-apply does not clear (it killed the first
bring-up on 2026-10-10; the fix was delete + re-apply for clean conditions). `K3S_TIMEOUT`
bounds the `rollout status` wait on top.

Prerequisite: `make down LEVEL=l0-local` first — one GPU, one owner at a time (ADR 0010).