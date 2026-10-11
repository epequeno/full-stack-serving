"""Run the L0 measurement matrix and regenerate results/l0-local/ (measure/README.md, ADR 0006).

The experiment: one warmup pass (discarded, counted), then every cell (concurrency × repeat)
runs the FULL fixture once, in a seeded, interleaved order so time-of-day effects spread across
cells. Percentiles are pooled per cell with ADR 0006's request-count floors; min/median/max
across the repeats are reported beside the pooled figure. GPU clocks/temperature and a few
server counters are scraped before and after each run; a decreasing request counter is how a
server restart between repeats is detected.

Also records, per the ADRs: the harness-overhead pair (one cell with and without the
continuous-usage request option, ADR 0005) and the prefix-cache check on the ``distinct``
fixture (fixtures README, "Not verified": its hit counter must stay near zero).

Writes results/l0-local/l0.json, env.md and runs/*.json. Overwrites its own outputs only;
results/l0-local/host-inventory.md is never touched. Re-runs are safe with the same seed.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import aiohttp

from loadgen.client import percentile, pct_or_na

REPO = Path(__file__).resolve().parent.parent

# Server counters worth scraping per run (name fragments; vLLM names have moved between
# versions, so anything matching a fragment is recorded and restarts are detected from
# monotonicity, not from one hard-coded name).
COUNTER_FRAGMENTS = ("num_requests_started", "prefix_cache")


def sh(cmd: list[str], timeout: int = 30) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except Exception:
        return ""


def http_get(url: str, timeout: int = 10) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode()
    except Exception:
        return None


def parse_metrics(text: str | None) -> dict[str, float]:
    """Counter values for lines whose metric name contains a watched fragment."""
    out: dict[str, float] = {}
    if not text:
        return out
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0].split("{")[0]
        if any(f in name for f in COUNTER_FRAGMENTS):
            try:
                out[name] = float(parts[-1])
            except ValueError:
                pass
    return out


def gpu_snapshot() -> dict:
    out = sh(["nvidia-smi", "--query-gpu=name,driver_version,clocks.sm,temperature.gpu,"
              "power.draw,memory.used", "--format=csv,noheader,nounits"])
    if not out:
        return {"available": False}
    p = [x.strip() for x in out.splitlines()[0].split(",")]
    if len(p) < 6:
        return {"available": False, "raw": out}
    return {"available": True, "name": p[0], "driver_version": p[1], "sm_clock_mhz": p[2],
            "temp_c": p[3], "power_w": p[4], "mem_used_mib": p[5]}


def cuda_driver_max() -> str:
    for line in sh(["nvidia-smi"]).splitlines():
        if "CUDA Version:" in line:
            return line.split("CUDA Version:")[1].strip().split()[0]
    return "unknown"


def run_loadgen(*, endpoint: str, model: str, fixture: str, concurrency: int, seed: int,
                out: Path, extra_body: str | None, requests: int | None = None,
                level: str = "l0-local"
                ) -> tuple[dict | None, int]:
    """One client pass. ``level`` is recorded in the run file's ``run`` block; each
    caller must pass its own level (L0 default), or the raw runs mislabel their
    provenance — see results/l1-k8s/README.md, "Raw-run provenance"."""
    cmd = [sys.executable, "-m", "loadgen.client", "--endpoint", endpoint, "--model", model,
           "--fixture", fixture, "--concurrency", str(concurrency), "--seed", str(seed),
           "--level", level, "--output", str(out)]
    if requests:
        cmd += ["--requests", str(requests)]
    if extra_body:
        cmd += ["--extra-body", extra_body]
    proc = subprocess.run(cmd, cwd=REPO, check=False)
    try:
        with open(out, encoding="utf-8") as f:
            return json.load(f), proc.returncode
    except (FileNotFoundError, json.JSONDecodeError):
        return None, proc.returncode


def pool_run(run: dict) -> dict:
    """Raw pooled values (per request) + per-run figures, from one client run file."""
    ok = [r for r in run["requests"] if r["status"] == "ok"]
    ttfts = [r["ttft_ms"] for r in ok if r["ttft_ms"] is not None]
    itls = [b["t_ms"] - a["t_ms"] for r in ok for a, b in zip(r["events"], r["events"][1:])]
    tpots = []
    for r in ok:
        if r.get("tokens_out") and r["tokens_out"] > 1 and r.get("e2e_ms") is not None \
                and r.get("ttft_ms") is not None:
            tpots.append((r["e2e_ms"] - r["ttft_ms"]) / (r["tokens_out"] - 1))
    toks = [r.get("tokens_out") or 0 for r in ok]
    return {
        "n_ok": len(ok), "n_error": len(run["requests"]) - len(ok),
        "ttfts": ttfts, "itls": itls, "tpots": tpots,
        "tokens_out_total": sum(toks),
        "tokens_per_s": run["summary"]["tokens_per_s"],
        "wall_s": run["wall_s"],
    }


def cell_block(pooled_runs: list[dict]) -> dict:
    """ADR 0006 assembly: pooled percentiles (floored) + min/median/max across repeats."""
    n = sum(p["n_ok"] for p in pooled_runs)
    errs = sum(p["n_error"] for p in pooled_runs)
    ttfts = [v for p in pooled_runs for v in p["ttfts"]]
    itls = [v for p in pooled_runs for v in p["itls"]]
    tpots = [v for p in pooled_runs for v in p["tpots"]]

    def runs_of(key):
        return [p[key] for p in pooled_runs]

    def mmm(values):
        return {"min": round(min(values), 3), "median": round(statistics.median(values), 3),
                "max": round(max(values), 3)} if values else None

    return {
        "requests_ok": n,
        "requests_error": errs,
        "pooled": {
            "ttft_ms": {"p50": pct_or_na(ttfts, 50, n), "p99": pct_or_na(ttfts, 99, n)},
            "itl_event_ms": {"p50": pct_or_na(itls, 50, n), "p99": pct_or_na(itls, 99, n)},
            "tpot_ms": {"p50": pct_or_na(tpots, 50, n), "p99": pct_or_na(tpots, 99, n)},
        },
        "across_repeats": {
            "ttft_p50_ms": mmm([percentile(p["ttfts"], 50) for p in pooled_runs if p["ttfts"]]),
            "itl_p50_ms": mmm([percentile(p["itls"], 50) for p in pooled_runs if p["itls"]]),
            "tpot_p50_ms": mmm([percentile(p["tpots"], 50) for p in pooled_runs if p["tpots"]]),
            "tokens_per_s": mmm(runs_of("tokens_per_s")),
            "wall_s": mmm(runs_of("wall_s")),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="measure-l0", description=__doc__.splitlines()[0])
    ap.add_argument("--endpoint", default="http://localhost:8000/v1/chat/completions")
    ap.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--revision", default="989aa7980e4cf806f80c7fef2b1adb7bc71aa306")
    ap.add_argument("--fixture", default="loadgen/fixtures/distinct.jsonl")
    ap.add_argument("--out-dir", default="results/l0-local")
    ap.add_argument("--concurrencies", default="1,4,16,64")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--warmup-requests", type=int, default=32)
    ap.add_argument("--warmup-concurrency", type=int, default=4)
    ap.add_argument("--overhead-concurrency", type=int, default=16)
    ap.add_argument("--continuous-usage-body",
                    default='{"stream_options": {"include_usage": true, "continuous_usage_stats": true}}',
                    help="the pinned vLLM's per-chunk usage request option (ADR 0005); "
                         "confirmed against v0.23.0's served OpenAPI: "
                         "stream_options.continuous_usage_stats, nested — unknown top-level "
                         "fields are ignored, not rejected")
    ap.add_argument("--drop-caches", action="store_true",
                    help="attempt to drop page caches before the matrix (needs root)")
    cfg = ap.parse_args()

    out_dir = (REPO / cfg.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = out_dir / "runs"
    if runs_dir.exists():
        shutil.rmtree(runs_dir)
    runs_dir.mkdir()

    endpoint_root = cfg.endpoint.rsplit("/v1/", 1)[0]
    server_version = None
    v = http_get(f"{endpoint_root}/version")
    if v:
        try:
            server_version = json.loads(v).get("version")
        except json.JSONDecodeError:
            server_version = None
    if http_get(f"{endpoint_root}/v1/models", timeout=5) is None:
        print(f"measure-l0: server not reachable at {endpoint_root} — `make up LEVEL=l0-local` "
              "first.", file=sys.stderr)
        return 2

    caches = "no (not attempted)"
    if cfg.drop_caches:
        try:
            with open("/proc/sys/vm/drop_caches", "w") as f:
                f.write("1")
            caches = "yes"
        except OSError as e:
            caches = f"attempted, failed: {e}"

    # RepoDigests is an image-level field: `docker inspect` on the container has none
    # (that lookup always failed and recorded "unknown"), so resolve the digest from the
    # image the container runs, via its ref.
    image_ref = sh(["docker", "inspect", "--format", "{{.Config.Image}}", "l0-vllm"])
    image_digest = (sh(["docker", "image", "inspect", image_ref, "--format",
                        "{{index .RepoDigests 0}}"]) if image_ref else "") \
        or "unknown (image digest unavailable)"

    env = {
        "host": platform.platform(),
        "kernel": os.uname().release,
        "python": platform.python_version(),
        "aiohttp": aiohttp.__version__,
        "gpu": gpu_snapshot(),
        "cuda_max_supported": cuda_driver_max(),
        "server_version": server_version,
        "server_endpoint": cfg.endpoint,
        "model": cfg.model,
        "model_revision": cfg.revision,
        "quantization": "none — bf16 weights as published (full-stack-serving ADR 0011)",
        "image": image_ref or None,
        "image_digest": image_digest,
        "cuda_graphs": ("enabled (server log mentions CUDA graphs)"
                        if "cuda graph" in sh(["docker", "logs", "l0-vllm"], timeout=60).lower()
                        else "unknown (no CUDA-graph line found in server log)"),
        "caches_dropped": caches,
        "fixture": str(cfg.fixture),
        "seed": cfg.seed,
    }

    # --- fixture check: distinct must not hit the prefix cache (FIRST: cold cache) --------
    m0 = parse_metrics(http_get(f"{endpoint_root}/metrics"))
    run, rc = run_loadgen(endpoint=cfg.endpoint, model=cfg.model, fixture=cfg.fixture,
                          concurrency=1, seed=cfg.seed,
                          out=runs_dir / "prefixcheck.json", extra_body=None)
    m1 = parse_metrics(http_get(f"{endpoint_root}/metrics"))
    delta = {k: m1.get(k, 0) - m0.get(k, 0) for k in set(m0) | set(m1)}
    hits = {k: v for k, v in delta.items() if "prefix_cache" in k}
    fixture_check = {
        "note": "one c=1 pass over the distinct fixture, run FIRST — before warmup — so the "
                "prefix cache holds only earlier prompts, not this pass's own blocks. "
                "Counter deltas must stay near zero (fixtures README, 'Not verified'); a "
                "warm cache shows replay self-hits (L0 2026-10-10: 98.7% when run last).",
        "requests": (run["summary"]["requests_ok"] if run else f"run failed (exit {rc})"),
        "counter_deltas": hits or "no prefix_cache metrics found (names vary by vLLM version)",
    }

    # --- warmup: discarded, counted (ADR 0006) ---------------------------------------------
    warm_run, rc = run_loadgen(endpoint=cfg.endpoint, model=cfg.model, fixture=cfg.fixture,
                               concurrency=cfg.warmup_concurrency, seed=cfg.seed,
                               out=runs_dir / "warmup.json", extra_body=None,
                               requests=cfg.warmup_requests)
    warmup = ({"requests": warm_run["summary"]["requests_ok"], "wall_s": warm_run["wall_s"],
               "discarded": True} if warm_run
              else {"requests": 0, "discarded": True, "error": f"client exit {rc}"})

    # --- the matrix: seeded, interleaved cell order (ADR 0006) -----------------------------
    concs = [int(x) for x in cfg.concurrencies.split(",")]
    cells = [(c, r) for c in concs for r in range(cfg.repeats)]
    random.Random(cfg.seed).shuffle(cells)

    pooled: dict[int, list[dict]] = {c: [] for c in concs}
    run_index: list[dict] = []
    prev_counter_total: float | None = None
    server_restarted = False
    failures = 0
    for i, (c, r) in enumerate(cells):
        pre_gpu = gpu_snapshot()
        pre_counters = parse_metrics(http_get(f"{endpoint_root}/metrics"))
        out = runs_dir / f"c{c}_r{r}.json"
        run, rc = run_loadgen(endpoint=cfg.endpoint, model=cfg.model, fixture=cfg.fixture,
                              concurrency=c, seed=cfg.seed, out=out, extra_body=None)
        post_counters = parse_metrics(http_get(f"{endpoint_root}/metrics"))
        post_gpu = gpu_snapshot()
        entry = {"cell": [c, r], "file": f"runs/c{c}_r{r}.json", "client_exit": rc,
                 "gpu_pre": pre_gpu, "gpu_post": post_gpu,
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

    # --- harness-overhead pair: with and without the continuous-usage option (ADR 0005) ----
    overhead = {"note": "one extra pair of runs, same cell, request option on vs off"}
    pair = {}
    for label, body in (("with_continuous_usage", cfg.continuous_usage_body),
                        ("without", None)):
        run, rc = run_loadgen(endpoint=cfg.endpoint, model=cfg.model, fixture=cfg.fixture,
                              concurrency=cfg.overhead_concurrency, seed=cfg.seed,
                              out=runs_dir / f"overhead_{label}.json", extra_body=body)
        pair[label] = ({"summary": run["summary"], "wall_s": run["wall_s"]} if run
                       else {"error": f"client exit {rc} or no output"})
        if "with_continuous_usage" not in label:
            continue
        if run is None:
            overhead["note"] += (" — NOTE: the continuous-usage run failed; if the server "
                                 "rejected the request option, its exact name for the pinned "
                                 "vLLM version must be corrected (ADR 0005).")
            overhead["continuous_usage_honored"] = None
        else:
            evs = [e for r in run["requests"] for e in r["events"]]
            nulls = sum(1 for e in evs if e.get("tokens") is None)
            overhead["continuous_usage_honored"] = bool(evs) and nulls != len(evs)
            if evs and nulls == len(evs):
                overhead["note"] += (" — WARNING: every event in the continuous-usage run is "
                                     "tokens == null, so the server ignored the request "
                                     "option and the pair measures the same request twice; "
                                     "the option name for the pinned vLLM is wrong (ADR 0005).")
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


    result = {
        "schema": "l0-results/1",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "level": "l0-local",
        "env": env,
        "warmup": warmup,
        "cell_order": [list(c) for c in cells],
        "server_restarted_between_repeats": server_restarted,
        "cells": cells_out,
        "overhead": overhead,
        "fixture_check": fixture_check,
        "runs": run_index,
        "findings": [],
        "findings_note": "L0 is the baseline; it makes no comparison. make verify and its "
                         "findings.json arrive with L1 (ADR 0008).",
        "failures": failures,
    }
    (out_dir / "l0.json").write_text(json.dumps(result, indent=1) + "\n")

    g = env["gpu"]
    env_md = f"""# L0 — environment

