import json
import os
import threading
import urllib.request
from datetime import datetime, timedelta, timezone

from prolific_annotator.server import Store, serve


def _batches(out):
    with open(os.path.join(out, "backend", "batches.json")) as f:
        return json.load(f)


def test_store_lifecycle(tree_study, tmp_path):
    _, out, _ = tree_study
    bs = _batches(out)
    s = Store(str(tmp_path / "db.sqlite"), bs, stale_minutes=90)
    a = s.claim("P1")
    assert a["ok"] and a["batch_id"] == bs[0]["batch_id"] and a["resume_pos"] == 0
    assert s.claim("P2")["batch_id"] == bs[1]["batch_id"]
    s.post({"action": "item", "pid": "P1", "batch_id": a["batch_id"], "row": {"item_id": "t01", "pos": 0, "outcome": "false",
                                                                          "answers": {"q1": "no"}, "time_on_item": 1}})
    assert s.claim("P1")["resume_pos"] == 1
    s.post({"action": "complete", "pid": "P1", "batch_id": a["batch_id"], "summary": {}})
    assert not s.claim("P1")["ok"]
    # never-claimed batches go first; P2's silent batch only once nothing else is free
    old = (datetime.now(timezone.utc) - timedelta(minutes=120)).isoformat()
    s.conn.execute("UPDATE batches SET last_seen=? WHERE pid='P2'", (old,))
    s.conn.commit()
    assert s.claim("P3")["batch_id"] == bs[2]["batch_id"]
    for i in range(3, len(bs)):
        assert s.claim(f"F{i}")["ok"]
    assert s.claim("P3b")["batch_id"] == bs[1]["batch_id"]
    s.release(bs[5]["batch_id"])
    s.claim("TEST_me")
    assert "freed 1" in s.reset_tests()
    s.release(a["batch_id"])
    assert s.claim("P4")["batch_id"] == a["batch_id"]
    counts = s.export(str(tmp_path / "exp"))
    assert counts["responses"] == 1 and counts["batches"] == len(bs)
    with open(tmp_path / "exp" / "batches.csv") as f:
        assert f.readline().strip().endswith("last_seen")
    with open(tmp_path / "exp" / "responses.csv") as f:
        header = f.readline().strip().split(",")
    assert header == ["ts", "pid", "batch_id", "item_id", "pos", "label", "time_on_item", "trace", "row_json"]


def test_store_persists_and_runs_out(tree_study, tmp_path):
    _, out, _ = tree_study
    bs = _batches(out)
    db = str(tmp_path / "db.sqlite")
    s = Store(db, bs)
    for i in range(len(bs)):
        assert s.claim(f"W{i}")["ok"]
    assert not s.claim("late")["ok"]
    s2 = Store(db, bs)  # reopening does not re-insert batches
    assert s2.conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == len(bs)


def test_http_end_to_end(tree_study, tmp_path):
    _, out, _ = tree_study
    httpd, store = serve(out, "127.0.0.1", 0, str(tmp_path / "db.sqlite"))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{port}"
        html = urllib.request.urlopen(base + "/").read().decode()
        assert "flow.js" in html
        study = json.loads(urllib.request.urlopen(base + "/data/study.json").read())
        assert study["task"]["start"] == "q1"
        c = json.loads(urllib.request.urlopen(base + "/api?action=claim&pid=H1").read())
        assert c["ok"]
        body = json.dumps({"action": "item", "pid": "H1", "batch_id": c["batch_id"],
                           "row": {"item_id": c["items"][0], "pos": 0, "answers": {"q1": "no"}, "outcome": "no_claim",
                                   "time_on_item": 1000}}).encode()
        req = urllib.request.Request(base + "/api", data=body, headers={"Content-Type": "text/plain"})
        assert json.loads(urllib.request.urlopen(req).read())["ok"]
        assert store.conn.execute("SELECT label FROM responses").fetchone()[0] == "no_claim"
        try:
            urllib.request.urlopen(urllib.request.Request(base + "/api", data=b"{nope", headers={"Content-Type": "text/plain"}))
            raise AssertionError("bad json accepted")
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        httpd.shutdown()


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _fill(s, bs, prefix="X"):
    for i in range(len(bs)):
        s.claim(f"{prefix}{i}")


