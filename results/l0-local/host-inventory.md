# Host inventory — saturn

Collected: 2026-10-11T03:29:30Z  ·  via `tools/host-inventory.sh`  ·  root: yes

## 1. OS and kernel

| field | value |
|---|---|
| hostname | saturn |
| uname -r | 7.0.0-38-generic |
| uname -m | x86_64 |
| distro | Ubuntu 26.04.1 LTS |
| uptime | up 15 hours, 49 minutes |
| virtualisation | none |
| secure boot | SecureBoot enabled |
> Bare metal or a VM matters: nested virtualisation can hide kernel features. Secure boot + kernel lockdown can block kprobes entirely.

## 2. eBPF prerequisites

| field | value |
|---|---|
| BTF /sys/kernel/btf/vmlinux | yes (7067009 bytes) |
| /proc/kallsyms symbols | 361213 |
| tcp_sendmsg in kallsyms | 1 |
| tcp_recvmsg in kallsyms | 1 |
| tracefs mounted | yes |
| debugfs tracing (root only) | yes |
| kprobe_events | yes |
| uprobe_events | yes |
| sched tracepoints | 32 |
| net tracepoints | 18 |
| kmem tracepoints | 16 |
| syscalls tracepoints | 736 |
| kernel.perf_event_paranoid | 4 |
| kernel.unprivileged_bpf_disabled | 2 |
| kernel lockdown | none [integrity] confidentiality |

Key kernel config flags:
```
CONFIG_BPF_SYSCALL=y
CONFIG_BPF_JIT=y
CONFIG_BPF_EVENTS=y
CONFIG_KPROBES=y
CONFIG_KPROBE_EVENTS=y
CONFIG_UPROBE_EVENTS=y
CONFIG_FTRACE=y
CONFIG_FUNCTION_TRACER=y
CONFIG_DEBUG_INFO_BTF=y
```

## 3. eBPF toolchain

| field | value |
|---|---|
| bpftrace | bpftrace v0.25.0 |
| bpftool | bpftool v7.7.0 |
| bcc-tools | 0 tools in /usr/share/bcc/tools |
| clang | Ubuntu clang version 21.1.8 (6ubuntu1) |
| llvm-strip | absent |
| libbpf | 1 |
| gcc | gcc (Ubuntu 15.2.0-16ubuntu1) 15.2.0 |
| go | absent |
| rustc | absent |
| python3 | Python 3.14.4 |
| uv | absent |
| docker | Docker version 29.1.3, build 29.1.3-0ubuntu4.1 |
| headers | yes (/usr/src/linux-headers-7.0.0-38-generic) |

## 4. GPU

| field | value |
|---|---|
| vendor | NVIDIA (nvidia-smi present) |

```
Sat Oct 10 22:29:31 2026       
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 595.99.02              Driver Version: 595.99.02      CUDA Version: 13.2     |
+-----------------------------------------+------------------------+----------------------+
| GPU  Name                 Persistence-M | Bus-Id          Disp.A | Volatile Uncorr. ECC |
| Fan  Temp   Perf          Pwr:Usage/Cap |           Memory-Usage | GPU-Util  Compute M. |
|                                         |                        |               MIG M. |
|=========================================+========================+======================|
|   0  NVIDIA GeForce RTX 4070 ...    Off |   00000000:01:00.0 Off |                  N/A |
| 33%   42C    P8             15W /  285W |   14826MiB /  16376MiB |     10%      Default |
|                                         |                        |                  N/A |
+-----------------------------------------+------------------------+----------------------+

+-----------------------------------------------------------------------------------------+
| Processes:                                                                              |
```

Full query (name, memory, compute capability, driver, CUDA):
```
index, name, memory.total [MiB], memory.used [MiB], compute_cap, driver_version, vbios_version
0, NVIDIA GeForce RTX 4070 Ti SUPER, 16376 MiB, 14826 MiB, 8.9, 595.99.02, 95.03.45.80.07
```
| field | value |
|---|---|
| GPU count | 1 |
| max CUDA the driver supports | CUDA Version: 13.2 |
| CUDA driver (nvidia-smi) | 595.99.02 |
| CUDA runtime (nvcc) | release 12.4 |
| cuLaunchKernel symbol | PRESENT in /usr/lib/x86_64-linux-gnu/libcuda.so.1 |

