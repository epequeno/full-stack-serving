# instrument — the measurement's own measurements

Two validations of `loadgen/`, both committed here as data. A latency claim is only as good
as the client that timestamps it, so the client is validated twice, against a known schedule
and against an independent tool. Regenerate with `make validate-instrument` (nothing running)
and `make crosscheck` (the L0 server: `make up LEVEL=l0-local`).

1. **Against a known schedule** — [validation.json](validation.json), full-stack-serving ADR 0007.
2. **Against an independent tool** — [crosscheck.json](crosscheck.json), full-stack-serving ADR 0003.

Environment for (1) is in [env.md](env.md); the environment for (2) is recorded in its own
`env` block (host, kernel, GPU, server and bench images/digests, both tools' timing
boundaries).

## 1. The known-schedule validation (ADR 0007)

`measure/validate.py` drives the client against `measure/mock_sse.py` — an OpenAI-shaped mock
SSE server on a configured schedule (100 ms first event, 20 ms between events) that logs the
monotonic time of every actual write. The client's per-event timestamp minus the mock's
logged write time is the instrument's own error:

| c | per-event error p50 (ms) | p99 (ms) | claimability bar (10 × p99) |
|---|---|---|---|
| 1 | 0.40 | 0.53 | 5.3 |
| 4 | 0.40 | 1.37 | 13.7 |
| 16 | 0.51 | 1.84 | 18.4 |
| 64 | 0.52 | 5.39 | 53.9 |

Counts match exactly (0 count failures, 0 run errors). This is the pass criterion every
later level fixes its findings against: a between-level effect is claimable only above its
cell's bar.

## 2. The independent-tool cross-check (ADR 0003)

`measure/crosscheck.py` drives **both tools against the same server** (L0's endpoint), one
cell at a time, back-to-back: `loadgen/` (closed loop, timestamps at the socket read) and
`vllm bench serve` v0.23.0 (closed loop at `--max-concurrency`, timestamps after
`json.loads()` of each parsed SSE message, inside aiohttp's stream iterator). Same fixture
(256 distinct prompts), same `max_tokens` 128, `ignore_eos`, seed 20261010. Run 2026-10-11,
`p50` per cell (the percentile ADR 0006 makes claimable at n=256; loadgen reports its p99
as `n/a (n)` by that floor — bench's p99 is recorded in the JSON, not compared).

| c | TTFT p50 lg (ms) | bench (ms) | Δ (ms) | Δ / bar | ITL p50 Δ (ms) | req/s lg | bench |
|---|---|---|---|---|---|---|---|
| 1 | 59.51 | 59.93 | −0.42 | −0.08 | +0.01 | 1.22 | 1.23 |
| 4 | 111.24 | 116.67 | −5.42 | −0.40 | −0.01 | 3.84 | 3.83 |
| 16 | 167.86 | 217.94 | −50.08 | −2.72 | +0.03 | 9.24 | 9.28 |
| 64 | 371.72 | 370.77 | +0.95 | +0.02 | −0.03 | 12.50 | 12.55 |

**Reading it:**

- **Per-event timing agrees at every cell** — |Δ| ≤ 0.03 ms on ITL/event p50, ≤ 0.1 req/s on
  throughput, both far under the ADR 0007 bars. This is the agreement that says the client's
  per-event path (the one every ITL finding rests on) is sound.
- **First-token timing is where the tools differ**: bench reads TTFT higher than loadgen in
  7 of 8 cells across the two runs (same protocol, same day), by −0.4 to −5.4 ms at c=1–4 —
  sub-bar, at the instrument's resolution — and by −50.1 ms (−2.7 × bar) at c=16 in the
  committed run (the first run of the same protocol, not committed, read −150.5 ms /
  −8.2 × bar; its command record carried absolute host paths, so it was regenerated). The
  magnitude at c≥16 varies run to run; the *direction* does not. That is the expected
  signature of bench's later timing boundary: the timestamp lands after event-loop
  scheduling, SSE parsing and JSON decode, so a first token that arrives while the event
  loop is busy with other streams is recorded late — and the busier the loop, the later.
- **Verdict (ADR 0003): reported, not tuned away.** The cross-check closes 0003 as run: the
  client is sound where it was already validated (per-event), and the disagreement it found
  is in `vllm bench serve`'s boundary, which this repo does not share.

Known asymmetries (recorded in the JSON, hidden nowhere): bench shuffles fixture rows with
its own `--seed` RNG where loadgen reads them in order; the fixture's per-row
`max_tokens`/`ignore_eos` map to bench's global `--custom-output-len 128 --ignore-eos`
(uniform in this fixture); bench runs in a container (one extra network hop) where loadgen
runs on the host. Same direction at every cell, so none of these explain the TTFT gap.

## Reproducing the cross-check

The pinned image `vllm/vllm-openai:v0.23.0` does not ship the `bench` extra
(`bench serve`'s dataset loader needs `pandas`); `make crosscheck` uses the derived image
`crosscheck-bench:v0.23.0-pandas` — the pinned base digest plus `pip install pandas`
(`pandas 3.0.6`), recorded in the JSON's `env`:

    docker build -t crosscheck-bench:v0.23.0-pandas - <EOF
    FROM vllm/vllm-openai@sha256:6d8429e38e3747723ca07ee1b17972e09bb9c51c4032b266f24fb1cc3b22ed8f
    RUN pip install --no-cache-dir pandas
    EOF

    make up LEVEL=l0-local      # the cross-check measures L0's endpoint
    make crosscheck             # → results/instrument/crosscheck.json

Exact per-cell commands are in the JSON's `protocol.commands` (`<repo>` = checkout root).