#!/usr/bin/env bash
# host-inventory.sh — read-only host capability reconnaissance. Installs nothing and
# changes nothing (one opt-in exception: PROBE_IMAGE starts a short-lived container).
#
# Shared by two projects that run on the same host, so one run answers both:
#   ebpf-inference-observability  — BTF, kprobes/uprobes, tracepoints, CUDA symbols
#   full-stack-serving            — container runtime, k3s/helm, GPU-in-container, IaC
#
# The report is one document; read the sections your project needs.
#
#   ./tools/host-inventory.sh                    # print to stdout
#   ./tools/host-inventory.sh > inventory/report.md
#   sudo ./tools/host-inventory.sh               # adds the root-only checks
#   PROBE_IMAGE=ubuntu:22.04 ./tools/host-inventory.sh   # also prove the GPU is visible in a
#                                                        # container (image must be local; never pulled)
#
# Everything is best-effort: missing commands and unreadable files are reported,
# never fatal. Safe to run on a box you care about.

set -u
export LC_ALL=C

SUDO=""
[ "$(id -u)" -eq 0 ] && SUDO="yes"

have() { command -v "$1" >/dev/null 2>&1; }
tmo() { # run with a timeout where the platform has one
  if have timeout; then timeout "$@"; else shift; "$@"; fi
}
# First line of a command's stdout. 'absent' if the command is missing. Do NOT write
# `cmd | head -1 || echo absent` — the pipeline's status is head's, so the fallback never
# fires and the cell renders empty.
ver() {
  have "$1" || { echo absent; return; }
  local out; out=$("$@" 2>/dev/null | head -1)
  echo "${out:-present (no version output)}"
}
http() { # reachability of a URL: any HTTP status counts (401 from a registry /v2/ means reachable)
  have curl || { echo 'curl absent'; return; }
  local c; c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$1" 2>/dev/null)
  case "$c" in ''|000) echo unreachable ;; *) echo "HTTP $c" ;; esac
}

# Each table gets its header lazily: sec() and any prose/code block reset it (intable=0),
# so every run of kv rows renders as a Markdown table.
intable=0
sec() { intable=0; printf '\n## %s\n\n' "$1"; }
kv()  {
  if [ "$intable" -eq 0 ]; then printf '| field | value |\n|---|---|\n'; intable=1; fi
  printf '| %s | %s |\n' "$1" "${2:-—}"
}
run() { # run a command, print its output indented, tolerate failure
  local out; out=$("$@" 2>&1) || true
  printf '```\n%s\n```\n' "${out:-<no output>}"
}
exist() { [ -e "$1" ] && echo "yes" || echo "**no**"; }
modstate() { # is a kernel module loaded? '?' where lsmod is unavailable (e.g. macOS)
  if have lsmod; then
    lsmod | awk -v m="$1" '$1 == m { print "loaded"; found = 1 } END { if (!found) print "not loaded" }'
  else
    echo '? (lsmod absent)'
  fi
}

printf '# Host inventory — %s\n\n' "$(hostname 2>/dev/null || echo unknown)"
printf 'Collected: %s  ·  via `%s`  ·  root: %s\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" "$0" "${SUDO:-no}"

# ---------------------------------------------------------------- 1. OS / kernel
sec '1. OS and kernel'
kv 'hostname'      "$(hostname 2>/dev/null)"
kv 'uname -r'      "$(uname -r 2>/dev/null)"
kv 'uname -m'      "$(uname -m 2>/dev/null)"
kv 'distro'        "$(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-unknown}")"
kv 'uptime'        "$(uptime -p 2>/dev/null || uptime 2>/dev/null)"
kv 'virtualisation' "$(systemd-detect-virt 2>/dev/null || echo 'not systemd / unknown')"
kv 'secure boot'   "$(if have mokutil; then mokutil --sb-state 2>/dev/null || echo 'needs root'; else echo 'mokutil absent'; fi)"
echo '> Bare metal or a VM matters: nested virtualisation can hide kernel features. Secure boot + kernel lockdown can block kprobes entirely.'

