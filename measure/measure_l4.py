"""Run the L4 measurement — both arms — and regenerate results/l4-otel/ (ADR 0009).

L4 is the same experiment applied to tracing, in two arms over the identical engine
(vLLM v0.23.0, same image/model/revision/flags as L0 — stack/l4-otel/docker-compose.*):

  base     no exporter configured (docker-compose.base.yaml)
  tracing  --otlp-traces-endpoint → otel collector → Phoenix, all running for real
           (docker-compose.tracing.yaml); the collector also writes every span to
           tmp/l4-otel/spans/spans.json, the pairing source for Finding 2

One GPU means the arms run SEQUENTIALLY (base first, then tracing) — the arm order is a
known confound, recorded in the protocol block, not hidden. Within each arm the matrix
is the L0/L1 protocol (same fixture, seed, interleaved cell order, discarded counted
warmup). `loadgen/` sends traceparent + x-request-id in BOTH arms (ADR 0009), so the
request bytes differ only by the fresh per-request traceparent.

Findings (declared before any data, ADR 0009; claimability per ADR 0007):

  Finding 1 — tracing overhead: per cell x metric, the tracing arm's per-run medians
  vs the base arm's get the ADR 0008 sign rule; claimable only if |effect| >= the
  cell's instrument bar (10x committed p99 error, results/instrument/). Throughput has
  no instrument bar (the bars are latency ms), so its verdict is recorded as
  information only. 'indistinguishable' reads "no overhead detectable at this
  resolution", never "zero overhead".

  Finding 2 — the span-to-client TTFT gap: per request in the tracing arm,
  gap = client_ttft - span_ttft, paired by trace id (the join key; x-request-id
  collides across runs — it is the fixture index — and is checked for agreement, not
  used to join). Coverage = matched/ok per cell, with the floor fixed below; a cell
  below the floor is flagged invalid, not hidden.

Span attribute units: vLLM v0.23.0's RequestStateStats timestamps are perf_counter
seconds, so gen_ai.latency.* arrive in seconds and are x1000 here; the 2026-10-11
smoke run confirmed the alignment against the client's ms.

Writes results/l4-otel/l4.json, findings.json, env.md, runs/{arm}/, and the
committed example trace (one paired client record + span, ADR 0009's deliverable,
extracted from the collector's scratch file as data).
Overwrites its own outputs only. `measure.verify_l4` re-runs this into scratch and
gates on the claimable findings.
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import shutil
import statistics
import sys
import time
from pathlib import Path

from measure.measure_l0 import (REPO, cell_block, gpu_snapshot, http_get,
                                parse_metrics, pool_run, run_loadgen, sh)
from measure.measure_l1 import load_bars, per_run_metric_values

# --- the L4 protocol, fixed before any data (ADR 0009/0007) ------------------------------
ARMS = ("base", "tracing")  # run in this order; the order is a recorded confound
COMPOSE_DIR = REPO / "stack" / "l4-otel"
COMPOSE_FILE = {arm: COMPOSE_DIR / f"docker-compose.{arm}.yaml" for arm in ARMS}
COMPOSE_PROJECT = {arm: f"l4-otel-{arm}" for arm in ARMS}
SPAN_FILE = REPO / "tmp" / "l4-otel" / "spans" / "spans.json"
SPAN_NAME = "llm_request"
# ADR 0009: "the floor below which a cell is invalid is declared in a committed file
# before L4 data is taken". It is this constant: 98% of a cell's ok requests must have
# exactly one matched span, else the cell's gap finding is invalid (flagged, never hidden).
COVERAGE_FLOOR = 0.98
GAP_METRIC = "gap_ttft_ms"  # client ttft − span ttft, per matched request
REQS_PER_S = "reqs_per_s"

CLAIMABILITY_RULE = (
    "ADR 0007: a finding is claimable only if |effect| >= 10x the cell's committed "
    "instrument p99 error (results/instrument/validation.json). Every verdict is "
    "still computed by the mechanical ADR 0008 sign rule; sub-bar verdicts sit at "
    "the instrument's resolution, flip between fresh runs by design (2026-10-10: "
    "L1 c=1 TTFT, 0.012 ms) and are recorded as information, never gated. "
    "L4: the bar applies to the overhead finding (tracing vs base latency medians) "
    "and to the gap finding (client vs span TTFT medians); throughput has no bar "
    "(the instrument errors are latency ms) and is information only. A gap cell "
    f"with coverage < {COVERAGE_FLOOR:.0%} is invalid — flagged, never gated.")


def compose(arm: str, *args: str, timeout: int = 300) -> str:
    """Strict compose: a failed bring-up raises instead of measuring the wrong arm —
    2026-10-11, the smoke's tracing arm still held :8000 when the base arm came up and
    the base warmup silently ran against the tracing engine."""
    import subprocess
    r = subprocess.run(["docker", "compose", "-p", COMPOSE_PROJECT[arm],
                        "-f", str(COMPOSE_FILE[arm]), *args],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"docker compose -p {COMPOSE_PROJECT[arm]} {args} failed:\n"
                           f"{(r.stderr or r.stdout)[-800:]}")
    return r.stdout.strip()


def wait_ready(endpoint_root: str, timeout_s: int = 900) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if http_get(f"{endpoint_root}/v1/models", timeout=5) is not None:
            return True
        time.sleep(2)
    return False


def norm_trace_id(v):
    """OTLP/JSON encodes bytes fields (traceId) base64; loadgen's trace ids are hex.
    Normalise both spellings of the same 16 bytes; anything else passes through."""
    if not isinstance(v, str):
        return v
    if len(v) == 32 and all(c in "0123456789abcdefABCDEF" for c in v):
        return v.lower()
    try:
        dec = base64.b64decode(v, validate=True)
        if len(dec) == 16:
            return dec.hex()
    except Exception:  # noqa: BLE001 — pass through, the smoke run surfaces the format
        pass
    return v.lower()


def parse_spans(path: Path) -> list[dict]:
    """llm_request spans from the collector's NDJSON ExportTraceServiceRequests."""
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn tail line while the collector is mid-write
        for rs in doc.get("resourceSpans", []):
            for ss in rs.get("scopeSpans", []):
                for sp in ss.get("spans", []):
                    if sp.get("name") != SPAN_NAME:
                        continue
                    attrs = {}
                    for kv in sp.get("attributes", []):
                        v = kv.get("value", {})
                        attrs[kv["key"]] = next(
                            (v[k] for k in ("stringValue", "doubleValue", "intValue",
                                           "boolValue") if k in v), None)
                    ttft_s = attrs.get("gen_ai.latency.time_to_first_token")
                    if not isinstance(ttft_s, (int, float)):
                        continue  # no TTFT attribute → unpairable (coverage counts it)
                    out.append({
                        "trace_id": norm_trace_id(sp.get("traceId")),
                        "request_id": attrs.get("gen_ai.request.id"),
                        "ttft_ms": round(float(ttft_s) * 1000.0, 3),  # seconds → ms
                        "e2e_ms": round(float(attrs.get("gen_ai.latency.e2e") or 0) * 1000.0, 3)
                        if isinstance(attrs.get("gen_ai.latency.e2e"), (int, float)) else None,
                        "attributes": attrs,
                        "span": sp,
                    })
    return out