def test_heartbeat_and_idle_release(tree_study, tmp_path):
    _, out, _ = tree_study
    bs = _batches(out)
    s = Store(str(tmp_path / "db.sqlite"), bs, idle_minutes=15)
    _fill(s, bs)
    w = s.claim("NEW")
    assert not w["ok"] and w["waiting"]  # waiting room signal
    s.conn.execute("UPDATE batches SET last_seen=? WHERE pid='X0'", (_ago(20),))
    s.conn.commit()
    assert s.post({"action": "ping", "pid": "X0", "batch_id": bs[0]["batch_id"]}) == {"ok": True, "held": True}
    assert not s.claim("NEW")["ok"]  # the ping kept it
    s.conn.execute("UPDATE batches SET last_seen=? WHERE pid='X0'", (_ago(20),))
    s.conn.commit()
    s.post({"action": "item", "pid": "X0", "batch_id": bs[0]["batch_id"], "row": {"item_id": "t01", "pos": 0,
                                                                              "answers": {}, "time_on_item": 1}})
    assert not s.claim("NEW")["ok"]  # an answer counts as a sign of life
    s.conn.execute("UPDATE batches SET last_seen=? WHERE pid='X0'", (_ago(20),))
    s.conn.commit()
    g = s.claim("NEW")
    assert g["ok"] and g["batch_id"] == bs[0]["batch_id"]
    assert s.post({"action": "ping", "pid": "X0", "batch_id": bs[0]["batch_id"]})["held"] is False
    ev = s.conn.execute("SELECT pid, event FROM sessions").fetchall()
    assert ("X0", "released_stale") in ev


def test_rows_without_heartbeat_use_stale_minutes(tree_study, tmp_path):
    _, out, _ = tree_study
    bs = _batches(out)
    s = Store(str(tmp_path / "db.sqlite"), bs, idle_minutes=15, stale_minutes=90)
    _fill(s, bs)
    s.conn.execute("UPDATE batches SET last_seen='', claimed_at=? WHERE pid='X1'", (_ago(30),))
    s.conn.commit()
    assert not s.claim("NEW")["ok"]
    s.conn.execute("UPDATE batches SET claimed_at=? WHERE pid='X1'", (_ago(100),))
    s.conn.commit()
    assert s.claim("NEW")["batch_id"] == bs[1]["batch_id"]


def test_prolific_status_release(tree_study, tmp_path):
    _, out, _ = tree_study
    bs = _batches(out)
    calls = []

    def statuses(study):
        calls.append(study)
        return {"X0": "ACTIVE", "X1": "RETURNED", "X2": "AWAITING REVIEW"}

    s = Store(str(tmp_path / "db.sqlite"), bs, idle_minutes=15, statuses=statuses, default_study="STUDY")
    _fill(s, bs)
    s.conn.execute("UPDATE batches SET last_seen=? WHERE pid IN ('X0','X3')", (_ago(500),))
    s.conn.commit()
    g = s.claim("NEW1")
    assert g["batch_id"] == bs[1]["batch_id"] and calls == ["STUDY"]  # RETURNED freed at once
    g = s.claim("NEW2", study="FROM_LINK")
    assert g["batch_id"] == bs[3]["batch_id"] and calls[-1] == "FROM_LINK"  # unknown id: heartbeat rule
    assert not s.claim("NEW3")["ok"]  # X0 is ACTIVE: kept however long the pause
    ev = [e for _, e in s.conn.execute("SELECT pid, event FROM sessions").fetchall()]
    assert "released_prolific_returned" in ev


def test_prolific_client(monkeypatch):
    from prolific_annotator.server import ProlificStatuses
    seen = []

    class Resp:
        def __init__(self, body):
            self.body = body

        def read(self):
            return json.dumps(self.body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    pages = [{"results": [{"participant_id": "a", "status": "ACTIVE"}], "_links": {"next": {"href": "https://x/next"}}},
             {"results": [{"participant_id": "b", "status": "RETURNED"}]}]

    def opener(req, timeout):
        seen.append((req.full_url, req.get_header("Authorization")))
        return Resp(pages[len(seen) - 1])

    c = ProlificStatuses("tok", opener=opener)
    assert c("S1") == {"a": "ACTIVE", "b": "RETURNED"}
    assert seen[0] == ("https://api.prolific.com/api/v1/studies/S1/submissions/", "Token tok")
    assert c("S1") == {"a": "ACTIVE", "b": "RETURNED"} and len(seen) == 2  # cached
    assert c("") is None

    def broken(req, timeout):
        raise OSError("down")

    assert ProlificStatuses("tok", opener=broken)("S1") is None  # errors -> fall back to heartbeat rule


def test_old_database_is_upgraded(tree_study, tmp_path):
    import sqlite3
    _, out, _ = tree_study
    db = str(tmp_path / "old.sqlite")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE batches (ord INTEGER PRIMARY KEY, batch_id TEXT UNIQUE, items TEXT, status TEXT DEFAULT '', "
              "pid TEXT DEFAULT '', claimed_at TEXT DEFAULT '', completed_at TEXT DEFAULT '')")
    c.execute("INSERT INTO batches (batch_id, items) VALUES ('s-000', '[\"t01\"]')")
    c.commit()
    c.close()
    s = Store(db, None)
    assert s.claim("P")["batch_id"] == "s-000"
