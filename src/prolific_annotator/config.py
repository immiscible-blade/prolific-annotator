"""Study configuration: loading, defaults and validation.

A study is described by one YAML (or JSON) file, ``study.yaml``. Everything the
tool does (build the site, generate the backend, batch items, compute flags)
reads from it. See ``docs/config-reference.md`` for every field.
"""

from __future__ import annotations

import copy
import json
import os
import re
from typing import Any

import yaml

QUESTION_TYPES = {"single", "multi", "likert", "text"}
POSITIONS = {"any", "first_half", "second_half"}

DEFAULTS: dict[str, Any] = {
    "study": {
        "title": "Annotation study",
        "completion_url": "",
        "backend": {"type": "demo", "url": ""},
        "consent_html": "<p>By continuing you agree to take part in this study.</p>",
        "instructions_html": "<p>Read each text and answer the questions.</p>",
        "estimated_minutes": 20,
        "block_copy": True,
        # batch release: a claimed batch is given to the next participant when its holder
        # has left. With a Prolific API token (never in this file; see docs/deployment.md)
        # Prolific's own status decides; otherwise no heartbeat/answer for idle_minutes.
        "idle_minutes": 15,
        "stale_minutes": 90,          # rows without any heartbeat record (older app versions)
        "prolific_study_id": "",      # optional; else the STUDY_ID in the participant's link
        "heartbeat_seconds": 60,
        "waiting_poll_seconds": 30,   # waiting room: how often to retry when everything is taken
        "waiting_minutes": 15,        # ...and for how long
    },
    "items": {
        "file": "items.csv",
        "id_column": "id",
        "text_column": "text",
        "format": "text",
        "highlight": {"start": "<hl>", "end": "</hl>"},
    },
    "task": {"start": None, "questions": [], "outcomes": {}, "show_outcome": True},
    "design": {
        "votes_per_item": 5,
        "items_per_session": 10,
        "seed": 1,
        "include": None,
        "gold": {"per_session": 0, "items": [], "hidden_instruction_html": ""},
        "attention": [],
    },
    "flags": {
        "threshold": 2,
        "ignore_pid_prefixes": ["TEST_", "DEV_"],
        "rejected_pids_file": None,
        "rules": {
            "gold_wrong": True,
            "attention_wrong": True,
            "fast": {"min_seconds_per_item": 12},
            "skim": {"max_words_per_second": 25, "min_words": 500},
            "long_absence": {"minutes": 5},
            "tab_burst": {"away_seconds": 20, "burst_seconds": 10},
            "highlight_unseen": True,
        },
    },
}


class ConfigError(ValueError):
    """Raised when a study configuration is invalid. The message says what to fix."""


class _Loader(yaml.SafeLoader):
    """SafeLoader that keeps yes/no/on/off as strings.

    YAML 1.1 turns unquoted ``yes``/``no`` into booleans, so ``value: yes`` would
    silently become ``"True"``. Only ``true``/``false`` are booleans here.
    """


_Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:bool"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
for _first in "tTfF":
    _Loader.yaml_implicit_resolvers.setdefault(_first, []).insert(
        0, ("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")))


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str) -> dict:
    """Read a study file, apply defaults, validate, and remember where it lives.

    Relative paths inside the config (``items.file``, ``flags.rejected_pids_file``)
    are resolved against the config file's directory.
    """
    with open(path, encoding="utf-8") as f:
        raw = json.load(f) if path.endswith(".json") else yaml.load(f, Loader=_Loader)  # noqa: S506 (safe subclass)
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    cfg = _merge(DEFAULTS, raw)
    cfg["_dir"] = os.path.dirname(os.path.abspath(path))
    validate(cfg)
    return cfg


def resolve(cfg: dict, rel: str | None) -> str | None:
    """Resolve a path from the config relative to the config file."""
    if rel is None:
        return None
    return rel if os.path.isabs(rel) else os.path.join(cfg.get("_dir", "."), rel)


def questions_by_id(cfg: dict) -> dict[str, dict]:
    return {q["id"]: q for q in cfg["task"]["questions"]}


