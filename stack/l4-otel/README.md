# L4 — observability, measured (full-stack-serving ADR 0009)

Two compose arms over the IDENTICAL engine — vLLM v0.23.0, the L0/L1 image, same
model/revision/flags; only the exporter differs:

| arm | file | what runs |
|---|---|---|
| `base` | `docker-compose.base.yaml` | vLLM only, NO exporter configured — the control |
| `tracing` | `docker-compose.tracing.yaml` | vLLM `--otlp-traces-endpoint` → otel collector ([`otel-collector-config.yaml`](otel-collector-config.yaml)) → Phoenix, plus every span to a scratch file |

Images (digests verified by the 2026-10-11 pull on saturn):

| piece | image | digest |
|---|---|---|
| engine | vllm/vllm-openai:v0.23.0 | sha256:6d8429e38e3747723ca07ee1b17972e09bb9c51c4032b266f24fb1cc3b22ed8f (same as L0/L1) |
| collector | otel/opentelemetry-collector-contrib:latest | sha256:39923a8e431bd1f57be82411999d389fcfe40857492e4365456d97a4c1f74be6 |
| Phoenix | arizephoenix/phoenix:latest | sha256:3a2e5a049f706f29c7b13bd82bcd94101bb10701874ce134ec9eb7f0299741aa |

Bring-up gotchas (all observed 2026-10-11):

- **The contrib collector reads `/etc/otelcol-contrib/config.yaml`**, not
  `/etc/otelcol/config.yaml` (mount it there or it silently runs its default config with a
  `debug` exporter — caught by the empty spans file).
- **The collector runs `user: 1000:1000`** (the image's 10001 cannot write the host-owned
  scratch bind).
- **`depends_on` is start-order, not readiness:** the collector's first Phoenix exports hit
  "connection refused" for ~2 s until Phoenix's gRPC listener came up; the gRPC exporter
  retried and every span landed. Harmless, but visible in `docker logs l4-otelcol`.

- **One GPU** → the arms are mutually exclusive; `make measure LEVEL=l4-otel` runs
  them sequentially, base first. The order is a recorded confound, not a hidden one.
- **Core pinning** (ADR 0009): engine `cpuset 0-15`, Phoenix `16-23`, collector
  `24-25` — the backend runs on a separate core set, so the cost measured is the
  engine's.
- The collector's `file` exporter writes every span to `tmp/l4-otel/spans/spans.json`
  (gitignored scratch) — the pairing source for the span-to-client TTFT gap; Phoenix
  is the live backend the spans also flow to.
- The weights volume reuses L0's `l0-local_hf-cache` (same pinned model — no
  re-download). Phoenix keeps its data in `l4-phoenix-data`, which
  `make down LEVEL=l4-otel` leaves standing.
- The vLLM image runs as root inside the container (upstream default), so the
  collector's scratch dir needs no permission juggling; spans.json is written by the
  collector container itself.

`make up LEVEL=l4-otel` (tracing arm; `ARM=base` for the control) ·
`make down LEVEL=l4-otel` (both arms) · `make measure LEVEL=l4-otel` ·
`make verify LEVEL=l4-otel`. Findings: ADR 0009; numbers: `results/l4-otel/`.
