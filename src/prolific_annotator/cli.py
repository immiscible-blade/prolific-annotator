"""`pannot` command-line interface.

    pannot init my-study [--example decision-tree|survey]   start a study from an example
    pannot validate my-study/study.yaml                      check the config and items
    pannot build my-study/study.yaml --out build             site + backend + private manifest
    pannot serve build [--port 8765]                         self-hosted app + API (SQLite)
    pannot export build --out exports                        server database -> CSV
    pannot reset-tests build                                 undo TEST_/DEV_ sessions (server)
    pannot release build BATCH_ID                            reopen one batch (server)
    pannot simulate my-study/study.yaml build --out sim.csv  fake responses for a dry run
    pannot ingest my-study/study.yaml build responses.csv --out results
    pannot compare old_votes.csv results/votes.csv --out comparison.csv
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from importlib import resources

from . import __version__
from .config import ConfigError, load_config

EXAMPLES = ["decision-tree", "survey"]


def _example_dir(name: str):
    return resources.files("prolific_annotator").joinpath("examples", name)


def cmd_init(a):
    if os.path.exists(a.dir) and os.listdir(a.dir):
        sys.exit(f"{a.dir} exists and is not empty")
    os.makedirs(a.dir, exist_ok=True)
    src = _example_dir(a.example)
    for f in src.iterdir():
        if f.is_file():
            (open(os.path.join(a.dir, f.name), "wb")).write(f.read_bytes())
    print(f"Created {a.dir} from the '{a.example}' example. Next:\n"
          f"  pannot validate {a.dir}/study.yaml\n  pannot build {a.dir}/study.yaml --out {a.dir}/build\n"
          f"  pannot serve {a.dir}/build      # then open http://127.0.0.1:8765/?PROLIFIC_PID=TEST_me")


def cmd_validate(a):
    from .batching import build_batches, summary
    from .items import load_items
    cfg = load_config(a.config)
    items = load_items(cfg)
    s = summary(build_batches(cfg, items))
    print(f"OK: {len(cfg['task']['questions'])} questions, {len(items)} items, "
          f"{s['sessions']} sessions of {s['items_per_session']} items "
          f"(analysis words per session {s['words_min']}-{s['words_max']}).")


def cmd_build(a):
    from .build import build
    cfg = load_config(a.config)
    if a.demo:  # public demo: no backend, nothing sent anywhere, no redirect
        cfg["study"]["backend"] = {"type": "demo", "url": ""}
        cfg["study"]["completion_url"] = ""
    s = build(cfg, a.out, batch_prefix=a.prefix)
    print(f"Built {s['sessions']} sessions into {a.out}/\n"
          f"  deploy:      {a.out}/site/\n  apps script: {a.out}/backend/Code.gs\n"
          f"  private:     {a.out}/private/  (never deploy)")


def _store(build_dir, a, cfg=None):
    from .server import Store
    with open(os.path.join(build_dir, "backend", "batches.json"), encoding="utf-8") as f:
        batches = json.load(f)
    return Store(a.db or os.path.join(build_dir, "responses.sqlite"), batches)


def cmd_serve(a):
    from .server import serve
    httpd, store = serve(a.build, a.host, a.port, a.db, a.stale_minutes, idle_minutes=a.idle_minutes)
    print("Prolific status check: " + ("on (PROLIFIC_TOKEN set)" if store.statuses else
                                       "off (set PROLIFIC_TOKEN to free returned batches at once)"))
    print(f"Serving {a.build}/site with the API at http://{a.host}:{a.port}/api  (database: {store.db_path})\n"
          f"Test link: http://{a.host}:{a.port}/?PROLIFIC_PID=TEST_me   (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


def cmd_export(a):
    print(_store(a.build, a).export(a.out))


def cmd_reset_tests(a):
    print(_store(a.build, a).reset_tests())


def cmd_release(a):
    _store(a.build, a).release(a.batch_id)
    print(f"released {a.batch_id}")


def cmd_simulate(a):
    from .simulate import simulate
    cfg = load_config(a.config)
    with open(os.path.join(a.build, "private", "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    print(simulate(cfg, manifest, a.out, seed=a.seed))


def cmd_ingest(a):
    from .ingest import ingest
    cfg = load_config(a.config)
    s = ingest(cfg, a.build, a.responses, a.out, a.rejected, a.drop_flagged)
    print(json.dumps(s, indent=1))
    print(f"Wrote worker_flags.csv, votes.csv, item_distributions.csv, answer_counts.csv to {a.out}/")


def cmd_compare(a):
    from .compare import compare, read_votes
    from .ingest import write_csv
    old = read_votes(a.old, a.item_col, a.old_label_col)
    new = read_votes(a.new, "item_id", "outcome")
    r = compare(old, new, a.n_perm, a.seed)
    if a.out:
        write_csv(a.out, r["items"])
    print(json.dumps({k: v for k, v in r.items() if k != "items"}, indent=1))


def main(argv=None):
    p = argparse.ArgumentParser(prog="pannot", description="Annotation studies on Prolific.")
    p.add_argument("--version", action="version", version=f"prolific-annotator {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a new study folder from an example")
    s.add_argument("dir")
    s.add_argument("--example", choices=EXAMPLES, default="decision-tree")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("validate", help="check a study config and its items")
    s.add_argument("config")
    s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("build", help="build the site, backend and private manifest")
    s.add_argument("config")
    s.add_argument("--out", default="build")
    s.add_argument("--prefix", default="s", help="batch id prefix (e.g. pilot)")
    s.add_argument("--demo", action="store_true", help="build a backend-free demo (answers downloadable as CSV)")
    s.set_defaults(fn=cmd_build)

    for name, fn, hlp in [("serve", cmd_serve, "run the self-hosted app + API"),
                          ("export", cmd_export, "export the server database to CSV"),
                          ("reset-tests", cmd_reset_tests, "undo sessions of TEST_/DEV_ participants"),
                          ("release", cmd_release, "make one batch available again")]:
        s = sub.add_parser(name, help=hlp)
        s.add_argument("build")
        s.add_argument("--db", default=None, help="SQLite file (default: <build>/responses.sqlite)")
        if name == "serve":
            s.add_argument("--host", default="127.0.0.1")
            s.add_argument("--port", type=int, default=8765)
            s.add_argument("--stale-minutes", type=float, default=None, help="default: from study.yaml")
            s.add_argument("--idle-minutes", type=float, default=None, help="default: from study.yaml")
        if name == "export":
            s.add_argument("--out", default="exports")
        if name == "release":
            s.add_argument("batch_id")
        s.set_defaults(fn=fn)

    s = sub.add_parser("simulate", help="write fake responses for a dry run")
    s.add_argument("config")
    s.add_argument("build")
    s.add_argument("--out", default="simulated_responses.csv")
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(fn=cmd_simulate)

    s = sub.add_parser("ingest", help="flags, votes and per-item distributions from a responses export")
    s.add_argument("config")
    s.add_argument("build")
    s.add_argument("responses")
    s.add_argument("--out", default="results")
    s.add_argument("--rejected", default=None, help="file with one rejected participant id per line")
    s.add_argument("--drop-flagged", action="store_true", help="leave flagged workers out of the distributions")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("compare", help="exchangeability test: old labels vs new crowd labels")
    s.add_argument("old", help="CSV, one row per old vote")
    s.add_argument("new", help="votes.csv written by `pannot ingest`")
    s.add_argument("--item-col", default="item_id")
    s.add_argument("--old-label-col", default="outcome")
    s.add_argument("--n-perm", type=int, default=2000)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--out", default=None)
    s.set_defaults(fn=cmd_compare)

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except ConfigError as e:
        sys.exit(f"config error: {e}")


if __name__ == "__main__":
    main()