def validate(cfg: dict) -> None:
    """Check the config for mistakes a researcher is likely to make.

    Raises ConfigError with a message naming the offending field.
    """
    task = cfg["task"]
    qs = task.get("questions") or []
    if not qs:
        raise ConfigError("task.questions: define at least one question")
    ids = [q.get("id") for q in qs]
    if any(not i for i in ids):
        raise ConfigError("task.questions: every question needs an `id`")
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ConfigError(f"task.questions: duplicate ids {sorted(dup)}")
    if task.get("start") is None:
        task["start"] = ids[0]
    byid = questions_by_id(cfg)
    if task["start"] not in byid:
        raise ConfigError(f"task.start: unknown question {task['start']!r}")

    outcomes = task.get("outcomes") or {}
    for q in qs:
        qt = q.setdefault("type", "single")
        if qt not in QUESTION_TYPES:
            raise ConfigError(f"question {q['id']}: type must be one of {sorted(QUESTION_TYPES)}")
        q.setdefault("text", "")
        q.setdefault("title", "")
        if qt in ("single", "multi", "likert"):
            opts = q.get("options")
            if not opts:
                raise ConfigError(f"question {q['id']}: {qt} questions need `options`")
            vals = [str(o.get("value", "")) for o in opts]
            if any(v == "" for v in vals) or len(set(vals)) != len(vals):
                raise ConfigError(f"question {q['id']}: option values must be non-empty and unique")
            for o in opts:
                o["value"] = str(o["value"])
                o.setdefault("label", o["value"])
        if qt == "single":
            for o in q["options"]:
                _check_target(o, f"question {q['id']} option {o['value']}", byid, outcomes)
        elif any(("next" in o or "outcome" in o) for o in q.get("options", [])):
            raise ConfigError(f"question {q['id']}: only `single` questions can branch per option; "
                              "use question-level `next`/`outcome` for other types")
        _check_target(q, f"question {q['id']}", byid, outcomes, optional=True)
        q.setdefault("required", True)
        if not isinstance(q["required"], bool):
            raise ConfigError(f"question {q['id']}: `required` must be true or false")
        if qt == "single" and q["required"] is False:
            raise ConfigError(f"question {q['id']}: a `single` question decides the path and must be required")
        if qt == "multi":
            q.setdefault("min_selected", 1)
        if qt == "text":
            q.setdefault("min_chars", 1)
    _check_acyclic(cfg)

    d = cfg["design"]
    for k in ("votes_per_item", "items_per_session"):
        if not isinstance(d[k], int) or d[k] < 1:
            raise ConfigError(f"design.{k}: must be a positive integer")
    g = d["gold"]
    if g.get("per_session", 0) > len(g.get("items") or []):
        raise ConfigError("design.gold.per_session: larger than the number of gold items")
    for gi in g.get("items") or []:
        if "id" not in gi or not (gi.get("expected_outcome") or gi.get("expected_answers")):
            raise ConfigError("design.gold.items: each needs `id` and `expected_outcome` or `expected_answers`")
        gi["id"] = str(gi["id"])
    seen = set()
    for a in d.get("attention") or []:
        if not a.get("id") or not a.get("html") or not a.get("expected_answers"):
            raise ConfigError("design.attention: each item needs `id`, `html` and `expected_answers`")
        if a["id"] in seen:
            raise ConfigError(f"design.attention: duplicate id {a['id']!r}")
        seen.add(a["id"])
        a.setdefault("position", "any")
        if a["position"] not in POSITIONS:
            raise ConfigError(f"design.attention {a['id']}: position must be one of {sorted(POSITIONS)}")
        hidden = (a.get("hidden_instruction") or {}).get("answers") or {}
        for qid in list(a["expected_answers"]) + list(hidden):
            if qid not in byid:
                raise ConfigError(f"design.attention {a['id']}: unknown question {qid!r}")
        if hidden and all(str(a["expected_answers"].get(k)) == str(v) for k, v in hidden.items()):
            raise ConfigError(f"design.attention {a['id']}: the hidden instruction's answers must differ "
                              "from the expected answers, or honest participants look like they followed it")

    st = cfg["study"]
    for k in ("idle_minutes", "stale_minutes", "heartbeat_seconds", "waiting_poll_seconds", "waiting_minutes"):
        if not isinstance(st[k], (int, float)) or st[k] <= 0:
            raise ConfigError(f"study.{k}: must be a positive number")
    if st["heartbeat_seconds"] * 3 > st["idle_minutes"] * 60:
        raise ConfigError("study.heartbeat_seconds: must be well below idle_minutes (at most a third), "
                          "or active participants could lose their batch")

    b = cfg["study"]["backend"]
    if b.get("type") not in ("demo", "apps_script", "server"):
        raise ConfigError("study.backend.type: one of demo, apps_script, server")
    if b["type"] == "apps_script" and not b.get("url"):
        raise ConfigError("study.backend.url: required for apps_script (the /exec URL)")
    if cfg["items"]["format"] not in ("text", "html"):
        raise ConfigError("items.format: `text` (escaped) or `html` (trusted)")


def _check_target(obj: dict, where: str, byid: dict, outcomes: dict, optional: bool = False) -> None:
    has_next, has_out = "next" in obj, "outcome" in obj
    if has_next and has_out:
        raise ConfigError(f"{where}: use either `next` or `outcome`, not both")
    if has_next and obj["next"] not in byid:
        raise ConfigError(f"{where}: `next` points to unknown question {obj['next']!r}")
    if has_out and outcomes and obj["outcome"] not in outcomes:
        raise ConfigError(f"{where}: outcome {obj['outcome']!r} is not listed in task.outcomes")


def _check_acyclic(cfg: dict) -> None:
    byid = questions_by_id(cfg)

    def succ(q):
        targets = []
        if q["type"] == "single":
            targets += [o["next"] for o in q["options"] if "next" in o]
            if any("next" not in o and "outcome" not in o for o in q["options"]) and "next" in q:
                targets.append(q["next"])
        elif "next" in q:
            targets.append(q["next"])
        return targets

    state: dict[str, int] = {}

    def dfs(qid, stack):
        state[qid] = 1
        for t in succ(byid[qid]):
            if state.get(t) == 1:
                raise ConfigError(f"task: the question flow has a cycle: {' -> '.join(stack + [qid, t])}")
            if t not in state:
                dfs(t, stack + [qid])
        state[qid] = 2

    dfs(cfg["task"]["start"], [])


def public_config(cfg: dict) -> dict:
    """The part of the config the browser may see: no gold answers, no item roles."""
    s = cfg["study"]
    return {
        "title": s["title"],
        "completion_url": s["completion_url"],
        "backend": {"type": s["backend"]["type"], "url": s["backend"].get("url", "")},
        "consent_html": s["consent_html"],
        "instructions_html": s["instructions_html"],
        "block_copy": bool(s["block_copy"]),
        "heartbeat_seconds": s["heartbeat_seconds"],
        "waiting_poll_seconds": s["waiting_poll_seconds"],
        "waiting_minutes": s["waiting_minutes"],
        "task": {k: cfg["task"][k] for k in ("start", "questions", "outcomes", "show_outcome")},
    }
