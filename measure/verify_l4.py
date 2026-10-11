"""The ADR 0008 gate for L4: re-run measure_l4 into scratch and reproduce the findings.

Same gate shape as L1 (measure.verify_l1), adapted to L4's two-armed experiment
(ADR 0009):

  HARD: every **claimable** finding must reproduce — a committed claimable finding that
  goes missing, changes verdict, or a new one appearing on the fresh run fails the
  gate. Claimability per ADR 0007 (|effect| >= 10x the committed instrument p99 error).
  Sub-bar verdicts sit at the instrument's resolution and flip between fresh runs by
  design (observed at L1: c=1 TTFT, 0.012 ms) — recorded as information, never gated.
  Throughput has no bar and is always information. A gap cell whose span coverage fell
  under the committed floor is flagged INVALID, never gated (ADR 0009: coverage is
  reported next to every number, and a low cell is flagged, not hidden).

  SOFT: absolute medians vs the tolerance declared at measure time (from the base
  arm's across-run spread, before any fresh comparison). Reported, never fatal, and
  skipped on environment drift — including image digest drift, which `latest`-tagged
  Phoenix/collector images make a real possibility.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from measure.measure_l0 import REPO
from measure.measure_l4 import run_level

POOLED = {
    "ttft_p50_ms": ("ttft_ms", "p50"),
    "itl_p50_ms": ("itl_event_ms", "p50"),
    "tpot_p50_ms": ("tpot_ms", "p50"),
}


def main() -> int:
    ap = argparse.ArgumentParser(prog="verify-l4", description=__doc__.splitlines()[0])
    ap.add_argument("--endpoint", default="http://localhost:8000/v1/chat/completions")
    ap.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--revision", default="989aa7980e4cf806f80c7fef2b1adb7bc71aa306")
    ap.add_argument("--scratch", default="tmp/verify-l4-otel")
    ap.add_argument("--findings", default="results/l4-otel/findings.json")
    ap.add_argument("--bars", default="results/instrument/validation.json")
    cfg = ap.parse_args()

    findings_path = (REPO / cfg.findings).resolve()
    if not findings_path.exists():
        print(f"verify-l4: no committed findings at {findings_path} — run and commit "
              "`make measure LEVEL=l4-otel` first.", file=sys.stderr)
        return 2
    committed = json.loads(findings_path.read_text(encoding="utf-8"))
    l4_path = findings_path.parent / "l4.json"
    committed_l4 = json.loads(l4_path.read_text(encoding="utf-8"))
    proto = committed_l4["protocol"]

    scratch = (REPO / cfg.scratch).resolve()
    if scratch.exists():
        shutil.rmtree(scratch)

    print(f"verify-l4: re-measuring both arms with the committed protocol "
          f"(seed={proto['seed']}, c={proto['concurrencies']}, repeats={proto['repeats']}, "
          f"arms={proto['arms']}) into {scratch} — results/ is never touched",
          file=sys.stderr)
    fresh = run_level(scratch, endpoint=cfg.endpoint, model=cfg.model,
                      revision=cfg.revision, fixture=proto["fixture"],
                      concs=proto["concurrencies"], repeats=proto["repeats"],
                      seed=proto["seed"], warmup_requests=proto["warmup_requests"],
                      warmup_concurrency=proto["warmup_concurrency"],
                      baseline_bars=(REPO / cfg.bars).resolve())

    # --- HARD: claimable findings must reproduce (ADR 0008, restricted by ADR 0007) ---
    def claim_key(f):
        return (f["finding"], f["metric"], f["cell"])

    committed_claim = {claim_key(f): f["verdict"] for f in committed["findings"]
                       if f.get("claimable")}
    fresh_claim = {claim_key(f): f["verdict"] for f in fresh["findings"]
                   if f.get("claimable")}
    mismatches = [(k, cv, fresh_claim.get(k, "MISSING in fresh run"))
                  for k, cv in committed_claim.items() if fresh_claim.get(k) != cv]
    mismatches += [(k, "not committed", fv)
                   for k, fv in fresh_claim.items() if k not in committed_claim]

    committed_by_key = {claim_key(f): f for f in committed["findings"]}
    print("")
    print("verify-l4: verdicts (fresh vs committed) — [gated] = claimable (ADR 0007), "
          "[info] = sub-bar, at the instrument's resolution:")
    for f in fresh["findings"]:
        key = claim_key(f)
        cf = committed_by_key.get(key, {})
        if f.get("claimable"):
            mark = "MISMATCH" if fresh_claim.get(key) != cf.get("verdict") else "OK"
        elif f.get("valid") is False:
            mark = "info(coverage INVALID — flagged, not gated — ADR 0009)"
        elif f.get("bar") is None:
            mark = "info(no instrument bar — throughput, recorded, never gated)"
        else:
            mark = ("ok(sub-bar: flip recorded, not gated — ADR 0007/0008)"
                    if f["verdict"] == cf.get("verdict") else
                    "info(sub-bar FLIP recorded, not gated — ADR 0007/0008)")
        print(f"  [{f['finding']}] c={f['cell']:3d} {f['metric']:12s} "
              f"fresh={f['verdict']:16s} committed={cf.get('verdict')} [{mark}]")

    # --- SOFT: absolute numbers vs the declared tolerance ----------------------------
    tol = committed["tolerance_soft_check"]["tolerance_ms"]
    old_env, new_env = committed_l4["env"], fresh["env"]
    drift = {}
    for img in ("vllm", "phoenix", "otel_collector"):
        if old_env["images"][img]["digest"] != new_env["images"][img]["digest"]:
            drift[img] = (old_env["images"][img]["digest"],
                          new_env["images"][img]["digest"])
    if old_env["gpu"].get("name") != new_env["gpu"].get("name") or \
            old_env["gpu"].get("driver_version") != new_env["gpu"].get("driver_version"):
        drift["gpu"] = (old_env["gpu"].get("name"),
                        old_env["gpu"].get("driver_version"))
    print("")
    if drift:
        print(f"verify-l4: environment drifted ({drift}) — soft check on absolute "
              "numbers skipped (ADR 0008). The verdicts gate still applies.")
    else:
        print("verify-l4: soft check (absolute numbers vs declared tolerance — "
              "reported, never fatal):")
        for arm in proto["arms"]:
            for c in proto["concurrencies"]:
                for metric, (block, key) in POOLED.items():
                    try:
                        old_v = committed_l4["cells"][arm][str(c)]["pooled"][block][key]
                        new_v = fresh["cells"][arm][str(c)]["pooled"][block][key]
                    except (KeyError, TypeError):
                        continue
                    t = tol.get(f"c{c}/{metric}")
                    if t is None or not isinstance(old_v, (int, float)) \
                            or not isinstance(new_v, (int, float)):
                        continue
                    delta = new_v - old_v
                    flag = "within" if abs(delta) <= t else "OUTSIDE tolerance"
                    print(f"  {arm:8s} c={c:3d} {metric:12s} committed={old_v:9.2f} "
                          f"fresh={new_v:9.2f} Δ={delta:+8.2f} tol=±{t} [{flag}]")

    if mismatches:
        print("\nGATE FAILURE — claimable findings did not reproduce (ADR 0008):",
              file=sys.stderr)
        for (kind, metric, cell), cv, fv in mismatches:
            print(f"  [{kind}] c={cell} {metric}: committed={cv} fresh={fv}",
                  file=sys.stderr)
        return 1
    print("\nverify-l4: gate PASS — every committed claimable finding reproduced on a "
          "fresh run.\nNumbers are environment-bound; the conclusions are what hold "
          "(ADR 0008).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
