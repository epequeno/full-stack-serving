# full-stack-serving — bring-up, measurement, teardown.
#
# L0 (docker compose), L1 (k3s; root kubeconfig → sudo kubectl) and L4 (OTel tracing,
# two compose arms over the identical engine) are implemented: up/down/measure for
# LEVEL=l0-local|l1-k8s|l4-otel, verify for LEVEL=l1-k8s|l4-otel (L0 is the baseline
# and commits no findings — ADR 0008). Other levels fail loudly rather than
# pretending. uv syncs the Python env automatically on first use.

LEVEL ?= l0-local
# Names are load-bearing: LEVEL=<l> maps to stack/<l>/ and results/<l>/ (stack/README.md).
# l2-routing, l3-autoscaling and l5-multinode are placeholders for the cut levels, listed so
# `make status` and `make help` show them as empty rather than hiding them. The implemented
# levels are exactly l0-local, l1-k8s and l4-otel.
LEVELS := l0-local l1-k8s l2-routing l3-autoscaling l4-otel l5-multinode

INVENTORY_SRC ?= $(HOME)/code/ebpf-inference-observability/tools/host-inventory.sh
INVENTORY_OUT ?= results/l0-local/host-inventory.md

ENDPOINT ?= http://localhost:8000/v1/chat/completions
L1_ENDPOINT ?= http://localhost:30080/v1/chat/completions
KUBECTL ?= sudo kubectl
MODEL ?= Qwen/Qwen2.5-1.5B-Instruct
CONTINUOUS_USAGE_BODY ?= {"stream_options": {"include_usage": true, "continuous_usage_stats": true}}

.DEFAULT_GOAL := help
.PHONY: help doctor doctor-check inventory up down measure verify gate-log validate-instrument crosscheck clean status \
        up-l0-local down-l0-local up-l1-k8s down-l1-k8s measure-l0-local measure-l1-k8s verify-l1-k8s \
        up-l4-otel down-l4-otel measure-l4-otel verify-l4-otel

help:
	@echo "full-stack-serving"
	@echo ""
	@echo "  make doctor              print what this host has (installs nothing, writes nothing)"
	@echo "  make inventory           write that report to $(INVENTORY_OUT) (Linux GPU host only)"
	@echo "  make doctor-check        is the vendored inventory script intact and in sync? (exit 1 if not)"
	@echo "  make up   LEVEL=<l>      bring the stack up at level <l>"
	@echo "  make down LEVEL=<l>      tear it down"
	@echo "  make measure LEVEL=<l>   regenerate results/<l>/ from scratch"
	@echo "  make validate-instrument  validate loadgen against the mock server (ADR 0007) → results/instrument/"
	@echo "  make verify  LEVEL=<l>   re-measure into scratch; fail if a committed finding changes (L1+)"
	@echo "  make gate-log LEVEL=<l>  run verify and commit its output to results/<l>/gates/ (ADR 0014)"
	@echo "  make crosscheck          cross-check loadgen against vllm bench serve (ADR 0003) → results/instrument/ (needs the L0 server up)"
	@echo "  make status              show what is currently built"
	@echo ""
	@echo "  levels: $(LEVELS)"
	@echo "  default LEVEL=$(LEVEL)"

# --- works today ------------------------------------------------------------

# The checks live in tools/host-inventory.sh, which is VENDORED from the canonical copy in
# ebpf-inference-observability. Both projects run on the same host, so one run answers both —
# and two scripts would eventually disagree about the same machine.
doctor:
	@tools/host-inventory.sh

