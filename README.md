# prolific-annotator

[![CI](https://github.com/immiscible-blade/prolific-annotator/actions/workflows/ci.yml/badge.svg)](https://github.com/immiscible-blade/prolific-annotator/actions/workflows/ci.yml)

Run text annotation studies on [Prolific](https://www.prolific.com) without writing a web app.

You describe the task in one YAML file: the questions, how they branch, the texts, how many annotators per text. `pannot` then builds:

* a **participant app** you can host anywhere as static files;
* a **backend** that hands each participant a balanced batch of texts and stores their answers. Use a free Google Sheet (Apps Script) or the bundled self-hosted server;
* **analysis-ready outputs**: per-worker quality flags, one row per vote, and per-text label distributions.

It grew out of a research study that collected larger-N label distributions for argument annotation, and it keeps that study's lessons as defaults (see [Design choices](#design-choices)).

**Live demo:** <https://dehlan.win/prolific-annotator/> (decision tree) · <https://dehlan.win/prolific-annotator/survey/> (questionnaire). Demo mode sends nothing anywhere; at the end you can download your answers as CSV.

## Features

* **Any task from config.** Branching decision trees, single choice, multiple choice, Likert scales and free text, with optional questions. Outcomes (labels) are derived from the answers.
* **Balanced design.** Every text gets exactly *k* independent annotators, sessions are balanced by reading length, and the same seed gives the same design.
* **Quality checks built in.**
  * Known-answer *gold* items and instructed-response *attention* items, placed in each session.
  * Optional hidden instructions that only an AI tool reading the page would follow.
  * Timing, reading speed, long absences, tab-switch bursts, and whether the highlighted passage was ever on screen.
  * Copying the text is blocked and logged.
* **Flags, not silent exclusion.** Every worker gets flags, and nobody is dropped automatically. Run analyses with and without flagged workers, and drop only participants you rejected by hand.
* **Two backends, one API.** Google Apps Script (free, no server) or `pannot serve` (Python standard library + SQLite). The exports are identical.
* **Resumable sessions.** A reload resumes where the participant left off. Abandoned batches are reassigned automatically, and test sessions can be wiped.
* **"Are k annotations enough?"** `pannot compare` runs an exchangeability permutation test of earlier labels against the new crowd labels.

## Quickstart

```bash
pip install git+https://github.com/immiscible-blade/prolific-annotator
pannot init my-study                     # copies the decision-tree example
pannot validate my-study/study.yaml      # checks config + items, shows the session design
pannot build my-study/study.yaml --out my-study/build
pannot serve my-study/build              # open http://127.0.0.1:8765/?PROLIFIC_PID=TEST_me
```

Dry-run the analysis before anyone takes part:

```bash
pannot simulate my-study/study.yaml my-study/build --out sim.csv
pannot ingest   my-study/study.yaml my-study/build sim.csv --out results
```

`results/` then holds `worker_flags.csv`, `votes.csv`, `item_distributions.csv` and `answer_counts.csv`.

## How a study runs

```
study.yaml + items.csv ──pannot build──► site/      → static host (Netlify, GitHub Pages, …)
                                         backend/   → Code.gs (Google Sheet) or `pannot serve`
                                         private/   → gold answers, item roles (keep local)

Prolific ──link with PROLIFIC_PID──► site ──claim batch / post answers──► backend
                                      └── last item → redirect to Prolific completion URL

backend export (CSV) ──pannot ingest──► flags, votes, distributions ──pannot compare──► test vs. old labels
```

## Documentation

* [User guide](docs/user-guide.md): from an idea to a finished Prolific study, step by step, no web development needed.
* [Config reference](docs/config-reference.md): every field of `study.yaml`.
* [Deployment](docs/deployment.md): Google Apps Script, Netlify, GitHub Pages, self-hosting.
* [Developer guide](docs/developer-guide.md): architecture, the app–backend API, file formats, extending question types and flags, tests.

## Design choices

These come from running a real study with this code:

* **Flag, don't exclude.** Automatic exclusion hides judgment calls in code. Every vote is kept with its worker's flags, and the decision to drop anyone is explicit.
* **Don't gate the Next button.** Forcing a minimum time or a scroll before Next teaches people to satisfy the gate, not to read. Behaviour is logged silently and flagged instead.
* **Calibrate flags against your earlier annotators.** If your trained annotators also skimmed, a strict speed flag holds the crowd to a different standard. Say so in the write-up.
* **Never trust a spreadsheet cell's type.** Google Sheets turns the text `false` into the boolean `FALSE`, so outcomes are recomputed from the answers. YAML would turn `yes`/`no` into booleans, so the loader keeps them as text.
* **Hidden instructions must lead to an answer no honest participant gives.** Otherwise careful humans look like bots. The validator checks this for attention items.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                      # unit, Python/JS parity and backend tests (needs node for some)
pip install playwright && python -m playwright install --with-deps chromium
pytest tests/e2e            # real-browser tests
```

## License

MIT. See [LICENSE](LICENSE).
