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
    # make P2's claim stale -> a newcomer gets it
    old = (datetime.now(timezone.utc) - timedelta(minutes=120)).isoformat()
    s.conn.execute("UPDATE batches SET claimed_at=? WHERE pid='P2'", (old,))
    s.conn.commit()
    assert s.claim("P3")["batch_id"] == bs[1]["batch_id"]
    s.claim("TEST_me")
    assert "freed 1" in s.reset_tests()
    s.release(a["batch_id"])
    assert s.claim("P4")["batch_id"] == a["batch_id"]
    counts = s.export(str(tmp_path / "exp"))
    assert counts["responses"] == 1 and counts["batches"] == len(bs)
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
