"""full-stack-serving ADR 0003 — cross-check loadgen/ against `vllm bench serve`.

Both tools, the same fixture (`loadgen/fixtures/distinct.jsonl`), the same server
(L0's endpoint), the same concurrency (the L0 matrix: 1/4/16/64), the same
`max_tokens` (128) and `ignore_eos` (true). Both tools are driven per cell,
back-to-back, so the disagreement in TTFT/ITL percentiles and throughput is a
measurement of *where each tool puts its timing boundary*, not of the server.

- loadgen timestamps at the socket read, as early in user space as Python allows.
- `vllm bench serve` (v0.23.0) timestamps after `json.loads()` of each parsed SSE
  message, inside aiohttp's stream iterator (ADR 0003) — several messages that
  arrive in one read get near-identical timestamps.

The disagreement is **reported, never tuned away** (ADR 0003). Agreement is
evidence the client is sound; a systematic gap is a finding about where bench's
boundary costs accuracy. Both outcomes are results. `tokens_out` comes from the
server's `usage` in both tools (loadgen: never counts chunks; bench:
`stream_options.include_usage`).

Known asymmetries, recorded not hidden:

- bench shuffles the fixture rows with its own RNG (`--seed`, fixed 20261010);
  loadgen consumes the file in order. Same 256 prompts, different order — and
  per-cell percentiles over the whole fixture are order-insensitive at this n.
- The fixture's per-row `max_tokens`/`ignore_eos` map to bench's global
  `--custom-output-len 128 --ignore-eos` (uniform in this fixture; a stated
  mapping, not an equivalent of the general case).

Writes `results/instrument/crosscheck.json` (schema `instrument-crosscheck/1`)
alongside `validation.json`, and never touches `results/l0-local/`.
`results/` is committed; scratch (`tmp/crosscheck/`) is not.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from measure.measure_l0 import REPO, gpu_snapshot, run_loadgen, sh
from measure.measure_l1 import load_bars

SCRATCH = REPO / "tmp" / "crosscheck"
BENCH_IMAGE = "crosscheck-bench:v0.23.0-pandas"  # vllm/vllm-openai:v0.23.0 + pandas (bench extra)
CONCS = (1, 4, 16, 64)
REQUESTS = 256
SEED = 20261010
MAX_TOKENS = 128


def bench_cell(*, c: int, out: Path, endpoint_root: str, model: str, fixture: str,
               seed: int, requests: int = REQUESTS) -> tuple[dict, list[str]]:
    """One `vllm bench serve` cell. Returns (result-json, argv)."""
    cmd = ["docker", "run", "--rm", "--network", "host",
           "-v", f"{REPO / fixture}:/app/fixture.jsonl:ro",
           "-v", f"{out.parent}:/app/out", "-w", "/app",
           "--entrypoint", "vllm", BENCH_IMAGE, "bench", "serve",
           "--backend", "openai-chat", "--base-url", endpoint_root,
           "--endpoint", "/v1/chat/completions", "--model", model,
           "--tokenizer", model, "--dataset-name", "custom",
           "--dataset-path", "/app/fixture.jsonl", "--custom-output-len", str(MAX_TOKENS),
           "--num-prompts", str(requests), "--max-concurrency", str(c), "--ignore-eos",
           "--percentile-metrics", "ttft,itl,tpot", "--metric-percentiles", "50,99",
           "--seed", str(seed), "--save-result",
           "--result-dir", "/app/out", "--result-filename", out.name]
    t0 = time.monotonic()
    proc = subprocess.run(cmd, cwd=REPO, check=False,
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    wall = time.monotonic() - t0
    if proc.returncode != 0 or not out.exists():
        print(proc.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"crosscheck: vllm bench serve c={c} failed (exit {proc.returncode})")
    result = json.loads(out.read_text(encoding="utf-8"))
    result["container_wall_s"] = round(wall, 3)
    return result, cmd


def loadgen_cell(*, c: int, out: Path, endpoint: str, model: str, fixture: str,
                 seed: int, requests: int = REQUESTS) -> tuple[dict, int]:
    """One loadgen closed-loop cell over the same protocol."""
    run, rc = run_loadgen(endpoint=endpoint, model=model, fixture=fixture,
                          concurrency=c, seed=seed, out=out, extra_body=None,
                          requests=requests if requests < REQUESTS else None,
                          level="instrument-crosscheck")
    if run is None:
        raise SystemExit(f"crosscheck: loadgen c={c} produced no run file (exit {rc})")
    if rc != 0:
        raise SystemExit(f"crosscheck: loadgen c={c} exited {rc}")
    return run, rc


def main() -> int:
    ap = argparse.ArgumentParser(prog="crosscheck",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("--endpoint", default="http://localhost:8000/v1/chat/completions")
    ap.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--fixture", default="loadgen/fixtures/distinct.jsonl")
    ap.add_argument("--concurrencies", default=",".join(str(c) for c in CONCS))
    ap.add_argument("--requests", type=int, default=REQUESTS)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--bars", default="results/instrument/validation.json",
                    help="the committed ADR 0007 instrument errors the disagreement "
                         "is scaled against (reported, never gated)")
    ap.add_argument("--out", default="results/instrument/crosscheck.json")
    cfg = ap.parse_args()

    concs = [int(x) for x in cfg.concurrencies.split(",")]
    fixture_abs = (REPO / cfg.fixture).resolve()
    if not fixture_abs.exists():
        raise SystemExit(f"crosscheck: no fixture at {fixture_abs}")
    endpoint_root = cfg.endpoint.rsplit("/v1/", 1)[0]

    if SCRATCH.exists():
        import shutil
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)

    # server reachable before anything runs (sh returns "" on failure, never None)
    if not sh(["curl", "-sf", f"{endpoint_root}/v1/models"], timeout=10):
        raise SystemExit(f"crosscheck: server not reachable at {endpoint_root} — "
                         "`make up LEVEL=l0-local` first")

    bench_version = sh(["docker", "run", "--rm", "--entrypoint", "python3", BENCH_IMAGE,
                         "-c", "import pandas, vllm.benchmarks.serve as s; "
                         "print(pandas.__version__); "
                         "print(s.__file__)"]).splitlines()
    bench_digest = sh(["docker", "image", "inspect", "--format",
                       "{{index .RepoDigests 0}}", BENCH_IMAGE]) or "unavailable"

    cells: list[dict] = []
    commands: dict[str, list[str]] = {}
    for c in concs:
        print(f"crosscheck: cell c={c}: loadgen, then bench, same server", file=sys.stderr)
        lg_run, lg_rc = loadgen_cell(c=c, out=SCRATCH / f"c{c}_loadgen.json",
                                     endpoint=cfg.endpoint, model=cfg.model,
                                     fixture=cfg.fixture, seed=cfg.seed,
                                     requests=cfg.requests)
        bench, bench_cmd = bench_cell(c=c, out=SCRATCH / f"c{c}_bench.json",
                                      endpoint_root=endpoint_root, model=cfg.model,
                                      fixture=cfg.fixture, seed=cfg.seed,
                                      requests=cfg.requests)
        # the exact commands that ran, as canonical strings (<repo> stands for the
        # checkout root; the artifacts are public, so no absolute host paths)
        lg_out_rel = (SCRATCH / f"c{c}_loadgen.json").relative_to(REPO)
        commands[f"c{c}"] = {
            "loadgen": " ".join(
                ["uv run python -m loadgen.client", "--endpoint", cfg.endpoint,
                 "--model", cfg.model, "--fixture", cfg.fixture,
                 "--concurrency", str(c), "--seed", str(cfg.seed),
                 "--level", "instrument-crosscheck", "--output", str(lg_out_rel)]),
            "bench": " ".join(
                ["docker run --rm --network host",
                 f"-v <repo>/{cfg.fixture}:/app/fixture.jsonl:ro",
                 "-v <repo>/tmp/crosscheck:/app/out", "-w /app",
                 f"--entrypoint vllm {BENCH_IMAGE} bench serve",
                 "--backend openai-chat", "--base-url", endpoint_root,
                 "--endpoint /v1/chat/completions", "--model", cfg.model,
                 "--tokenizer", cfg.model, "--dataset-name custom",
                 "--dataset-path /app/fixture.jsonl",
                 f"--custom-output-len {MAX_TOKENS}",
                 f"--num-prompts {cfg.requests}", f"--max-concurrency {c}",
                 "--ignore-eos", "--percentile-metrics ttft,itl,tpot",
                 "--metric-percentiles 50,99", f"--seed {cfg.seed}",
                 "--save-result --result-dir /app/out",
                 f"--result-filename c{c}_bench.json"]),
        }
        s = lg_run["summary"]
        cells.append({
            "c": c,
            "loadgen": {
                "wall_s": lg_run["wall_s"],
                "requests_ok": s["requests_ok"], "requests_error": s["requests_error"],
                "ttft_p50_ms": s["ttft_ms"]["p50"], "ttft_p99_ms": s["ttft_ms"]["p99"],
                "itl_p50_ms": s["itl_event_ms"]["p50"], "itl_p99_ms": s["itl_event_ms"]["p99"],
                "tpot_p50_ms": s["tpot_ms"]["p50"],
                "tokens_per_s": s["tokens_per_s"],
                "requests_per_s": round(s["requests_ok"] / lg_run["wall_s"], 2)
                if lg_run["wall_s"] else None,
            },
            "bench": {
                "duration_s": bench["duration"], "completed": bench["completed"],
                "failed": bench["failed"],
                "ttft_p50_ms": bench["p50_ttft_ms"], "ttft_p99_ms": bench["p99_ttft_ms"],
                "itl_p50_ms": bench["p50_itl_ms"], "itl_p99_ms": bench["p99_itl_ms"],
                "tpot_p50_ms": bench["p50_tpot_ms"], "tpot_p99_ms": bench["p99_tpot_ms"],
                "output_throughput": bench["output_throughput"],
                "request_throughput": bench["request_throughput"],
                "container_wall_s": bench["container_wall_s"],
            },
        })

    # the disagreement, scaled against the instrument's own error (ADR 0007 bars)
    bars = load_bars((REPO / cfg.bars).resolve(), concs)
    for cell in cells:
        lg, b = cell["loadgen"], cell["bench"]
        bar = bars.get(cell["c"])  # load_bars: 10 x the cell's committed instrument p99 ms
        deltas = {
            "ttft_p50_ms": round(lg["ttft_p50_ms"] - b["ttft_p50_ms"], 3)
            if isinstance(lg["ttft_p50_ms"], (int, float)) and
            isinstance(b["ttft_p50_ms"], (int, float)) else None,
            "itl_p50_ms": round(lg["itl_p50_ms"] - b["itl_p50_ms"], 3)
            if isinstance(lg["itl_p50_ms"], (int, float)) and
            isinstance(b["itl_p50_ms"], (int, float)) else None,
            "tpot_p50_ms": round(lg["tpot_p50_ms"] - b["tpot_p50_ms"], 3)
            if isinstance(lg["tpot_p50_ms"], (int, float)) and
            isinstance(b["tpot_p50_ms"], (int, float)) else None,
            "tokens_per_s": round(lg["tokens_per_s"] - b["output_throughput"], 1)
            if isinstance(lg.get("tokens_per_s"), (int, float)) else None,
        }
        cell["delta_loadgen_minus_bench"] = deltas
        cell["delta_over_instrument_bar"] = {
            k: round(d / bar, 2) if d is not None and bar else None
            for k, d in deltas.items()}

    result = {
        "schema": "instrument-crosscheck/1",
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tool": "python -m measure.crosscheck",
        "adr": "0003",
        "env": {
            "host": sh(["hostname", "-s"]) or "unknown",
            "kernel": sh(["uname", "-r"]) or "unknown",
            "gpu": gpu_snapshot(),
            "server_version": sh(["curl", "-sf", f"{endpoint_root}/version"]),
            "server_image": "vllm/vllm-openai:v0.23.0",
            "bench_image": BENCH_IMAGE,
            "bench_image_digest": bench_digest,
            "bench_vllm": "vllm/vllm-openai:v0.23.0 (pinned by image tag + digest)",
            "bench_pandas": bench_version[0] if bench_version else "unknown",
            "bench_timing_boundary": "aiohttp stream iterator, time.perf_counter() after "
                                     "json.loads() of each parsed SSE message "
                                     "(ADR 0003, read from v0.23.0 image source)",
            "loadgen_timing_boundary": "time.monotonic_ns() at the socket read "
                                       "(loadgen/README.md, validated in results/instrument/validation.json)",
        },
        "protocol": {
            "fixture": cfg.fixture, "requests": cfg.requests, "seed": cfg.seed,
            "max_tokens": MAX_TOKENS, "ignore_eos": True,
            "concurrencies": concs, "load_model": "closed (both tools)",
            "bench_flags": ["--backend openai-chat", "--request-rate inf (default)",
                            "--num-prompts 256", "--max-concurrency <c>",
                            "--custom-output-len 128", "--ignore-eos",
                            "--percentile-metrics ttft,itl,tpot --metric-percentiles 50,99"],
            "commands": commands,
        },
        "asymmetries": [
            "bench shuffles fixture rows with its own --seed RNG; loadgen reads in file order",
            "fixture per-row max_tokens/ignore_eos map to bench's global "
            "--custom-output-len 128 --ignore-eos (uniform in this fixture)",
            "bench runs in a container (extra network hop: docker bridge → host); "
            "loadgen runs on the host — recorded, same direction at every cell",
        ],
        "bars_note": "delta_over_instrument_bar scales each p50 delta against the "
                     "committed ADR 0007 p99 instrument error for that cell "
                     "(results/instrument/validation.json); reported, never gated",
        "cells": cells,
    }

    out_path = (REPO / cfg.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    print(f"crosscheck: wrote {out_path.relative_to(REPO)}", file=sys.stderr)

    print("\ncrosscheck: loadgen − vllm bench serve, per cell (p50 ms):")
    hdr = f"  {'c':>4s} {'ttft_lg':>8s} {'ttft_b':>8s} {'Δ':>8s} {'Δ/bar':>7s}  " \
          f"{'itl_lg':>7s} {'itl_b':>7s} {'Δ':>7s}  {'rps_lg':>7s} {'rps_b':>7s}"
    print(hdr)
    for cell in cells:
        lg, b, d = cell["loadgen"], cell["bench"], cell["delta_loadgen_minus_bench"]
        bar = cell["delta_over_instrument_bar"]
        print(f"  {cell['c']:>4d} {lg['ttft_p50_ms']:>8.2f} {b['ttft_p50_ms']:>8.2f} "
              f"{d['ttft_p50_ms']:>+8.2f} {str(bar.get('ttft_p50_ms'))[:6]:>7s}  "
              f"{lg['itl_p50_ms']:>7.2f} {b['itl_p50_ms']:>7.2f} {d['itl_p50_ms']:>+7.2f}  "
              f"{lg['requests_per_s']:>7.2f} {b['request_throughput']:>7.2f}")
    print("\nThe disagreement is reported, never tuned away (ADR 0003).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())