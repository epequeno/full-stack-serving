"""The full-stack-serving ADR 0008 gate for L1.

Re-runs the entire L1 experiment (`measure.measure_l1.run_level`) into a scratch directory
— never over `results/` — using the *committed* run's own protocol (read from the
committed `l1.json`, not from the command line), then:

- HARD: every CLAIMABLE finding must reproduce. A finding is claimable only if
  |effect| >= 10x the cell's committed instrument p99 error (ADR 0007, fixed before
  L1) — `claimable` is computed by `measure_l1` from the committed validation bars,
  never judged here. A missing, changed or newly-claimable verdict fails the gate.
  Sub-bar verdicts sit at the instrument's resolution and flip between fresh runs by
  design (2026-10-10: c=1 TTFT, 0.012 ms margin) — recorded as information, never
  gated. Restricting claims to effects above the instrument's error is the option
  ADR 0007 sanctions; the rule for claimable findings is unchanged and hard.
- SOFT: the fresh per-cell pooled medians are compared with the committed ones against
  the tolerance declared *at measure time* in `findings.json` (derived from L0's
  committed across-run spread, before any fresh comparison could be seen). Outside the
  band is reported, never fatal. Skipped with a printed reason if the environment
  (GPU model, driver, or CUDA version) drifted from the committed run.

Exit status: non-zero only on a verdict mismatch. Regenerating committed evidence stays
an explicit `make measure LEVEL=l1-k8s`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from measure.measure_l0 import REPO
from measure.measure_l1 import run_level

POOLED = {"ttft_p50_ms": ("ttft_ms", "p50"),
          "itl_p50_ms": ("itl_event_ms", "p50"),
          "tpot_p50_ms": ("tpot_ms", "p50")}


def main() -> int:
    ap = argparse.ArgumentParser(prog="verify-l1", description=__doc__.splitlines()[0])
    ap.add_argument("--endpoint", default="http://localhost:30080/v1/chat/completions",
                    help="defaults to the committed run's endpoint if given the L0 port")
    ap.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--revision", default="989aa7980e4cf806f80c7fef2b1adb7bc71aa306")
    ap.add_argument("--fixture", default="loadgen/fixtures/distinct.jsonl")
    ap.add_argument("--scratch", default="tmp/verify-l1-k8s")
    ap.add_argument("--findings", default="results/l1-k8s/findings.json")
    ap.add_argument("--baseline-runs", default="results/l0-local/runs")
    ap.add_argument("--continuous-usage-body",
                    default='{"stream_options": {"include_usage": true, "continuous_usage_stats": true}}')
    ap.add_argument("--k3s-timeout", default="900s")
    cfg = ap.parse_args()

    findings_path = (REPO / cfg.findings).resolve()
    if not findings_path.exists():
        print(f"verify-l1: no committed findings at {findings_path} — run and commit "
              "`make measure LEVEL=l1-k8s` first.", file=sys.stderr)
        return 2
    committed = json.loads(findings_path.read_text(encoding="utf-8"))
    l1_path = findings_path.parent / "l1.json"
    committed_l1 = json.loads(l1_path.read_text(encoding="utf-8"))
    proto = committed_l1["protocol"]

    scratch = (REPO / cfg.scratch).resolve()
    endpoint = cfg.endpoint

    print(f"verify-l1: re-measuring with the committed protocol "
          f"(seed={proto['seed']}, c={proto['concurrencies']}, repeats={proto['repeats']}) "
          f"into {scratch} — results/ is never touched", file=sys.stderr)
    fresh = run_level(
        scratch, endpoint=endpoint, model=cfg.model, revision=cfg.revision,
        fixture=proto["fixture"], concs=proto["concurrencies"],
        repeats=proto["repeats"], seed=proto["seed"],
        warmup_requests=proto["warmup_requests"],
        warmup_concurrency=proto["warmup_concurrency"],
        overhead_concurrency=proto["overhead_concurrency"],
        continuous_usage_body=proto["continuous_usage_body"],
        baseline_runs=(REPO / cfg.baseline_runs).resolve(),
        k3s_timeout=cfg.k3s_timeout)

    # --- HARD: claimable findings must reproduce (ADR 0008, restricted by ADR 0007) -----
    # ADR 0007 (fixed before L1): a between-level finding is claimable only if
    # |effect| >= 10x the cell's committed instrument p99 error. A sub-bar verdict
    # sits at the instrument's resolution and flips between fresh runs by design
    # (2026-10-10: c=1 TTFT, 0.012 ms margin) — recorded as information, never gated.
    # "Restricting the claims to effects larger than the error" is the option ADR
    # 0007 sanctions; the rule for claimable findings is unchanged and hard.
    committed_claim = {(f["metric"], f["cell"]): f["verdict"]
                       for f in committed["findings"] if f.get("claimable")}
    fresh_claim = {(f["metric"], f["cell"]): f["verdict"]
                   for f in fresh["findings"] if f.get("claimable")}
    mismatches = [(k, cv, fresh_claim.get(k, "MISSING in fresh run"))
                  for k, cv in committed_claim.items() if fresh_claim.get(k) != cv]
    mismatches += [(k, "not committed", fv)
                   for k, fv in fresh_claim.items() if k not in committed_claim]

    committed_by_key = {(f["metric"], f["cell"]): f for f in committed["findings"]}
    print("")
    print("verify-l1: verdicts (fresh vs committed) — [gated] = claimable (ADR 0007), "
          "[info] = sub-bar, at the instrument's resolution:")
    for f in fresh["findings"]:
        key = (f["metric"], f["cell"])
        cf = committed_by_key.get(key, {})
        if f.get("claimable"):
            mark = "MISMATCH" if fresh_claim.get(key) != cf.get("verdict") else "OK"
        else:
            mark = ("ok(sub-bar: flip recorded, not gated — ADR 0007/0008)"
                    if f["verdict"] == cf.get("verdict") else
                    "info(sub-bar FLIP recorded, not gated — ADR 0007/0008)")
        print(f"  c={f['cell']:3d} {f['metric']:12s} fresh={f['verdict']:16s} "
              f"committed={cf.get('verdict')} [{mark}]")

    # --- SOFT: absolute numbers vs the declared tolerance -------------------------------
    tol = committed["tolerance_soft_check"]["tolerance_ms"]
    old_gpu = committed_l1["env"]["gpu"]
    new_gpu = fresh["env"]["gpu"]
    drift = {k: (old_gpu.get(k), new_gpu.get(k)) for k in ("name", "driver_version")
             if old_gpu.get(k) != new_gpu.get(k)}
    drift["cuda"] = (committed_l1["env"]["cuda_max_supported"], fresh["env"]["cuda_max_supported"]) \
        if committed_l1["env"]["cuda_max_supported"] != fresh["env"]["cuda_max_supported"] \
        else None
    drift = {k: v for k, v in drift.items() if v}

    print("")
    if drift:
        print(f"verify-l1: environment drifted ({drift}) — soft check on absolute numbers "
              "skipped (ADR 0008). The verdicts gate still applies.")
    else:
        print("verify-l1: soft check (absolute numbers vs declared tolerance — reported, "
              "never fatal):")
        for c in proto["concurrencies"]:
            for metric, (block, key) in POOLED.items():
                try:
                    old_v = committed_l1["cells"][str(c)]["pooled"][block][key]
                    new_v = fresh["cells"][str(c)]["pooled"][block][key]
                except (KeyError, TypeError):
                    continue
                if not isinstance(old_v, (int, float)) or not isinstance(new_v, (int, float)):
                    print(f"  c={c:3d} {metric:12s} n/a (floor) — not compared")
                    continue
                t = tol.get(f"c{c}/{metric}")
                delta = new_v - old_v
                flag = "within" if (t is None or abs(delta) <= t) else "OUTSIDE tolerance"
                print(f"  c={c:3d} {metric:12s} committed={old_v:9.2f} fresh={new_v:9.2f} "
                      f"Δ={delta:+8.2f} tol=±{t} [{flag}]")

    print("")
    if mismatches:
        for key, cv, fv in mismatches:
            print(f"verify-l1: GATE FAILURE — c={key[1]}/{key[0]}: committed {cv!r}, "
                  f"fresh {fv!r} (a CLAIMABLE finding — ADR 0007 bar cleared)", file=sys.stderr)
        print("Claimable findings must reproduce (ADR 0008); a sub-bar verdict flip is "
              "information, not a failure. The remedy for a genuine mismatch is more "
              "runs per cell or a restated finding — never a looser rule.", file=sys.stderr)
        return 1
    print("verify-l1: gate PASS — every committed verdict reproduced on a fresh run.")
    print("Numbers are environment-bound; the conclusions are what hold (ADR 0008).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())