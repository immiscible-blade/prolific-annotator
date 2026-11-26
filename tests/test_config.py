import copy

import pytest
import yaml

from prolific_annotator.config import ConfigError, load_config, validate


def _write(tmp_path, cfg):
    p = tmp_path / "study.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return str(p)


BASE = {
    "task": {"questions": [
        {"id": "q1", "type": "single", "options": [{"value": "yes", "next": "q2"}, {"value": "no", "outcome": "a"}]},
        {"id": "q2", "type": "single", "options": [{"value": "yes", "outcome": "b"}, {"value": "no", "outcome": "a"}]},
    ], "outcomes": {"a": "A", "b": "B"}},
}


def test_defaults_and_start(tmp_path):
    cfg = load_config(_write(tmp_path, BASE))
    assert cfg["task"]["start"] == "q1"
    assert cfg["design"]["votes_per_item"] == 5
    assert cfg["study"]["backend"]["type"] == "demo"


def test_yes_no_stay_strings(tmp_path):
    p = tmp_path / "study.yaml"
    p.write_text("task:\n  questions:\n    - id: q1\n      options:\n        - {value: yes, outcome: a}\n"
                 "        - {value: no, outcome: b}\n")
    cfg = load_config(str(p))
    assert [o["value"] for o in cfg["task"]["questions"][0]["options"]] == ["yes", "no"]


@pytest.mark.parametrize("mutate, msg", [
    (lambda c: c["task"]["questions"][1]["options"].__setitem__(0, {"value": "yes", "next": "q1"}), "cycle"),
    (lambda c: c["task"]["questions"][0]["options"][0].update(next="nope"), "unknown question"),
    (lambda c: c["task"]["questions"][0]["options"][1].update(next="q2"), "either `next` or `outcome`"),
    (lambda c: c["task"]["questions"][0].update(type="slider"), "type must be"),
    (lambda c: c["task"]["questions"][1].update(id="q1"), "duplicate"),
    (lambda c: c["task"]["questions"][1]["options"][0].update(outcome="zzz"), "not listed"),
    (lambda c: c["task"]["questions"][0].update(required=False), "must be required"),
    (lambda c: c.update(design={"votes_per_item": 0}), "positive integer"),
    (lambda c: c.update(study={"backend": {"type": "apps_script"}}), "required for apps_script"),
])
def test_validation_errors(tmp_path, mutate, msg):
    cfg = copy.deepcopy(BASE)
    mutate(cfg)
    with pytest.raises(ConfigError, match=msg):
        load_config(_write(tmp_path, cfg))


def test_multi_cannot_branch_per_option(tmp_path):
    cfg = copy.deepcopy(BASE)
    cfg["task"]["questions"][0] = {"id": "q1", "type": "multi",
                                   "options": [{"value": "x", "next": "q2"}, {"value": "y"}]}
    with pytest.raises(ConfigError, match="only `single`"):
        load_config(_write(tmp_path, cfg))


def test_attention_hidden_must_differ(tmp_path):
    cfg = copy.deepcopy(BASE)
    cfg["design"] = {"attention": [{"id": "attn1", "html": "<p>x</p>", "expected_answers": {"q1": "no"},
                                    "hidden_instruction": {"html": "h", "answers": {"q1": "no"}}}]}
    with pytest.raises(ConfigError, match="must differ"):
        load_config(_write(tmp_path, cfg))


def test_tree_example_validates(tree_study):
    validate(tree_study[0])


def test_survey_example_validates(survey_study):
    validate(survey_study[0])
