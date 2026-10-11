# 0013 — Run the open-loop half at L0

- **Status:** proposed
- **Date:** 2026-10-11

## Context

**full-stack-serving ADR 0004** supports two load models and assigns them to levels:

| Level | Model |
|---|---|
| L0, L1 | **Closed loop**, concurrency 1/4/16/64 |
| L2, L3 | **Open loop**, target arrival rate |

That assignment was correct when it was written, because open loop exists to expose the queueing
that L2 (routing) and L3 (queue-depth autoscaling) react to.

**The assignment assumed L2/L3 would run. They will not, for now.** `docs/design.md`'s own risk
mitigation says it: *"L2 and L3 have fiddly failure modes and need more than one replica.
Timebox them; cut if the GPU cannot host replicas."* There is one GPU. No second system is being
provisioned. So L2/L3 are cut by the design's own instruction, and with them the only levels
ADR 0004 gives open loop to.

**The consequence ADR 0004 itself names is therefore left unmeasured.** From its Consequences:

> **Closed-loop tails at saturation are understated.** The ADR does not fix this. It limits the
> claim, and L0/L1 results must not be presented as overload behaviour.

So today the repo holds a queueing-shaped finding, measured by a method that cannot build a queue
deeper than N. **full-stack-serving ADR 0009 / L4** measured the span-to-client TTFT gap growing
about an order of magnitude from c=1 to c=64 and concluded that the unspanned path — HTTP
frontend, event loop, SSE delivery — **queues under load**. That is a statement about queueing
behaviour produced entirely by a closed-loop client, whose coordinated omission is exactly the
bias that would distort it.

Meanwhile the capability already exists and is unexercised. `loadgen/client.py` implements
`--mode open` with **seeded Poisson arrivals**, `--rate`, `--duration`, `--inflight-cap`,
per-request `scheduled_ms`/`sent_ms` so schedule lag is computable, and `refused` counting for
requests that would exceed the cap. `loadgen/README.md` documents it as the contract. No target
in the `Makefile` ever runs it in open mode, and no committed result uses it.

Open loop needs one container, one GPU and one host. It is not blocked by the thing that blocks
L2/L3: it needs no second replica and no router.

## Decision

**Run the open-loop half at L0**, single host, single GPU, as a supplementary experiment at the
same endpoint L0 already uses.

- **It is not a level.** It introduces no L-number, no entry in the README's level table, and no
  change to the cut order. It is a second experiment at L0's endpoint. `docs/design.md`'s level
  list and `README.md`'s level table are unchanged by this ADR.
- **It does not alter L0's or L1's committed numbers.** Those stay closed-loop and stay the
  baseline L1 is compared against (ADR 0008). Open-loop results are committed separately and
  never substituted into the L0 baseline.
- **Rate is the independent variable.** Sweep λ across a range that spans the saturation point
  the closed-loop matrix brackets (the c=1/4/16/64 cells), with seeded Poisson arrivals so the
  schedule is reproducible.
- **The schedule-lag invalidity rule is declared before the data, and reported.** ADR 0004
  requires a run whose lag exceeds a threshold to be logged as invalid, "per `measure/README.md`"
  — but **`measure/README.md` does not define that number.** It is fixed in the experiment's own
  protocol and recorded in its `env`/protocol block *before* the run, in the manner of ADR 0007's
  bars and ADR 0009's declared prediction. Choosing it after seeing the results would let the
  harness's own failure be silently reclassified as server behaviour, which is the failure mode
  the rule exists to prevent.
- **Refused requests are reported, never hidden.** The in-flight cap and the count of requests
  refused for exceeding it are part of the result.
- **The result states its own limit.** Open-loop tails include whatever the *client* cannot keep
  up with. The lag percentiles are reported next to every latency number so a reader can see
  whether the harness or the server was the constraint.

## Consequences

- **ADR 0004's unmeasured consequence becomes measurable.** The repo gains a statement about
  behaviour the closed-loop design cannot reach, on hardware it already has.
- **L4's gap finding gains a second view.** If the unspanned path queues under load, open loop
  should show the tail bending in a way closed loop cannot produce. If it does not, that is
  evidence *against* the closed-loop reading of the L4 gap — which is worth knowing either way.
- **A second load model in the harness is a second way to be wrong.** The lag rule is the
  guard; exercising it deliberately (including at least one run that is *expected* to go invalid)
  is part of validating the mode rather than assuming it.
- **`results/` layout.** `measure/README.md` says "one directory per level, named exactly as the
  level". A supplementary experiment at L0 therefore lives under the L0 level directory, not as a
  new sibling level directory — the load model is named inside it, and its README states that its
  numbers are not comparable to the closed-loop matrix.
- **ADR 0004 is superseded by this record** (its status is set to `superseded by 0013`). Its
  assignment no longer holds where it could not be exercised, and it is restated here so that
  superseding it does not lose the half that does: **L0 and L1 stay closed-loop** and the
  closed-loop matrix stays the baseline L1 is compared against (ADR 0008); the open-loop half
  moves from L2/L3, which are cut, to L0. 0013 introduces no L-number and no new level —
  `docs/design.md`'s level list and `README.md`'s level table are unchanged.
- **Cost.** The client must sustain the arrival rate. At high λ the run is expected to go invalid;
  that is a finding about the harness, and it caps the range in which server behaviour is
  observable at all.