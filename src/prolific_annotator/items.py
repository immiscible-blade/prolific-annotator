"""Loading items (the texts to annotate) and turning them into safe HTML."""

from __future__ import annotations

import csv
import html
import json
import os
import re

from .config import ConfigError, resolve


def load_items(cfg: dict) -> list[dict]:
    """Read the items file named in ``items.file`` (CSV, JSONL or JSON list).

    Returns one dict per item: ``id`` (str), ``text`` (raw), ``html`` (rendered,
    with the highlight as ``<mark>``), ``words`` (word count) and ``meta`` (all
    other columns, kept for analysis but never sent to the browser).
    """
    ic = cfg["items"]
    path = resolve(cfg, ic["file"])
    if not os.path.exists(path):
        raise ConfigError(f"items.file: {path} does not exist")
    if path.endswith(".csv"):
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    elif path.endswith(".jsonl"):
        with open(path, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    elif path.endswith(".json"):
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
    else:
        raise ConfigError("items.file: use .csv, .jsonl or .json")

    idc, tc = ic["id_column"], ic["text_column"]
    items, seen = [], set()
    for i, r in enumerate(rows):
        if idc not in r or tc not in r:
            raise ConfigError(f"items row {i + 1}: needs columns {idc!r} and {tc!r}")
        iid = str(r[idc]).strip()
        if not iid or iid in seen:
            raise ConfigError(f"items row {i + 1}: id {iid!r} is empty or duplicated")
        if iid.startswith("attn"):
            raise ConfigError(f"items row {i + 1}: ids starting with 'attn' are reserved for attention items")
        seen.add(iid)
        text = str(r[tc])
        items.append({
            "id": iid,
            "text": text,
            "html": to_html(text, ic),
            "words": count_words(text, ic),
            "meta": {k: v for k, v in r.items() if k not in (idc, tc)},
        })
    if not items:
        raise ConfigError("items.file: no items found")
    return items


def _markers(ic: dict) -> tuple[str, str]:
    h = ic.get("highlight") or {}
    return h.get("start", "<hl>"), h.get("end", "</hl>")


def to_html(text: str, ic: dict) -> str:
    """Render one item. Plain text is escaped and split into paragraphs on newlines;
    ``format: html`` is trusted as is. Highlight markers become ``<mark>``; the first
    one gets ``id="span"`` so the app can tell when it has been scrolled into view."""
    start, end = _markers(ic)
    if ic.get("format") == "html":
        body, s, e = text, start, end
    else:
        body, s, e = html.escape(text), html.escape(start), html.escape(end)
    pattern = re.compile(re.escape(s) + r"(.*?)" + re.escape(e), re.S)
    n = 0

    def repl(m):
        nonlocal n
        n += 1
        return (f'<mark id="span">{m.group(1)}</mark>' if n == 1 else f"<mark>{m.group(1)}</mark>")

    body = pattern.sub(repl, body)
    if ic.get("format") == "html":
        return body
    return "".join(f"<p>{p}</p>" for p in body.split("\n") if p.strip())


def count_words(text: str, ic: dict) -> int:
    start, end = _markers(ic)
    plain = text.replace(start, " ").replace(end, " ")
    if ic.get("format") == "html":
        plain = re.sub(r"<[^>]+>", " ", plain)
    return len(plain.split())