# ------------------------------------------------------- 2. eBPF prerequisites
sec '2. eBPF prerequisites'
kv 'BTF /sys/kernel/btf/vmlinux' "$(if [ -e /sys/kernel/btf/vmlinux ]; then echo "yes ($(wc -c </sys/kernel/btf/vmlinux) bytes)"; else echo '**NO — CO-RE and bpftrace will not work**'; fi)"
kv '/proc/kallsyms symbols'      "$([ -r /proc/kallsyms ] && wc -l </proc/kallsyms || echo 'unreadable')"
kv 'tcp_sendmsg in kallsyms'     "$(if [ -r /proc/kallsyms ]; then grep -c ' tcp_sendmsg$' /proc/kallsyms; else echo unreadable; fi)"
kv 'tcp_recvmsg in kallsyms'     "$(if [ -r /proc/kallsyms ]; then grep -c ' tcp_recvmsg$' /proc/kallsyms; else echo unreadable; fi)"
kv 'tracefs mounted'             "$([ -r /proc/mounts ] && grep -q tracefs /proc/mounts && echo yes || echo 'no')"
kv 'debugfs tracing (root only)' "$([ -e /sys/kernel/debug/tracing/kprobe_events ] && echo yes || echo 'no / not readable')"
kv 'kprobe_events'               "$([ -e /sys/kernel/tracing/kprobe_events ] && echo yes || echo no)"
kv 'uprobe_events'               "$([ -e /sys/kernel/tracing/uprobe_events ] && echo yes || echo no)"
kv 'sched tracepoints'           "$(ls /sys/kernel/tracing/events/sched/ 2>/dev/null | wc -l)"
kv 'net tracepoints'             "$(ls /sys/kernel/tracing/events/net/ 2>/dev/null | wc -l)"
kv 'kmem tracepoints'            "$(ls /sys/kernel/tracing/events/kmem/ 2>/dev/null | wc -l)"
kv 'syscalls tracepoints'        "$(ls /sys/kernel/tracing/events/syscalls/ 2>/dev/null | wc -l)"
kv 'kernel.perf_event_paranoid'  "$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo '?')"
kv 'kernel.unprivileged_bpf_disabled' "$(cat /proc/sys/kernel/unprivileged_bpf_disabled 2>/dev/null || echo '?')"
kv 'kernel lockdown'             "$(cat /sys/kernel/security/lockdown 2>/dev/null || echo 'not readable (needs root) / absent')"

echo
echo 'Key kernel config flags:'
cfg="/boot/config-$(uname -r)"
if [ -r "$cfg" ]; then
  echo '```'
  for f in BPF_SYSCALL BPF_JIT BPF_EVENTS KPROBES KPROBE_EVENTS UPROBE_EVENTS FTRACE FUNCTION_TRACER DEBUG_INFO_BTF; do
    grep -E "^(CONFIG_${f}=|# CONFIG_${f} is not set)" "$cfg" 2>/dev/null || echo "CONFIG_${f}: <absent>"
  done
  echo '```'
else
  echo "_($cfg not readable — /proc/config.gz or the distro's config package would have it)_"
  if [ -r /proc/config.gz ]; then
    zcat /proc/config.gz 2>/dev/null | grep -E 'CONFIG_(BPF_SYSCALL|KPROBES|DEBUG_INFO_BTF)=' || true
  fi
fi

# ------------------------------------------------------------ 3. toolchain
sec '3. eBPF toolchain'
kv 'bpftrace'   "$(bpftrace --version 2>/dev/null || echo '**absent**')"
kv 'bpftool'    "$(ver bpftool version)"
kv 'bcc-tools'  "$(ls /usr/share/bcc/tools 2>/dev/null | wc -l) tools in /usr/share/bcc/tools"
kv 'clang'      "$(ver clang --version)"
kv 'llvm-strip' "$(ver llvm-strip --version)"
kv 'libbpf'     "$(ldconfig -p 2>/dev/null | grep -c libbpf)"
kv 'gcc'        "$(ver gcc --version)"
kv 'go'         "$(go version 2>/dev/null || echo absent)"
kv 'rustc'      "$(rustc --version 2>/dev/null || echo absent)"
kv 'python3'    "$(python3 --version 2>/dev/null || echo absent)"
kv 'uv'         "$(uv --version 2>/dev/null || echo absent)"
kv 'docker'     "$(docker --version 2>/dev/null || echo absent)"
kv 'headers'    "$([ -d "/lib/modules/$(uname -r)/build" ] && echo "yes ($(readlink -f "/lib/modules/$(uname -r)/build" 2>/dev/null))" || echo 'no (kernel headers/module build tree absent)')"

