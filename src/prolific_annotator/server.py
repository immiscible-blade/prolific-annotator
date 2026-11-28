"""Self-hosted backend: one process serves the participant app and the API.

Standard library only (http.server + sqlite3). Same API and same export columns
as the Google Apps Script backend, so the app, `pannot export` and `pannot ingest`
work identically with either.

    GET  /api?action=claim&pid=..&study=..  -> {ok, batch_id, items, resume_pos} | {ok: false, waiting: true}
    POST /api  {action: "item", pid, batch_id, row}
    POST /api  {action: "ping", pid, batch_id}          -> {ok, held}
    POST /api  {action: "complete", pid, batch_id, summary}

Abandoned batches are given to the next participant exactly as in the Apps Script
backend: never-claimed batches first; then, with a Prolific API token
(environment variable PROLIFIC_TOKEN), batches whose holder Prolific reports as
RETURNED / TIMED-OUT / REJECTED / SCREENED OUT; otherwise (unknown ids, no token,
API errors) batches with no heartbeat or answer for ``idle_minutes``.

For a real study, run it behind HTTPS (e.g. a reverse proxy such as Caddy or nginx);
Prolific participants will not accept a plain-HTTP study link on most browsers.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MAX_BODY = 1_000_000  # bytes; one item row is a few KB
RESP_COLS = ["ts", "pid", "batch_id", "item_id", "pos", "label", "time_on_item", "trace", "row_json"]
SESS_COLS = ["ts", "pid", "batch_id", "event", "summary_json"]
BATCH_COLS = ["batch_id", "items", "status", "pid", "claimed_at", "completed_at", "last_seen"]
PROLIFIC_API = "https://api.prolific.com/api/v1"
PROLIFIC_RELEASE = {"RETURNED", "TIMED-OUT", "REJECTED", "SCREENED OUT"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProlificStatuses:
    """{participant_id: status} for a study from Prolific's API, cached briefly.

    Returns None without a study id or on any error, so callers fall back to the
    heartbeat rule. ``GET /studies/{id}/submissions/`` with ``Authorization: Token ..``.
    """

    def __init__(self, token: str, cache_seconds: float = 30, timeout: float = 10, opener=None):
        self.token = token
        self.cache_seconds = cache_seconds
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen
        self._cache: dict[str, tuple[float, dict]] = {}

    def __call__(self, study: str) -> dict | None:
        if not study:
            return None
        hit = self._cache.get(study)
        if hit and time.monotonic() - hit[0] < self.cache_seconds:
            return hit[1]
        out: dict[str, str] = {}
        url = f"{PROLIFIC_API}/studies/{urllib.parse.quote(study)}/submissions/"
        try:
            for _ in range(50):
                req = urllib.request.Request(url, headers={"Authorization": f"Token {self.token}"})
                with self.opener(req, timeout=self.timeout) as r:
                    body = json.loads(r.read())
                for sub in body.get("results") or []:
                    out[str(sub.get("participant_id"))] = sub.get("status")
                url = ((body.get("_links") or {}).get("next") or {}).get("href")  # not documented; followed if present
                if not url:
                    break
        except Exception:
            return None
        self._cache[study] = (time.monotonic(), out)
        return out


class Store:
    """SQLite-backed batch assignment and response log. Thread-safe."""

    def __init__(self, db_path: str, batches: list[dict] | None = None, stale_minutes: float = 90,
                 test_prefixes: tuple[str, ...] = ("TEST_", "DEV_"), idle_minutes: float = 15,
                 statuses=None, default_study: str = ""):
        self.db_path = db_path
        self.stale_minutes = stale_minutes
        self.idle_minutes = idle_minutes
        self.statuses = statuses          # callable study -> {pid: status} | None (e.g. ProlificStatuses)
        self.default_study = default_study
        self.test_prefixes = tuple(test_prefixes)
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS batches (ord INTEGER PRIMARY KEY, batch_id TEXT UNIQUE, items TEXT,
                status TEXT DEFAULT '', pid TEXT DEFAULT '', claimed_at TEXT DEFAULT '', completed_at TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS responses (id INTEGER PRIMARY KEY, ts TEXT, pid TEXT, batch_id TEXT,
                item_id TEXT, pos INTEGER, label TEXT, time_on_item INTEGER, trace TEXT, row_json TEXT);
            CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY, ts TEXT, pid TEXT, batch_id TEXT,
                event TEXT, summary_json TEXT);
        """)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(batches)")}
        if "last_seen" not in cols:  # databases created by 0.1
            self.conn.execute("ALTER TABLE batches ADD COLUMN last_seen TEXT DEFAULT ''")
            self.conn.commit()
        if batches is not None and self.conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0:
            self.conn.executemany("INSERT INTO batches (batch_id, items) VALUES (?, ?)",
                                  [(b["batch_id"], json.dumps(b["items"])) for b in batches])
            self.conn.commit()

    def _release_reason(self, pid: str, claimed_at: str, last_seen: str, statuses: dict | None, now) -> str | None:
        st = statuses.get(pid) if statuses else None
        if st:  # Prolific knows this participant: trust Prolific
            return "prolific_" + st.lower().replace(" ", "_") if st in PROLIFIC_RELEASE else None
        if last_seen:
            return "stale" if (now - datetime.fromisoformat(last_seen)).total_seconds() / 60 > self.idle_minutes else None
        if claimed_at and (now - datetime.fromisoformat(claimed_at)).total_seconds() / 60 > self.stale_minutes:
            return "stale"
        return None

    def claim(self, pid: str, study: str = "") -> dict:
        if not pid:
            return {"ok": False, "error": "bad request"}
        with self.lock:
            c = self.conn
            now = _now()
            row = c.execute("SELECT batch_id, items, status FROM batches WHERE pid = ?", (pid,)).fetchone()
            if row:
                if row[2] == "done":
                    return {"ok": False, "error": "You have already completed this study."}
                c.execute("UPDATE batches SET last_seen=? WHERE pid=?", (now.isoformat(), pid))
                c.commit()
                n = c.execute("SELECT COUNT(DISTINCT pos) FROM responses WHERE pid = ? AND batch_id = ?",
                              (pid, row[0])).fetchone()[0]
                return {"ok": True, "batch_id": row[0], "items": json.loads(row[1]), "resume_pos": n}

            def give(ord_, bid, items, old_pid, reason):
                c.execute("UPDATE batches SET status='claimed', pid=?, claimed_at=?, last_seen=? WHERE ord=?",
                          (pid, now.isoformat(), now.isoformat(), ord_))
                if reason:
                    c.execute("INSERT INTO sessions (ts, pid, batch_id, event, summary_json) VALUES (?,?,?,?,?)",
                              (now.isoformat(), old_pid, bid, f"released_{reason}", ""))
                c.commit()
                return {"ok": True, "batch_id": bid, "items": json.loads(items), "resume_pos": 0}

            rows = c.execute("SELECT ord, batch_id, items, status, pid, claimed_at, last_seen "
                             "FROM batches ORDER BY ord").fetchall()
            for ord_, bid, items, status, old_pid, _, _ in rows:
                if not status:
                    return give(ord_, bid, items, old_pid, "")
            statuses = self.statuses(study or self.default_study) if self.statuses else None
            for ord_, bid, items, status, old_pid, claimed_at, last_seen in rows:
                if status == "claimed":
                    reason = self._release_reason(old_pid, claimed_at, last_seen, statuses, now)
                    if reason:
                        return give(ord_, bid, items, old_pid, reason)
            return {"ok": False, "waiting": True,
                    "error": "No study slots are available right now. Please return the study."}

    def _touch(self, pid: str, bid: str) -> bool:
        cur = self.conn.execute("UPDATE batches SET last_seen=? WHERE batch_id=? AND pid=?",
                                (_now().isoformat(), bid, pid))
        return cur.rowcount > 0

    def post(self, d: dict) -> dict:
        action, pid, bid = d.get("action"), d.get("pid"), d.get("batch_id")
        ts = _now().isoformat()
        with self.lock:
            if action == "ping":
                held = self._touch(pid, bid)
                self.conn.commit()
                return {"ok": True, "held": held}
            if action == "item":
                self._touch(pid, bid)
                r = d.get("row") or {}
                self.conn.execute(
                    "INSERT INTO responses (ts, pid, batch_id, item_id, pos, label, time_on_item, trace, row_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (ts, pid, bid, str(r.get("item_id")), r.get("pos"),
                     "" if r.get("outcome") is None else str(r.get("outcome")),
                     r.get("time_on_item"), json.dumps(r.get("answers")), json.dumps(r)))
            elif action == "complete":
                self.conn.execute("UPDATE batches SET status='done', completed_at=? WHERE batch_id=? AND pid=?",
                                  (ts, bid, pid))
                self.conn.execute("INSERT INTO sessions (ts, pid, batch_id, event, summary_json) VALUES (?,?,?,?,?)",
                                  (ts, pid, bid, "complete", json.dumps(d.get("summary"))))
            else:
                return {"ok": False, "error": "unknown action"}
            self.conn.commit()
        return {"ok": True}

    def reset_tests(self) -> str:
        with self.lock:
            c = self.conn
            freed = deleted = 0
            for bid, pid in c.execute("SELECT batch_id, pid FROM batches").fetchall():
                if pid and pid.startswith(self.test_prefixes):
                    c.execute("UPDATE batches SET status='', pid='', claimed_at='', completed_at='', last_seen='' "
                              "WHERE batch_id=?", (bid,))
                    freed += 1
            for table in ("responses", "sessions"):
                for rid, pid in c.execute(f"SELECT id, pid FROM {table}").fetchall():
                    if pid and pid.startswith(self.test_prefixes):
                        c.execute(f"DELETE FROM {table} WHERE id=?", (rid,))
                        deleted += 1
            c.commit()
        return f"freed {freed} batch(es), deleted {deleted} test row(s)"

    def release(self, batch_id: str) -> None:
        """Make one batch available again (e.g. after rejecting its participant)."""
        with self.lock:
            self.conn.execute("UPDATE batches SET status='', pid='', claimed_at='', completed_at='', last_seen='' "
                              "WHERE batch_id=?", (batch_id,))
            self.conn.commit()

    def export(self, out_dir: str) -> dict:
        """Write responses.csv, sessions.csv and batches.csv (same columns as the Google Sheet tabs)."""
        os.makedirs(out_dir, exist_ok=True)
        counts = {}
        for name, cols, sql in [
            ("responses", RESP_COLS, f"SELECT {', '.join(RESP_COLS)} FROM responses ORDER BY id"),
            ("sessions", SESS_COLS, f"SELECT {', '.join(SESS_COLS)} FROM sessions ORDER BY id"),
            ("batches", BATCH_COLS, f"SELECT {', '.join(BATCH_COLS)} FROM batches ORDER BY ord"),
        ]:
            rows = self.conn.execute(sql).fetchall()
            with open(os.path.join(out_dir, f"{name}.csv"), "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(cols)
                w.writerows(rows)
            counts[name] = len(rows)
        return counts


def make_handler(store: Store, site_dir: str):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=site_dir, **kw)

        def log_message(self, fmt, *args):  # quieter default logging
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/api":
                q = parse_qs(u.query)
                if q.get("action", [""])[0] != "claim":
                    return self._json({"ok": False, "error": "bad request"}, 400)
                return self._json(store.claim(q.get("pid", [""])[0], q.get("study", [""])[0]))
            return super().do_GET()

        def do_POST(self):
            if urlparse(self.path).path != "/api":
                return self._json({"ok": False, "error": "not found"}, 404)
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                return self._json({"ok": False, "error": "bad request"}, 400)
            try:
                d = json.loads(self.rfile.read(n))
            except (ValueError, UnicodeDecodeError):
                return self._json({"ok": False, "error": "bad json"}, 400)
            return self._json(store.post(d))

        def end_headers(self):
            if not urlparse(self.path).path.startswith("/api"):
                self.send_header("Cache-Control", "no-cache")
            super().end_headers()

    return Handler


