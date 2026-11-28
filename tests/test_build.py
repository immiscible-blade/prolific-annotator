import json
import os
import shutil
import subprocess

import pytest


def _read(*p):
    with open(os.path.join(*p), encoding="utf-8") as f:
        return f.read()


def test_site_layout(tree_study):
    _, out, _ = tree_study
    site = os.path.join(out, "site")
    for f in ["index.html", "app.js", "flow.js", "style.css", "_headers", "robots.txt",
              "data/study.json", "data/items.json", "data/batches_demo.json"]:
        assert os.path.exists(os.path.join(site, f)), f
    assert os.path.exists(os.path.join(out, "private", "manifest.json"))
    assert os.path.exists(os.path.join(out, "backend", "Code.gs"))


def test_no_private_data_in_site(tree_study):
    cfg, out, _ = tree_study
    site = os.path.join(out, "site")
    blob = "".join(_read(dp, f) for dp, _, fs in os.walk(site) for f in fs)
    for secret in ["expected_outcome", "expected_answers", "hidden_answers", '"role"', "gold"]:
        assert secret not in blob, secret
    pub = json.loads(_read(site, "data", "study.json"))
    assert set(pub) == {"title", "completion_url", "backend", "consent_html", "instructions_html", "block_copy", "task",
                        "heartbeat_seconds", "waiting_poll_seconds", "waiting_minutes"}


def test_highlight_and_hidden_text(tree_study):
    _, out, _ = tree_study
    items = json.loads(_read(out, "site", "data", "items.json"))
    assert '<mark id="span">' in items["t01"]
    assert "cnry" not in items["t01"]  # analysis items carry no hidden text
    assert 'class="cnry"' in items["g1"] and items["g1"].index("cnry") < items["g1"].index('<mark id="span">')
    assert 'class="cnry"' in items["attn1"]
    assert "&lt;hl&gt;" not in items["t01"] and "<hl>" not in items["t01"]


def test_escaping(tree_study, tmp_path):
    from prolific_annotator.items import to_html
    ic = {"format": "text", "highlight": {"start": "<hl>", "end": "</hl>"}}
    h = to_html("a <script>x</script> <hl>b & c</hl>\nnext", ic)
    assert "<script>" not in h and "&lt;script&gt;" in h
    assert '<mark id="span">b &amp; c</mark>' in h and h.count("<p>") == 2


def test_apps_script_generated(tree_study):
    _, out, _ = tree_study
    gs = _read(out, "backend", "Code.gs")
    assert "__" not in gs.split("// ---- batch list")[0].replace("__proto__", "")
    assert "const STALE_MIN = 90;" in gs and "const IDLE_MIN = 15;" in gs and '["TEST_", "DEV_", "DEMO_"]' in gs
    assert 'const DEFAULT_STUDY_ID = "";' in gs


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_apps_script_backend_in_mock(tree_study):
    _, out, _ = tree_study
    runner = os.path.join(os.path.dirname(__file__), "js", "test_code_gs.js")
    r = subprocess.run(["node", runner, os.path.join(out, "backend", "Code.gs")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FAIL" not in r.stdout and r.stdout.count("PASS") >= 30