def span_count(path: Path) -> int:
    return len(parse_spans(path))


def settle_spans(path: Path, expected: int, timeout_s: int = 120) -> tuple[int, int]:
    """Wait for the exporter batch to drain: stable count across 2 reads 3s apart."""
    n, prev = span_count(path), -1
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and (n != prev or n < expected):
        prev, n = n, span_count(path)
    return n, prev


def sign_rule(a: list[float], b: list[float]) -> str:
    """ADR 0008 sign rule over two per-run-median samples; direction-neutral tokens."""
    if not a or not b:
        return "indistinguishable"
    if min(a) > max(b):
        return "greater"
    if max(a) < min(b):
        return "less"
    return "indistinguishable"


def image_digest(image: str) -> str:
    d = sh(["docker", "image", "inspect", "--format",
            "{{index .RepoDigests 0}}", image])
    return d.split("@", 1)[1] if "@" in d else "unavailable"


def run_arm(arm: str, out_dir: Path, *, endpoint: str, model: str, fixture: str,
            concs: list[int], repeats: int, seed: int, warmup_requests: int,
            warmup_concurrency: int) -> dict:
    """One arm end-to-end: compose up → warmup → interleaved matrix → compose down."""
    endpoint_root = endpoint.rsplit("/v1/", 1)[0]
    compose(arm, "up", "-d")
    try:
        if not wait_ready(endpoint_root):
            raise SystemExit(f"measure-l4: {arm} arm did not become ready at {endpoint_root} "
                             f"— check: docker compose -p {COMPOSE_PROJECT[arm]} logs")
        server_version = None
        v = http_get(f"{endpoint_root}/version")
        if v:
            try:
                server_version = json.loads(v).get("version")
            except json.JSONDecodeError:
                pass

        runs_dir = out_dir / "runs" / arm
        runs_dir.mkdir(parents=True, exist_ok=True)

        # warmup pass: discarded, counted (same protocol as L0/L1)
        warmup, wrc = run_loadgen(endpoint=endpoint, model=model, fixture=fixture,
                                  concurrency=warmup_concurrency, seed=seed,
                                  out=runs_dir / "warmup.json", extra_body=None,
                                  requests=warmup_requests, level="l4-otel")
        warmup_block = ({"requests_ok": warmup["summary"]["requests_ok"],
                         "requests_error": warmup["summary"]["requests_error"],
                         "requests_refused": warmup["summary"]["requests_refused"]}
                        if warmup else {"error": f"client exit {wrc} or no output"})

        # spans.json is never reset mid-flight (the collector holds it open); pairing
        # is scoped by per-run trace ids, so warmup spans and spans from any earlier
        # verify run in the same file can never join this matrix's records.

        cells = [(c, r) for c in concs for r in range(repeats)]
        random.Random(seed).shuffle(cells)
        pooled: dict[int, list[dict]] = {c: [] for c in concs}
        run_index, failures = [], 0
        prev_counter_total, server_restarted = None, False
        for i, (c, r) in enumerate(cells):
            pre_counters = parse_metrics(http_get(f"{endpoint_root}/metrics"))
            out = runs_dir / f"c{c}_r{r}.json"
            run, rc = run_loadgen(endpoint=endpoint, model=model, fixture=fixture,
                                  concurrency=c, seed=seed, out=out, extra_body=None,
                                  level="l4-otel")
            post_counters = parse_metrics(http_get(f"{endpoint_root}/metrics"))
            entry = {"arm": arm, "cell": [c, r], "file": f"runs/{arm}/c{c}_r{r}.json",
                     "client_exit": rc, "gpu_pre": gpu_snapshot(), "gpu_post": gpu_snapshot(),
                     "counters_pre": pre_counters, "counters_post": post_counters}
            if run is None:
                entry["error"] = "client produced no output"
                failures += 1
            else:
                p = pool_run(run)
                pooled[c].append(p)
                entry.update({"n_ok": p["n_ok"], "n_error": p["n_error"],
                              "wall_s": p["wall_s"]})
                total = sum(v for k, v in post_counters.items()
                            if "num_requests_started" in k)
                if prev_counter_total is not None and total < prev_counter_total:
                    server_restarted = True
                prev_counter_total = total
            run_index.append(entry)
            print(f"measure-l4 [{arm}] [{i + 1}/{len(cells)}] c={c} r={r}: "
                  + (f"ok={entry.get('n_ok')} err={entry.get('n_error')} "
                     f"wall={entry.get('wall_s')}s" if run else "FAILED"),
                  file=sys.stderr)

        expected = sum(p["n_ok"] for ps in pooled.values() for p in ps)
        return {"arm": arm, "server_version": server_version, "warmup": warmup_block,
                "cells": {str(c): cell_block(pooled[c]) for c in concs if pooled[c]},
                "pooled": pooled, "runs": run_index, "failures": failures,
                "server_restarted": server_restarted, "expected_spans": expected}
    finally:
        compose(arm, "down", timeout=600)