def serve(build_dir: str, host: str = "127.0.0.1", port: int = 8765, db_path: str | None = None,
          stale_minutes: float | None = None, test_prefixes=None, idle_minutes: float | None = None,
          prolific_token: str | None = None):
    """Start (but do not run) the server. Settings default to build/backend/settings.json;
    the Prolific token defaults to the PROLIFIC_TOKEN environment variable."""
    site = os.path.join(build_dir, "site")
    with open(os.path.join(build_dir, "backend", "batches.json"), encoding="utf-8") as f:
        batches = json.load(f)
    settings = {}
    sp = os.path.join(build_dir, "backend", "settings.json")
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as f:
            settings = json.load(f)
    token = prolific_token if prolific_token is not None else os.environ.get("PROLIFIC_TOKEN", "")
    store = Store(db_path or os.path.join(build_dir, "responses.sqlite"), batches,
                  stale_minutes if stale_minutes is not None else settings.get("stale_minutes", 90),
                  tuple(test_prefixes or settings.get("test_prefixes") or ("TEST_", "DEV_")),
                  idle_minutes if idle_minutes is not None else settings.get("idle_minutes", 15),
                  ProlificStatuses(token) if token else None, settings.get("prolific_study_id", ""))
    httpd = ThreadingHTTPServer((host, port), make_handler(store, site))
    return httpd, store