# Committed evidence of what the measurements ran on. Refuses to run off Linux: the repo lives
# on a dev laptop too, and a macOS report must never replace the GPU host's. Pass
# PROBE_IMAGE=<local image> to include the GPU-in-container probe; `sudo make inventory`
# adds the root-only checks. Written via a temp file so a failed run keeps the previous report.
# The inventory is redacted before it is written: it is published, and RFC1918 addresses plus
# the wireless interface name are host-identifying. The vendored script is left byte-identical
# so that tools/HOST-INVENTORY.source's sha256 still detects real drift.
inventory:
	@if [ "$$(uname -s)" != Linux ] && [ -z "$(FORCE)" ]; then \
	  echo "refusing: this host is $$(uname -s), not the Linux GPU host." >&2; \
	  echo "  run it on the GPU host, or FORCE=1 to write anyway." >&2; \
	  exit 1; \
	fi
	@mkdir -p "$(dir $(INVENTORY_OUT))"
	@tmp=$$(mktemp "$(INVENTORY_OUT).XXXXXX") || exit 1; \
	if tools/host-inventory.sh \
	  | sed -E \
	      -e 's/(^|[^0-9])((192\.168|172\.(1[6-9]|2[0-9]|3[01]))(\.[0-9]{1,3}){2}|10(\.[0-9]{1,3}){3})/\1<private-ip>/g' \
	      -e 's/dev wl[a-z0-9]*/dev <iface>/g' \
	  > "$$tmp"; then \
	  chmod 644 "$$tmp"; mv "$$tmp" "$(INVENTORY_OUT)"; \
	  echo "wrote $(INVENTORY_OUT)"; \
	else \
	  rm -f "$$tmp"; echo "inventory failed; $(INVENTORY_OUT) left untouched" >&2; exit 1; \
	fi

# Three hashes: vendored (the file here), recorded (HOST-INVENTORY.source), canonical (the
# other repo). Local edits are detectable without the canonical copy; upstream drift is not.
# Exits non-zero on any mismatch so it can gate; a missing canonical alone is not a failure.
doctor-check:
	@echo "vendored inventory script vs canonical"
	@hash_of() { if command -v sha256sum >/dev/null; then sha256sum "$$1" | cut -d' ' -f1; else shasum -a 256 "$$1" | cut -d' ' -f1; fi; }; \
	src="$(INVENTORY_SRC)"; \
	v=$$(hash_of tools/host-inventory.sh); \
	r=$$(awk '/^sha256:/{print $$2}' tools/HOST-INVENTORY.source); \
	echo "  vendored   $$v"; \
	echo "  recorded   $$r"; \
	if [ "$$v" != "$$r" ]; then \
	  echo "  DRIFT LOCAL — vendored copy differs from the recorded hash. Restore it from canonical,"; \
	  echo "  or re-vendor properly and update HOST-INVENTORY.source (commit + sha256)."; \
	  exit 1; \
	fi; \
	if [ ! -f "$$src" ]; then \
	  echo "  canonical  not found at $$src (set INVENTORY_SRC=<path>)"; \
	  echo "  OK — vendored matches its recorded hash; upstream not checked"; \
	  exit 0; \
	fi; \
	c=$$(hash_of "$$src"); \
	echo "  canonical  $$c"; \
	if [ "$$c" != "$$v" ]; then \
	  echo "  DRIFT UPSTREAM — canonical changed since sync. Re-vendor and update"; \
	  echo "  HOST-INVENTORY.source (commit + sha256)."; \
	  exit 1; \
	fi; \
	echo "  OK — in sync"

status:
	@echo "levels and their committed evidence:"
	@for l in $(LEVELS); do \
	  n=$$(ls -1 results/$$l 2>/dev/null | grep -v '^\.' | wc -l | tr -d ' '); \
	  printf "  %-18s results/%-18s %s file(s)\n" "$$l" "$$l/" "$$n"; \
	done

# --- level bring-up, measurement, teardown ------------------------------------------------
# One target per level, dispatched by LEVEL. Names are load-bearing: LEVEL=l1-k8s maps
# to stack/l1-k8s/ and writes to results/l1-k8s/ (stack/README.md).

K3S_TIMEOUT ?= 900s

up:
	@case "$(LEVEL)" in \
	  l0-local) $(MAKE) --no-print-directory up-l0-local ;; \
	  l1-k8s) $(MAKE) --no-print-directory up-l1-k8s ;; \
	  l4-otel) $(MAKE) --no-print-directory up-l4-otel ;; \
	  *) echo "make up LEVEL=$(LEVEL): not implemented (L2, L3, L5)." >&2; exit 1 ;; \
	esac