def pair_gap(arm_runs_dir: Path, spans: list[dict],
             concs: list[int]) -> tuple[dict[int, dict], dict]:
    """Finding 2's raw material: per-cell matched (client, span) TTFT pairs by trace id.

    The join key is the trace id — x-request-id is the fixture index (1..N per run), so
    it collides across runs and is only checked for agreement once paired.
    """
    by_trace: dict[str, dict] = {}
    for sp in spans:
        by_trace.setdefault(sp["trace_id"], []).append(sp)

    clients: list[dict] = []
    for c in concs:
        r = 0
        while (f := arm_runs_dir / f"c{c}_r{r}.json").exists():
            d = json.loads(f.read_text(encoding="utf-8"))
            for rec in d["requests"]:
                if rec["status"] != "ok" or rec.get("ttft_ms") is None:
                    continue
                # v0.23.0 sets gen_ai.request.id to the ENGINE's id (chatcmpl-N),
                # not the client's X-Request-Id (that header is response-side only);
                # the client records the same id from the SSE chunks (response_id),
                # so the cross-check is engine-id vs engine-id, seen by both sides.
                clients.append({"cell": c, "repeat": r, "trace_id": rec.get("trace_id"),
                                "response_id": rec.get("response_id"),
                                "client_ttft_ms": rec["ttft_ms"]})
            r += 1

    per_cell: dict[int, dict] = {c: {"n_ok": 0, "matched": 0, "gaps_ms": [],
                                     "span_ttfts_ms": [], "client_ttfts_ms": [],
                                     "duplicate_trace_ids": 0, "rid_mismatch": 0,
                                     "per_run": {}}
                                 for c in concs}
    unmatched_examples: list[dict] = []
    for cl in clients:
        cell = per_cell[cl["cell"]]
        cell["n_ok"] += 1
        cands = by_trace.get(cl["trace_id"])
        if not cands:
            if len(unmatched_examples) < 5:
                unmatched_examples.append(cl)
            continue
        if len(cands) > 1:
            cell["duplicate_trace_ids"] += 1
        sp = cands[0]
        if sp["request_id"] is not None and cl["response_id"] is not None \
                and sp["request_id"] != cl["response_id"]:
            cell["rid_mismatch"] += 1  # trace ids agree, engine ids disagree: bad join
            continue
        gap = cl["client_ttft_ms"] - sp["ttft_ms"]
        cell["matched"] += 1
        cell["gaps_ms"].append(gap)
        cell["span_ttfts_ms"].append(sp["ttft_ms"])
        cell["client_ttfts_ms"].append(cl["client_ttft_ms"])
        pr = cell["per_run"].setdefault(cl["repeat"], {"gaps_ms": [], "span_ttfts_ms": [],
                                                       "client_ttfts_ms": []})
        pr["gaps_ms"].append(gap)
        pr["span_ttfts_ms"].append(sp["ttft_ms"])
        pr["client_ttfts_ms"].append(cl["client_ttft_ms"])

    for c, cell in per_cell.items():
        n = cell["n_ok"]
        cell["coverage"] = round(cell["matched"] / n, 4) if n else 0.0
        cell["valid"] = n > 0 and cell["coverage"] >= COVERAGE_FLOOR
    return per_cell, {"n_clients": len(clients), "unmatched_examples": unmatched_examples}


