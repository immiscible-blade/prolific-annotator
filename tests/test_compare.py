import random

from prolific_annotator.compare import compare, jsd


def test_jsd_bounds():
    assert jsd([1, 0], [1, 0]) < 1e-6
    assert 0.99 < jsd([1, 0], [0, 1]) <= 1.0


def _draw(rng, p, n):
    return rng.choices(["a", "b", "c"], weights=p, k=n)


def test_null_not_rejected_when_same_distribution():
    rng = random.Random(1)
    ps = [[rng.random() for _ in range(3)] for _ in range(30)]
    old = {str(i): _draw(rng, p, 3) for i, p in enumerate(ps)}
    new = {str(i): _draw(rng, p, 10) for i, p in enumerate(ps)}
    r = compare(old, new, n_perm=500, seed=0)
    assert r["n_items"] == 30 and r["p_mean"] > 0.05


def test_rejected_when_crowd_shifted():
    rng = random.Random(2)
    ps = [[rng.random() for _ in range(3)] for _ in range(30)]
    old = {str(i): _draw(rng, p, 3) for i, p in enumerate(ps)}
    new = {str(i): _draw(rng, [p[0] + 3, p[1], p[2]], 10) for i, p in enumerate(ps)}
    assert compare(old, new, n_perm=500, seed=0)["p_mean"] < 0.01
