# 0002 — Size the workload for observability, not capability

- **Status:** accepted
- **Date:** 2026-10-09

## Context

The project is about *how* a serving stack is built and measured, not *what* it serves. The model is
a fixed input to every experiment, not a subject of study. Benchmarking large models or
hardware is already a non-goal (`docs/design.md`).

Model size still constrains everything downstream, and not only through VRAM:

- **L2 and L3 need more than one replica.** Prefix-aware vs round-robin routing is meaningless with
  one backend, and queue-depth autoscaling needs somewhere to scale into. The host is a single
  GPU on a home LAN. A bigger model leaves no room for a second replica.
- **Iteration cost.** Weight download, load and cold start occur on every bring-up, every `make
  measure`, and every L3 scale-up. A large model makes each experiment slow enough that it gets run
  less often and reproduced less carefully.
- **Observability.** The levels need the engine to expose the signals under test: queue depth,
  KV-cache utilisation, prefix-cache hit counters. These are properties of the engine and its
  configuration, not of the model's capability.

Both ways of getting this wrong are real. Too large: the stack can't show the behaviours the
levels exist to show. Too small: per-request GPU time shrinks until client, HTTP and gateway
overhead dominate, so the measurement mostly measures the harness.

The host GPU has **not been inventoried**, so no concrete model, quantization or replica count can
be chosen yet.

## Decision

Choose the **smallest model that still makes the behaviour under test visible**, and prefer
configurations that are easy to observe over ones that are impressive.

A candidate model is acceptable only if it meets all of these:

1. **Fits at least two replicas** on the host GPU, each with enough KV-cache headroom that cache
   pressure, not weight size, is what a load ramp exercises. (Exactly *how* two replicas share the
   GPU is **not decided here**, see below.)
2. **Starts fast.** Cold start is short enough that a full `make measure` is routine and L3's
   scale-up latency can be measured many times.
3. **Is not so small that the harness is the bottleneck.** At the L0 concurrency levels, GPU
   compute must dominate server-side latency. Checked using the harness-overhead measurement
   already required by `measure/README.md`, not assumed.
4. **Downloads without an account or token, under a licence that permits redistribution of
   the measurements.** A fresh clone must be able to run it.
5. **Is supported by the chosen engine's metrics endpoint and prefix caching**, so L2 and L3
   have real signals to read.

The prompt fixtures are chosen with the same bias: long shared prefixes, so prefill cost is
non-trivial even on a small model and prefix-cache effects are large enough to measure.

**Not decided here** (waits on the host inventory; each is its own ADR):

- The concrete model and quantization.
- How L2/L3 get multiple replicas: fractional-GPU replicas, N copies of a smaller model, or
  the L5 node.
- Whether L2/L3 are cut, if the inventoried GPU cannot satisfy (1).

## Consequences

- **Absolute numbers do not transfer.** TTFT/ITL on a small model say nothing about a 70B model.
  The claims this repo may make are *relative*: L1 vs L0, prefix-aware vs round-robin, scaled
  vs fixed. Every results page must state the model size next to the numbers.
- **Cold-start and scale-up latency are optimistic.** Small weights load quickly. L3's cold-start
  figure must be reported with the model's size, and never presented as typical.
- **Criterion 3 can fail.** If the harness-overhead measurement shows the model is too small, the
  model changes. It does not get excused. This is the main way this decision could be wrong.
- **Criterion 1 may not be satisfiable** on a small GPU. That is a finding to record, and it
  triggers the cut-or-rescope ADR, not a quiet exception.
- The model becomes an interchangeable, pinned parameter in `stack/l0-local/`. Swapping it
  should not require touching `loadgen/` or `measure/`.