# --------------------------------------------------------------- 4. GPU
sec '4. GPU'
if have nvidia-smi; then
  kv 'vendor' 'NVIDIA (nvidia-smi present)'
  echo
  echo '```'
  nvidia-smi 2>&1 | head -15
  echo '```'
  echo
  echo 'Full query (name, memory, compute capability, driver, CUDA):'
  echo '```'
  nvidia-smi --query-gpu=index,name,memory.total,memory.used,compute_cap,driver_version,vbios_version \
             --format=csv 2>&1 | head -12
  echo '```'
  intable=0
  kv 'GPU count'                    "$(nvidia-smi -L 2>/dev/null | wc -l | tr -d ' ')"
  kv 'max CUDA the driver supports' "$(nvidia-smi 2>/dev/null | grep -o 'CUDA Version: [0-9.]*' | head -1)"
  kv 'CUDA driver (nvidia-smi)'     "$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)"
  kv 'CUDA runtime (nvcc)'          "$(nvcc --version 2>/dev/null | grep -o 'release [0-9.]*' || echo 'nvcc absent')"
  culk=""
  for p in $(ldconfig -p 2>/dev/null | awk '/libcuda\.so/{print $NF}'); do
    nm -D --defined-only "$p" 2>/dev/null | grep -qw cuLaunchKernel && culk="PRESENT in $p" && break
  done
  kv 'cuLaunchKernel symbol'   "${culk:-**not found**}"
else
  kv 'vendor' 'no nvidia-smi'
fi
echo
echo 'libcuda / libcudart libraries found:'
echo '```'
ldconfig -p 2>/dev/null | grep -E 'libcuda\.so|libcudart\.so|libnvidia-ml' | head -8 || echo '<none>'
echo '```'
echo
echo 'CUDA symbols central to Phase 3 (uprobe targets):'
echo '```'
for p in $(ldconfig -p 2>/dev/null | awk '/libcuda\.so|libcudart\.so/{print $NF}' | sort -u); do
  [ -r "$p" ] || continue
  printf '%s:\n' "$p"
  nm -D --defined-only "$p" 2>/dev/null \
    | grep -oE 'cu(LaunchKernel|LaunchKernelEx|MemAlloc|MemFree|MemcpyHtoD|MemcpyDtoH|GraphLaunch)[A-Za-z]*' \
    | sort -u | sed 's/^/  /' | head -12
done
echo '```'
if have rocm-smi; then
  echo '**ROCm also present:**'; run rocm-smi --showproductname
fi
echo
echo 'All graphics devices (lspci):'
echo '```'
(lspci 2>/dev/null | grep -iE 'vga|3d|display' || echo '<lspci absent or no display devices>')
echo '```'

# ------------------------------------------------------- 5. CPU / RAM / disk
sec '5. CPU, memory, disk'
kv 'CPU model'  "$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ //' || echo '?')"
kv 'CPU cores'  "$(nproc 2>/dev/null)"
kv 'CPU phys'   "$(grep -c '^processor' /proc/cpuinfo 2>/dev/null)"
kv 'NUMA nodes' "$(ls -d /sys/devices/system/node/node* 2>/dev/null | wc -l)"
kv 'RAM total'  "$(free -h 2>/dev/null | awk '/^Mem:/{print $2}' || echo '?')"
kv 'RAM avail'  "$(free -h 2>/dev/null | awk '/^Mem:/{print $7}' || echo '?')"
kv 'swap'       "$(free -h 2>/dev/null | awk '/^Swap:/{print $2}' || echo '?')"
kv 'hugepages'  "$(grep -i HugePages_Total /proc/meminfo 2>/dev/null || echo '?')"
kv 'free in $HOME'     "$(df -Pk "$HOME" 2>/dev/null | awk 'NR==2{printf "%.0f GiB", $4/1048576}')"
kv 'free in /var/lib/docker' "$([ -d /var/lib/docker ] && df -Pk /var/lib/docker 2>/dev/null | awk 'NR==2{printf "%.0f GiB", $4/1048576}' || echo 'n/a (no /var/lib/docker or not readable)')"
echo
echo 'Filesystems (model storage matters):'
echo '```'
df -h -x tmpfs -x devtmpfs -x squashfs 2>/dev/null | head -12
echo '```'

