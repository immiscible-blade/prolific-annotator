# Contributing

1. Fork, create a branch, and set up: `python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"`.
2. Make the change with tests. `pytest` must pass. Node is needed for the flow-parity and Apps Script tests.
3. If you touch the question flow, change `routing.py` **and** `templates/app/flow.js` together; `tests/test_flow_parity.py` checks they agree.
4. If you touch the app–backend API, update `Code.template.gs`, `server.py`, both contract tests and `docs/developer-guide.md`.
5. Never commit real participant data. `.gitignore` excludes the usual export names, but check `git status`.

See `docs/developer-guide.md` for the architecture and how to add question types or flags.
