"""The load generator: the client and the ground truth (loadgen/README.md, ADRs 0003-0005, 0009).

Timing boundary: every event is stamped with ``time.monotonic_ns()`` at the return of
``StreamReader.readany()`` — the earliest point in user space at which the bytes are visible
to this process. Events that arrive in one TCP read share one timestamp; that is the honest
observation (full-stack-serving ADR 0005), not a defect. ``sent`` is stamped immediately before
the ``session.post`` call. Whatever this boundary costs is measured, not assumed, by the mock
validation (full-stack-serving ADR 0007).

Token accounting: ``tokens`` per event is the delta of the server's cumulative
``usage.completion_tokens`` between consecutive content-bearing chunks, when the server reports
it; ``null`` otherwise, never estimated. ``tokens_out``/``tokens_in`` come from the final
``usage`` chunk, never from counting events.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import random
import secrets
import sys
import time
from dataclasses import dataclass, field

import aiohttp

CLIENT_VERSION = "loadgen/1"
CLOCK = time.monotonic_ns

# Percentile floors (full-stack-serving ADR 0006): a percentile is reported only if the
# REQUEST count (never the ITL-event count) supports it. Below the floor the summary shows
# n/a with the actual n; pooling across repeats is measure/'s job.
FLOORS = ((50, 30), (90, 100), (99, 1000))


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolated percentile of an unsorted list."""
    if not values:
        raise ValueError("empty")
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    i = (len(s) - 1) * p / 100.0
    lo = int(i)
    frac = i - lo
    return s[lo] if frac == 0 else s[lo] + (s[lo + 1] - s[lo]) * frac


def pct_or_na(values: list[float], p: int, n_requests: int):
    for threshold, need in FLOORS:
        if p <= threshold:
            return percentile(values, p) if n_requests >= need else {"n/a (n)": n_requests}
    raise ValueError(p)  # unreachable with the standard floors


@dataclass
class Event:
    t_ms: float                 # relative to the request's send
    tokens: int | None          # delta of cumulative completion_tokens, or null
    t_abs_ns: int | None = None # absolute monotonic ns (--absolute-times only)


@dataclass
class RequestRecord:
    id: int
    fixture_id: str
    set: str | None = None
    group: int | None = None
    trace_id: str = ""                      # W3C trace id this request propagates (ADR 0009)
    response_id: str | None = None          # the engine's id (chatcmpl-…), seen in every SSE chunk
    scheduled_ms: float | None = None   # relative to run start (open loop)
    sent_ms: float | None = None        # relative to run start
    sent_abs_ns: int | None = None
    ttft_ms: float | None = None
    e2e_ms: float | None = None
    e2e_abs_ns: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    status: str = "ok"                  # ok | error | refused
    http_status: int | None = None
    error: str | None = None
    events: list[Event] = field(default_factory=list)


def new_traceparent() -> tuple[str, str]:
    """W3C traceparent; sent on every request, tracing on or off (ADR 0009)."""
    trace_id = secrets.token_hex(16)
    return trace_id, f"00-{trace_id}-{secrets.token_hex(8)}-01"


async def consume_stream(resp, rec: RequestRecord, started_ns: int, abs_times: bool):
    """Read the SSE stream, stamping each chunk read as early as user space allows."""
    buf = b""
    prev_cum = 0
    usage = None
    done = False
    while not done:
        chunk = await resp.content.readany()
        t_ns = CLOCK()
        if not chunk:
            break  # EOF without [DONE]
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.rstrip(b"\r")
            if not line.startswith(b"data:"):
                continue
            payload = line[len(b"data:"):].strip()
            if payload == b"[DONE]":
                done = True
                break
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            choices = obj.get("choices") or [{}]
            delta = (choices[0] or {}).get("delta") or {}
            if rec.response_id is None:
                rec.response_id = obj.get("id")
            content = delta.get("content")
            u = obj.get("usage")
            if isinstance(u, dict):
                usage = u
            if content is None:
                continue  # not a content-bearing event
            tokens = None
            if isinstance(u, dict) and isinstance(u.get("completion_tokens"), int):
                tokens = max(0, u["completion_tokens"] - prev_cum)
                prev_cum = u["completion_tokens"]
            rec.events.append(Event(
                t_ms=(t_ns - started_ns) / 1e6,
                tokens=tokens,
                t_abs_ns=t_ns if abs_times else None,
            ))
    if rec.events:
        rec.ttft_ms = rec.events[0].t_ms
    if usage:
        rec.tokens_in = usage.get("prompt_tokens")
        rec.tokens_out = usage.get("completion_tokens")
    return done, usage


