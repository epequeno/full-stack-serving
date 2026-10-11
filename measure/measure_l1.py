"""Run the L1 measurement matrix on k3s and regenerate results/l1-k8s/ (ADR 0008).

Same protocol as L0 (measure.measure_l0): same fixture, seed, concurrencies, interleaved
cell order, discarded counted warmup, continuous-usage overhead pair and prefix-cache
fixture check — applying the *same* experiment is what makes "L1 vs L0" a comparable
statement. Differences: the endpoint is the NodePort Service (kube-proxy → CNI → pod, with
the client on the host — part of what L1 measures, ADR 0010), the pod is rolled fresh
before the matrix so every run starts from a cold prefix cache like L0's container did,
and the env block records the cluster.

L1 makes a comparison, so it also writes findings.json (ADR 0008): for each cell and
metric, the per-run medians of this run (l1) against the committed L0 per-run medians
(results/l0-local/runs/) get the fixed verdict rule — never judged:

    l1>l0            if min(l1 values) > max(l0 values)
    l1<l0            if max(l1 values) < min(l0 values)
    indistinguishable otherwise

Wording (ADR 0010): "Kubernetes deployment vs Docker deployment", never "Kubernetes
overhead" — the runtime, CNI and Service networking all differ. A latency regression is a
finding, not a failure. The soft-check tolerance is declared here, at measure time,
derived from L0's committed across-run spread — before any fresh-run comparison (ADR 0008).

Writes results/l1-k8s/l1.json, findings.json, env.md, runs/*. Overwrites its own outputs
only. `measure.verify_l1` re-runs this into scratch and gates on the verdicts.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import shutil
import statistics
import sys
import time
from pathlib import Path

import aiohttp

from measure.measure_l0 import (REPO, cell_block, cuda_driver_max, gpu_snapshot,
                                http_get, parse_metrics, pool_run, run_loadgen, sh)

KUBECTL = ["sudo", "kubectl"]

# verdict metric → (summary block, key) in a loadgen run file
METRICS = (
    ("ttft_p50_ms", ("ttft_ms", "p50")),
    ("itl_p50_ms", ("itl_event_ms", "p50")),
    ("tpot_p50_ms", ("tpot_ms", "p50")),
)


def kubectl(*args: str, timeout: int = 60) -> str:
    return sh(KUBECTL + list(args), timeout=timeout)


def verdict_rule(a: list[float], b: list[float]) -> str:
    """ADR 0008 sign rule over the two per-run-median samples (whatever R each side ran)."""
    if not a or not b:
        return "indistinguishable"
    if min(a) > max(b):
        return "l1>l0"
    if max(a) < min(b):
        return "l1<l0"
    return "indistinguishable"


def l0_per_run_values(baseline_runs: Path, concs: list[int]
                     ) -> dict[tuple[str, int], list[float]]:
    """The committed L0 per-run medians, per (metric, cell) — the baseline of every verdict.

    Reads every baseline run file that exists (r=0, 1, …); the baseline's R is whatever
    L0 committed (5), NOT this run's R (10) — assuming they matched cost a full R=10
    matrix on 2026-10-10 (baseline c1_r5.json does not exist).
    """
    out: dict[tuple[str, int], list[float]] = {}
    for c in concs:
        for metric, (block, key) in METRICS:
            vals = []
            r = 0
            while (f := baseline_runs / f"c{c}_r{r}.json").exists():
                d = json.loads(f.read_text(encoding="utf-8"))
                v = d["summary"][block][key]
                if not isinstance(v, (int, float)):
                    raise SystemExit(
                        f"measure-l1: baseline {metric} is n/a at c={c} r={r} "
                        f"({v}); the verdict rule needs per-run medians")
                vals.append(float(v))
                r += 1
            if not vals:
                raise SystemExit(f"measure-l1: no baseline runs for c={c} under {baseline_runs}")
            out[(metric, c)] = vals
    return out


def per_run_metric_values(pooled_runs: list[dict]) -> dict[str, list[float]]:
    """This run's per-run medians, per metric (percentile over each run's raw values)."""
    from loadgen.client import percentile  # local import: loadgen is the instrument

    out = {}
    for metric, src in (("ttft_p50_ms", "ttfts"), ("itl_p50_ms", "itls"),
                        ("tpot_p50_ms", "tpots")):
        out[metric] = [percentile(p[src], 50) for p in pooled_runs if p[src]]
    return out


def k8s_snapshot() -> dict:
    node = kubectl("get", "nodes", "-o", "jsonpath={.items[0].metadata.name}").strip() or None
    runtime = ""
    if node:
        runtime = kubectl("get", "node", node,
                          "-o", "jsonpath={.status.nodeInfo.containerRuntimeVersion}").strip()
    pod = kubectl("get", "pods", "-l", "app=l1-vllm", "-A",
                  "-o", "jsonpath={.items[0].metadata.name}").strip() or None
    image_id = ""
    if pod:
        image_id = kubectl("get", "pod", pod, "-o",
                           "jsonpath={.status.containerStatuses[0].imageID}").strip()
    plugin = kubectl("-n", "kube-system", "get", "ds", "nvidia-device-plugin-daemonset",
                      "-o", "jsonpath={.spec.template.spec.containers[0].image}").strip()
    gpu_alloc = ""
    if node:
        gpu_alloc = kubectl("get", "node", node,
                             "-o", "jsonpath={.status.allocatable.nvidia\\.com/gpu}").strip()
    return {
        "distribution": sh(["sudo", "k3s", "--version"]).splitlines()[0]
                       or "k3s (version unavailable)",
        "node": node,
        "container_runtime": runtime or None,
        "runtime_class": "nvidia (k3s default stays runc; set per-pod — stack/l1-k8s/vllm.yaml)",
        "device_plugin": plugin or "n/a (not deployed?)",
        "gpu_allocatable": gpu_alloc or "0",
        "pod": pod,
        "image_id": image_id or None,
        "network_path": "NodePort 30080: host client → kube-proxy → CNI → pod (ADR 0010: "
                        "this path is part of what L1 measures)",
        "client_location": "host (saturn), not in-cluster",
    }


def load_bars(validation_json: Path, concs: list[int]) -> dict[int, float]:
    """ADR 0007 claimability bar per cell: 10 x the committed instrument p99 error
    (the plain ``closed c=N`` runs in ``results/instrument/validation.json``)."""
    d = json.loads(validation_json.read_text(encoding="utf-8"))
    bars: dict[int, float] = {}
    for r in d.get("runs", []):
        p99 = (r.get("client_minus_mock_write") or {}).get("p99_ms")
        if not isinstance(p99, (int, float)):
            continue
        for c in concs:
            if r.get("label") == f"closed c={c}":
                bars[c] = round(10 * float(p99), 1)
    missing = [c for c in concs if c not in bars]
    if missing:
        raise SystemExit(f"measure-l1: no committed instrument error for cells {missing} "
                          f"in {validation_json} — re-run `make validate-instrument` first.")
    return bars


def build_findings(l1_pooled: dict[int, list[dict]], baseline: dict[tuple[str, int], list[float]],
                   concs: list[int], bars: dict[int, float]) -> tuple[list[dict], dict]:
    findings = []
    tolerance: dict[str, float] = {}
    for c in concs:
        per_run = per_run_metric_values(l1_pooled[c])
        for metric, _ in METRICS:
            l1_vals, l0_vals = per_run[metric], baseline[(metric, c)]
            v = verdict_rule(l1_vals, l0_vals)
            l1_med = statistics.median(l1_vals) if l1_vals else None
            l0_med = statistics.median(l0_vals) if l0_vals else None
            margin = (round(min(abs(a - b) for a in l1_vals for b in l0_vals), 3)
                      if l1_vals and l0_vals else None)
            effect = (round(abs(l1_med - l0_med), 3)
                      if l1_med is not None and l0_med is not None else None)
            bar = bars.get(c)
            # ADR 0007 (fixed before L1): a between-level finding is claimable only if
            # |effect| >= 10x the cell's committed instrument p99 error. The mechanical
            # ADR 0008 sign rule is still computed and recorded for every cell x metric —
            # at a sub-bar margin it flips between fresh runs by design, so it is recorded
            # as information and never re-claimed as a finding.
            claimable = bool(effect is not None and bar is not None and effect >= bar)
            findings.append({
                "metric": metric, "cell": c,
                "conditions": {"a": "l1-k8s (this run)", "b": "l0-local (committed runs/)"},
                "l1_values": [round(x, 3) for x in l1_vals],
                "l0_values": [round(x, 3) for x in l0_vals],
                "verdict": v,
                "l1_median": round(l1_med, 3) if l1_med is not None else None,
                "l0_median": round(l0_med, 3) if l0_med is not None else None,
                "effect_size_ratio": (round(l1_med / l0_med, 3)
                                      if l1_med and l0_med else None),
                "effect_ms": effect,
                "margin_ms": margin,
                "bar_ms": bar,
                "claimable": claimable,
                "finding": (f"{v} — Kubernetes deployment vs Docker deployment (ADR 0010)"
                            if claimable else
                            "at the instrument's resolution: NOT claimable in either "
                            f"direction (ADR 0007: |effect| {effect} ms < bar {bar} ms); "
                            "the mechanical ADR 0008 verdict is recorded for the trace "
                            "and is expected to flip between fresh runs"),
                "wording": ("Kubernetes deployment vs Docker deployment (ADR 0010) — "
                            "never 'Kubernetes overhead'"),
            })
            # soft-check tolerance, declared at measure time from L0's committed spread
            tolerance[f"c{c}/{metric}"] = round(max(l0_vals) - min(l0_vals), 3)
    soft = {
        "declared": time.strftime("%Y-%m-%dT%H:%M:%S%z") + " (at measure time, before any "
                   "fresh-run comparison — ADR 0008)",
        "derivation": "L0's committed across-run spread (max − min of its per-run "
                      "medians), per cell and metric. A fresh L1 median outside this band "
                      "is reported, and never fails the gate.",
        "tolerance_ms": tolerance,
    }
    return findings, soft


CLAIMABILITY_RULE = (
    "ADR 0007 (fixed before L1): a between-level finding is claimable only if "
    "|effect| >= 10x the cell's committed instrument p99 error "
    "(results/instrument/validation.json). Every cell x metric verdict is computed "
    "and recorded by the mechanical ADR 0008 sign rule; at a sub-bar margin it "
    "sits at the instrument's resolution and flips between fresh runs by design "
    "(2026-10-10: c=1 TTFT, 0.012 ms). The gate checks the claimable set; the "
    "sub-bar set is reported as information. Restricting claims to effects above "
    "the instrument's error is the option ADR 0007 sanctions.")


def findings_document(generated: str, findings: list[dict], soft: dict) -> dict:
    return {
        "schema": "l1-findings/1",
        "generated": generated,
        "level": "l1-k8s",
        "verdict_rule": ("l1>l0 if min(l1) > max(l0); l1<l0 if max(l1) < min(l0); "
                         "indistinguishable otherwise (ADR 0008, over ALL per-run medians "
                         "each side — this run's R and the committed L0 baseline's R, "
                         "which differ: R=10 vs R=5)"),
        "claimability_rule": CLAIMABILITY_RULE,
        "claimable_findings": [f for f in findings if f["claimable"]],
        "findings": findings,
        "tolerance_soft_check": soft,
    }


def run_level(out_dir: Path, *, endpoint: str, model: str, revision: str, fixture: str,
              concs: list[int], repeats: int, seed: int, warmup_requests: int,
              warmup_concurrency: int, overhead_concurrency: int,
              continuous_usage_body: str | None, baseline_runs: Path,
              k3s_timeout: str = "900s", drop_caches: bool = False) -> dict:
    """The whole L1 experiment. Returns the result dict (findings included). Shared by
    the measure CLI (writes results/) and verify (runs into scratch — never results/)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = out_dir / "runs"
    if runs_dir.exists():
        shutil.rmtree(runs_dir)
    runs_dir.mkdir()

    endpoint_root = endpoint.rsplit("/v1/", 1)[0]
    if http_get(f"{endpoint_root}/v1/models", timeout=10) is None:
        print(f"measure-l1: server not reachable at {endpoint_root} — `make up LEVEL=l1-k8s` "
              "first.", file=sys.stderr)
        raise SystemExit(2)

    caches = "no (not attempted)"
    if drop_caches:
        try:
            with open("/proc/sys/vm/drop_caches", "w") as f:
                f.write("1")
            caches = "yes"
        except OSError as e:
            caches = f"attempted, failed: {e}"

    # Roll the pod fresh: the matrix then starts from a cold prefix cache, exactly the
    # condition L0's committed run had (fresh container). Weights load from the hostPath.
    # Verified, not assumed: with RollingUpdate a 1-GPU single replica deadlocks (surge pod
    # Pending on the GPU, old pod never dies), `rollout status` times out, and a blind
    # continuation measures the old warm pod — caught live 2026-10-10. The deployment is
    # Recreate (stack/l1-k8s/vllm.yaml); this still proves the roll happened.
    print("measure-l1: rolling deployment l1-vllm fresh (cold prefix cache, as at L0)",
          file=sys.stderr)
    old_pod = kubectl("get", "pods", "-l", "app=l1-vllm",
                      "-o", "jsonpath={.items[0].metadata.name}").strip()
    kubectl("rollout", "restart", "deployment/l1-vllm")
    k3s_wait = int(k3s_timeout.rstrip("s")) + 120
    status_out = kubectl("rollout", "status", "deployment/l1-vllm",
                         f"--timeout={k3s_timeout}", timeout=k3s_wait)
    if "successfully rolled out" not in status_out:
        raise SystemExit("measure-l1: `kubectl rollout status` did not report success — "
                          "the pod was NOT rolled; refusing to measure a warm pod.")
    new_pod = kubectl("get", "pods", "-l", "app=l1-vllm",
                      "-o", "jsonpath={.items[0].metadata.name}").strip()
    if not new_pod or new_pod == old_pod:
        raise SystemExit(f"measure-l1: pod {new_pod!r} is the pre-roll pod ({old_pod!r}) — "
                         "the rollout did not recreate it; refusing to measure a warm pod.")
    print(f"measure-l1: rolled {old_pod} → {new_pod}", file=sys.stderr)

    server_version = None
    v = http_get(f"{endpoint_root}/version")
    if v:
        try:
            server_version = json.loads(v).get("version")
        except json.JSONDecodeError:
            server_version = None

    k8s = k8s_snapshot()
    env = {
        "host": platform.platform(),
        "kernel": os.uname().release,
        "python": platform.python_version(),
        "aiohttp": aiohttp.__version__,
        "gpu": gpu_snapshot(),
        "cuda_max_supported": cuda_driver_max(),
        "server_version": server_version,
        "server_endpoint": endpoint,
        "model": model,
        "model_revision": revision,
        "quantization": "none — bf16 weights as published (full-stack-serving ADR 0011)",
        "image": "vllm/vllm-openai:v0.23.0",
        "image_digest": k8s["image_id"],
        "orchestration": k8s,
        "cuda_graphs": ("enabled (server log mentions CUDA graphs)"
                        if "cuda graph" in kubectl("logs", "deployment/l1-vllm",
                                                   "--tail=2000", timeout=60).lower()
                        else "unknown (no CUDA-graph line found in server log)"),
        "caches_dropped": caches,
        "fixture": fixture,
        "seed": seed,
    }

    # --- fixture check FIRST: cold cache (the L0 lesson — run last it reads 98.7%) ----
    m0 = parse_metrics(http_get(f"{endpoint_root}/metrics"))
    run, rc = run_loadgen(endpoint=endpoint, model=model, fixture=fixture,
                          concurrency=1, seed=seed, out=runs_dir / "prefixcheck.json",
                          extra_body=None)
    m1 = parse_metrics(http_get(f"{endpoint_root}/metrics"))
    delta = {k: m1.get(k, 0) - m0.get(k, 0) for k in set(m0) | set(m1)}
    hits = {k: v for k, v in delta.items() if "prefix_cache" in k}

    def pick(fragment: str) -> float | None:
        """The matching counter, preferring the node-local one (the ``external_*``
        counters are always 0 here and must not shadow the real ones)."""
        cands = [v for k, v in hits.items() if fragment in k and "external" not in k]
        return cands[0] if cands else None

    queries, hit_total = pick("queries_total"), pick("hits_total")
    rate = (round(100 * hit_total / queries, 2)
            if queries and hit_total is not None else None) if hits else None
    fixture_check = {
        "note": "one c=1 pass over the distinct fixture, run FIRST on the fresh pod — "
                "cold prefix cache. Counter deltas must stay near zero (fixtures "
                "README); a warm cache reads replay self-hits (L0 2026-10-10: 98.7%).",
        "requests": (run["summary"]["requests_ok"] if run else f"run failed (exit {rc})"),
        "hit_rate_pct": rate,
        "counter_deltas": hits or "no prefix_cache metrics found (names vary by version)",
    }

    # --- warmup: discarded, counted (ADR 0006) -----------------------------------------
    warm_run, rc = run_loadgen(endpoint=endpoint, model=model, fixture=fixture,
                               concurrency=warmup_concurrency, seed=seed,
                               out=runs_dir / "warmup.json", extra_body=None,
                               requests=warmup_requests)
    warmup = ({"requests": warm_run["summary"]["requests_ok"], "wall_s": warm_run["wall_s"],
               "discarded": True} if warm_run
              else {"requests": 0, "discarded": True, "error": f"client exit {rc}"})

    # --- the matrix: seeded, interleaved cell order — same seed as L0 -------------------
    cells = [(c, r) for c in concs for r in range(repeats)]
    random.Random(seed).shuffle(cells)

    pooled: dict[int, list[dict]] = {c: [] for c in concs}
    run_index: list[dict] = []
    prev_counter_total: float | None = None
    server_restarted = False
    failures = 0
    for i, (c, r) in enumerate(cells):
        pre_counters = parse_metrics(http_get(f"{endpoint_root}/metrics"))
        out = runs_dir / f"c{c}_r{r}.json"
        run, rc = run_loadgen(endpoint=endpoint, model=model, fixture=fixture,
                              concurrency=c, seed=seed, out=out, extra_body=None)
        post_counters = parse_metrics(http_get(f"{endpoint_root}/metrics"))
        entry = {"cell": [c, r], "file": f"runs/c{c}_r{r}.json", "client_exit": rc,
                 "gpu_pre": gpu_snapshot(), "gpu_post": gpu_snapshot(),
                 "counters_pre": pre_counters, "counters_post": post_counters}
        if run is None:
            entry["error"] = "client produced no output"
            failures += 1
        else:
            p = pool_run(run)
            pooled[c].append(p)
            entry.update({"n_ok": p["n_ok"], "n_error": p["n_error"], "wall_s": p["wall_s"]})
            total = sum(v for k, v in post_counters.items() if "num_requests_started" in k)
            if prev_counter_total is not None and total < prev_counter_total:
                server_restarted = True
            prev_counter_total = total
        run_index.append(entry)
        print(f"[{i + 1}/{len(cells)}] c={c} r={r}: "
              + (f"ok={entry.get('n_ok')} err={entry.get('n_error')} wall={entry.get('wall_s')}s"
                 if run else "FAILED"), file=sys.stderr)

    cells_out = {str(c): cell_block(pooled[c]) for c in concs if pooled[c]}

    # --- harness-overhead pair (ADR 0005) ----------------------------------------------
    overhead = {"note": "one extra pair of runs, same cell, request option on vs off"}
    pair = {}
    for label, body in (("with_continuous_usage", continuous_usage_body), ("without", None)):
        run, rc = run_loadgen(endpoint=endpoint, model=model, fixture=fixture,
                              concurrency=overhead_concurrency, seed=seed,
                              out=runs_dir / f"overhead_{label}.json", extra_body=body)
        pair[label] = ({"summary": run["summary"], "wall_s": run["wall_s"]} if run
                       else {"error": f"client exit {rc} or no output"})
        if "with_continuous_usage" not in label:
            continue
        if run is None:
            overhead["continuous_usage_honored"] = None
        else:
            evs = [e for rq in run["requests"] for e in rq["events"]]
            nulls = sum(1 for e in evs if e.get("tokens") is None)
            overhead["continuous_usage_honored"] = bool(evs) and nulls != len(evs)
            if evs and nulls == len(evs):
                overhead["note"] += (" — WARNING: every event is tokens == null; the server "
                                     "ignored the request option and the pair measures the "
                                     "same request twice (ADR 0005).")
    if all(isinstance(v, dict) and "summary" in v for v in pair.values()):
        a, b = pair["with_continuous_usage"]["summary"], pair["without"]["summary"]
        overhead["ttft_p50_delta_ms"] = round(
            a["ttft_ms"]["p50"] - b["ttft_ms"]["p50"], 3) \
            if isinstance(a["ttft_ms"]["p50"], float) and isinstance(b["ttft_ms"]["p50"], float) \
            else None
        overhead["itl_p50_delta_ms"] = round(
            a["itl_event_ms"]["p50"] - b["itl_event_ms"]["p50"], 3) \
            if isinstance(a["itl_event_ms"]["p50"], float) \
            and isinstance(b["itl_event_ms"]["p50"], float) else None
    overhead["pair"] = pair

    # --- findings vs the committed L0 baseline (ADR 0008, restricted by ADR 0007) --------
    baseline = l0_per_run_values(baseline_runs, concs)
    bars = load_bars(REPO / "results/instrument/validation.json", concs)
    findings, soft = build_findings(pooled, baseline, concs, bars)
    headline = {
        "verdicts": sorted({f["verdict"] for f in findings}),
        "claimable": sorted({(f["metric"], f["cell"]) for f in findings if f["claimable"]}),
        "reading": ("'indistinguishable'/'l1<l0'/'l1>l0' are the mechanical ADR 0008 "
                    "sign rule over ALL per-run medians each side (never 'equal'); "
                    "ADR 0007 restricts what may be CLAIMED to |effect| >= the cell's "
                    "bar (10x instrument p99 error). Sub-bar verdicts are recorded, "
                    "flip between fresh runs by design, and are gated as information "
                    "only — see findings[].finding."),
    }

    result = {
        "schema": "l1-results/1",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "level": "l1-k8s",
        "protocol": {
            "fixture": fixture, "seed": seed, "concurrencies": concs, "repeats": repeats,
            "warmup_requests": warmup_requests,
            "warmup_concurrency": warmup_concurrency,
            "overhead_concurrency": overhead_concurrency,
            "continuous_usage_body": continuous_usage_body,
        },
        "baseline": {"level": "l0-local", "runs": str(baseline_runs)},
        "env": env,
        "warmup": warmup,
        "cell_order": [list(c) for c in cells],
        "server_restarted_between_repeats": server_restarted,
        "cells": cells_out,
        "overhead": overhead,
        "fixture_check": fixture_check,
        "runs": run_index,
        "findings": findings,
        "findings_headline": headline,
        "tolerance_soft_check": soft,
        "failures": failures,
    }
    (out_dir / "l1.json").write_text(json.dumps(result, indent=1) + "\n")
    (out_dir / "findings.json").write_text(
        json.dumps(findings_document(result["generated"], findings, soft), indent=1) + "\n")

    g = env["gpu"]
    env_md = f"""# L1 — environment

Generated {result['generated']} by `python -m measure.measure_l1` (`make measure LEVEL=l1-k8s`).

| field | value |
|---|---|
| host | {env['host']} |
| kernel | {env['kernel']} |
| GPU | {g.get('name', 'unavailable')} |
| driver | {g.get('driver_version', 'unavailable')} · CUDA (max supported by driver) {env['cuda_max_supported']} |
| cluster | {k8s['distribution']} · node {k8s['node']} · {k8s['container_runtime']} |
| device plugin | {k8s['device_plugin']} · allocatable nvidia.com/gpu={k8s['gpu_allocatable']} |
| runtime class | {k8s['runtime_class']} |
| network path | {k8s['network_path']} |
| server | vLLM {env['server_version']} |
| image | {env['image']} · {env['image_digest']} |
| model | {env['model']} @ {env['model_revision']} |
| quantization | {env['quantization']} |
| CUDA graphs | {env['cuda_graphs']} |
| page caches | {env['caches_dropped']} |
| client | python {env['python']}, aiohttp {env['aiohttp']}, loadgen (loadgen/README.md) |
| fixture | {env['fixture']} · seed {env['seed']} |

Raw runs in [runs/](runs/); the assembled matrix and findings in [l1.json](l1.json) and
[findings.json](findings.json).
"""
    (out_dir / "env.md").write_text(env_md)

    print(f"measure-l1: {len(cells) - failures}/{len(cells)} runs ok → {out_dir / 'l1.json'}")
    for f in findings:
        print(f"  c={f['cell']:3d} {f['metric']:12s} verdict={f['verdict']:16s} "
              f"ratio={f['effect_size_ratio']} margin={f['margin_ms']}ms")
    if server_restarted:
        print("WARNING: the server restarted between repeats.", file=sys.stderr)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(prog="measure-l1", description=__doc__.splitlines()[0])
    ap.add_argument("--endpoint", default="http://localhost:30080/v1/chat/completions")
    ap.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--revision", default="989aa7980e4cf806f80c7fef2b1adb7bc71aa306")
    ap.add_argument("--fixture", default="loadgen/fixtures/distinct.jsonl")
    ap.add_argument("--out-dir", default="results/l1-k8s")
    ap.add_argument("--concurrencies", default="1,4,16,64")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--warmup-requests", type=int, default=32)
    ap.add_argument("--warmup-concurrency", type=int, default=4)
    ap.add_argument("--overhead-concurrency", type=int, default=16)
    ap.add_argument("--continuous-usage-body",
                    default='{"stream_options": {"include_usage": true, "continuous_usage_stats": true}}',
                    help="the pinned vLLM's per-chunk usage request option (ADR 0005), "
                         "confirmed against v0.23.0")
    ap.add_argument("--baseline-runs", default="results/l0-local/runs",
                    help="the committed L0 per-run files findings are computed against")
    ap.add_argument("--k3s-timeout", default="900s")
    ap.add_argument("--drop-caches", action="store_true")
    ap.add_argument("--findings-only", action="store_true",
                    help="recompute findings.json (and the findings blocks of l1.json) "
                         "from the committed runs/ + baseline + validation bars — no "
                         "server, no cluster. The raw runs are never touched.")
    cfg = ap.parse_args()

    out_dir = (REPO / cfg.out_dir).resolve()
    baseline_runs = (REPO / cfg.baseline_runs).resolve()
    concs = [int(x) for x in cfg.concurrencies.split(",")]

    if cfg.findings_only:
        l1_path = out_dir / "l1.json"
        if not l1_path.exists():
            raise SystemExit(f"measure-l1: {l1_path} not found — run the matrix first.")
        prev = json.loads(l1_path.read_text(encoding="utf-8"))
        concs = prev["protocol"]["concurrencies"]
        pooled: dict[int, list[dict]] = {}
        for c in concs:
            files = sorted((out_dir / "runs").glob(f"c{c}_r*.json"))
            if not files:
                raise SystemExit(f"measure-l1: no run files for c={c} under {out_dir / 'runs'}")
            pooled[c] = [pool_run(json.loads(f.read_text(encoding="utf-8"))) for f in files]
        baseline = l0_per_run_values(baseline_runs, concs)
        bars = load_bars(REPO / "results/instrument/validation.json", concs)
        findings, soft = build_findings(pooled, baseline, concs, bars)
        prev["findings"] = findings
        prev["findings_headline"] = {
            "verdicts": sorted({f["verdict"] for f in findings}),
            "claimable": sorted({(f["metric"], f["cell"]) for f in findings if f["claimable"]}),
            "reading": ("'indistinguishable'/'l1<l0'/'l1>l0' are the mechanical ADR 0008 "
                        "sign rule over ALL per-run medians each side (never 'equal'); "
                        "ADR 0007 restricts what may be CLAIMED to |effect| >= the "
                        "cell's bar (10x instrument p99 error). Sub-bar verdicts are "
                        "recorded, flip between fresh runs by design, and are gated as "
                        "information only — see findings[].finding."),
        }
        prev["tolerance_soft_check"] = soft
        l1_path.write_text(json.dumps(prev, indent=1) + "\n")
        generated = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        (out_dir / "findings.json").write_text(
            json.dumps(findings_document(generated, findings, soft), indent=1) + "\n")
        print(f"measure-l1: findings recomputed from committed runs → {out_dir / 'findings.json'}")
        for f in findings:
            print(f"  c={f['cell']:3d} {f['metric']:12s} verdict={f['verdict']:16s} "
                  f"claimable={f['claimable']} effect={f['effect_ms']}ms bar={f['bar_ms']}ms")
        return 0

    result = run_level(
        out_dir, endpoint=cfg.endpoint, model=cfg.model, revision=cfg.revision,
        fixture=cfg.fixture, concs=concs, repeats=cfg.repeats, seed=cfg.seed,
        warmup_requests=cfg.warmup_requests, warmup_concurrency=cfg.warmup_concurrency,
        overhead_concurrency=cfg.overhead_concurrency,
        continuous_usage_body=cfg.continuous_usage_body, baseline_runs=baseline_runs,
        k3s_timeout=cfg.k3s_timeout, drop_caches=cfg.drop_caches)
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())