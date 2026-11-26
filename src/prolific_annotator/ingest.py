"""Responses -> quality flags, votes and per-item distributions.

Input is the ``responses`` export (Google Sheet tab saved as CSV, or
``pannot export`` from the server; both have the same columns) plus the private
manifest written by ``pannot build``.

Design choices
--------------
* **Nothing is excluded automatically.** Every worker gets quality flags; a worker
  is ``flagged`` when the number of flags reaches ``flags.threshold``. Outputs keep
  everyone and carry a ``flagged`` column, so analyses can be run with and without.
* Only workers listed as **rejected** (``flags.rejected_pids_file`` or ``--rejected``)
  are left out of the distributions: they were reviewed by a person and replaced.
* Outcomes are **recomputed from the answers** with the same routing as the app.
  The ``label`` column of a Google Sheet cannot be trusted: Sheets turns the string
  "false" into the boolean FALSE.
"""

from __future__ import annotations

import csv
import json
import math
import os
import statistics
from collections import Counter, defaultdict

from .config import resolve
from .routing import matches, walk


def read_responses(path: str, ignore_prefixes=("TEST_", "DEV_")) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = {}
    for r in sorted(rows, key=lambda r: r["ts"]):
        pid = str(r["pid"])
        if pid.startswith(tuple(ignore_prefixes)):
            continue
        row = json.loads(r["row_json"]) if r.get("row_json") else {}
        answers = row.get("answers")
        if answers is None:
            answers = json.loads(r["trace"]) if r.get("trace") else {}
        out[(pid, str(r["item_id"]))] = {  # a reload can re-post an item: keep the last one
            "pid": pid, "batch_id": r["batch_id"], "item_id": str(r["item_id"]),
            "pos": int(float(r["pos"])) if r.get("pos") not in (None, "") else None,
            "time_on_item": float(r["time_on_item"]) if r.get("time_on_item") not in (None, "") else 0.0,
            "answers": answers, "row": row,
        }
    return list(out.values())


def away_spells(row: dict) -> list[float]:
    """Durations (ms) of each spell where the page was hidden or unfocused."""
    spells, t_out = [], None
    for ev, t in row.get("focus") or []:
        if ev in ("blur", "hidden") and t_out is None:
            t_out = t
        elif ev in ("focus", "visible") and t_out is not None:
            spells.append(t - t_out)
            t_out = None
    return spells


def tab_burst(row: dict, away_ms: float, burst_ms: float) -> bool:
    """A long spell away followed by >= 2 answers right after returning (looks like looking things up)."""
    returns, t_out = [], None
    for ev, t in row.get("focus") or []:
        if ev in ("blur", "hidden") and t_out is None:
            t_out = t
        elif ev in ("focus", "visible") and t_out is not None:
            if t - t_out > away_ms:
                returns.append(t)
            t_out = None
    times = [h[2] for h in row.get("history") or [] if len(h) == 3]
    return any(sum(back <= t <= back + burst_ms for t in times) >= 2 for back in returns)


