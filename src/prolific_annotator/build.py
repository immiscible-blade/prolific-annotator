"""Turn a study config into deployable files.

Output layout (``pannot build study.yaml --out build``)::

    build/site/       static participant app: deploy this folder (Netlify, GitHub Pages, any host)
    build/backend/    Code.gs (Google Apps Script); batches.json + settings.json (for `pannot serve`)
    build/private/    manifest.json: item roles, gold answers, attention expectations,
                      word counts. Needed by `pannot ingest`. NEVER deploy this folder.

The browser only ever receives the public part of the config, item HTML keyed by id,
and batch id lists. Which items are gold, and their answers, stay in ``private/``.
"""

from __future__ import annotations

import json
import os
import shutil
from importlib import resources

from .batching import build_batches, summary
from .config import public_config
from .items import load_items

APP_FILES = ["index.html", "app.js", "flow.js", "style.css"]
HEADERS = """/*
  X-Robots-Tag: noindex, nofollow
  Referrer-Policy: no-referrer
/data/*
  Cache-Control: no-cache
/*.js
  Cache-Control: no-cache
"""


def _template(*parts: str) -> str:
    return resources.files("prolific_annotator").joinpath("templates", *parts).read_text(encoding="utf-8")


def hidden_span(html: str) -> str:
    return f'<span class="cnry" aria-hidden="true">{html}</span>'


def insert_hidden(body: str, html: str) -> str:
    """Put a hidden instruction right before the highlighted passage (or at the end)."""
    if not html:
        return body
    marker = '<mark id="span">'
    return body.replace(marker, hidden_span(html) + marker, 1) if marker in body else body + hidden_span(html)


def build(cfg: dict, out_dir: str, batch_prefix: str = "s") -> dict:
    items = load_items(cfg)
    batches = build_batches(cfg, items, prefix=batch_prefix)
    d = cfg["design"]
    gold = {g["id"]: g for g in d["gold"]["items"]}
    used = {x["id"] for b in batches for x in b["items"] if x["role"] in ("analysis", "gold")}

    site = os.path.join(out_dir, "site")
    if os.path.isdir(site):
        shutil.rmtree(site)
    os.makedirs(os.path.join(site, "data"))
    for name in APP_FILES:
        with open(os.path.join(site, name), "w", encoding="utf-8") as f:
            f.write(_template("app", name))
    with open(os.path.join(site, "_headers"), "w") as f:
        f.write(HEADERS)
    with open(os.path.join(site, "robots.txt"), "w") as f:
        f.write("User-agent: *\nDisallow: /\n")

    item_html = {}
    for it in items:
        if it["id"] not in used:
            continue
        h = it["html"]
        if it["id"] in gold:
            h = insert_hidden(h, d["gold"].get("hidden_instruction_html", ""))
        item_html[it["id"]] = h
    for a in d.get("attention") or []:
        item_html[a["id"]] = insert_hidden(a["html"], (a.get("hidden_instruction") or {}).get("html", ""))

    pub = public_config(cfg)
    ids_only = [{"batch_id": b["batch_id"], "items": [x["id"] for x in b["items"]]} for b in batches]
    _dump(os.path.join(site, "data", "study.json"), pub)
    _dump(os.path.join(site, "data", "items.json"), item_html)
    _dump(os.path.join(site, "data", "batches_demo.json"), ids_only)

    backend = os.path.join(out_dir, "backend")
    os.makedirs(backend, exist_ok=True)
    gs = (_template("apps_script", "Code.template.gs")
          .replace("__IDLE_MIN__", json.dumps(cfg["study"]["idle_minutes"]))
          .replace("__STUDY_ID__", json.dumps(str(cfg["study"].get("prolific_study_id") or "")))
          .replace("__STALE_MIN__", str(int(cfg["study"]["stale_minutes"])))
          .replace("__TEST_PREFIXES__", json.dumps(cfg["flags"]["ignore_pid_prefixes"]))
          .replace("__BATCHES__", json.dumps(ids_only)))
    with open(os.path.join(backend, "Code.gs"), "w", encoding="utf-8") as f:
        f.write(gs)
    _dump(os.path.join(backend, "batches.json"), ids_only)
    _dump(os.path.join(backend, "settings.json"), {
        "idle_minutes": cfg["study"]["idle_minutes"], "stale_minutes": cfg["study"]["stale_minutes"],
        "test_prefixes": cfg["flags"]["ignore_pid_prefixes"],
        "prolific_study_id": str(cfg["study"].get("prolific_study_id") or "")})

    private = os.path.join(out_dir, "private")
    os.makedirs(private, exist_ok=True)
    manifest = {
        "items": {it["id"]: {"role": "gold" if it["id"] in gold else "analysis", "words": it["words"],
                             "has_highlight": 'id="span"' in it["html"], "meta": it["meta"]}
                  for it in items if it["id"] in used},
        "gold": gold,
        "gold_hidden_answers": d["gold"].get("hidden_answers") or {},
        "attention": {a["id"]: {"expected_answers": a["expected_answers"],
                                "hidden_answers": (a.get("hidden_instruction") or {}).get("answers") or {}}
                      for a in d.get("attention") or []},
        "batches": batches,
    }
    _dump(os.path.join(private, "manifest.json"), manifest)
    with open(os.path.join(private, "README.txt"), "w") as f:
        f.write("Private study data (gold answers, item roles). Do not deploy or publish this folder.\n")
    return summary(batches)


def _dump(path: str, obj) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1 if path.endswith(("manifest.json", "batches.json")) else None)
