"""Self-hosted backend: one process serves the participant app and the API.

Standard library only (http.server + sqlite3). Same API and same export columns
as the Google Apps Script backend, so the app, `pannot export` and `pannot ingest`
work identically with either.

    GET  /api?action=claim&pid=..   -> {ok, batch_id, items, resume_pos}
    POST /api  {action: "item", pid, batch_id, row}
    POST /api  {action: "complete", pid, batch_id, summary}

For a real study, run it behind HTTPS (e.g. a reverse proxy such as Caddy or nginx);
Prolific participants will not accept a plain-HTTP study link on most browsers.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MAX_BODY = 1_000_000  # bytes; one item row is a few KB
RESP_COLS = ["ts", "pid", "batch_id", "item_id", "pos", "label", "time_on_item", "trace", "row_json"]
SESS_COLS = ["ts", "pid", "batch_id", "event", "summary_json"]
BATCH_COLS = ["batch_id", "items", "status", "pid", "claimed_at", "completed_at"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    """SQLite-backed batch assignment and response log. Thread-safe."""

    def __init__(self, db_path: str, batches: list[dict] | None = None, stale_minutes: float = 90,
                 test_prefixes: tuple[str, ...] = ("TEST_", "DEV_")):
        self.db_path = db_path
        self.stale_minutes = stale_minutes
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
        if batches is not None and self.conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0:
            self.conn.executemany("INSERT INTO batches (batch_id, items) VALUES (?, ?)",
                                  [(b["batch_id"], json.dumps(b["items"])) for b in batches])
            self.conn.commit()

    def claim(self, pid: str) -> dict:
        if not pid:
            return {"ok": False, "error": "bad request"}
        with self.lock:
            c = self.conn
            row = c.execute("SELECT batch_id, items, status FROM batches WHERE pid = ?", (pid,)).fetchone()
            if row:
                if row[2] == "done":
                    return {"ok": False, "error": "You have already completed this study."}
                n = c.execute("SELECT COUNT(DISTINCT pos) FROM responses WHERE pid = ? AND batch_id = ?",
                              (pid, row[0])).fetchone()[0]
                return {"ok": True, "batch_id": row[0], "items": json.loads(row[1]), "resume_pos": n}
            now = _now()
            for ord_, bid, items, status, old_pid, claimed_at in c.execute(
                    "SELECT ord, batch_id, items, status, pid, claimed_at FROM batches ORDER BY ord").fetchall():
                stale = (status == "claimed" and claimed_at and
                         (now - datetime.fromisoformat(claimed_at)).total_seconds() / 60 > self.stale_minutes)
                if not status or stale:
                    c.execute("UPDATE batches SET status='claimed', pid=?, claimed_at=? WHERE ord=?",
                              (pid, now.isoformat(), ord_))
                    if stale:
                        c.execute("INSERT INTO sessions (ts, pid, batch_id, event, summary_json) VALUES (?,?,?,?,?)",
                                  (now.isoformat(), old_pid, bid, "released_stale", ""))
                    c.commit()
                    return {"ok": True, "batch_id": bid, "items": json.loads(items), "resume_pos": 0}
            return {"ok": False, "error": "No study slots are available right now. Please return the study."}

    def post(self, d: dict) -> dict:
        action, pid, bid = d.get("action"), d.get("pid"), d.get("batch_id")
        ts = _now().isoformat()
        with self.lock:
            if action == "item":
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
                    c.execute("UPDATE batches SET status='', pid='', claimed_at='', completed_at='' WHERE batch_id=?",
                              (bid,))
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
            self.conn.execute("UPDATE batches SET status='', pid='', claimed_at='', completed_at='' WHERE batch_id=?",
                              (batch_id,))
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
                return self._json(store.claim(q.get("pid", [""])[0]))
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
          stale_minutes: float = 90, test_prefixes=("TEST_", "DEV_")):
    site = os.path.join(build_dir, "site")
    with open(os.path.join(build_dir, "backend", "batches.json"), encoding="utf-8") as f:
        batches = json.load(f)
    store = Store(db_path or os.path.join(build_dir, "responses.sqlite"), batches, stale_minutes, test_prefixes)
    httpd = ThreadingHTTPServer((host, port), make_handler(store, site))
    return httpd, store
