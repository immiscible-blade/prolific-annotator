"""Simulated responses for dry runs and tests.

Produces a ``responses.csv`` in the backend export format for a built study, so the
whole pipeline (ingest, flags, distributions, compare) can be exercised before any
real participant takes part. Honest workers answer along the configured flow with
item-specific preferences, mostly pass gold and attention items, and take
plausible time. Planted workers make sure each flag fires:

  hidden    follows the hidden instruction on attention items
  careless  fails every attention item
  fast      a few seconds per item, and gets gold wrong
  absent    leaves the tab for a long time and skims, and gets gold wrong
"""

from __future__ import annotations

import csv
import json
import random

from .routing import is_answered, matches, walk

PLANTED = ["hidden", "careless", "fast", "absent"]


def _random_answer(q: dict, rng: random.Random, weights: dict | None = None):
    if q["type"] in ("single", "likert"):
        vals = [o["value"] for o in q["options"]]
        w = [(weights or {}).get(v, 1.0) for v in vals]
        return rng.choices(vals, weights=w)[0]
    if q["type"] == "multi":
        vals = [o["value"] for o in q["options"]]
        k = rng.randint(max(1, q.get("min_selected", 1)), len(vals))
        return rng.sample(vals, k)
    return "simulated free-text answer"


def complete(task: dict, partial: dict, rng: random.Random, prefs: dict | None = None) -> dict:
    """Fill in answers along the flow, keeping any given in ``partial``."""
    ans = {}
    byid = {q["id"]: q for q in task["questions"]}
    for _ in range(len(byid) + 1):
        w = walk(task, ans)
        if w["complete"]:
            return ans
        qid = w["path"][-1]
        q = byid[qid]
        if qid in partial and is_answered(q, partial[qid]):
            ans[qid] = partial[qid]
        else:
            ans[qid] = _random_answer(q, rng, (prefs or {}).get(qid))
    return ans


def answers_for_outcome(task: dict, outcome: str, rng: random.Random, tries: int = 500) -> dict:
    for _ in range(tries):
        a = complete(task, {}, rng)
        if walk(task, a)["outcome"] == outcome:
            return a
    raise ValueError(f"could not reach outcome {outcome!r} by random search")


def simulate(cfg: dict, manifest: dict, out_csv: str, seed: int = 0) -> dict:
    rng = random.Random(seed)
    task = cfg["task"]
    items, gold, attn = manifest["items"], manifest["gold"], manifest["attention"]
    # item-specific preferences: each analysis item leans towards some options
    prefs = {iid: {q["id"]: {o["value"]: rng.uniform(0.2, 3.0) for o in q.get("options", [])}
                   for q in task["questions"]} for iid in items}
    rows = []
    for w, b in enumerate(manifest["batches"]):
        kind = PLANTED[w] if w < len(PLANTED) else "honest"
        pid, t = f"SIM{w:03d}", 0
        for pos, it in enumerate(b["items"]):
            iid = it["id"]
            if kind == "fast":
                dur = rng.uniform(2_000, 6_000)
            else:
                dur = rng.lognormvariate(10.6, 0.6)  # median ~40 s
            focus, shift = [], 0
            if it["role"] == "attention":
                exp, hid = attn[iid]["expected_answers"], attn[iid]["hidden_answers"]
                if kind == "hidden" and hid:
                    a = complete(task, hid, rng)
                elif kind == "careless" or rng.random() > 0.95:
                    a = complete(task, {}, rng)
                    for _ in range(50):
                        if not matches(task, a, exp):
                            break
                        a = complete(task, {}, rng)
                else:
                    a = complete(task, exp, rng)
            elif it["role"] == "gold":
                g = gold[iid]
                ok = kind not in ("fast", "absent") and rng.random() < 0.9
                if ok:
                    a = (complete(task, g["expected_answers"], rng) if g.get("expected_answers")
                         else answers_for_outcome(task, g["expected_outcome"], rng))
                else:
                    a = complete(task, {}, rng)
                    for _ in range(50):
                        if not matches(task, a, g.get("expected_answers"), g.get("expected_outcome")):
                            break
                        a = complete(task, {}, rng)
            else:
                a = complete(task, {}, rng, prefs[iid])
                if kind == "absent":
                    dur = rng.uniform(2_000, 5_000) + (600_000 if pos == 0 else 0)
                    if pos == 0:
                        focus = [["hidden", t + 1_000], ["visible", t + 601_000]]
            hist = [[q, v, t + shift + 1_000 * (k + 1)] for k, (q, v) in enumerate(a.items())]
            row = {"item_id": iid, "pos": pos, "t_start": t, "t_end": t + dur, "time_on_item": dur,
                   "answers": a, "outcome": walk(task, a)["outcome"], "focus": focus, "blocked": [],
                   "history": hist, "span_seen_ms": 1500, "max_scroll": 1, "n_items": len(b["items"]),
                   "app_version": "simulated"}
            rows.append({"ts": f"2030-01-01T00:{w % 60:02d}:{pos:02d}.{w:03d}Z", "pid": pid, "batch_id": b["batch_id"],
                         "item_id": iid, "pos": pos, "label": row["outcome"] or "", "time_on_item": int(dur),
                         "trace": json.dumps(a), "row_json": json.dumps(row)})
            t += dur
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)
    return {"rows": len(rows), "workers": len(manifest["batches"]),
            "planted": {f"SIM{i:03d}": k for i, k in enumerate(PLANTED[:len(manifest["batches"])])}}
