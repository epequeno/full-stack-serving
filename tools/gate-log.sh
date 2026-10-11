#!/usr/bin/env bash
# Run the ADR 0008 gate and write its output where a reviewer can find it (ADR 0014).
#
#   tools/gate-log.sh <level> [out-dir]      e.g.  tools/gate-log.sh l1-k8s
#
# The log goes to results/<level>/gates/verify-<UTC timestamp>.txt and is committed. It is an
# audit record, never an input: nothing reads it and no number is taken from it. `make verify`
# itself stays read-only (ADR 0008) — this wrapper runs it, tees it, and exits with the gate's
# own status, so `make gate-log` is usable from a terminal and from a script.
set -uo pipefail

level=${1:?usage: tools/gate-log.sh <level> [out-dir]}
case "$level" in
  l1-k8s|l4-otel) ;;
  *) echo "gate-log: '${level}' has no gate — ADR 0008 starts at L1 (L0 is the baseline and commits no findings)." >&2; exit 2 ;;
esac

repo=$(cd "$(dirname "$0")/.." && pwd)
out_dir=${2:-$repo/results/$level/gates}
out="$out_dir/verify-$(date -u +%Y-%m-%dT%H%MZ).txt"
mkdir -p "$out_dir"

{
  echo "# make verify LEVEL=$level — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# host: $(uname -srm)"
  echo "# git:  $(git -C "$repo" rev-parse --short HEAD 2>/dev/null || echo unknown) — $(git -C "$repo" status --porcelain 2>/dev/null | wc -l | tr -d ' ') uncommitted path(s)"
  echo
} > "$out"

# The log is committed, and the repo is public: redact host-identifying literals at
# write time (the same policy as `make inventory` in the Makefile) — the verify stream
# carries uv's build paths and any traceback, all with the absolute home path in them.
# A regenerated log must not put the values back.
redact() {
  sed -E \
    -e 's|/home/[A-Za-z0-9._-]+|<home>|g' \
    -e 's|/Users/[A-Za-z0-9._-]+|<home>|g' \
    -e 's/(^|[^0-9])((192\.168|172\.(1[6-9]|2[0-9]|3[01]))(\.[0-9]{1,3}){2}|10(\.[0-9]{1,3}){3})/\1<private-ip>/g' \
    -e 's/dev wl[a-z0-9]*/dev <iface>/g'
}

( cd "$repo" && make --no-print-directory verify LEVEL="$level" ) 2>&1 | redact | tee -a "$out"
rc=${PIPESTATUS[0]}

{
  echo
  if [ "$rc" -eq 0 ]; then echo "result: PASS"; else echo "result: FAIL (exit $rc)"; fi
  echo "# written $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >> "$out"

echo "gate-log: $out" >&2
exit "$rc"