Generated {result['generated']} by `python -m measure.measure_l0` (`make measure LEVEL=l0-local`).

| field | value |
|---|---|
| host | {env['host']} |
| kernel | {env['kernel']} |
| GPU | {g.get('name', 'unavailable')} |
| driver | {g.get('driver_version', 'unavailable')} · CUDA (max supported by driver) {env['cuda_max_supported']} |
| server | vLLM {env['server_version']} |
| image | {env['image']} · digest {env['image_digest']} |
| model | {env['model']} @ {env['model_revision']} |
| quantization | {env['quantization']} |
| CUDA graphs | {env['cuda_graphs']} |
| page caches | {env['caches_dropped']} |
| client | python {env['python']}, aiohttp {env['aiohttp']}, loadgen (loadgen/README.md) |
| fixture | {env['fixture']} · seed {env['seed']} |

Raw runs in [runs/](runs/); the assembled matrix in [l0.json](l0.json).
"""
    (out_dir / "env.md").write_text(env_md)

    print(f"measure-l0: {len(cells) - failures}/{len(cells)} runs ok → {out_dir / 'l0.json'}")
    for c in concs:
        if str(c) in cells_out:
            p = cells_out[str(c)]["pooled"]
            print(f"  c={c:3d} n={cells_out[str(c)]['requests_ok']:5d} "
                  f"ttft_p50={p['ttft_ms']['p50']} itl_p50={p['itl_event_ms']['p50']} "
                  f"tpot_p50={p['tpot_ms']['p50']}")
    if server_restarted:
        print("WARNING: the server restarted between repeats (request counter went backwards).",
              file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
