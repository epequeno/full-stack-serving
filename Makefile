# full-stack-serving — bring-up, measurement, teardown.
#
# L0 is implemented: `up`/`down`/`measure`/`validate-instrument` work for LEVEL=l0-local
# (uv syncs the Python env automatically on first use). L1+ targets fail loudly rather
# than pretending.

LEVEL ?= l0-local
LEVELS := l0-local l1-k8s l2-routing l3-autoscaling l4-observability l5-multinode

INVENTORY_SRC ?= $(HOME)/code/ebpf-inference-observability/tools/host-inventory.sh
INVENTORY_OUT ?= results/l0-local/host-inventory.md

ENDPOINT ?= http://localhost:8000/v1/chat/completions
MODEL ?= Qwen/Qwen2.5-1.5B-Instruct
CONTINUOUS_USAGE_BODY ?= {"stream_options": {"include_usage": true, "continuous_usage_stats": true}}

.DEFAULT_GOAL := help
.PHONY: help doctor doctor-check inventory up down measure verify validate-instrument clean status

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
inventory:
	@if [ "$$(uname -s)" != Linux ] && [ -z "$(FORCE)" ]; then \
	  echo "refusing: this host is $$(uname -s), not the Linux GPU host." >&2; \
	  echo "  run it on the GPU host, or FORCE=1 to write anyway." >&2; \
	  exit 1; \
	fi
	@mkdir -p "$(dir $(INVENTORY_OUT))"
	@tmp=$$(mktemp "$(INVENTORY_OUT).XXXXXX") || exit 1; \
	if tools/host-inventory.sh > "$$tmp"; then \
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

# --- level bring-up, measurement, teardown (l0-local; L1+ fails loudly) ----------------------

up:
	@if [ "$(LEVEL)" != l0-local ]; then echo "make up LEVEL=$(LEVEL): not implemented yet (L1+)." >&2; exit 1; fi
	@docker compose -f stack/$(LEVEL)/docker-compose.yaml up -d
	@sleep 2
	@img=$$(docker inspect --format '{{.Config.Image}}' l0-vllm 2>/dev/null); \
	  if [ -n "$$img" ]; then \
	    docker image inspect --format "up: $$img (digest {{index .RepoDigests 0}})" "$$img" \
	      || echo "up: container l0-vllm is running on $$img (image digest unavailable)"; \
	  else \
	    echo "up: container l0-vllm not found yet — still starting; check: docker logs l0-vllm"; \
	  fi

down:
	@if [ "$(LEVEL)" != l0-local ]; then echo "make down LEVEL=$(LEVEL): not implemented yet (L1+)." >&2; exit 1; fi
	@docker compose -f stack/$(LEVEL)/docker-compose.yaml down

# ADR 0007: run before any level's numbers are trusted, and again whenever loadgen/ or the
# Python/aiohttp versions change. Committed under results/instrument/ with its environment.
validate-instrument:
	@uv run python -m measure.validate --out results/instrument

measure:
	@if [ "$(LEVEL)" != l0-local ]; then echo "make measure LEVEL=$(LEVEL): not implemented yet (L1+)." >&2; exit 1; fi
	@uv run python -m measure.measure_l0 --endpoint $(ENDPOINT) --model $(MODEL) \
	  --out-dir results/$(LEVEL) --continuous-usage-body '$(CONTINUOUS_USAGE_BODY)'

# ADR 0008: the gate reproduces findings, and findings start existing at L1 (L0 is the
# baseline and compares against nothing). Keep failing loudly until there is something to gate.
verify:
	@echo "make verify: nothing to verify yet — L0 is the baseline and commits no findings." >&2
	@echo "The gate (findings.json, sign-flip rule over five repeats) starts at L1." >&2
	@exit 1

clean:
	@echo "cleaning scratch (results/ is never touched)"
	@rm -rf tmp/ .scratch/
	@find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "done"