down:
	@case "$(LEVEL)" in \
	  l0-local) $(MAKE) --no-print-directory down-l0-local ;; \
	  l1-k8s) $(MAKE) --no-print-directory down-l1-k8s ;; \
	  l4-otel) $(MAKE) --no-print-directory down-l4-otel ;; \
	  *) echo "make down LEVEL=$(LEVEL): not implemented (L2, L3, L5)." >&2; exit 1 ;; \
	esac

measure:
	@case "$(LEVEL)" in \
	  l0-local) $(MAKE) --no-print-directory measure-l0-local ;; \
	  l1-k8s) $(MAKE) --no-print-directory measure-l1-k8s ;; \
	  l4-otel) $(MAKE) --no-print-directory measure-l4-otel ;; \
	  *) echo "make measure LEVEL=$(LEVEL): not implemented (L2, L3, L5)." >&2; exit 1 ;; \
	esac

# ADR 0008: the gate reproduces findings, and findings start at L1 (L0 is the
# baseline and compares against nothing). `make verify` re-measures into scratch
# (never over results/) and exits non-zero on a verdict change.
verify:
	@case "$(LEVEL)" in \
	  l1-k8s) $(MAKE) --no-print-directory verify-l1-k8s ;; \
	  l4-otel) $(MAKE) --no-print-directory verify-l4-otel ;; \
	  l0-local) echo "make verify LEVEL=l0-local: nothing to verify — L0 is the baseline and commits no findings (ADR 0008)." >&2; exit 1 ;; \
	  *) echo "make verify LEVEL=$(LEVEL): not implemented (L2, L3, L5)." >&2; exit 1 ;; \
	esac

# ADR 0014: commit the gate's output. The one thing written under results/ that `make
# measure` does not regenerate — an audit record, never an input to a number. `make verify`
# itself stays read-only (ADR 0008); tools/gate-log.sh runs it and tees.
GATE_LOG_DIR ?= gates
gate-log:
	@tools/gate-log.sh "$(LEVEL)" "results/$(LEVEL)/$(GATE_LOG_DIR)"

# The instrument's two validations (→ results/instrument/): ADR 0007 against the
# mock server, then ADR 0003 against `vllm bench serve`. validate needs nothing
# running; crosscheck needs the L0 server (`make up LEVEL=l0-local`) — same fixture,
# same endpoint, same concurrency as the L0 matrix, both tools driven per cell.
INSTRUMENT_OUT ?= results/instrument
validate-instrument:
	@uv run python -m measure.validate --out $(INSTRUMENT_OUT)

crosscheck:
	@uv run python -m measure.crosscheck --out $(INSTRUMENT_OUT)/crosscheck.json

up-l0-local:
	@docker compose -f stack/l0-local/docker-compose.yaml up -d
	@sleep 2
	@img=$$(docker inspect --format '{{.Config.Image}}' l0-vllm 2>/dev/null); \
	  if [ -n "$$img" ]; then \
	    docker image inspect --format "up: $$img (digest {{index .RepoDigests 0}})" "$$img" \
	      || echo "up: container l0-vllm is running on $$img (image digest unavailable)"; \
	  else \
	    echo "up: container l0-vllm not found yet — still starting; check: docker logs l0-vllm"; \
	  fi

down-l0-local:
	@docker compose -f stack/l0-local/docker-compose.yaml down

# L1: the plugin DaemonSet first (vendored, pinned — stack/l1-k8s/README.md), then the
# workload. rollout status gates on the pod being Ready; the NodePort path (kube-proxy →
# CNI → pod) is part of what L1 measures (ADR 0010).
up-l1-k8s:
	@$(KUBECTL) apply -f stack/l1-k8s/nvidia-device-plugin.yml
	@$(KUBECTL) apply -f stack/l1-k8s/vllm.yaml
	@$(KUBECTL) rollout status deployment/l1-vllm --timeout=$(K3S_TIMEOUT)
	@$(KUBECTL) get node -o custom-columns='NODE:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu' 2>/dev/null || true
	@echo "up: l1-vllm → $(L1_ENDPOINT) (NodePort 30080; the kube-proxy/CNI path is part of what L1 measures)"

