"""Balanced session design: who sees which items, in which order.

Guarantees (checked by ``check_batches`` and the tests):

* every analysis item appears in exactly ``votes_per_item`` sessions;
* no session contains the same item twice;
* session lengths (in words) are as even as the items allow;
* each session gets ``gold.per_session`` distinct gold items, used equally often;
* every attention item appears once per session, in its configured half;
* the first item of a session is always an ordinary analysis item.

Algorithm: deal the items ``votes_per_item`` times ("rounds"). In each round every
item goes to exactly one session of that round, longest items first (with a little
random jitter), always to the open session with the fewest words so far.
"""

from __future__ import annotations

import math
import random
from collections import Counter

from .config import ConfigError


def build_batches(cfg: dict, items: list[dict], prefix: str = "s") -> list[dict]:
    d = cfg["design"]
    rng = random.Random(d["seed"])
    gold_ids = [g["id"] for g in d["gold"]["items"]]
    item_ids = {it["id"] for it in items}
    missing = [g for g in gold_ids if g not in item_ids]
    if missing:
        raise ConfigError(f"design.gold.items: not in the items file: {missing}")
    include = d.get("include")
    pool = [it for it in items if it["id"] not in gold_ids and (include is None or it["id"] in set(map(str, include)))]
    if not pool:
        raise ConfigError("design: no analysis items left (all are gold or excluded by `include`)")
    per = d["items_per_session"]
    if per > len(pool):
        raise ConfigError(f"design.items_per_session ({per}) exceeds the number of analysis items ({len(pool)})")
    words = {it["id"]: it["words"] for it in pool}

    n_per_round = math.ceil(len(pool) / per)
    # sizes within a round differ by at most one item
    base, extra = divmod(len(pool), n_per_round)
    sizes = [base + (1 if i < extra else 0) for i in range(n_per_round)]

    sessions = []
    for _ in range(d["votes_per_item"]):
        bins = [[] for _ in range(n_per_round)]
        load = [0] * n_per_round
        order = sorted(pool, key=lambda it: -it["words"] * rng.uniform(0.7, 1.3))
        for it in order:
            open_b = [b for b in range(n_per_round) if len(bins[b]) < sizes[b]]
            b = min(open_b, key=lambda b: (load[b], rng.random()))
            bins[b].append(it["id"])
            load[b] += it["words"]
        sessions.extend(bins)
    rng.shuffle(sessions)

    g_per = d["gold"]["per_session"]
    attn = d.get("attention") or []
    out = []
    for i, ids in enumerate(sessions):
        seq = [{"id": x, "role": "analysis"} for x in rng.sample(ids, len(ids))]
        for j in range(g_per):
            gid = gold_ids[(i * g_per + j) % len(gold_ids)]
            seq.insert(rng.randint(1, len(seq)), {"id": gid, "role": "gold"})
        for a in attn:
            half = len(seq) // 2
            lo, hi = {"first_half": (1, max(1, half)), "second_half": (half + 1, len(seq)),
                      "any": (1, len(seq))}[a["position"]]
            seq.insert(rng.randint(lo, max(lo, hi)), {"id": a["id"], "role": "attention"})
        out.append({"batch_id": f"{prefix}-{i:03d}", "items": seq,
                    "analysis_words": sum(words[x] for x in ids)})
    check_batches(cfg, pool, out)
    return out


def check_batches(cfg: dict, pool: list[dict], batches: list[dict]) -> None:
    d = cfg["design"]
    cnt = Counter(x["id"] for b in batches for x in b["items"] if x["role"] == "analysis")
    if set(cnt) != {it["id"] for it in pool} or set(cnt.values()) != {d["votes_per_item"]}:
        raise AssertionError("every analysis item must appear exactly votes_per_item times")
    for b in batches:
        ids = [x["id"] for x in b["items"]]
        if len(ids) != len(set(ids)):
            raise AssertionError(f"{b['batch_id']}: an item appears twice")
        if b["items"][0]["role"] != "analysis":
            raise AssertionError(f"{b['batch_id']}: must start with an analysis item")
        if sum(x["role"] == "gold" for x in b["items"]) != d["gold"]["per_session"]:
            raise AssertionError(f"{b['batch_id']}: wrong number of gold items")
        if sorted(x["id"] for x in b["items"] if x["role"] == "attention") != sorted(a["id"] for a in d.get("attention") or []):
            raise AssertionError(f"{b['batch_id']}: each attention item must appear exactly once")


def summary(batches: list[dict]) -> dict:
    w = sorted(b["analysis_words"] for b in batches)
    return {"sessions": len(batches), "items_per_session": sorted({len(b["items"]) for b in batches}),
            "words_min": w[0], "words_median": w[len(w) // 2], "words_max": w[-1]}