def worker_flags(cfg: dict, manifest: dict, responses: list[dict], rejected: set[str] = frozenset()) -> list[dict]:
    task, rules = cfg["task"], cfg["flags"]["rules"]
    items, gold, attn = manifest["items"], manifest["gold"], manifest["attention"]
    by_pid = defaultdict(list)
    for r in responses:
        by_pid[r["pid"]].append(r)
    out = []
    for pid, rs in sorted(by_pid.items()):
        an = [r for r in rs if items.get(r["item_id"], {}).get("role") == "analysis"]
        go = [r for r in rs if r["item_id"] in gold]
        at = [r for r in rs if r["item_id"] in attn]
        f = {"pid": pid, "batch_id": rs[0]["batch_id"], "n_rows": len(rs),
             "complete": len(rs) == (rs[0]["row"].get("n_items") or len(rs)),
             "app_version": next((r["row"].get("app_version") for r in rs if r["row"].get("app_version")), "")}
        flags = 0

        f["n_gold"] = len(go)
        f["gold_wrong"] = sum(not matches(task, r["answers"], gold[r["item_id"]].get("expected_answers"),
                                          gold[r["item_id"]].get("expected_outcome")) for r in go)
        hidden_g = manifest.get("gold_hidden_answers") or {}
        f["gold_hidden_followed"] = bool(hidden_g) and any(matches(task, r["answers"], hidden_g) for r in go)
        if rules.get("gold_wrong"):
            flags += f["gold_wrong"]

        f["n_attention"] = len(at)
        f["attention_wrong"] = sum(not matches(task, r["answers"], attn[r["item_id"]]["expected_answers"]) for r in at)
        f["attention_hidden_followed"] = any(attn[r["item_id"]]["hidden_answers"] and
                                             matches(task, r["answers"], attn[r["item_id"]]["hidden_answers"]) for r in at)
        if rules.get("attention_wrong"):
            flags += f["attention_wrong"]

        times = [r["time_on_item"] for r in an]
        active = [r["time_on_item"] - sum(away_spells(r["row"])) for r in an]
        f["median_item_s"] = round(statistics.median(times) / 1000, 1) if times else None
        f["active_s_mean"] = round(sum(active) / len(active) / 1000, 1) if active else None

        rule = rules.get("fast")
        f["fast"] = bool(rule and times and sum(times) < rule["min_seconds_per_item"] * 1000 * len(times))
        rule = rules.get("skim")
        wps = []
        if rule:
            wps = [items[r["item_id"]]["words"] / max(a / 1000, 0.001) for r, a in zip(an, active)
                   if items[r["item_id"]]["words"] >= rule["min_words"]]
        f["active_words_per_s"] = round(statistics.median(wps), 1) if wps else None
        f["skim"] = bool(rule and wps and statistics.median(wps) > rule["max_words_per_second"])
        rule = rules.get("long_absence")
        f["long_absence"] = bool(rule and any(s > rule["minutes"] * 60_000 for r in rs for s in away_spells(r["row"])))
        rule = rules.get("tab_burst")
        f["tab_burst"] = bool(rule and any(tab_burst(r["row"], rule["away_seconds"] * 1000, rule["burst_seconds"] * 1000)
                                           for r in an))
        f["n_highlight_unseen"] = sum(items[r["item_id"]].get("has_highlight") and r["row"].get("span_seen_ms") is None
                                      for r in an)
        f["highlight_unseen"] = bool(rules.get("highlight_unseen") and f["n_highlight_unseen"] >= 1)
        f["copy_attempts"] = sum(len(r["row"].get("blocked") or []) for r in rs)
        flags += sum(int(f[k]) for k in ("fast", "skim", "long_absence", "tab_burst", "highlight_unseen"))
        f["n_flags"] = flags
        f["flagged"] = flags >= cfg["flags"]["threshold"]
        f["rejected"] = pid in rejected
        out.append(f)
    return out


def votes_table(cfg: dict, manifest: dict, responses: list[dict], flags: list[dict]) -> list[dict]:
    task = cfg["task"]
    fl = {f["pid"]: f for f in flags}
    qids = [q["id"] for q in task["questions"]]
    rows = []
    for r in responses:
        if manifest["items"].get(r["item_id"], {}).get("role") != "analysis":
            continue
        w = walk(task, r["answers"])
        row = {"pid": r["pid"], "batch_id": r["batch_id"], "item_id": r["item_id"],
               "outcome": w["outcome"] if w["complete"] else None, "complete": w["complete"],
               "time_on_item_s": round(r["time_on_item"] / 1000, 1),
               "flagged": fl[r["pid"]]["flagged"], "rejected": fl[r["pid"]]["rejected"]}
        for q in qids:
            a = r["answers"].get(q)
            row[q] = "|".join(a) if isinstance(a, list) else a
        rows.append(row)
    return rows