down-l1-k8s:
	@$(KUBECTL) delete -f stack/l1-k8s/vllm.yaml --ignore-not-found
	@$(KUBECTL) delete -f stack/l1-k8s/nvidia-device-plugin.yml --ignore-not-found

measure-l0-local:
	@uv run python -m measure.measure_l0 --endpoint $(ENDPOINT) --model $(MODEL) \
	  --out-dir results/l0-local --continuous-usage-body '$(CONTINUOUS_USAGE_BODY)'

# R=10 for L1 (ADR 0008's remedy): at R=5 the c=1 TTFT and c=16 ITL verdicts sat on
# 0.003–0.015 ms margins — below the instrument's claimability bars (ADR 0007) — and
# flipped between identical-protocol runs. R=10 pushes those cells into the stable
# (indistinguishable) regime so the committed verdicts reproduce at the gate.
measure-l1-k8s:
	@uv run python -m measure.measure_l1 --endpoint $(L1_ENDPOINT) --model $(MODEL) \
	  --out-dir results/l1-k8s --repeats 10 \
	  --continuous-usage-body '$(CONTINUOUS_USAGE_BODY)'

verify-l1-k8s:
	@uv run python -m measure.verify_l1 --endpoint $(L1_ENDPOINT) --model $(MODEL) \
	  --scratch tmp/verify-l1-k8s --findings results/l1-k8s/findings.json \
	  --continuous-usage-body '$(CONTINUOUS_USAGE_BODY)'

# --- L4: two compose arms over the identical engine (ADR 0009) ---------------------------
# base = no exporter (the control); tracing = --otlp-traces-endpoint → collector →
# Phoenix + scratch spans.json. One GPU: the arms are mutually exclusive and
# measure_l4 runs them sequentially, base first (the order is a recorded confound).
up-l4-otel:
	@case "$(ARM)" in base|tracing) ;; *) echo "make up LEVEL=l4-otel ARM=$(ARM): unknown arm (base|tracing)." >&2; exit 1 ;; esac
	@docker compose -p l4-otel-$(ARM) -f stack/l4-otel/docker-compose.$(ARM).yaml up -d
	@echo "up: l4-otel $(ARM) arm — waiting for /v1/models (weights are cached from L0)"
	@i=0; until curl -sf http://localhost:8000/v1/models >/dev/null 2>&1; do \
	  i=$$((i+1)); if [ $$i -gt 450 ]; then \
	    echo "up: l4-otel $(ARM) not ready after 900s — docker compose -p l4-otel-$(ARM) logs" >&2; exit 1; \
	  fi; sleep 2; done
	@docker inspect --format 'up: l4-vllm-$(ARM) {{.Config.Image}} (digest {{index .RepoDigests 0}})' l4-vllm-$(ARM) 2>/dev/null || true

down-l4-otel:
	@docker compose -p l4-otel-tracing -f stack/l4-otel/docker-compose.tracing.yaml down >/dev/null 2>&1 || true
	@docker compose -p l4-otel-base -f stack/l4-otel/docker-compose.base.yaml down >/dev/null 2>&1 || true
	@echo "down: both l4-otel arms (the l4-phoenix-data volume is kept)"

measure-l4-otel:
	@uv run python -m measure.measure_l4 --endpoint $(ENDPOINT) --model $(MODEL) \
	  --out-dir results/l4-otel

verify-l4-otel:
	@uv run python -m measure.verify_l4 --endpoint $(ENDPOINT) --model $(MODEL) \
	  --scratch tmp/verify-l4-otel --findings results/l4-otel/findings.json

clean:
	@echo "cleaning scratch (results/ is never touched)"
	@rm -rf tmp/ .scratch/
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "done"
