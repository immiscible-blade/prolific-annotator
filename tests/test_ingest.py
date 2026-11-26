import csv
import json
import os

from prolific_annotator.ingest import distributions, ingest, read_responses, votes_table, worker_flags
from prolific_annotator.simulate import PLANTED, simulate


def _manifest(out):
    with open(os.path.join(out, "private", "manifest.json")) as f:
        return json.load(f)


def _rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_planted_workers_flagged_honest_not(tree_study, tmp_path):
    cfg, out, _ = tree_study
    sim = tmp_path / "sim.csv"
    info = simulate(cfg, _manifest(out), str(sim), seed=1)
    flags = {f["pid"]: f for f in worker_flags(cfg, _manifest(out), read_responses(str(sim)))}
    for pid, kind in info["planted"].items():
        assert flags[pid]["flagged"], (pid, kind, flags[pid])
    assert flags["SIM000"]["attention_hidden_followed"]  # "hidden" follows the hidden instruction
    assert flags["SIM002"]["fast"] and flags["SIM003"]["long_absence"]
    honest = [f for p, f in flags.items() if p not in info["planted"]]
    assert honest and not any(f["flagged"] for f in honest)


def test_nobody_excluded_and_rejected_dropped(tree_study, tmp_path):
    cfg, out, _ = tree_study
    sim = tmp_path / "sim.csv"
    simulate(cfg, _manifest(out), str(sim))
    rej = tmp_path / "rejected.txt"
    rej.write_text("# reviewed by hand\nSIM002\n")
    res = tmp_path / "res"
    s = ingest(cfg, out, str(sim), str(res), rejected_file=str(rej))
    assert s["rejected"] == 1 and s["flagged"] >= len(PLANTED)
    votes = _rows(res / "votes.csv")
    assert {v["pid"] for v in votes} == {f"SIM{i:03d}" for i in range(6)}  # everyone kept in votes.csv
    dists = _rows(res / "item_distributions.csv")
    n_counted = sum(int(d["n_votes"]) for d in dists)
    assert n_counted == sum(1 for v in votes if v["rejected"] == "False")  # rejected left out of distributions
    for d in dists:
        assert abs(sum(float(d[k]) for k in d if k.startswith("p_")) - 1) < 1e-6
        assert d["agreement"] in ("perfect", "majority", "no_majority")


def test_outcome_recomputed_not_trusted(tree_study, tmp_path):
    """Google Sheets turns the label "false" into FALSE; ingest must not depend on that column."""
    cfg, out, _ = tree_study
    sim = tmp_path / "sim.csv"
    simulate(cfg, _manifest(out), str(sim))
    rows = _rows(sim)
    for r in rows:
        r["label"] = "FALSE"
    with open(sim, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    rs = read_responses(str(sim))
    votes = votes_table(cfg, _manifest(out), rs, worker_flags(cfg, _manifest(out), rs))
    assert {v["outcome"] for v in votes} <= {"no_claim", "supported", "unsupported"}


def test_test_ids_ignored_and_reposts_deduplicated(tree_study, tmp_path):
    cfg, out, _ = tree_study
    sim = tmp_path / "sim.csv"
    simulate(cfg, _manifest(out), str(sim))
    rows = _rows(sim)
    extra = dict(rows[0], pid="TEST_me")
    dup = dict(rows[0], ts="2031-01-01T00:00:00Z")  # same pid+item posted again later
    with open(sim, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writerow(extra)
        w.writerow(dup)
    rs = read_responses(str(sim), ["TEST_"])
    assert not any(r["pid"].startswith("TEST_") for r in rs)
    assert len(rs) == len(rows)


def test_no_majority_rule():
    cfg = {"task": {"outcomes": {"a": "A", "b": "B", "c": "C"}, "questions": []}}
    v = [{"item_id": "i", "outcome": o, "rejected": False, "flagged": False} for o in ["a", "b", "c", "a"]]
    d = distributions(cfg, v)[0]
    assert d["agreement"] == "no_majority" and d["majority"] == "NO_MAJORITY"  # 2 of 4 is not a majority
    v.append({"item_id": "i", "outcome": "a", "rejected": False, "flagged": False})
    assert distributions(cfg, v)[0]["majority"] == "a"


def test_survey_pipeline(survey_study, tmp_path):
    cfg, out, _ = survey_study
    sim = tmp_path / "sim.csv"
    simulate(cfg, _manifest(out), str(sim))
    res = tmp_path / "res"
    ingest(cfg, out, str(sim), str(res))
    counts = _rows(res / "answer_counts.csv")
    assert {c["question"] for c in counts} >= {"tone", "aspects", "helpful"}