libcuda / libcudart libraries found:
```
	libnvidia-ml.so.1 (libc6,x86-64) => /usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1
	libnvidia-ml.so (libc6,x86-64) => /usr/lib/x86_64-linux-gnu/libnvidia-ml.so
	libcudart.so.12 (libc6,x86-64) => /usr/lib/x86_64-linux-gnu/libcudart.so.12
	libcudart.so (libc6,x86-64) => /usr/lib/x86_64-linux-gnu/libcudart.so
	libcuda.so.1 (libc6,x86-64) => /usr/lib/x86_64-linux-gnu/libcuda.so.1
	libcuda.so (libc6,x86-64) => /usr/lib/x86_64-linux-gnu/libcuda.so
```

CUDA symbols central to Phase 3 (uprobe targets):
```
/usr/lib/x86_64-linux-gnu/libcuda.so:
  cuGraphLaunch
  cuLaunchKernel
  cuLaunchKernelEx
  cuMemAlloc
  cuMemAllocAsync
  cuMemAllocFromPoolAsync
  cuMemAllocHost
  cuMemAllocManaged
  cuMemAllocPitch
  cuMemFree
  cuMemFreeAsync
  cuMemFreeHost
/usr/lib/x86_64-linux-gnu/libcuda.so.1:
  cuGraphLaunch
  cuLaunchKernel
  cuLaunchKernelEx
  cuMemAlloc
  cuMemAllocAsync
  cuMemAllocFromPoolAsync
  cuMemAllocHost
  cuMemAllocManaged
  cuMemAllocPitch
  cuMemFree
  cuMemFreeAsync
  cuMemFreeHost
/usr/lib/x86_64-linux-gnu/libcudart.so:
/usr/lib/x86_64-linux-gnu/libcudart.so.12:
```

All graphics devices (lspci):
```
01:00.0 VGA compatible controller: NVIDIA Corporation AD103 [GeForce RTX 4070 Ti SUPER] (rev a1)
```

## 5. CPU, memory, disk

| field | value |
|---|---|
| CPU model | Intel(R) Core(TM) i9-14900KF |
| CPU cores | 32 |
| CPU phys | 32 |
| NUMA nodes | 1 |
| RAM total | 30Gi |
| RAM avail | 23Gi |
| swap | 8.0Gi |
| hugepages | HugePages_Total:       0 |
| free in $HOME | 1606 GiB |
| free in /var/lib/docker | 1606 GiB |

Filesystems (model storage matters):
```
Filesystem      Size  Used Avail Use% Mounted on
/dev/nvme0n1p2  1.8T  133G  1.6T   8% /
efivarfs        256K  131K  121K  53% /sys/firmware/efi/efivars
/dev/nvme0n1p1  1.1G  6.4M  1.1G   1% /boot/efi
overlay         1.8T  133G  1.6T   8% /run/k3s/containerd/io.containerd.runtime.v2.task/k8s.io/7550b08e5fbc0466562bcad615a73f0555fffd1bb3ebf21be4c186fccd254a4b/rootfs
overlay         1.8T  133G  1.6T   8% /run/k3s/containerd/io.containerd.runtime.v2.task/k8s.io/263a36cb1137f5572abc074f6b36380250e988ceda541b0cd3376500d43ef8b4/rootfs
overlay         1.8T  133G  1.6T   8% /run/k3s/containerd/io.containerd.runtime.v2.task/k8s.io/577d2006c934d28ef4c494abf8157c59c26745d30825e90d4884bf19aa45ab1f/rootfs
overlay         1.8T  133G  1.6T   8% /run/k3s/containerd/io.containerd.runtime.v2.task/k8s.io/834f8dc053ddbee59f6882c989482c3a93c82152dfad59336826500e39b735c6/rootfs
overlay         1.8T  133G  1.6T   8% /run/k3s/containerd/io.containerd.runtime.v2.task/k8s.io/f06865eb8ae23d10257b59c4d7cc702d6e8a17dc5dddbf281701cf0be114d209/rootfs
overlay         1.8T  133G  1.6T   8% /run/k3s/containerd/io.containerd.runtime.v2.task/k8s.io/33d43ad0631c74265fc260e11e70e6af5ea56f5a8a2e1a8620b3e43f148f5f8c/rootfs
overlay         1.8T  133G  1.6T   8% /run/k3s/containerd/io.containerd.runtime.v2.task/k8s.io/be9dced3e65f760fd140d8c1327c01ce714c177330c26fde5e54e9240868705a/rootfs
overlay         1.8T  133G  1.6T   8% /run/k3s/containerd/io.containerd.runtime.v2.task/k8s.io/b3f6e2e31a8f51213cbbe69534488c2724a466cc2bdc330e6052643cd65426e5/rootfs
```

