"""The browser (flow.js) and Python (routing.py) must route every answer set identically."""

import itertools
import json
import os
import random
import shutil
import subprocess

import pytest

from prolific_annotator.routing import walk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOW_JS = os.path.join(ROOT, "src", "prolific_annotator", "templates", "app", "flow.js")
RUNNER = os.path.join(ROOT, "tests", "js", "flow_runner.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _candidates(q):
    if q["type"] in ("single", "likert"):
        return [None] + [o["value"] for o in q["options"]] + ["not-an-option"]
    if q["type"] == "multi":
        vals = [o["value"] for o in q["options"]]
        return [None, []] + [[v] for v in vals] + [list(p) for p in itertools.combinations(vals, 2)] + [["bogus"]]
    return [None, "", "  ", "some text"]


def _cases(task, n_random=400, seed=0):
    qs = task["questions"]
    space = [_candidates(q) for q in qs]
    total = 1
    for s in space:
        total *= len(s)
    if total <= 5000:
        combos = itertools.product(*space)
    else:
        rng = random.Random(seed)
        combos = (tuple(rng.choice(s) for s in space) for _ in range(n_random))
    return [{q["id"]: v for q, v in zip(qs, c) if v is not None} for c in combos]


def _js_walks(task, cases):
    out = subprocess.run(["node", RUNNER, FLOW_JS], input=json.dumps({"task": task, "cases": cases}),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("fixture", ["tree_study", "survey_study"])
def test_parity(fixture, request):
    task = request.getfixturevalue(fixture)[0]["task"]
    cases = _cases(task)
    assert len(cases) > 50
    assert [walk(task, c) for c in cases] == _js_walks(task, cases)
