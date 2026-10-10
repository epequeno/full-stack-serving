#!/usr/bin/env python3
"""Generate the committed prompt fixtures. Stdlib only; output is byte-reproducible.

    python3 loadgen/fixtures/generate.py            # (re)write the fixtures + SHA256SUMS
    python3 loadgen/fixtures/generate.py --check    # regenerate in memory, compare to disk

Every word is drawn from WORDS below, which was written for this repo. No third-party text or
dataset is used, so there is nothing to attribute or relicense.

Selection uses SHA-256, not `random`, so the output does not depend on the Python version.
Sizes are in *words*. Token counts depend on the model's tokenizer and are measured at L0 from the
server's `usage`; they are not claimed here.
"""
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
SEED = "full-stack-serving/fixtures/v1"

# Shape. Both sets have the same number of requests and about the same prompt length, so the
# only thing that differs between them is whether prefixes are shared.
N_REQUESTS = 256
GROUPS = 8                 # shared-prefix set: requests per group = N_REQUESTS / GROUPS
PREFIX_WORDS = 800         # shared (or, in the distinct set, unshared) body
SUFFIX_WORDS = 40          # unique per request
MAX_TOKENS = 128           # output length is fixed by ignore_eos, so it is the same every run

WORDS = (
    "river stone market window engine garden paper signal harbor copper winter lantern bridge "
    "orchard thread ladder compass meadow anchor ribbon furnace valley mirror candle button "
    "hammer pillow barrel feather saddle kettle tunnel pepper velvet marble basket cellar "
    "trumpet shadow pebble quarry cabin glacier lever cotton ember prairie anvil whistle "
    "plank beacon cobble thimble rooftop canyon ferry blanket clock drum fountain gravel "
    "hinge island jacket kernel lagoon magnet needle oyster parcel quilt radish sparrow "
    "teapot umbrella village walnut yarn zipper almond bucket cargo dolphin eagle fabric "
    "gadget harvest iron jungle knot ledger mason nectar outpost pigeon quiver rudder "
    "satchel timber upland vessel wagon yoke zephyr archive bellows cistern dynamo "
    "estuary foundry granary hearth inkwell junction kiln lattice mortar nursery observatory "
    "pavilion quay reservoir silo trellis vineyard warehouse apiary boulder chimney dockyard "
    "easel flint gable hatch ironwork jetty keystone lighthouse mill nutmeg orbit pasture "
    "rafter scaffold tannery utility vault wharf yardarm abacus bobbin crucible distillery "
    "embankment forge gazebo hangar incline jib kennel loom monolith niche overpass "
    "pylon quarterdeck ramp sluice turbine understory viaduct windlass"
).split()
assert len(WORDS) == len(set(WORDS)), "duplicate word in WORDS"


def h(*parts) -> int:
    d = hashlib.sha256(":".join((SEED,) + tuple(str(p) for p in parts)).encode()).digest()
    return int.from_bytes(d[:8], "big")


def text(label: str, n_words: int) -> str:
    """n_words of filler, in sentences of 9-16 words, fully determined by (label, n_words)."""
    out, i = [], 0
    while i < n_words:
        k = min(9 + h(label, "len", i) % 8, n_words - i)
        s = [WORDS[h(label, "w", i + j) % len(WORDS)] for j in range(k)]
        s[0] = s[0].capitalize()
        out.append(" ".join(s) + ".")
        i += k
    return " ".join(out)


def record(set_name, idx, group, body, suffix):
    return {
        "id": f"{set_name}-{idx:04d}",
        "set": set_name,
        "group": group,
        "prompt": f"{body}\n\nQuestion {idx:04d}: {suffix}\nAnswer:",
        "max_tokens": MAX_TOKENS,
        "ignore_eos": True,
    }


def distinct():
    return [
        record("distinct", i, None, text(f"distinct-body-{i}", PREFIX_WORDS),
               text(f"distinct-suffix-{i}", SUFFIX_WORDS))
        for i in range(N_REQUESTS)
    ]


def shared_prefix():
    per_group = N_REQUESTS // GROUPS
    bodies = [text(f"shared-body-{g}", PREFIX_WORDS) for g in range(GROUPS)]
    # Group membership and request order come from a hash, NOT from i % GROUPS. A periodic
    # order would alias with round-robin routing (8 groups over 2 replicas puts every group on
    # one replica) and make the round-robin baseline look cache-perfect.
    slots = [(g, k) for g in range(GROUPS) for k in range(per_group)]
    slots.sort(key=lambda s: h("order", s[0], s[1]))
    return [
        record("shared-prefix", i, g, bodies[g], text(f"shared-suffix-{g}-{k}", SUFFIX_WORDS))
        for i, (g, k) in enumerate(slots)
    ]


def render(records) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=True, sort_keys=True) + "\n" for r in records).encode()


def outputs() -> dict:
    files = {"distinct.jsonl": render(distinct()), "shared-prefix.jsonl": render(shared_prefix())}
    sums = "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in sorted(files.items()))
    files["SHA256SUMS"] = sums.encode()
    return files


def aliasing_report() -> list[str]:
    """Under round-robin over R replicas, every group must land on >= 2 replicas."""
    recs = shared_prefix()
    lines, ok = [], True
    for r in (2, 3, 4):
        seen = {g: set() for g in range(GROUPS)}
        for i, rec in enumerate(recs):
            seen[rec["group"]].add(i % r)
        worst = min(len(v) for v in seen.values())
        ok &= worst >= 2
        lines.append(f"round-robin over {r} replicas: fewest replicas any group lands on = {worst}")
    if not ok:
        raise SystemExit("FAIL: shared-prefix order aliases with round-robin\n" + "\n".join(lines))
    return lines


def main() -> int:
    files = outputs()
    for line in aliasing_report():
        print(line)
    if "--check" in sys.argv:
        bad = [n for n, b in files.items() if not (HERE / n).exists() or (HERE / n).read_bytes() != b]
        print("MISMATCH: " + ", ".join(bad) if bad else "OK: committed fixtures match the generator")
        return 1 if bad else 0
    for n, b in files.items():
        (HERE / n).write_bytes(b)
        print(f"wrote {n} ({len(b)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
