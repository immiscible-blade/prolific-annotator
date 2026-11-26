# Developer guide

## Architecture

```
src/prolific_annotator/
  config.py      load + validate study.yaml; public_config() = what the browser may see
  items.py       read items (csv/jsonl/json), escape, highlight -> <mark id="span">, word counts
  routing.py     question flow (walk/prune/matches)        ── must equal ──┐
  templates/app/flow.js  the same flow in the browser  ◄─────────────────────┘ (tests/test_flow_parity.py)
  batching.py    balanced incomplete design + invariant checks
  build.py       config -> site/ (app + data), backend/ (Code.gs, batches.json), private/ (manifest)
  templates/app/ index.html, app.js (participant app), style.css
  templates/apps_script/Code.template.gs   Google Apps Script backend (placeholders filled by build)
  server.py      Store (SQLite) + HTTP handler: the same API as Code.gs
  ingest.py      responses CSV -> worker flags, votes, distributions, answer counts
  compare.py     exchangeability permutation test (pure Python)
  simulate.py    synthetic responses with planted bad workers
  cli.py         `pannot` subcommands
  examples/      decision-tree and survey studies used by `pannot init` and the tests
```

Data flow:

1. `build` writes everything the participant needs into `site/data/`:
   * `study.json`: public config only;
   * `items.json`: `{id: html}` for analysis, gold and attention items;
   * `batches_demo.json`: id lists, used only in demo mode.
2. The backend holds the batch list. The app asks it for a batch, shows the items, and posts one row per item.
3. `ingest` joins the export with `private/manifest.json` (roles, gold answers, attention expectations, word counts, `has_highlight`).

**Privacy boundary:** nothing that tells gold items apart, or reveals their answers, may reach `site/`. `tests/test_build.py::test_no_private_data_in_site` enforces this.

## The app–backend API

Both backends implement it exactly; `tests/js/test_code_gs.js` and `tests/test_server.py` test them.

### `GET <url>?action=claim&pid=<id>&study=..&session=..`

```json
{"ok": true, "batch_id": "s-003", "items": ["t07", "attn1", "g2", "..."], "resume_pos": 0}
```

| case | behaviour |
|---|---|
| pid already holds a batch, not done | the same batch, with `resume_pos` = distinct positions already posted |
| pid holds a batch that is done | `{"ok": false, "error": "You have already completed this study."}` |
| otherwise | the first free batch, or the first batch claimed more than `stale_minutes` ago (logged as `released_stale` in sessions) |
| none left | `{"ok": false, "error": "No study slots are available …"}` |

### `POST <url>` (body is JSON, sent as `Content-Type: text/plain` to avoid a CORS preflight)

* `{"action": "item", "pid", "batch_id", "row": {...}}` appends a response row.
* `{"action": "complete", "pid", "batch_id", "summary": {...}}` marks the batch done and logs the session.

### Item row (`row`, stored as `row_json`)

| key | meaning |
|---|---|
| `item_id`, `pos` | which item, position in the session |
| `answers` | `{qid: value}` (list for multi) |
| `outcome` | outcome reached, or null |
| `t_start`, `t_end`, `time_on_item` | ms since page load / ms on the item |
| `t_first` | ms until the first answer |
| `focus` | `[[event, ms], …]` with `blur`, `focus`, `hidden`, `visible` |
| `blocked` | blocked copy/cut/context-menu/shortcut attempts |
| `history` | `[[qid, value, ms], …]`, every answer change |
| `span_seen_ms` | ms until the highlighted passage first came into view, or null |
| `max_scroll` | furthest scroll position reached (0–1) |
| `n_items`, `study`, `session`, `app_version` | context |

### Export columns

The Google Sheet tabs and `pannot export` produce the same columns:

| file | columns |
|---|---|
| `responses` | `ts, pid, batch_id, item_id, pos, label, time_on_item, trace, row_json` (`trace` = answers JSON, `label` = outcome) |
| `sessions` | `ts, pid, batch_id, event, summary_json` |
| `batches` | `batch_id, items, status, pid, claimed_at, completed_at` |

## Extending

**A new question type** touches five places:
1. `config.QUESTION_TYPES` and validation in `config.validate`;
2. `routing.is_answered`;
3. `flow.js` `isAnswered` (keep them identical: the parity test enumerates answers per type in `_candidates`, so add the new type there too);
4. rendering in `app.js` `questionNode`;
5. `simulate._random_answer`, and `ingest.answer_counts` if the answer isn't a string or list.

**A new flag** touches three places:
1. compute it in `ingest.worker_flags` (the per-item rows carry everything the app logs);
2. add it to the `n_flags` sum if it should count;
3. add a default under `DEFAULTS["flags"]["rules"]` and document it in `docs/config-reference.md`. If the app must log something new, add it to the row in `app.js` and document it in the item row table above.

**A new backend** must implement the API above, store rows in the export column format, and reassign stale batches the same way. Reuse `tests/test_server.py` as a contract test.

## Tests

```bash
pytest                       # everything except the browser tests (~5 s)
pytest tests/e2e             # Playwright + Chromium; set REQUIRE_BROWSER=1 to fail instead of skip
```

| test | covers |
|---|---|
| `test_config.py` | defaults, YAML yes/no, every validation error |
| `test_routing.py` | outcomes, pruning, expectations, optional questions |
| `test_flow_parity.py` | Python and JavaScript flow agree on thousands of answer sets (needs node) |
| `test_batching.py` | exact vote counts, balance, determinism, uneven sizes, `include` |
| `test_build.py` | layout, no private data in the site, escaping, hidden text, generated Code.gs run against a mock Google Sheet (node) |
| `test_server.py` | claim/resume/complete/stale/reset/release/export, and HTTP end to end |
| `test_ingest.py` | planted bad workers flagged and honest ones not, `FALSE` labels, test ids, rejections, no-majority rule |
| `test_compare.py` | null calibration and power of the permutation test |
| `test_cli.py` | init → validate → build → simulate → ingest → compare |
| `e2e/test_browser.py` | a full session in Chromium against the server; demo mode CSV |

CI (`.github/workflows/ci.yml`) runs the unit tests on Python 3.10, 3.12 and 3.13, and the browser tests on 3.12. `pages.yml` publishes both examples in demo mode to GitHub Pages.