def build_findings(base: dict, tracing: dict, gap_cells: dict[int, dict],
                   concs: list[int], bars: dict[int, float]) -> tuple[list[dict], dict]:
    from loadgen.client import percentile

    findings: list[dict] = []
    tolerance: dict[str, float] = {}

    def note(kind: str, metric: str, cell: int, verdict: str, effect, bar, claimable: bool,
             extra: dict) -> dict:
        return {
            "finding": kind, "metric": metric, "cell": cell, "verdict": verdict,
            "effect": effect, "bar": bar, "claimable": claimable,
            **extra,
            "claimability_rule": CLAIMABILITY_RULE,
        }

    # --- Finding 1: tracing overhead (base arm = the control) --------------------------
    for c in concs:
        base_run = per_run_metric_values(base["pooled"][c])
        tracing_run = per_run_metric_values(tracing["pooled"][c])
        # request throughput (ADR 0009 metric; no instrument bar — bars are latency ms)
        base_tput = [round(p["n_ok"] / p["wall_s"], 3) for p in base["pooled"][c]]
        tracing_tput = [round(p["n_ok"] / p["wall_s"], 3) for p in tracing["pooled"][c]]
        for metric in ("ttft_p50_ms", "itl_p50_ms", "tpot_p50_ms", REQS_PER_S):
            a = (tracing_tput if metric == REQS_PER_S else tracing_run[metric])
            b = (base_tput if metric == REQS_PER_S else base_run[metric])
            tok = sign_rule(a, b)
            verdict = {"greater": "tracing>base", "less": "tracing<base"}.get(
                tok, "indistinguishable")
            a_med = statistics.median(a) if a else None
            b_med = statistics.median(b) if b else None
            effect = round(a_med - b_med, 3) if a_med is not None and b_med is not None else None
            bar = None if metric == REQS_PER_S else bars.get(c)
            claimable = bool(bar is not None and effect is not None and abs(effect) >= bar)
            direction = ("higher latency under tracing" if metric != REQS_PER_S
                         else "lower throughput under tracing")
            readings = {
                "tracing>base": f"tracing arm slower ({direction})",
                "tracing<base": f"tracing arm faster ({direction} — check for a confound)",
            }
            findings.append(note(
                "tracing_overhead", metric, c, verdict, effect, bar, claimable, {
                    "conditions": {"a": "l4-otel tracing arm (this run)",
                                   "b": "l4-otel base arm (this run, same day, ran first)"},
                    "tracing_values": [round(x, 3) for x in a],
                    "base_values": [round(x, 3) for x in b],
                    "units": "ms" if metric != REQS_PER_S else "req/s",
                    "reading": (f"{readings.get(verdict, verdict)} — claimable (|effect| "
                                f"{abs(effect)} >= bar {bar} ms)" if claimable else
                                (f"{verdict} at the instrument's resolution — NOT claimable "
                                 f"(|effect| {abs(effect)} < bar {bar} ms); recorded, "
                                 "flips between fresh runs by design" if bar is not None
                                 else f"{verdict} — information only: request throughput has "
                                      "no instrument bar (ADR 0007 bars are latency ms)")),
                }))
            # soft-check tolerance, declared at measure time from the BASE arm's spread
            if metric != REQS_PER_S:
                tolerance[f"c{c}/{metric}"] = round(max(b) - min(b), 3)

    # --- Finding 2: the span-to-client TTFT gap (tracing arm only) --------------------
    for c in concs:
        cell = gap_cells[c]
        pr = cell["per_run"]
        gap_medians = [percentile(v["gaps_ms"], 50) for v in pr.values() if v["gaps_ms"]]
        span_medians = [percentile(v["span_ttfts_ms"], 50) for v in pr.values()
                        if v["span_ttfts_ms"]]
        client_medians = [percentile(v["client_ttfts_ms"], 50) for v in pr.values()
                          if v["client_ttfts_ms"]]
        tok = sign_rule(client_medians, span_medians)
        verdict = {"greater": "client>span", "less": "client<span"}.get(
            tok, "indistinguishable")
        effect = (round(statistics.median(client_medians) - statistics.median(span_medians), 3)
                  if client_medians and span_medians else None)
        bar = bars.get(c)
        valid = cell["valid"]
        claimable = bool(valid and effect is not None and bar is not None
                         and abs(effect) >= bar)
        pred_ok = verdict == "client>span" or verdict == "indistinguishable"
        findings.append(note(
            "span_client_gap", GAP_METRIC, c, verdict, effect, bar, claimable, {
                "conditions": {"a": "client TTFT (loadgen, ms)",
                               "b": "span gen_ai.latency.time_to_first_token (engine, ms)"},
                "pairing": "by trace id (join key); x-request-id checked for agreement "
                           "(it is the fixture index, so it collides across runs)",
                "n_ok": cell["n_ok"], "n_matched": cell["matched"],
                "coverage": cell["coverage"], "coverage_floor": COVERAGE_FLOOR,
                "valid": valid, "duplicate_trace_ids": cell["duplicate_trace_ids"],
                "rid_mismatch": cell["rid_mismatch"],
                "gap_ms": {"p50": percentile(cell["gaps_ms"], 50),
                           "min": round(min(cell["gaps_ms"]), 3),
                           "max": round(max(cell["gaps_ms"]), 3)} if cell["gaps_ms"] else None,
                "prediction": "client > span for every cell (ADR 0009, declared before "
                              "data): the client interval strictly contains the span's",
                "prediction_holds": pred_ok,
                "reading": (f"{verdict} — the unspanned part of the path (HTTP frontend, "
                            "event loop, SSE delivery) adds {effect} ms at the median; "
                            "claimable (|effect| >= bar {bar} ms)" if claimable else
                            (f"INVALID cell (coverage {cell['coverage']:.1%} < floor "
                             f"{COVERAGE_FLOOR:.0%}) — spans dropped, biased towards the "
                             "busiest requests (ADR 0009); flagged, not hidden"
                             if not valid else
                             f"{verdict} at the instrument's resolution — NOT claimable "
                             f"(|effect| {abs(effect)} < bar {bar} ms); the paired gap "
                             "series is still reported per cell below")),
                }))

    soft = {
        "declared": time.strftime("%Y-%m-%dT%H:%M:%S%z") + " (at measure time, before any "
                   "fresh-run comparison — ADR 0008)",
        "derivation": "the BASE arm's across-run spread (max − min of its per-run "
                      "medians), per cell and latency metric. A fresh tracing-arm "
                      "median outside this band is reported, never fatal.",
        "tolerance_ms": tolerance,
    }
    return findings, soft