async def do_request(session, cfg, rec_: dict, rid: int, run_t0: int) -> RequestRecord:
    rec = RequestRecord(id=rid, fixture_id=rec_["id"], set=rec_.get("set"),
                         group=rec_.get("group"))
    rec.trace_id, traceparent = new_traceparent()
    body = {
        "model": cfg.model,
        "messages": [{"role": "user", "content": rec_["prompt"]}],
        "stream": True,
        "max_tokens": rec_.get("max_tokens", cfg.max_tokens),
        "stream_options": {"include_usage": True},
    }
    if rec_.get("ignore_eos", cfg.ignore_eos):
        body["ignore_eos"] = True
    if cfg.extra_body:
        body.update(cfg.extra_body)
    headers = {
        "traceparent": traceparent,
        "x-request-id": str(rid),
    }
    sent_abs = CLOCK()
    rec.sent_abs_ns = sent_abs if cfg.absolute_times else None
    rec.sent_ms = (sent_abs - run_t0) / 1e6
    try:
        async with session.post(cfg.endpoint, json=body, headers=headers) as resp:
            rec.http_status = resp.status
            if resp.status != 200:
                detail = (await resp.text())[:500]
                rec.status, rec.error = "error", f"HTTP {resp.status}: {detail}"
                return rec
            done, _ = await consume_stream(resp, rec, sent_abs, cfg.absolute_times)
            end_abs = CLOCK()
            rec.e2e_abs_ns = end_abs if cfg.absolute_times else None
            rec.e2e_ms = (end_abs - sent_abs) / 1e6
            if not rec.events:
                rec.status, rec.error = "error", "stream produced no content events"
    except asyncio.TimeoutError:
        rec.status, rec.error = "error", "timeout"
    except aiohttp.ClientError as e:
        rec.status, rec.error = "error", f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001 — the client records, never crashes the run
        rec.status, rec.error = "error", f"{type(e).__name__}: {e}"
    return rec


async def closed_loop(session, cfg, records, run_t0) -> list[RequestRecord]:
    out: list[RequestRecord] = []
    it = iter(enumerate(records))
    lock = asyncio.Lock()  # iterator handout (next() is atomic in CPython, but be explicit)

    async def worker():
        while True:
            async with lock:
                nxt = next(it, None)
            if nxt is None:
                return
            i, rec_ = nxt
            out.append(await do_request(session, cfg, rec_, i + 1, run_t0))
            if sys.stderr.isatty():
                print(f"\r{len(out)}/{len(records)}", end="", file=sys.stderr)
    await asyncio.gather(*(worker() for _ in range(cfg.concurrency)))
    print(file=sys.stderr)
    return out


async def open_loop(session, cfg, records, run_t0) -> list[RequestRecord]:
    out: list[RequestRecord] = []
    rng = random.Random(cfg.seed)
    state = {"in_flight": 0}
    tasks: list[tuple[asyncio.Task, float]] = []

    t = 0.0
    k = 0
    while True:
        t += rng.expovariate(cfg.rate)
        if t >= cfg.duration:
            break
        await asyncio.sleep(t - (CLOCK() - run_t0) / 1e9)
        rec_ = records[k % len(records)]
        k += 1
        if state["in_flight"] >= cfg.inflight_cap:
            r = RequestRecord(id=k, fixture_id=rec_["id"], set=rec_.get("set"),
                              group=rec_.get("group"))
            r.scheduled_ms = t * 1000.0
            r.status = "refused"
            out.append(r)
            continue
        state["in_flight"] += 1
        fut = asyncio.ensure_future(do_request(session, cfg, rec_, k, run_t0))
        fut.add_done_callback(lambda _f, s=state: s.__setitem__("in_flight", s["in_flight"] - 1))
        tasks.append((fut, t * 1000.0))
    results = await asyncio.gather(*[f for f, _ in tasks])
    for (_fut, sched), r in zip(tasks, results):
        r.scheduled_ms = sched
        out.append(r)
    out.sort(key=lambda r: r.id)
    return out


