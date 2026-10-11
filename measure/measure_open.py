"""full-stack-serving ADR 0013 — the open-loop half, run at L0's endpoint.

A supplementary experiment at L0 (NOT a level: no L-number, no level-table entry, no change
to the cut order). L0/L1's committed closed-loop numbers stay the baseline; these results are
committed under ``results/l0-local/open/`` and never substituted into them. The load model is
open loop — seeded Poisson arrivals at a swept target rate — because ADR 0004's own
consequence ("closed-loop tails at saturation are understated") was left unmeasured when
L2/L3 were cut, and L4's span-to-client gap finding is a queueing statement produced entirely
by a method that cannot build a queue deeper than N.

Protocol (pre-declared BEFORE the data, in the manner of ADR 0007's bars):

- Same fixture, endpoint, model, max_tokens (128) and ignore_eos as the closed-loop matrix;
  the independent variable is the arrival rate λ (req/s), not concurrency.
- Seeded Poisson arrivals (seed 20261010, identical schedule every repeat), in-flight cap 64
  (the closed-loop c=64 bracket). Requests that would exceed the cap are REFUSED — counted,
  never hidden — rather than silently widening the schedule.
- **Schedule-lag invalidity rule (declared here, applied per run):** lag is
  ``sent_ms − scheduled_ms`` per sent request. A run is INVALID if its lag p50 exceeds
  100/λ ms (10% of the mean inter-arrival) or its lag p99 exceeds 1000/λ ms (one mean
  inter-arrival). A run whose lag exceeds the bar has a schedule that no longer is the
  schedule, so its λ is not what was measured; it is logged as invalid and reported, never
  re-run. λ=32 is declared in advance as the cell expected to strain the harness — exercising
  the rule is part of validating the mode, not a surprise (ADR 0013).
- ADR 0006 applies unchanged: R=5 repeats per λ, interleaved in a seeded order; percentiles
  only where the request count supports them (p50 ≥ 30, p99 ≥ 1000); min/median/max across
  repeats beside the pooled figure; warmup discarded and counted; GPU and server counters
  captured around every run; server restart detected from counters, not assumed.

Writes ``results/l0-local/open/open.json`` + raw runs in ``runs/``. Overwrites only its own
outputs. Exit 1 only on request ERRORS (refusals and invalid-lag runs are results).
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from measure.measure_l0 import (REPO, cell_block, gpu_snapshot, http_get,
                                parse_metrics, pct_or_na, sh)

SEED = 20261010
REPEATS = 5
INFLIGHT_CAP = 64
MAX_TOKENS = 128
# (λ req/s, arrival-window s). Durations are sized so every cell clears the ADR 0006 p50
# floor per run and the p99 floor pooled (λ ≥ 8), and so the sweep spans the saturation the
# closed-loop matrix brackets (today's c=64 cell sustained ~12.5 ok/s; the committed L0
# matrix saw 21–37 ok/s — the λ=16 cell is inside that band, λ=24/32 beyond it).
RATES: tuple[tuple[float, float], ...] = ((2.0, 60.0), (4.0, 40.0), (8.0, 30.0),
                                          (16.0, 30.0), (24.0, 30.0), (32.0, 30.0))
LAG_P50_BARS = {lam: round(100.0 / lam, 3) for lam, _ in RATES}   # ms = 10% of 1/λ
LAG_P99_BARS = {lam: round(1000.0 / lam, 3) for lam, _ in RATES}  # ms = one mean inter-arrival
WARMUP = {"rate": 4.0, "duration": 10.0}


def run_open_cell(*, rate: float, duration: float, out: Path, endpoint: str, model: str,
                  fixture: str, seed: int) -> tuple[dict | None, int]:
    """One loadgen open-loop pass. Returns (run-json, exit code)."""
    cmd = [sys.executable, "-m", "loadgen.client", "--endpoint", endpoint, "--model", model,
           "--fixture", fixture, "--mode", "open", "--rate", str(rate),
           "--duration", str(duration), "--inflight-cap", str(INFLIGHT_CAP),
           "--seed", str(seed), "--level", "l0-open", "--output", str(out)]
    proc = subprocess.run(cmd, cwd=REPO, check=False)
    try:
        with open(out, encoding="utf-8") as f:
            return json.load(f), proc.returncode
    except (FileNotFoundError, json.JSONDecodeError):
        return None, proc.returncode


def pool_open_run(run: dict) -> dict:
    """Per-run values for one λ cell: statuses separated (refused ≠ error), lags computed.

    Refused requests have no send, so they carry no lag; errors were sent, so they do.
    """
    ok = [r for r in run["requests"] if r["status"] == "ok"]
    err = [r for r in run["requests"] if r["status"] == "error"]
    refused = [r for r in run["requests"] if r["status"] == "refused"]
    sent = ok + err
    ttfts = [r["ttft_ms"] for r in ok if r["ttft_ms"] is not None]
    itls = [b["t_ms"] - a["t_ms"] for r in ok for a, b in zip(r["events"], r["events"][1:])]
    tpots = []
    for r in ok:
        if r.get("tokens_out") and r["tokens_out"] > 1 and r.get("e2e_ms") is not None \
                and r.get("ttft_ms") is not None:
            tpots.append((r["e2e_ms"] - r["ttft_ms"]) / (r["tokens_out"] - 1))
    lags = [r["sent_ms"] - r["scheduled_ms"] for r in sent
            if r.get("sent_ms") is not None and r.get("scheduled_ms") is not None]
    return {
        "n_ok": len(ok), "n_error": len(err), "n_refused": len(refused), "n_sent": len(sent),
        "ttfts": ttfts, "itls": itls, "tpots": tpots, "lags_ms": lags,
        "tokens_out_total": sum(r.get("tokens_out") or 0 for r in ok),
        "tokens_per_s": run["summary"]["tokens_per_s"], "wall_s": run["wall_s"],
    }


def lag_block(lags: list[float], n: int, lam: float) -> dict:
    """Lag percentiles (floored per ADR 0006, n = sent requests) with the declared bars."""
    return {
        "p50": pct_or_na(lags, 50, n), "p99": pct_or_na(lags, 99, n),
        "bar_p50_ms": LAG_P50_BARS[lam], "bar_p99_ms": LAG_P99_BARS[lam],
    }


def run_verdict(lag: dict) -> str:
    p50, p99 = lag["p50"], lag["p99"]
    if isinstance(p50, (int, float)) and p50 > lag["bar_p50_ms"]:
        return "INVALID (lag p50 over bar)"
    if isinstance(p99, (int, float)) and p99 > lag["bar_p99_ms"]:
        return "INVALID (lag p99 over bar)"
    return "valid"



def main() -> int:
    ap = argparse.ArgumentParser(prog="measure-open",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("--endpoint", default="http://localhost:8000/v1/chat/completions")
    ap.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--fixture", default="loadgen/fixtures/distinct.jsonl")
    ap.add_argument("--out-dir", default="results/l0-local/open")
    ap.add_argument("--repeats", type=int, default=REPEATS)
    ap.add_argument("--rates", default=",".join(f"{lam:g}" for lam, _ in RATES),
                    help="comma-separated subset of the protocol's λ values "
                         "(protocol default: all)")
    cfg = ap.parse_args()

    selected = [float(x) for x in cfg.rates.split(",")]
    rates_sel = [(lam, d) for lam, d in RATES if any(abs(lam - s) < 1e-9 for s in selected)]
    if not rates_sel:
        raise SystemExit(f"measure-open: no protocol λ values in {cfg.rates!r} "
                         f"(protocol: {','.join(f'{lam:g}' for lam, _ in RATES)})")

    endpoint_root = cfg.endpoint.rsplit("/v1/", 1)[0]
    out_dir = (REPO / cfg.out_dir).resolve()
    runs_dir = out_dir / "runs"
    if runs_dir.exists():
        import shutil
        shutil.rmtree(runs_dir)
    runs_dir.mkdir(parents=True)

    if not sh(["curl", "-sf", f"{endpoint_root}/v1/models"], timeout=10):
        raise SystemExit(f"measure-open: server not reachable at {endpoint_root} — "
                         "`make up LEVEL=l0-local` first")

    server_version = http_get(f"{endpoint_root}/version") or "unknown"
    server_image = sh(["docker", "inspect", "--format", "{{.Config.Image}}", "l0-vllm"]) \
        or "unknown"
    image_digest = sh(["docker", "image", "inspect", "--format",
                       "{{index .RepoDigests 0}}",
                       "vllm/vllm-openai:v0.23.0"]) or "unavailable"

    # warmup: discarded, counted (ADR 0006)
    print(f"measure-open: warmup λ={WARMUP['rate']} for {WARMUP['duration']}s "
          "(discarded, counted)", file=sys.stderr)
    warm_run, warm_rc = run_open_cell(rate=WARMUP["rate"], duration=WARMUP["duration"],
                                      out=runs_dir / "warmup.json", endpoint=cfg.endpoint,
                                      model=cfg.model, fixture=cfg.fixture, seed=SEED)
    warm_summary = warm_run["summary"] if warm_run else \
        f"FAILED (exit {warm_rc}) — see {runs_dir / 'warmup.json'}"
    warmup_failed = warm_run is None or warm_rc != 0
    if warmup_failed:
        print(f"measure-open: warmup FAILED (exit {warm_rc})", file=sys.stderr)

    # interleaved, seeded order over (λ, r) — drift lands across cells, not in one (ADR 0006)
    cells_order = [(lam, r) for lam, _ in rates_sel for r in range(cfg.repeats)]
    random.Random(SEED).shuffle(cells_order)
    print(f"measure-open: {len(cells_order)} runs, interleaved seeded order: "
          f"{cells_order}", file=sys.stderr)

    per_run: dict[tuple[float, int], dict] = {}
    total_invalid: list[dict] = []
    failures: list[str] = [f"warmup: exit {warm_rc}"] if warmup_failed else []
    server_restarted = False

    for i, (lam, r) in enumerate(cells_order, 1):
        duration = dict(rates_sel)[lam]
        out = runs_dir / f"l{lam:g}_r{r}.json"
        m_before = parse_metrics(http_get(f"{endpoint_root}/metrics"))
        gpu_before = gpu_snapshot()
        run, rc = run_open_cell(rate=lam, duration=duration, out=out,
                                endpoint=cfg.endpoint, model=cfg.model,
                                fixture=cfg.fixture, seed=SEED)
        gpu_after = gpu_snapshot()
        m_after = parse_metrics(http_get(f"{endpoint_root}/metrics"))
        for k, v in m_after.items():
            if k in m_before and v < m_before[k]:
                server_restarted = True
        if run is None:
            failures.append(f"λ={lam:g} r{r}: loadgen exit {rc}, no run file")
            continue
        p = pool_open_run(run)
        n_sent = p["n_sent"]
        lag = lag_block(p["lags_ms"], n_sent, lam)
        verdict = run_verdict(lag)
        per_run[(lam, r)] = {
            "n_arrived": len(run["requests"]),
            "n_ok": p["n_ok"], "n_error": p["n_error"], "n_refused": p["n_refused"],
            "wall_s": p["wall_s"], "tokens_per_s": p["tokens_per_s"],
            "ttft_p50": pct_or_na(p["ttfts"], 50, p["n_ok"]),
            "itl_p50": pct_or_na(p["itls"], 50, p["n_ok"]),
            "schedule_lag": lag, "verdict": verdict,
            "gpu_start": gpu_before, "gpu_end": gpu_after,
        }
        if verdict != "valid":
            total_invalid.append({"rate": lam, "repeat": r, "verdict": verdict,
                                 "lag": lag})
        if p["n_error"] > 0:
            failures.append(f"λ={lam:g} r{r}: {p['n_error']} error requests")
        lag_p50_str = (f"{lag['p50']:.3f}" if isinstance(lag['p50'], (int, float))
                       else "n/a")
        print(f"  [{i}/{len(cells_order)}] λ={lam:g} r{r}: ok={p['n_ok']} "
              f"err={p['n_error']} refused={p['n_refused']} "
              f"lag_p50={lag_p50_str} verdict={verdict}", file=sys.stderr)

    # per-λ assembly: pooled percentiles + spread (ADR 0006), lag next to every latency figure
    cells: dict[str, dict] = {}
    for lam, duration in rates_sel:
        if not any((lam, r) in per_run for r in range(cfg.repeats)):
            continue
        # pool from the raw files — the single source of truth, statuses separated
        raws = []
        for r in range(cfg.repeats):
            f = runs_dir / f"l{lam:g}_r{r}.json"
            if f.exists():
                raws.append(pool_open_run(json.loads(f.read_text(encoding="utf-8"))))
        cell = cell_block(raws)
        n_sent = sum(p["n_sent"] for p in raws)
        all_lags = [v for p in raws for v in p["lags_ms"]]
        ok_total = sum(p["n_ok"] for p in raws)
        arr_total = sum(p["n_ok"] + p["n_error"] + p["n_refused"] for p in raws)
        refused_total = sum(p["n_refused"] for p in raws)
        cells[f"{lam:g}"] = {
            "rate_req_s": lam, "duration_s": duration, "repeats": cfg.repeats,
            "requests_arrived": arr_total, "requests_ok": ok_total,
            "requests_refused": refused_total, "refused_share": round(
                refused_total / arr_total, 4) if arr_total else None,
            "achieved_ok_per_s": {  # over the arrival window, not the drain tail
                "pooled": round(ok_total / (duration * cfg.repeats), 2),
                "per_run_min": round(min(p["n_ok"] for p in raws) / duration, 2),
                "per_run_max": round(max(p["n_ok"] for p in raws) / duration, 2)},
            "schedule_lag_pooled": lag_block(all_lags, n_sent, lam),
            # verdicts in repeat order (r ascending)
            "verdicts": [per_run[(lam, r)]["verdict"]
                         for r in range(cfg.repeats) if (lam, r) in per_run],
            "wall_s_median": round(statistics.median([p["wall_s"] for p in raws]), 3),
            **cell,
        }

    result = {
        "schema": "l0-open/1",
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tool": "python -m measure.measure_open",
        "adr": "0013",
        "note": "open-loop supplementary experiment at L0's endpoint — NOT a level, NOT "
                "comparable to the closed-loop matrix (coordinated omission applies to that "
                "matrix, not this); never substituted into the L0 baseline",
        "protocol": {
            "fixture": cfg.fixture, "seed": SEED, "max_tokens": MAX_TOKENS,
            "ignore_eos": True, "load_model": "open (seeded Poisson arrivals)",
            "inflight_cap": INFLIGHT_CAP, "repeats": cfg.repeats,
            "rates_and_durations": [{"rate_req_s": lam, "duration_s": d}
                                    for lam, d in rates_sel],
            "lag_invalidity_rule": {
                "definition": "lag = sent_ms − scheduled_ms per sent request; a run is "
                              "INVALID if lag p50 > 100/λ ms or lag p99 > 1000/λ ms",
                "bars_p50_ms": LAG_P50_BARS, "bars_p99_ms": LAG_P99_BARS,
                "declared": "before any open-loop data was taken (this file's constants, "
                            "ADR 0013); invalid runs are reported, never re-run"},
            "expected_to_strain": "λ=32 — declared in advance as the cell expected to go "
                                  "invalid; exercising the rule is part of validating the "
                                  "mode (ADR 0013)",
            "interleaved_order": [[lam, r] for lam, r in cells_order],
            "warmup": {"discarded": True, **WARMUP, "summary": warm_summary},
            "percentile_floors": {"p50": 30, "p90": 100, "p99": 1000,
                                  "unit": "requests (ADR 0006)"},
        },
        "env": {
            "host": sh(["hostname", "-s"]) or "unknown",
            "kernel": sh(["uname", "-r"]) or "unknown",
            "gpu": gpu_snapshot(),
            "server_version": server_version, "server_image": server_image,
            "server_image_digest": image_digest,
            "model": cfg.model, "fixture": cfg.fixture,
            "server_restarted_between_runs": server_restarted,
        },
        "cells": cells,
        "runs": [{"rate_req_s": lam, "repeat": r, **per_run[(lam, r)]}
                 for lam, r in cells_order if (lam, r) in per_run],
        "invalid_runs": total_invalid,
        "failures": failures,
    }

    (out_dir / "open.json").write_text(json.dumps(result, indent=1) + "\n",
                                       encoding="utf-8")
    print(f"measure-open: wrote {(out_dir / 'open.json').relative_to(REPO)}", file=sys.stderr)

    def fmt(p):
        """A floored percentile prints as n/a; a real one rounds to 2dp."""
        return "n/a" if isinstance(p, dict) else (f"{p:.2f}" if isinstance(p, (int, float))
                                                   else str(p))

    print("\nmeasure-open: per-λ pooled (ADR 0006 floors), lag next to every figure:")
    print(f"  {'λ':>5s} {'ok':>6s} {'refus':>6s} {'ok/s':>6s} {'ttft50':>8s} {'ttft99':>9s} "
          f"{'lag50':>8s} {'lag99':>8s} {'verdicts'}")
    for lam, _ in rates_sel:
        c = cells.get(f"{lam:g}")
        if not c:
            continue
        lag = c["schedule_lag_pooled"]
        v = " ".join("V" if x == "valid" else "I" for x in c["verdicts"])
        print(f"  {lam:>5g} {c['requests_ok']:>6d} {c['requests_refused']:>6d} "
              f"{c['achieved_ok_per_s']['pooled']:>6.2f} "
              f"{fmt(c['pooled']['ttft_ms']['p50']):>8s} "
              f"{fmt(c['pooled']['ttft_ms']['p99']):>9s} "
              f"{fmt(lag['p50']):>8s} {fmt(lag['p99']):>8s} {v}")
    if total_invalid:
        print(f"\n{len(total_invalid)} invalid run(s) — reported, not re-run (ADR 0013).")
    if failures:
        print(f"\n{len(failures)} failure(s): {failures}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