def findings_document(generated: str, findings: list[dict], soft: dict,
                      coverage: dict[int, dict]) -> dict:
    return {
        "schema": "l4-findings/1",
        "generated": generated,
        "level": "l4-otel",
        "verdict_rule": "the ADR 0008 sign rule over per-run medians (min(a) > max(b) / "
                         "max(a) < min(b)), whatever R each side ran; 'indistinguishable' "
                         "never means 'equal'",
        "claimability_rule": CLAIMABILITY_RULE,
        "coverage_per_cell": {str(c): {"n_ok": v["n_ok"], "matched": v["matched"],
                                       "coverage": v["coverage"], "valid": v["valid"]}
                              for c, v in coverage.items()},
        "claimable_findings": [f for f in findings if f["claimable"]],
        "findings": findings,
        "tolerance_soft_check": soft,
    }


def run_level(out_dir: Path, *, endpoint: str, model: str, revision: str, fixture: str,
              concs: list[int], repeats: int, seed: int, warmup_requests: int,
              warmup_concurrency: int, baseline_bars: Path) -> dict:
    bars = load_bars(baseline_bars, concs)
    out_dir.mkdir(parents=True, exist_ok=True)

    arms: dict[str, dict] = {}
    for arm in ARMS:  # base first, then tracing — the order is a recorded confound
        print(f"measure-l4: bringing up the {arm} arm", file=sys.stderr)
        arms[arm] = run_arm(arm, out_dir, endpoint=endpoint, model=model, fixture=fixture,
                            concs=concs, repeats=repeats, seed=seed,
                            warmup_requests=warmup_requests,
                            warmup_concurrency=warmup_concurrency)

    # spans: settle, then pair (tracing arm only — the base arm exports nothing)
    expected = arms["tracing"]["expected_spans"]
    n, prev = settle_spans(SPAN_FILE, expected)
    spans = parse_spans(SPAN_FILE)
    gap_cells, pair_stats = pair_gap(out_dir / "runs" / "tracing", spans, concs)
    print(f"measure-l4: spans settled at {len(spans)} llm_request spans "
          f"(expected ≈ {expected}, stable reads {prev}/{n})", file=sys.stderr)
    for c in concs:
        print(f"  c={c:3d} coverage={gap_cells[c]['coverage']:.1%} "
              f"({gap_cells[c]['matched']}/{gap_cells[c]['n_ok']})", file=sys.stderr)

    findings, soft = build_findings(arms["base"], arms["tracing"], gap_cells, concs, bars)

    # the committed example trace: the first matched pair of c=concs[0] r=0, extracted
    # as data (ADR 0009's committed trace) — client record + its raw llm_request span.
    example = None
    first_run = out_dir / "runs" / "tracing" / f"c{concs[0]}_r0.json"
    if first_run.exists():
        d = json.loads(first_run.read_text(encoding="utf-8"))
        span_by_trace = {sp["trace_id"]: sp for sp in spans}
        for rec in d["requests"]:
            sp = span_by_trace.get(rec.get("trace_id"))
            if rec["status"] == "ok" and sp and rec.get("ttft_ms") is not None:
                example = {
                    "note": "one matched pair, committed as the L4 example trace "
                            "(ADR 0009); the raw span is vLLM's llm_request export",
                    "cell": [concs[0], 0],
                    "client": {k: rec.get(k) for k in
                               ("id", "trace_id", "response_id", "ttft_ms", "e2e_ms",
                                "tokens_in", "tokens_out")},
                    "span": {"name": "llm_request", "traceId": sp["trace_id"],
                             "attributes": sp["attributes"], "span": sp["span"]},
                    "gap_ms": round(rec["ttft_ms"] - sp["ttft_ms"], 3),
                }
                break

    env = {
        "host": sh(["hostname", "-s"]) or "unknown",
        "kernel": sh(["uname", "-r"]) or "unknown",
        "gpu": gpu_snapshot(),
        "server_version": arms["tracing"]["server_version"],
        "images": {
            "vllm": {"image": "vllm/vllm-openai:v0.23.0",
                     "digest": image_digest("vllm/vllm-openai:v0.23.0")},
            "phoenix": {"image": "arizephoenix/phoenix:latest",
                        "digest": image_digest("arizephoenix/phoenix:latest")},
            "otel_collector": {
                "image": "otel/opentelemetry-collector-contrib:latest",
                "digest": image_digest("otel/opentelemetry-collector-contrib:latest")},
        },
        "core_pinning": {"engine": "cpuset 0-15", "phoenix": "cpuset 16-23",
                         "collector": "cpuset 24-25",
                         "why": "ADR 0009: the collector and Phoenix run on a separate "
                                "core set from the serving process, so the cost measured "
                                "is the engine's"},
        "tracing_path": "vLLM --otlp-traces-endpoint → otel collector (grpc 4317) → "
                        "Phoenix (grpc 4317) + scratch spans.json (the pairing source)",
        "model": model, "model_revision": revision, "fixture": fixture,
        "arm_order": list(ARMS),
        "arm_order_confound": "one GPU forces sequential arms; the base arm ran first, "
                              "so time-of-day drift lands in the arms' difference",
    }

    generated = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    result = {
        "schema": "l4-results/1",
        "generated": generated,
        "level": "l4-otel",
        "protocol": {
            "fixture": fixture, "seed": seed, "concurrencies": concs, "repeats": repeats,
            "warmup_requests": warmup_requests, "warmup_concurrency": warmup_concurrency,
            "arms": list(ARMS), "arm_order": list(ARMS),
            "coverage_floor": COVERAGE_FLOOR,
            "span_units": "vLLM v0.23.0 emits gen_ai.latency.* in seconds; x1000 to ms",
        },
        "env": env,
        "warmup": {arm: arms[arm]["warmup"] for arm in ARMS},
        "cells": {arm: arms[arm]["cells"] for arm in ARMS},
        "gap": {str(c): {k: v for k, v in gap_cells[c].items() if k != "per_run"}
                for c in concs},
        "gap_per_run": {str(c): {str(r): {"gap_p50_ms": round(statistics.median(v["gaps_ms"]), 3)
                                          if v["gaps_ms"] else None,
                                          "n": len(v["gaps_ms"])}
                                 for r, v in gap_cells[c]["per_run"].items()}
                        for c in concs},
        "pairing": pair_stats,
        "spans_observed": len(spans), "spans_expected": expected,
        "runs": [e for arm in ARMS for e in arms[arm]["runs"]],
        "server_restarted": any(arms[arm]["server_restarted"] for arm in ARMS),
        "trace_example": example,
        "findings": findings,
        "findings_headline": {
            "verdicts": sorted({f["verdict"] for f in findings}),
            "claimable": sorted({(f["finding"], f["metric"], f["cell"])
                                for f in findings if f["claimable"]}),
            "reading": "Finding 1: tracing arm vs base arm, same day, base first. "
                       "Finding 2: client TTFT vs engine span TTFT, paired by trace id. "
                       "Claimability per ADR 0007 (|effect| >= 10x instrument p99 error); "
                       "'indistinguishable' means not separated by the runs, never 'equal'; "
                       "throughput is information only (no bar).",
        },
        "tolerance_soft_check": soft,
        "failures": sum(arms[arm]["failures"] for arm in ARMS),
    }
    (out_dir / "l4.json").write_text(json.dumps(result, indent=1) + "\n")
    (out_dir / "findings.json").write_text(json.dumps(
        findings_document(generated, findings, soft, gap_cells), indent=1) + "\n")
    if example:
        (out_dir / "trace-example.json").write_text(json.dumps(example, indent=1) + "\n")

    g = env["gpu"]
    env_md = f"""# L4 — environment

Generated {generated} by `python -m measure.measure_l4` (`make measure LEVEL=l4-otel`).

| field | value |
|---|---|
| host | {env['host']} |
| kernel | {env['kernel']} |
| GPU | {g.get('name', 'unavailable')} · driver {g.get('driver_version', 'unavailable')} |
| server | vLLM {env['server_version']} ({env['images']['vllm']['image']}) |
| tracing | {env['tracing_path']} |
| phoenix | {env['images']['phoenix']['image']} @ {env['images']['phoenix']['digest']} |
| collector | {env['images']['otel_collector']['image']} @ {env['images']['otel_collector']['digest']} |
| core pinning | engine {env['core_pinning']['engine']}, phoenix {env['core_pinning']['phoenix']}, collector {env['core_pinning']['collector']} |
| model | {env['model']} @ {env['model_revision']} |
| fixture | {env['fixture']} · seed {seed} · R={repeats} |
| arm order | {' → '.join(env['arm_order'])} (one GPU; the order is a recorded confound) |

Raw runs in [runs/](runs/) (per arm); the assembled matrix, findings and example trace
in [l4.json](l4.json), [findings.json](findings.json) and [trace-example.json](trace-example.json).
"""
    (out_dir / "env.md").write_text(env_md)

    print(f"measure-l4: both arms done ({result['failures']} failures) "
          f"→ {out_dir / 'l4.json'}")
    for f in findings:
        bar = f["bar"]
        print(f"  [{f['finding']}] c={f['cell']:3d} {f['metric']:12s} "
              f"verdict={f['verdict']:16s} effect={f['effect']} "
              f"bar={bar if bar is not None else 'n/a'} "
              f"claimable={f['claimable']}")
    if result["server_restarted"]:
        print("WARNING: a server restarted between repeats.", file=sys.stderr)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(prog="measure-l4", description=__doc__.splitlines()[0])
    ap.add_argument("--endpoint", default="http://localhost:8000/v1/chat/completions")
    ap.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--revision", default="989aa7980e4cf806f80c7fef2b1adb7bc71aa306")
    ap.add_argument("--fixture", default="loadgen/fixtures/distinct.jsonl")
    ap.add_argument("--out-dir", default="results/l4-otel")
    ap.add_argument("--concurrencies", default="1,4,16,64")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--warmup-requests", type=int, default=32)
    ap.add_argument("--warmup-concurrency", type=int, default=4)
    ap.add_argument("--bars", default="results/instrument/validation.json",
                    help="the committed ADR 0007 instrument errors the claimability "
                         "bars are derived from")
    cfg = ap.parse_args()

    out_dir = (REPO / cfg.out_dir).resolve()
    runs_dir = out_dir / "runs"
    if runs_dir.exists():
        shutil.rmtree(runs_dir)
    runs_dir.mkdir(parents=True)

    result = run_level(out_dir, endpoint=cfg.endpoint, model=cfg.model,
                       revision=cfg.revision, fixture=cfg.fixture,
                       concs=[int(x) for x in cfg.concurrencies.split(",")],
                       repeats=cfg.repeats, seed=cfg.seed,
                       warmup_requests=cfg.warmup_requests,
                       warmup_concurrency=cfg.warmup_concurrency,
                       baseline_bars=(REPO / cfg.bars).resolve())
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
