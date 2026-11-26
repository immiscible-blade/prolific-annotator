"""Real-browser test of the participant app (Playwright + Chromium).

Skipped when Playwright or a launchable Chromium is not available; runs in CI.
    pip install playwright && python -m playwright install --with-deps chromium
"""

import json
import os
import threading

import pytest

playwright = pytest.importorskip("playwright.sync_api")

from prolific_annotator.server import serve  # noqa: E402


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # missing system libraries, no browser installed, ...
            if os.environ.get("REQUIRE_BROWSER"):
                raise
            pytest.skip(f"chromium not launchable: {e}")
        yield b
        b.close()


def _answer_until_next(page):
    """Pick the first option of every visible unanswered question until Next is enabled."""
    for _ in range(12):
        if page.is_enabled("#next-btn"):
            return
        for q in page.query_selector_all("#tree .q"):
            ta = q.query_selector("textarea")
            if ta and not ta.input_value():
                ta.fill("ok")
                continue
            if not q.query_selector("input:checked"):
                inp = q.query_selector("input")
                if inp:
                    inp.check()
                    break
    assert page.is_enabled("#next-btn")


def test_full_session_with_server(tree_study, tmp_path, browser):
    _, out, _ = tree_study
    httpd, store = serve(out, "127.0.0.1", 0, str(tmp_path / "db.sqlite"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base}/?PROLIFIC_PID=E2E_1&STUDY_ID=s&SESSION_ID=x")
        page.wait_for_selector("#screen-consent:not(.hidden)")
        assert page.is_disabled("#consent-btn")
        page.check("#consent-box")
        page.click("#consent-btn")
        page.click("#instr-btn")
        n = None
        for i in range(30):
            page.wait_for_selector("#screen-item:not(.hidden), #screen-done:not(.hidden)")
            if page.is_visible("#screen-done"):
                break
            prog = page.inner_text("#progress")
            n = int(prog.split(" of ")[1])
            assert page.is_disabled("#next-btn")
            page.dispatch_event("#item-text", "copy")  # blocked + logged
            page.eval_on_selector(".text-pane", "e => e.scrollTo(0, e.scrollHeight)")
            _answer_until_next(page)
            page.click("#next-btn")
        page.wait_for_selector("#screen-done:not(.hidden)")
        assert not errors, errors
        rows = store.conn.execute("SELECT item_id, row_json FROM responses WHERE pid='E2E_1'").fetchall()
        assert len(rows) == n
        r = json.loads(rows[0][1])
        assert r["answers"] and r["blocked"] and r["time_on_item"] > 0
        assert any(json.loads(x[1])["span_seen_ms"] is not None for x in rows)
        assert store.conn.execute("SELECT status FROM batches WHERE pid='E2E_1'").fetchone()[0] == "done"
        # a reload after finishing does not hand out a new batch
        page.goto(f"{base}/?PROLIFIC_PID=E2E_1")
        page.wait_for_selector("#screen-error:not(.hidden)")
    finally:
        httpd.shutdown()


def test_demo_mode_offers_csv(survey_study, browser, tmp_path):
    import functools
    import http.server
    _, out, _ = survey_study
    site = os.path.join(out, "site")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{httpd.server_address[1]}/?batch=0")
        page.check("#consent-box")
        page.click("#consent-btn")
        page.click("#instr-btn")
        for _ in range(10):
            page.wait_for_selector("#screen-item:not(.hidden), #screen-done:not(.hidden)")
            if page.is_visible("#screen-done"):
                break
            _answer_until_next(page)
            page.click("#next-btn")
        link = page.wait_for_selector("#done-msg a")
        assert link.get_attribute("download").startswith("responses_DEMO_")
    finally:
        httpd.shutdown()