# ------------------------------------------------------- 6. inference stack
sec '6. Existing inference stack'
kv 'llama.cpp'      "$(d=$(ls -d ~/llama.cpp /opt/llama.cpp /usr/local/llama.cpp 2>/dev/null | head -1); echo "${d:-not found in usual places}")"
kv 'llama-server'   "$(command -v llama-server || command -v llama-cli || echo absent)"
kv 'vllm'           "$(python3 -c 'import vllm; print(vllm.__version__)' 2>/dev/null || echo absent)"
kv 'torch'          "$(python3 -c 'import torch; print(torch.__version__, torch.cuda.is_available())' 2>/dev/null || echo absent)"
kv 'transformers'   "$(python3 -c 'import transformers; print(transformers.__version__)' 2>/dev/null || echo absent)"
kv 'huggingface cache' "$(s=$(du -sh ~/.cache/huggingface 2>/dev/null | cut -f1); echo "${s:-absent}")"
echo
echo 'GGUF / safetensors files on disk (top 10 by size, KiB):'
echo '```'
found=$(find ~ /opt /srv -maxdepth 4 \( -name '*.gguf' -o -name '*.safetensors' \) -exec du -kL {} + 2>/dev/null | sort -rn | head -10)
echo "${found:-<none found>}"
echo '```'
echo
echo 'Hugging Face hub cache (too deep for the find above):'
echo '```'
hf="${HF_HOME:-$HOME/.cache/huggingface}/hub"
hfm=$(ls -d "$hf"/models--* 2>/dev/null | sed 's|.*/models--||; s|--|/|g')
echo "${hfm:-<none found in $hf>}"
echo '```'

# ------------------------------------- 7. Kubernetes / serving prerequisites
sec '7. Kubernetes and serving prerequisites'
kv 'container runtime' "$(docker --version 2>/dev/null || echo 'docker absent')"
dock=no
if have docker && tmo 10 docker info >/dev/null 2>&1; then dock=yes; fi
kv 'docker daemon'     "$(if ! have docker; then echo 'docker absent'; elif [ "$dock" = yes ]; then echo reachable; else echo '**not reachable** (daemon down, or no permission — is the user in the docker group?)'; fi)"
kv 'docker runtimes'   "$([ "$dock" = yes ] && tmo 10 docker info --format '{{range $k,$v := .Runtimes}}{{$k}} {{end}}(default: {{.DefaultRuntime}})' 2>/dev/null || echo 'n/a')"
kv 'user in docker group' "$(if id -nG 2>/dev/null | tr ' ' '\n' | grep -qx docker; then echo yes; else echo no; fi)"
kv 'containerd'        "$(ver containerd --version)"
kv 'podman'            "$(podman --version 2>/dev/null || echo absent)"
kv 'kubectl'           "$(have kubectl && kubectl version --client 2>/dev/null | head -1 | sed 's/^[^:]*: *//' || echo absent)"
kv 'k3s'               "$(ver k3s --version)"
kv 'kind'              "$(kind version 2>/dev/null || echo absent)"
kv 'helm'              "$(helm version --short 2>/dev/null || echo absent)"
kv 'terraform'         "$(ver terraform version)"
kv 'opentofu'          "$(ver tofu version)"
kv 'pulumi'            "$(pulumi version 2>/dev/null || echo absent)"
kv 'argocd'            "$(have argocd && argocd version --client --short 2>/dev/null | head -1 || echo absent)"
kv 'aws cli'           "$(have aws && aws --version 2>&1 | head -1 || echo absent)"
kv 'nvidia-container-toolkit' "$( if have nvidia-smi; then (have nvidia-ctk && nvidia-ctk --version 2>/dev/null | head -1) || echo '**absent — a GPU will not be visible inside containers**'; else echo 'no GPU on this host'; fi )"
kv 'nvidia kernel module' "$(modstate nvidia)"
kv 'cgroup version'    "$( [ -e /sys/fs/cgroup ] && stat -fc %T /sys/fs/cgroup 2>/dev/null | sed 's/tmpfs/cgroup v1/; s/cgroup2fs/cgroup v2/' || echo '? (no /sys/fs/cgroup)')"
kv 'swap'              "$(have free && free -h 2>/dev/null | awk '/^Swap:/{print $2}' || echo '?')  (kubelet wants swap off)"
kv 'br_netfilter'      "$(modstate br_netfilter)  (k8s prerequisite)"
kv 'overlay'           "$(modstate overlay)  (k8s prerequisite)"
kv 'iptables'          "$(iptables --version 2>/dev/null || echo absent)"
kv 'firewall'          "$( (have ufw && ufw status 2>/dev/null | head -1) || (have firewall-cmd && firewall-cmd --state 2>/dev/null) || echo 'none detected')"
kv 'k8s API port 6443' "$( (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -q ':6443' && echo 'listening' || echo 'not listening')"
probe_state=none
echo
echo '**GPU-in-container probe** (opt-in):'
if [ -z "${PROBE_IMAGE:-}" ]; then
  echo '_not run — set `PROBE_IMAGE=<image already present locally>` to start one short-lived container and run `nvidia-smi -L` in it. The image is never pulled._'
