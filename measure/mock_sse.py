"""Mock SSE server — the known-timing reference for validating loadgen (ADR 0007).

Serves the same OpenAI-compatible streaming shape as the real engine, including per-chunk
cumulative ``usage`` (the continuous-usage path the client's token deltas depend on) and the
final usage chunk. Events are emitted on a configured schedule — first event a fixed delay
after arrival, event *k* at ``t_first + k*Δ`` — and the *actual* monotonic timestamp of every
write is logged immediately before the write call. The mock's *intended* schedule is not the
truth; this log is.

Run as a separate process from the client (same host): ``python -m measure.mock_sse``.
Comparison against the client's record is done by ``measure.validate``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from aiohttp import web

CLOCK = time.monotonic_ns

FILLER = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]


def sse_bytes(obj: dict) -> bytes:
    return b"data: " + json.dumps(obj, separators=(",", ":")).encode() + b"\n\n"


class ScheduleLog:
    """JSONL log of every actual write, flushed per line so a kill loses at most one entry."""

    def __init__(self, path: str):
        self._f = open(path, "w", encoding="utf-8", buffering=1)

    def write(self, entry: dict):
        entry["t_ns"] = CLOCK()  # immediately before the write call — this IS the reference
        self._f.write(json.dumps(entry) + "\n")

    def close(self):
        self._f.close()


def make_app(cfg, log: ScheduleLog) -> web.Application:
    async def chat(request: web.Request) -> web.StreamResponse:
        req_id = request.headers.get("x-request-id", "?")
        body = await request.json()
        msgs = body.get("messages") or []
        if not msgs or not msgs[-1].get("content"):
            return web.json_response({"error": "messages[-1].content required"}, status=400)
        prompt_tokens = len(str(msgs[-1]["content"]).split())  # deterministic fake tokenizer

        arrival = CLOCK()
        t_first = arrival + int(cfg.first_delay_ms * 1e6)
        delta_ns = int(cfg.delta_ms * 1e6)
        k_tok = cfg.tokens_per_event

        resp = web.StreamResponse(status=200, headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
        })
        await resp.prepare(request)

        for k in range(cfg.events):
            intended = t_first + k * delta_ns
            now = CLOCK()
            if intended > now:
                await asyncio.sleep((intended - now) / 1e9)
            chunk = {
                "id": req_id,
                "object": "chat.completion.chunk",
                "created": 0,
                "model": body.get("model", "mock"),
                "choices": [{
                    "index": 0,
                    "delta": {"content": " ".join(
                        FILLER[(k * k_tok + j) % len(FILLER)] for j in range(k_tok))},
                    "finish_reason": None,
                }],
            }
            if cfg.continuous_usage:
                chunk["usage"] = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": (k + 1) * k_tok,
                    "total_tokens": prompt_tokens + (k + 1) * k_tok,
                }
            data = sse_bytes(chunk)
            log.write({"kind": "content", "req": req_id, "k": k, "tokens": k_tok,
                       "intended_ns": intended, "arrival_ns": arrival})
            await resp.write(data)

        final = {
            "id": req_id,
            "object": "chat.completion.chunk",
            "created": 0,
            "model": body.get("model", "mock"),
            "choices": [{"index": 0, "delta": {}, "finish_reason": "length"}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": cfg.events * k_tok,
                "total_tokens": prompt_tokens + cfg.events * k_tok,
            },
        }
        log.write({"kind": "usage", "req": req_id, "k": cfg.events, "tokens": 0,
                   "intended_ns": t_first + cfg.events * delta_ns, "arrival_ns": arrival})
        await resp.write(sse_bytes(final))
        log.write({"kind": "done", "req": req_id, "k": cfg.events + 1, "tokens": 0,
                   "intended_ns": t_first + cfg.events * delta_ns, "arrival_ns": arrival})
        await resp.write(b"data: [DONE]\n\n")
        await resp.write_eof()
        return resp

    async def version(_request: web.Request) -> web.Response:
        return web.json_response({"version": "mock-sse/1"})

    async def metrics(_request: web.Request) -> web.Response:
        # Stub so measure_l0's plumbing can be exercised end-to-end against this mock.
        return web.Response(text="# mock: no metrics\n", content_type="text/plain")

    async def models(_request: web.Request) -> web.Response:
        return web.json_response(
            {"object": "list", "data": [{"id": "mock", "object": "model"}]})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    app.router.add_get("/version", version)
    app.router.add_get("/metrics", metrics)
    app.router.add_get("/v1/models", models)
    return app


def main() -> int:
    ap = argparse.ArgumentParser(prog="mock-sse")
    ap.add_argument("--port", type=int, default=18100)
    ap.add_argument("--first-delay-ms", type=float, default=100.0)
    ap.add_argument("--delta-ms", type=float, default=20.0)
    ap.add_argument("--events", type=int, default=32)
    ap.add_argument("--tokens-per-event", type=int, default=4)
    ap.add_argument("--schedule-log", required=True)
    ap.add_argument("--no-continuous-usage", action="store_true")
    cfg = ap.parse_args()
    cfg.continuous_usage = not cfg.no_continuous_usage

    log = ScheduleLog(cfg.schedule_log)
    print(f"mock-sse: port={cfg.port} first={cfg.first_delay_ms}ms delta={cfg.delta_ms}ms "
          f"events={cfg.events} tokens/event={cfg.tokens_per_event} "
          f"continuous_usage={cfg.continuous_usage}", flush=True)
    web.run_app(make_app(cfg, log), host="127.0.0.1", port=cfg.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