def summarize(requests: list[RequestRecord], wall_s: float) -> dict:
    ok = [r for r in requests if r.status == "ok"]
    n_ok = len(ok)
    ttfts = [r.ttft_ms for r in ok if r.ttft_ms is not None]
    itls = [b.t_ms - a.t_ms for r in ok for a, b in zip(r.events, r.events[1:])]
    tpots = []
    for r in ok:
        if r.tokens_out and r.tokens_out > 1 and r.e2e_ms is not None and r.ttft_ms is not None:
            tpots.append((r.e2e_ms - r.ttft_ms) / (r.tokens_out - 1))
    tokens_out_total = sum(r.tokens_out or 0 for r in ok)
    return {
        "requests_ok": n_ok,
        "requests_error": sum(1 for r in requests if r.status == "error"),
        "requests_refused": sum(1 for r in requests if r.status == "refused"),
        "tokens_out_total": tokens_out_total,
        "tokens_per_s": round(tokens_out_total / wall_s, 1) if wall_s > 0 else None,
        "ttft_ms": {"p50": pct_or_na(ttfts, 50, n_ok), "p99": pct_or_na(ttfts, 99, n_ok)},
        "itl_event_ms": {"p50": pct_or_na(itls, 50, n_ok), "p99": pct_or_na(itls, 99, n_ok)},
        "tpot_ms": {"p50": pct_or_na(tpots, 50, n_ok), "p99": pct_or_na(tpots, 99, n_ok)},
    }


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="loadgen", description=__doc__.splitlines()[0])
    ap.add_argument("--endpoint", required=True,
                    help="OpenAI-compatible chat completions URL, e.g. http://host:8000/v1/chat/completions")
    ap.add_argument("--model", required=True)
    ap.add_argument("--fixture", required=True, help="JSONL of prompt records")
    ap.add_argument("--mode", choices=("closed", "open"), default="closed")
    ap.add_argument("--concurrency", type=int, default=4, help="closed loop: in-flight requests")
    ap.add_argument("--rate", type=float, default=1.0, help="open loop: Poisson arrival rate (req/s)")
    ap.add_argument("--duration", type=float, default=10.0, help="open loop: arrival window (s)")
    ap.add_argument("--inflight-cap", type=int, default=64, help="open loop: refuse above this many in flight")
    ap.add_argument("--seed", type=int, default=0, help="open loop: Poisson schedule seed")
    ap.add_argument("--requests", type=int, default=0, help="use only the first N fixture records (0 = all)")
    ap.add_argument("--level", default="l0-local", help="recorded in the run block")
    ap.add_argument("--max-tokens", type=int, default=128)
    ap.add_argument("--no-ignore-eos", action="store_true", help="override the fixture's ignore_eos")
    ap.add_argument("--extra-body", default=None,
                    help="JSON object merged into every request body (engine-specific options)")
    ap.add_argument("--absolute-times", action="store_true",
                    help="also record absolute monotonic ns per event (ADR 0007 validation)")
    ap.add_argument("--timeout", type=float, default=300.0, help="per-read socket timeout (s)")
    ap.add_argument("--output", default=None, help="write JSON here (default: stdout)")
    cfg = ap.parse_args(argv)
    if cfg.extra_body is not None:
        cfg.extra_body = json.loads(cfg.extra_body)
    if cfg.no_ignore_eos:
        cfg.ignore_eos = False
    else:
        cfg.ignore_eos = True
    return cfg


async def run(cfg) -> dict:
    records = []
    with open(cfg.fixture, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    if cfg.requests:
        records = records[: cfg.requests]
    if not records:
        raise SystemExit("fixture is empty")

    timeout = aiohttp.ClientTimeout(total=None, connect=15.0, sock_read=cfg.timeout)
    connector = aiohttp.TCPConnector(limit=0, limit_per_host=0)  # concurrency is ours to control
    t_wall0 = CLOCK()
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        run_t0 = CLOCK()
        if cfg.mode == "closed":
            requests = await closed_loop(session, cfg, records, run_t0)
        else:
            requests = await open_loop(session, cfg, records, run_t0)
        wall_ns = CLOCK() - run_t0
    t_all_ns = CLOCK() - t_wall0

    wall_s = wall_ns / 1e9
    out = {
        "run": {
            "level": cfg.level,
            "load_model": cfg.mode,
            "concurrency": cfg.concurrency if cfg.mode == "closed" else None,
            "rate_req_s": cfg.rate if cfg.mode == "open" else None,
            "duration_s": cfg.duration if cfg.mode == "open" else None,
            "inflight_cap": cfg.inflight_cap if cfg.mode == "open" else None,
            "endpoint": cfg.endpoint,
            "model": cfg.model,
            "fixture": cfg.fixture,
            "requests_planned": len(records),
            "seed": cfg.seed,
            "extra_body": cfg.extra_body,
            "ignore_eos": cfg.ignore_eos,
            "max_tokens_default": cfg.max_tokens,
        },
        "env": {
            "python": platform.python_version(),
            "aiohttp": aiohttp.__version__,
            "client": CLIENT_VERSION,
            "clock": "time.monotonic_ns",
            "loop": "asyncio (stdlib)",
            "boundary": "event t = return of StreamReader.readany(); sent = before session.post",
        },
        "wall_s": round(wall_s, 3),
        "harness_setup_s": round((t_all_ns - wall_ns) / 1e9, 3),
        "requests": [
            {**vars(r), "events": [vars(e) for e in r.events]}
            for r in sorted(requests, key=lambda r: r.id)
        ],
        "summary": summarize(requests, wall_s),
    }
    return out


def main(argv=None) -> int:
    cfg = parse_args(argv)
    out = asyncio.run(run(cfg))
    text = json.dumps(out, indent=1)
    if cfg.output:
        with open(cfg.output, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    s = out["summary"]
    print(f"loadgen: {s['requests_ok']} ok, {s['requests_error']} err, "
          f"{s['requests_refused']} refused; wall {out['wall_s']}s", file=sys.stderr)
    return 0 if s["requests_error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
