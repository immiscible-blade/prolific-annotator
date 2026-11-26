from prolific_annotator.routing import is_answered, matches, prune, walk


def test_walk_outcomes(tree_study):
    task = tree_study[0]["task"]
    assert walk(task, {"q1": "no"}) == {"path": ["q1"], "outcome": "no_claim", "complete": True}
    assert walk(task, {"q1": "yes", "q2": "yes", "q3": "no"})["outcome"] == "unsupported"
    assert walk(task, {"q1": "yes", "q2": "no", "q4": "yes"})["outcome"] == "supported"
    w = walk(task, {"q1": "yes"})
    assert w == {"path": ["q1", "q2"], "outcome": None, "complete": False}


def test_prune_drops_off_path_answers(tree_study):
    task = tree_study[0]["task"]
    assert prune(task, {"q1": "no", "q2": "yes", "q3": "no"}) == {"q1": "no"}


def test_matches(tree_study):
    task = tree_study[0]["task"]
    a = {"q1": "yes", "q2": "no", "q4": "no"}
    assert matches(task, a, {"q1": "yes", "q2": "no", "q4": "no"})
    assert not matches(task, a, {"q1": "no"})
    assert matches(task, a, expected_outcome="unsupported")
    assert not matches(task, a, expected_outcome="supported")


def test_survey_types_and_optional(survey_study):
    task = survey_study[0]["task"]
    q = {x["id"]: x for x in task["questions"]}
    assert is_answered(q["aspects"], ["price", "quality"])
    assert not is_answered(q["aspects"], [])
    assert not is_answered(q["aspects"], ["bogus"])
    assert is_answered(q["comment"], None)  # optional
    w = walk(task, {"tone": "4", "aspects": ["price"], "helpful": "yes"})
    assert w["complete"] and w["outcome"] == "helpful" and w["path"] == ["tone", "aspects", "comment", "helpful"]
    assert not walk(task, {"tone": "9"})["complete"]
