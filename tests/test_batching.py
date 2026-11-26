import copy
from collections import Counter

import pytest

from prolific_annotator.batching import build_batches
from prolific_annotator.config import ConfigError
from prolific_annotator.items import load_items


def _items(cfg):
    return load_items(cfg)


def test_invariants(tree_study):
    cfg = tree_study[0]
    b = build_batches(cfg, _items(cfg))
    d = cfg["design"]
    cnt = Counter(x["id"] for s in b for x in s["items"] if x["role"] == "analysis")
    assert set(cnt.values()) == {d["votes_per_item"]} and len(cnt) == 12
    for s in b:
        roles = [x["role"] for x in s["items"]]
        assert roles[0] == "analysis"
        assert roles.count("gold") == d["gold"]["per_session"]
        assert roles.count("attention") == 2
        ids = [x["id"] for x in s["items"]]
        assert len(ids) == len(set(ids))
        half = len(ids) // 2
        assert ids.index("attn1") <= half + 1  # attn1 in the first half
    gold_use = Counter(x["id"] for s in b for x in s["items"] if x["role"] == "gold")
    assert max(gold_use.values()) - min(gold_use.values()) <= 1


def test_deterministic(tree_study):
    cfg = tree_study[0]
    assert build_batches(cfg, _items(cfg)) == build_batches(cfg, _items(cfg))
    other = copy.deepcopy(cfg)
    other["design"]["seed"] = 99
    assert build_batches(other, _items(other)) != build_batches(cfg, _items(cfg))


@pytest.mark.parametrize("per, votes", [(5, 3), (7, 2), (12, 1), (1, 4)])
def test_uneven_sizes(tree_study, per, votes):
    cfg = copy.deepcopy(tree_study[0])
    cfg["design"].update(items_per_session=per, votes_per_item=votes)
    b = build_batches(cfg, _items(cfg))
    sizes = [sum(x["role"] == "analysis" for x in s["items"]) for s in b]
    assert max(sizes) - min(sizes) <= 1 and max(sizes) <= per
    cnt = Counter(x["id"] for s in b for x in s["items"] if x["role"] == "analysis")
    assert set(cnt.values()) == {votes}


def test_errors(tree_study):
    cfg = copy.deepcopy(tree_study[0])
    cfg["design"]["items_per_session"] = 99
    with pytest.raises(ConfigError, match="exceeds"):
        build_batches(cfg, _items(cfg))
    cfg = copy.deepcopy(tree_study[0])
    cfg["design"]["gold"]["items"].append({"id": "missing", "expected_outcome": "supported"})
    with pytest.raises(ConfigError, match="not in the items file"):
        build_batches(cfg, _items(cfg))


def test_include_subset(tree_study):
    cfg = copy.deepcopy(tree_study[0])
    cfg["design"]["include"] = ["t01", "t02", "t03", "t04", "t05", "t06"]
    b = build_batches(cfg, _items(cfg))
    assert {x["id"] for s in b for x in s["items"] if x["role"] == "analysis"} == set(cfg["design"]["include"])