elif have docker || have podman; then
  if have docker; then
    probe_cmd=(docker run --rm --pull=never --gpus all)
  else
    probe_cmd=(podman run --rm --pull=never --device nvidia.com/gpu=all)
  fi
  probe_out=$(tmo 60 "${probe_cmd[@]}" "$PROBE_IMAGE" nvidia-smi -L 2>&1); probe_rc=$?
  echo "\`${probe_cmd[*]} $PROBE_IMAGE nvidia-smi -L\` → exit $probe_rc"
  echo '```'
  echo "${probe_out:-<no output>}"
  echo '```'
  if [ "$probe_rc" -eq 0 ] && printf '%s\n' "$probe_out" | grep -q '^GPU '; then probe_state=pass; else probe_state=fail; fi
else
  echo '_no docker or podman — nothing to probe with._'
fi
intable=0
echo '> L0 needs a container runtime plus a GPU the driver can see. L1 needs k3s + kubectl.'
echo '> L5 needs terraform (or equivalent) plus a second node. Fargate cannot host the GPU node.'

# ---------------------------------------------------------------- 8. network
sec '8. Network'
kv 'hostname -I' "$(hostname -I 2>/dev/null || echo '?')"
kv 'default route' "$(r=$(ip route show default 2>/dev/null | head -1); echo "${r:-? (no ip command or no default route)}")"
kv 'huggingface.co'     "$(http https://huggingface.co)"
kv 'ghcr.io'            "$(http https://ghcr.io/v2/)"
kv 'nvcr.io'            "$(http https://nvcr.io/v2/)"
kv 'docker hub'         "$(http https://registry-1.docker.io/v2/)"
kv 'listening ports' "$( (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | awk 'NR>1{print $4}' | head -8 | tr '\n' ' ')"

# ------------------------------------------------------------ 9. root-only
sec '9. Root-only checks'
if [ -n "$SUDO" ]; then
  kv 'lockdown'        "$(cat /sys/kernel/security/lockdown 2>/dev/null || echo absent)"
  kv 'dmesg bpf lines' "$(dmesg 2>/dev/null | grep -ic bpf)"
  kv 'tracefs contents' "$(ls /sys/kernel/tracing 2>/dev/null | wc -l) entries"
else
  echo 'Not root — re-run with `sudo` for: lockdown state, tracefs contents, dmesg.'
