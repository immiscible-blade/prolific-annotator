"""Compare existing labels with new crowd labels for the same items.

Typical use: you have a few annotations per item from an earlier study and
collected more on Prolific. Are the old labels a fair small sample of the same
distribution?

Exchangeability permutation test: if the old votes and the new votes for an item
come from the same distribution, they are exchangeable. Pool them, re-split at
random into groups of the original sizes, and recompute the Jensen-Shannon
divergence (JSD, base 2) between the two groups. The observed JSD is compared to
this null per item and, for the mean over items, overall.

Caveat that belongs in any write-up: if items were *selected* because of their old
votes (e.g. "items where annotators disagreed"), regression to the mean pushes the
observed JSD above the null even when old votes are fair draws.

Pure Python; no numpy needed.
"""

from __future__ import annotations

import csv
import math
import random
from collections import Counter, defaultdict


def jsd(p: list[float], q: list[float], eps: float = 1e-6) -> float:
    p = [x + eps for x in p]
    q = [x + eps for x in q]
    sp, sq = sum(p), sum(q)
    p = [x / sp for x in p]
    q = [x / sq for x in q]
    m = [(a + b) / 2 for a, b in zip(p, q)]
    return 0.5 * sum(a * math.log2(a / c) for a, c in zip(p, m)) + 0.5 * sum(b * math.log2(b / c) for b, c in zip(q, m))


def read_votes(path: str, item_col: str = "item_id", label_col: str = "outcome") -> dict[str, list[str]]:
    """Long format: one row per vote with an item id and a label column."""
    out = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get(label_col) not in (None, ""):
                out[str(r[item_col])].append(str(r[label_col]))
    return dict(out)


def compare(old: dict[str, list[str]], new: dict[str, list[str]], n_perm: int = 2000, seed: int = 0) -> dict:
    rng = random.Random(seed)
    items = sorted(set(old) & set(new))
    labels = sorted({x for v in list(old.values()) + list(new.values()) for x in v})

    def dist(votes):
        c = Counter(votes)
        return [c.get(lab, 0) / len(votes) for lab in labels]

    per_item, null_means = [], [0.0] * n_perm
    for iid in items:
        a, b = old[iid], new[iid]
        obs = jsd(dist(a), dist(b))
        pool = a + b
        null = []
        for k in range(n_perm):
            rng.shuffle(pool)
            v = jsd(dist(pool[:len(a)]), dist(pool[len(a):]))
            null.append(v)
            null_means[k] += v / len(items)
        p = (sum(v >= obs - 1e-12 for v in null) + 1) / (n_perm + 1)
        per_item.append({"item_id": iid, "n_old": len(a), "n_new": len(b), "jsd": round(obs, 4),
                         "null_mean": round(sum(null) / n_perm, 4), "p": round(p, 4),
                         "old_majority": Counter(a).most_common(1)[0][0],
                         "new_majority": Counter(b).most_common(1)[0][0]})
    mean_obs = sum(r["jsd"] for r in per_item) / len(per_item) if per_item else float("nan")
    p_mean = (sum(m >= mean_obs - 1e-12 for m in null_means) + 1) / (n_perm + 1) if per_item else float("nan")
    srt = sorted(null_means)
    return {"items": per_item, "n_items": len(per_item), "labels": labels,
            "mean_jsd": round(mean_obs, 4), "null_mean_jsd": round(sum(null_means) / n_perm, 4) if per_item else None,
            "null_95": [round(srt[int(0.025 * n_perm)], 4), round(srt[int(0.975 * n_perm) - 1], 4)] if per_item else None,
            "p_mean": round(p_mean, 4),
            "majority_flips": sum(r["old_majority"] != r["new_majority"] for r in per_item)}
