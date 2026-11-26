"""Question flow: which questions are shown, and which outcome the answers lead to.

This must stay identical to ``templates/app/flow.js`` (the browser copy). The
test suite runs both on every possible answer combination of the example tasks.

Rules
-----
* Start at ``task.start``.
* A ``single`` question moves on through the chosen option's ``next`` or ends
  with its ``outcome``. An option with neither falls back to the question-level
  ``next`` / ``outcome``.
* Other types (``multi``, ``likert``, ``text``) use the question-level ``next``
  or ``outcome``.
* A question with no ``next`` and no ``outcome`` ends the flow without an outcome
  (useful for plain questionnaires: the answers themselves are the data).
* ``required: false`` makes a question optional: left blank, it counts as answered.
"""

from __future__ import annotations

from typing import Any


def is_answered(q: dict, a: Any) -> bool:
    if q.get("required") is False and a in (None, "", []):
        return True  # optional question left blank
    t = q["type"]
    if t in ("single", "likert"):
        return isinstance(a, str) and a in {o["value"] for o in q["options"]}
    if t == "multi":
        vals = {o["value"] for o in q["options"]}
        return (isinstance(a, list) and len(a) >= q.get("min_selected", 1)
                and all(isinstance(x, str) and x in vals for x in a))
    if t == "text":
        return isinstance(a, str) and len(a.strip()) >= q.get("min_chars", 1)
    return False


def walk(task: dict, answers: dict) -> dict:
    """Follow the flow from the start question using ``answers``.

    Returns ``{"path": [question ids shown], "outcome": str | None, "complete": bool}``.
    ``complete`` is False while some question on the path is still unanswered.
    """
    byid = {q["id"]: q for q in task["questions"]}
    node, path = task["start"], []
    for _ in range(len(byid) + 1):  # the config validator guarantees no cycles
        q = byid[node]
        path.append(node)
        a = answers.get(node)
        if not is_answered(q, a):
            return {"path": path, "outcome": None, "complete": False}
        target = q
        if q["type"] == "single":
            opt = next(o for o in q["options"] if o["value"] == a)
            if "next" in opt or "outcome" in opt:
                target = opt
        if "next" in target:
            node = target["next"]
            continue
        return {"path": path, "outcome": target.get("outcome"), "complete": True}
    raise RuntimeError("question flow did not terminate")  # pragma: no cover


def prune(task: dict, answers: dict) -> dict:
    """Drop answers to questions that are no longer on the path (after a change)."""
    keep = set(walk(task, answers)["path"])
    return {k: v for k, v in answers.items() if k in keep}


def matches(task: dict, answers: dict, expected_answers: dict | None = None,
            expected_outcome: str | None = None) -> bool:
    """True if a response meets every expectation given (used for gold and attention items)."""
    if expected_answers:
        for qid, want in expected_answers.items():
            got = answers.get(qid)
            if isinstance(want, list):
                if sorted(got or []) != sorted(str(w) for w in want):
                    return False
            elif got != str(want):
                return False
    if expected_outcome is not None and walk(task, answers)["outcome"] != str(expected_outcome):
        return False
    return True