def distributions(cfg: dict, votes: list[dict], drop_flagged: bool = False) -> list[dict]:
    """Per item: outcome counts, proportions, entropy (nats) and agreement status.

    The no-majority rule: an item has a majority when one outcome holds strictly more
    than half of the votes; ``perfect`` when all votes agree.
    """
    outcomes = list((cfg["task"].get("outcomes") or {}).keys()) or sorted({v["outcome"] for v in votes if v["outcome"]})
    by_item = defaultdict(list)
    for v in votes:
        if v["rejected"] or (drop_flagged and v["flagged"]) or not v["outcome"]:
            continue
        by_item[v["item_id"]].append(v["outcome"])
    rows = []
    for iid, outs in sorted(by_item.items(), key=lambda kv: kv[0]):
        c = Counter(outs)
        n = len(outs)
        top, topn = c.most_common(1)[0]
        status = "perfect" if topn == n else ("majority" if 2 * topn > n else "no_majority")
        row = {"item_id": iid, "n_votes": n}
        for o in outcomes:
            row[f"n_{o}"] = c.get(o, 0)
        for o in outcomes:
            row[f"p_{o}"] = round(c.get(o, 0) / n, 6)
        row["entropy"] = round(-sum((k / n) * math.log(k / n) for k in c.values()), 6)
        row["majority"] = top if status != "no_majority" else "NO_MAJORITY"
        row["agreement"] = status
        rows.append(row)
    return rows


def answer_counts(cfg: dict, votes: list[dict], drop_flagged: bool = False) -> list[dict]:
    """Long table of how often each answer was given per item and question (any task type)."""
    c = Counter()
    for v in votes:
        if v["rejected"] or (drop_flagged and v["flagged"]):
            continue
        for q in cfg["task"]["questions"]:
            a = v.get(q["id"])
            if a is None:
                continue
            vals = a.split("|") if q["type"] == "multi" else ([a] if q["type"] != "text" else ["<text>"])
            for x in vals:
                c[(v["item_id"], q["id"], x)] += 1
    return [{"item_id": i, "question": q, "value": x, "count": n} for (i, q, x), n in sorted(c.items())]


def write_csv(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if not rows:
        open(path, "w").close()
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def load_rejected(cfg: dict, extra: str | None = None) -> set[str]:
    out = set()
    for p in (resolve(cfg, cfg["flags"].get("rejected_pids_file")), extra):
        if p and os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                out |= {line.strip() for line in f if line.strip() and not line.startswith("#")}
    return out


def ingest(cfg: dict, build_dir: str, responses_csv: str, out_dir: str, rejected_file: str | None = None,
           drop_flagged: bool = False) -> dict:
    with open(os.path.join(build_dir, "private", "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    rs = read_responses(responses_csv, cfg["flags"]["ignore_pid_prefixes"])
    rejected = load_rejected(cfg, rejected_file)
    flags = worker_flags(cfg, manifest, rs, rejected)
    votes = votes_table(cfg, manifest, rs, flags)
    dists = distributions(cfg, votes, drop_flagged)
    write_csv(os.path.join(out_dir, "worker_flags.csv"), flags)
    write_csv(os.path.join(out_dir, "votes.csv"), votes)
    write_csv(os.path.join(out_dir, "item_distributions.csv"), dists)
    write_csv(os.path.join(out_dir, "answer_counts.csv"), answer_counts(cfg, votes, drop_flagged))
    return {"workers": len(flags), "flagged": sum(f["flagged"] for f in flags),
            "rejected": sum(f["rejected"] for f in flags), "votes": len(votes), "items": len(dists),
            "flag_totals": {k: sum(int(f[k]) for f in flags) for k in
                            ("gold_wrong", "attention_wrong", "fast", "skim", "long_absence", "tab_burst",
                             "highlight_unseen", "attention_hidden_followed", "gold_hidden_followed")}}