fi

# --------------------------------------------------------------- 10. verdict
sec '10. Readiness verdict'

btf=$([ -e /sys/kernel/btf/vmlinux ] && echo yes || echo no)
tracefs=$([ -e /sys/kernel/tracing/kprobe_events ] && echo yes || echo no)
lockdown="$(cat /sys/kernel/security/lockdown 2>/dev/null || echo unknown)"
kv 'eBPF usable (BTF + kprobe_events)' "$([ "$btf" = yes ] && [ "$tracefs" = yes ] && echo 'YES' || echo 'NO — see section 2')"
case "$lockdown" in
  *"[none]"*|unknown) kv 'lockdown' "$lockdown (kprobes expected to work)" ;;
  *) kv 'lockdown' "$lockdown — **kprobes may be blocked**" ;;
esac
kv 'bpftrace installed' "$(have bpftrace && echo yes || echo 'no — install with the distro package')"

if have nvidia-smi; then
  vram=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
  cap=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1)
  kv 'GPU' "$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1) (${vram:-?} MiB, compute ${cap:-?})"
  kv 'Phase 3 (CUDA uprobes) viable' "$([ -n "${culk:-}" ] && echo 'yes — libcuda exports cuLaunchKernel' || echo '**no — cuLaunchKernel not found in libcuda**')"
  if [ -n "${vram:-}" ] && [ "$vram" -gt 0 ] 2>/dev/null; then
    fit=$(awk -v v="$vram" 'BEGIN { g = v/1024*0.8; printf "FP16 ~%.0fB · FP8/INT8 ~%.0fB · 4-bit ~%.0fB params", g/2, g, g*2 }')
    kv 'largest weights that fit, per GPU' "$fit (80% of ${vram} MiB, before KV cache and activations — a ceiling, not a recommendation)"
  fi
else
  kv 'GPU' 'none detected — Phase 3 needs an NVIDIA GPU; Phases 1/2/4 still work on CPU'
fi

echo
echo '**Serving-stack readiness** (full-stack-serving levels):'
crt=no; { have docker || have containerd || have podman; } && crt=yes
gpu=no; have nvidia-smi && gpu=yes
ctk=no; have nvidia-ctk && ctk=yes
kv 'L0 (container + engine)' "$( if [ "$crt" = no ]; then echo '**NO container runtime**'; elif have docker && [ "$dock" = no ]; then echo '**docker present, daemon not reachable**'; elif [ "$gpu" = no ]; then echo 'runtime ok, but no NVIDIA GPU (CPU-only)'; else echo ready; fi )"
kv 'L1 (k3s + kubectl)' "$( if have k3s && have kubectl; then echo ready; else echo "no — k3s:$(have k3s && echo yes || echo no) kubectl:$(have kubectl && echo yes || echo no)"; fi )"
kv 'L2 (routing / Gateway API)' "$( if have kubectl && have helm; then echo 'helm present — installable'; else echo 'needs kubectl + helm'; fi )"
kv 'L3 (autoscaling / KEDA)' "$( have helm && echo 'helm present — installable' || echo 'needs helm' )"
kv 'L5 (cloud IaC)' "$( if have terraform || have tofu || have pulumi; then echo ready; else echo 'no IaC tool found'; fi )"
if [ "$probe_state" = pass ]; then
  kv 'GPU in containers' '**verified** — the probe container saw the GPU'
elif [ "$probe_state" = fail ]; then
  kv 'GPU in containers' '**FAILED** — see probe output in section 7'
elif [ "$gpu" = yes ] && [ "$ctk" = no ]; then
  kv 'GPU in containers' '**driver present, nvidia-container-toolkit missing — k8s will not see the GPU**'
elif [ "$gpu" = yes ]; then
  kv 'GPU in containers' 'driver + toolkit present — NOT verified (re-run with PROBE_IMAGE=<local image>)'
else
  kv 'GPU in containers' 'no NVIDIA GPU — L0 runs CPU-only; L5 needs a cloud GPU node'
fi

printf '\n---\n_Generated by tools/host-inventory.sh. Read-only: nothing on this host was modified._\n'