## 6. Existing inference stack

| field | value |
|---|---|
| llama.cpp | not found in usual places |
| llama-server | absent |
| vllm | absent |
| torch | absent |
| transformers | absent |
| huggingface cache | absent |

GGUF / safetensors files on disk (top 10 by size, KiB):
```
<none found>
```

Hugging Face hub cache (too deep for the find above):
```
<none found in /root/.cache/huggingface/hub>
```

## 7. Kubernetes and serving prerequisites

| field | value |
|---|---|
| container runtime | Docker version 29.1.3, build 29.1.3-0ubuntu4.1 |
| docker daemon | reachable |
| docker runtimes | io.containerd.runc.v2 nvidia runc (default: runc) |
| user in docker group | no |
| containerd | containerd github.com/containerd/containerd/v2 2.2.2  |
| podman | absent |
| kubectl | v1.37.1+k3s1 |
| k3s | k3s version v1.37.1+k3s1 (356c0254) |
| kind | absent |
| helm | absent |
| terraform | absent |
| opentofu | absent |
| pulumi | absent |
| argocd | absent |
| aws cli | absent |
| nvidia-container-toolkit | NVIDIA Container Toolkit CLI version 1.20.1 |
| nvidia kernel module | loaded |
| cgroup version | cgroup v2 |
| swap | 8.0Gi  (kubelet wants swap off) |
| br_netfilter | loaded  (k8s prerequisite) |
| overlay | loaded  (k8s prerequisite) |
| iptables | iptables v1.8.11 (nf_tables) |
| firewall | Status: inactive |
| k8s API port 6443 | listening |

**GPU-in-container probe** (opt-in):
_not run — set `PROBE_IMAGE=<image already present locally>` to start one short-lived container and run `nvidia-smi -L` in it. The image is never pulled._
> L0 needs a container runtime plus a GPU the driver can see. L1 needs k3s + kubectl.
> L5 needs terraform (or equivalent) plus a second node. Fargate cannot host the GPU node.

## 8. Network

| field | value |
|---|---|
| hostname -I | <private-ip> <private-ip> <private-ip> <private-ip>  |
| default route | default via <private-ip> dev <iface> proto dhcp src <private-ip> metric 600  |
| huggingface.co | HTTP 200 |
| ghcr.io | HTTP 401 |
| nvcr.io | HTTP 401 |
| docker hub | HTTP 401 |
| listening ports | 127.0.0.1:6444 0.0.0.0:17500 127.0.0.1:631 127.0.0.54:53 127.0.0.1:17600 127.0.0.1:17603 127.0.0.1:36611 127.0.0.53%lo:53  |

## 9. Root-only checks

| field | value |
|---|---|
| lockdown | none [integrity] confidentiality |
| dmesg bpf lines | 5 |
| tracefs contents | 64 entries |

## 10. Readiness verdict

| field | value |
|---|---|
| eBPF usable (BTF + kprobe_events) | YES |
| lockdown | none [integrity] confidentiality — kprobes **may** be blocked (verify empirically) |
| bpftrace installed | yes |
| GPU | NVIDIA GeForce RTX 4070 Ti SUPER (16376 MiB, compute 8.9) |
| Phase 3 (CUDA uprobes) viable | yes — libcuda exports cuLaunchKernel |
| largest weights that fit, per GPU | FP16 ~6B · FP8/INT8 ~13B · 4-bit ~26B params (80% of 16376 MiB, before KV cache and activations — a ceiling, not a recommendation) |

**Serving-stack readiness** (full-stack-serving levels):
| L0 (container + engine) | ready |
| L1 (k3s + kubectl) | ready |
| L2 (routing / Gateway API) | needs kubectl + helm |
| L3 (autoscaling / KEDA) | needs helm |
| L5 (cloud IaC) | no IaC tool found |
| GPU in containers | driver + toolkit present — NOT verified (re-run with PROBE_IMAGE=<local image>) |

---
_Generated by tools/host-inventory.sh. Read-only: nothing on this host was modified._
