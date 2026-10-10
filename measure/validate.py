"""Validate loadgen against the mock server with a logged schedule (ADR 0007).

Orchestrates, in one invocation: start the mock (separate process) → run the client at each
concurrency (and once under deliberate extra CPU load on the client's core) → compare the
client's per-event record against the mock's logged actual write times.

The comparison:
- error is ``client_t − mock_actual_write_t`` per event, both on the same host monotonic clock;
- the mock's own scheduling jitter (actual − intended) is reported separately, so a noisy mock
  is not mistaken for a noisy client;
- counts (events, per-event tokens, tokens_out) must match the schedule exactly — a miscount is
  a FAILURE, not a margin.

The pass threshold is deliberately NOT applied here: per ADR 0007 it stays undefined until L0
produces the smallest between-level effect this repo will report, and it is written down (with
its derivation) before the validation result is called a pass. This tool reports the raw error
distribution; the verdict comes later, on top of it.

Writes ``<out>/validation.json`` (+ ``env.md``). Exit code is non-zero only on count failures
or run errors — timing error alone cannot fail a run whose threshold does not exist yet.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

import aiohttp

from loadgen.client import percentile

REPO = Path(__file__).resolve().parent.parent


def write_fixture(path: Path, n: int) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps({
                "id": f"mock-{i:04d}", "set": "mock", "group": None,
                "prompt": "filler prompt for the mock server: timing is the subject, not text.",
                "max_tokens": 128, "ignore_eos": True,
            }) + "\n")


def start_mock(port: int, log: Path, cfg) -> subprocess.Popen:
    out = open(log.parent / "mock.out", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "measure.mock_sse",
         "--port", str(port), "--schedule-log", str(log),
         "--first-delay-ms", str(cfg.first_delay_ms), "--delta-ms", str(cfg.delta_ms),
         "--events", str(cfg.events), "--tokens-per-event", str(cfg.tokens_per_event)],
        cwd=REPO, stdout=out, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return proc
        except OSError:
            if proc.poll() is not None:
                raise SystemExit(f"mock died at startup; see {log.parent / 'mock.out'}")
            time.sleep(0.1)
    proc.terminate()
    raise SystemExit("mock did not become ready in 15s")


def run_loadgen(port: int, fixture: Path, out: Path, *, concurrency: int, requests: int,
                pin_core: int | None) -> dict | None:
    """One client pass; None means the client produced no usable output."""
    cmd = [sys.executable, "-m", "loadgen.client",
           "--endpoint", f"http://127.0.0.1:{port}/v1/chat/completions",
           "--model", "mock", "--fixture", str(fixture), "--mode", "closed",
           "--concurrency", str(concurrency), "--requests", str(requests),
           "--absolute-times", "--output", str(out)]
    preexec = None
    if pin_core is not None and hasattr(os, "sched_setaffinity"):
        preexec = lambda core=pin_core: os.sched_setaffinity(0, {core})  # noqa: E731
    subprocess.run(cmd, cwd=REPO, check=False, preexec_fn=preexec)
    try:
        with open(out, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def stats_ms(values_ns: list[int]) -> dict | None:
    if not values_ns:
        return None
    ms = sorted(v / 1e6 for v in values_ns)
    return {
        "n": len(ms),
        "min_ms": round(ms[0], 3),
        "mean_ms": round(statistics.fmean(ms), 3),
        "p50_ms": round(percentile(ms, 50), 3),
        "p99_ms": round(percentile(ms, 99), 3),
        "max_ms": round(ms[-1], 3),
    }


def compare(client: dict, mock_log: Path, tokens_per_event: int, mark: int = 0) -> dict:
    """Pair the client's events against mock log entries written after byte offset ``mark``."""
    by_req: dict[int, list[dict]] = {}
    with open(mock_log, "rb") as f:
        f.seek(mark)
        for raw in f:
            e = json.loads(raw)
            if e["kind"] == "content":
                by_req.setdefault(int(e["req"]), []).append(e)

    errors_ns: list[int] = []
    jitter_ns: list[int] = []
    failures: list[dict] = []
    n_ok = n_err = 0
    for r in client["requests"]:
        if r["status"] != "ok":
            n_err += 1
            continue
        n_ok += 1
        logged = by_req.get(r["id"], [])
        evs = r["events"]
        if len(evs) != len(logged):
            failures.append({"req": r["id"], "kind": "event_count",
                             "client": len(evs), "mock": len(logged)})
        for ev, le in zip(evs, logged):
            if ev["tokens"] is not None and ev["tokens"] != le["tokens"]:
                failures.append({"req": r["id"], "kind": "tokens", "event": le["k"],
                                 "client": ev["tokens"], "mock": le["tokens"]})
            if ev.get("t_abs_ns") is not None:
                errors_ns.append(ev["t_abs_ns"] - le["t_ns"])
        for le in logged:
            jitter_ns.append(le["t_ns"] - le["intended_ns"])
        expected_out = len(logged) * tokens_per_event
        if r["tokens_out"] is not None and r["tokens_out"] != expected_out:
            failures.append({"req": r["id"], "kind": "tokens_out",
                             "client": r["tokens_out"], "mock": expected_out})
    return {
        "requests_ok": n_ok,
        "requests_error": n_err,
        "events_paired": len(errors_ns),
        "count_failures": failures,
        "client_minus_mock_write": stats_ms(errors_ns),
        "mock_jitter_actual_minus_intended": stats_ms(jitter_ns),
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="validate", description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="results/instrument")
    ap.add_argument("--concurrencies", default="1,4,16,64")
    ap.add_argument("--requests", type=int, default=128)
    ap.add_argument("--mock-port", type=int, default=18100)
    ap.add_argument("--first-delay-ms", type=float, default=100.0)
    ap.add_argument("--delta-ms", type=float, default=20.0)
    ap.add_argument("--events", type=int, default=32)
    ap.add_argument("--tokens-per-event", type=int, default=4)
    ap.add_argument("--cpu-spinners", type=int, default=2,
                    help="busy processes sharing the client's core (best effort)")
    cfg = ap.parse_args()

    out_dir = (REPO / cfg.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = REPO / "tmp" / "validate"
    scratch.mkdir(parents=True, exist_ok=True)

    pin_core = None
    pinning = "unavailable (no os.sched_setaffinity on this platform)"
    if hasattr(os, "sched_getaffinity"):
        pin_core = min(os.sched_getaffinity(0))
        pinning = f"client and {cfg.cpu_spinners} spinner(s) pinned to core {pin_core}"

    fixture = scratch / "fixture.jsonl"
    write_fixture(fixture, cfg.requests)
    mock_log = scratch / "mock-schedule.jsonl"
    mock = start_mock(cfg.mock_port, mock_log, cfg)

    spinners: list[subprocess.Popen] = []
    try:
        runs = []
        run_errors = 0

        def one(conc: int, label: str) -> None:
            nonlocal run_errors
            slug = label.replace(" ", "_").replace("=", "").replace("+", "plus")
            out_file = scratch / f"client_{slug}.json"
            mark = mock_log.stat().st_size
            res = run_loadgen(cfg.mock_port, fixture, out_file,
                              concurrency=conc, requests=cfg.requests, pin_core=pin_core)
            if res is None:
                run_errors += 1
                runs.append({"label": label, "error": "client produced no output",
                             "count_failures": []})
                return
            runs.append({"label": label,
                         **compare(res, mock_log, cfg.tokens_per_event, mark)})

        for c in [int(x) for x in cfg.concurrencies.split(",")]:
            one(c, f"closed c={c}")

        # the case that matters: load on the client's own core (ADR 0007)
        if cfg.cpu_spinners > 0:
            preexec = None
            if pin_core is not None and hasattr(os, "sched_setaffinity"):
                preexec = lambda core=pin_core: os.sched_setaffinity(0, {core})  # noqa: E731
            spinners = [subprocess.Popen([sys.executable, "-c", "while True: pass"],
                                         preexec_fn=preexec) for _ in range(cfg.cpu_spinners)]
            c_load = max(int(x) for x in cfg.concurrencies.split(","))
            one(c_load, f"closed c={c_load} + {cfg.cpu_spinners} cpu spinner(s)")
    finally:
        for p in spinners:
            p.terminate()
        mock.terminate()
        mock.wait()

    n_fail = sum(len(r["count_failures"]) for r in runs)
    result = {
        "schema": "instrument-validation/1",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tool": "python -m measure.validate",
        "env": {
            "host": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "aiohttp": aiohttp.__version__,
            "cpu_pinning": pinning,
        },
        "mock": {
            "first_delay_ms": cfg.first_delay_ms, "delta_ms": cfg.delta_ms,
            "events_per_request": cfg.events, "tokens_per_event": cfg.tokens_per_event,
        },
        "runs": runs,
        "pass_threshold": None,
        "note": ("Pass threshold undefined until L0 reports its first between-level effect "
                 "(ADR 0007). Counts must already match exactly; timing error is reported, "
                 "not judged, here."),
        "count_failures_total": n_fail,
        "run_errors_total": run_errors,
    }
    (out_dir / "validation.json").write_text(json.dumps(result, indent=1) + "\n")

    env_md = (f"# instrument validation — environment\n\n"
              f"Generated {result['generated']} by `{result['tool']}` on:\n\n"
              f"- host: {result['env']['host']} ({result['env']['machine']})\n"
              f"- python {result['env']['python']}, aiohttp {result['env']['aiohttp']}\n"
              f"- CPU pinning: {pinning}\n\n"
              f"Regenerate with `make validate-instrument`.\n")
    (out_dir / "env.md").write_text(env_md)

    print(f"instrument validation → {out_dir / 'validation.json'}")
    for r in runs:
        e = r.get("client_minus_mock_write") or {}
        j = r.get("mock_jitter_actual_minus_intended") or {}
        ok = r.get("requests_ok", "-")
        print(f"  {r['label']:42s} n={ok} err_p50={e.get('p50_ms')}ms "
              f"err_p99={e.get('p99_ms')}ms mock_jitter_p99={j.get('p99_ms')}ms "
              f"count_failures={len(r['count_failures'])}")
    if n_fail:
        print(f"COUNT FAILURES: {n_fail} — a miscount is a failure (ADR 0007), not a margin.")
    if run_errors:
        print(f"RUN ERRORS: {run_errors} client run(s) produced no output.")
    return 1 if (n_fail or run_errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
