/* prolific-annotator — participant app.
 *
 * Flow: consent -> instructions -> items -> submit -> redirect to Prolific.
 * Everything study-specific comes from data/study.json (built by `pannot build`).
 *
 * Backends (study.backend.type):
 *   apps_script  Google Apps Script web app (GET ?action=claim, POST JSON as text/plain)
 *   server       the bundled Python server (`pannot serve`), same API under /api
 *   demo         no backend: a batch from data/batches_demo.json, answers kept in the
 *                page and offered as a CSV download (same columns as the backend export)
 *
 * Batches and leaving: the loading screen stays up until a batch has arrived; if every
 * batch is taken, a waiting room retries every waiting_poll_seconds for waiting_minutes.
 * While a batch is held the page sends a heartbeat every heartbeat_seconds, so the
 * backend can give the batch to someone else once the participant has really left
 * (Prolific status, or no heartbeat for idle_minutes).
 *
 * Logged per item: answers, outcome, time on item, time to first answer, focus/blur
 * and visibility events, blocked copy attempts, answer history, whether and when the
 * highlighted passage came into view, and how far the text was scrolled.
 */
(function () {
  "use strict";
  const APP_VERSION = "prolific-annotator/0.2";
  const qs = new URLSearchParams(location.search);
  const $ = (id) => document.getElementById(id);
  const S = { cfg: null, items: {}, pid: null, study: qs.get("STUDY_ID") || "", session: qs.get("SESSION_ID") || "",
              batch: null, seq: [], pos: 0, answers: {}, rows: [], t0: Date.now(), sessionEvents: [] };
  const now = () => Date.now() - S.t0;
  let cur = null;

  // ------------------------------------------------------------------ backend
  async function api(action, payload) {
    const b = S.cfg.backend;
    if (b.type === "demo") return demoApi(action, payload);
    const url = b.url || "/api";
    if (action === "claim") {
      const u = `${url}${url.includes("?") ? "&" : "?"}action=claim&pid=${encodeURIComponent(S.pid)}` +
                `&study=${encodeURIComponent(S.study)}&session=${encodeURIComponent(S.session)}`;
      return (await fetch(u)).json();
    }
    // text/plain avoids a CORS preflight, which Apps Script cannot answer
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "text/plain" },
      body: JSON.stringify({ action, pid: S.pid, batch_id: S.batch, ...payload }) });
    return r.json();
  }

  async function demoApi(action) {
    if (action !== "claim") return { ok: true };
    const all = await (await fetch("data/batches_demo.json")).json();
    const k = qs.has("batch") ? Number(qs.get("batch")) % all.length : Math.floor(Math.random() * all.length);
    return { ok: true, batch_id: all[k].batch_id, items: all[k].items, resume_pos: 0 };
  }

  async function postWithRetry(action, payload, tries = 4) {
    for (let i = 0; i < tries; i++) {
      try { const r = await api(action, payload); if (r && r.ok) return r; } catch (e) { /* retry */ }
      await new Promise((res) => setTimeout(res, 800 * 2 ** i));
    }
    throw new Error("Could not reach the server.");
  }

  // ------------------------------------------------------------------ logging
  document.addEventListener("visibilitychange", () => logFocus(document.hidden ? "hidden" : "visible"));
  window.addEventListener("blur", () => logFocus("blur"));
  window.addEventListener("focus", () => logFocus("focus"));
  function logFocus(ev) { (cur ? cur.focus : S.sessionEvents).push([ev, now()]); }

  function setupCopyBlocking() {
    if (!S.cfg.block_copy) return;
    $("item-text").classList.add("noselect");
    for (const ev of ["copy", "cut", "contextmenu", "selectstart", "dragstart"]) {
      document.addEventListener(ev, (e) => {
        if (e.target.closest && e.target.closest("#item-text")) {
          e.preventDefault();
          if (cur && ev !== "selectstart") cur.blocked.push([ev, now()]);
        }
      });
    }
    document.addEventListener("keydown", (e) => {
      if (cur && (e.ctrlKey || e.metaKey) && "acxpsu".includes(e.key.toLowerCase())
          && !(e.target.tagName === "TEXTAREA")) {
        e.preventDefault(); cur.blocked.push(["key_" + e.key.toLowerCase(), now()]);
      }
    });
  }

  // ------------------------------------------------------------------ screens
  function show(id) {
    document.querySelectorAll(".screen").forEach((s) => s.classList.add("hidden"));
    $(id).classList.remove("hidden");
    window.scrollTo(0, 0);
  }
  function fail(msg) { $("error-msg").textContent = msg; show("screen-error"); }

  async function start() {
    try {
      const [cfg, items] = await Promise.all([fetch("data/study.json").then((r) => r.json()),
                                              fetch("data/items.json").then((r) => r.json())]);
      S.cfg = cfg; S.items = items;
    } catch (e) { return fail("Could not load the study files."); }
    document.title = S.cfg.title;
    $("study-title").textContent = S.cfg.title;
    document.querySelectorAll(".title").forEach((e) => (e.textContent = S.cfg.title));
    S.pid = qs.get("PROLIFIC_PID") ||
            (S.cfg.backend.type === "demo" ? "DEMO_" + Math.random().toString(36).slice(2, 8) : null);
    if (!S.pid) return fail("This page must be opened from Prolific (missing participant ID).");
    $("consent-text").innerHTML = S.cfg.consent_html;
    $("instr-text").innerHTML = S.cfg.instructions_html;
    $("consent-box").onchange = (e) => ($("consent-btn").disabled = !e.target.checked);
    $("consent-btn").onclick = () => show("screen-instr");
    $("instr-btn").onclick = () => { S.tInstrDone = now(); S.pos >= S.seq.length ? finish() : renderItem(); };
    setupCopyBlocking();
    let claim;
    try {
      const t0 = Date.now();
      for (;;) {
        claim = await api("claim");
        if (claim.ok) break;
        const full = claim.waiting || /no study slots/i.test(claim.error || "");
        if (!full) return fail(claim.error || "Could not start the study. Please return it on Prolific.");
        if (Date.now() - t0 > S.cfg.waiting_minutes * 60000)
          return fail("All places in this study are still in use. Please return the study on Prolific; sorry for the wait.");
        $("loading-msg").textContent = "All places in this study are in use right now, usually because someone has just left. " +
          `This page checks again every ${S.cfg.waiting_poll_seconds} seconds and will continue by itself. ` +
          `Please keep it open (at most ${S.cfg.waiting_minutes} minutes).`;
        await new Promise((res) => setTimeout(res, S.cfg.waiting_poll_seconds * 1000));
      }
    } catch (e) { return fail("Could not reach the study server. Please check your connection and reload."); }
    S.batch = claim.batch_id; S.seq = claim.items; S.pos = claim.resume_pos || 0;
    if (!S.batch || !Array.isArray(S.seq) || !S.seq.length) return fail("Could not load your texts. Please reload the page.");
    S.heartbeat = setInterval(() => { api("ping").catch(() => {}); }, S.cfg.heartbeat_seconds * 1000);
    show("screen-consent");
  }

  // ------------------------------------------------------------------ items
  function renderItem() {
    const id = String(S.seq[S.pos]);
    $("progress").textContent = `Text ${S.pos + 1} of ${S.seq.length}`;
    $("item-text").innerHTML = S.items[id] || "<p>(missing text)</p>";
    const pane = $("item-text").parentElement;
    pane.scrollTop = 0;
    cur = { item_id: id, pos: S.pos, t_start: now(), t_first: null, focus: [], blocked: [], history: [],
            span_seen_ms: null, max_scroll: 0, app_version: APP_VERSION };
    const span = document.getElementById("span");
    if (span && "IntersectionObserver" in window) {
      const io = new IntersectionObserver((es) => {
        if (es[0].isIntersecting && cur && cur.span_seen_ms === null) { cur.span_seen_ms = now() - cur.t_start; io.disconnect(); }
      }, { root: pane });
      io.observe(span);
    }
    pane.onscroll = () => {
      if (cur) cur.max_scroll = Math.max(cur.max_scroll, (pane.scrollTop + pane.clientHeight) / pane.scrollHeight);
    };
    S.answers = {};
    $("next-btn").textContent = "Next";
    $("tree").innerHTML = "";
    drawTree();
    show("screen-item");
    requestAnimationFrame(() => { if (cur && pane.scrollHeight <= pane.clientHeight + 4) cur.max_scroll = 1; });
  }

  function answer(qid, value) {
    if (cur.t_first === null) cur.t_first = now() - cur.t_start;
    cur.history.push([qid, value, now()]);
    S.answers[qid] = value;
    S.answers = Flow.prune(S.cfg.task, S.answers);
    drawTree();
  }

  function questionNode(q) {
    const d = document.createElement("div");
    d.className = "q"; d.dataset.qid = q.id;
    d.innerHTML = (q.title ? `<h3>${q.title}</h3>` : "") + (q.text ? `<div class="qtext">${q.text}</div>` : "");
    const box = document.createElement("div");
    box.className = "opts" + (q.type === "likert" ? " likert" : "");
    if (q.type === "single" || q.type === "likert") {
      q.options.forEach((o) => {
        const l = document.createElement("label");
        l.innerHTML = `<input type="radio" name="${q.id}" value="${o.value}"> <span>${o.label}</span>`;
        l.querySelector("input").onchange = () => answer(q.id, o.value);
        box.appendChild(l);
      });
    } else if (q.type === "multi") {
      q.options.forEach((o) => {
        const l = document.createElement("label");
        l.innerHTML = `<input type="checkbox" value="${o.value}"> <span>${o.label}</span>`;
        l.querySelector("input").onchange = () =>
          answer(q.id, [...box.querySelectorAll("input:checked")].map((x) => x.value));
        box.appendChild(l);
      });
    } else if (q.type === "text") {
      const t = document.createElement("textarea");
      if (q.placeholder) t.placeholder = q.placeholder;
      t.oninput = () => answer(q.id, t.value);
      box.appendChild(t);
    }
    d.appendChild(box);
    return d;
  }

  // Reconcile the visible questions with the current path instead of re-rendering,
  // so a text box keeps its focus and caret while the participant types.
  function drawTree() {
    const task = S.cfg.task;
    const res = Flow.walk(task, S.answers);
    const tree = $("tree");
    const byId = {}; task.questions.forEach((q) => (byId[q.id] = q));
    const existing = {}; [...tree.children].forEach((n) => (existing[n.dataset.qid] = n));
    Object.keys(existing).forEach((k) => { if (!res.path.includes(k)) existing[k].remove(); });
    let prev = null;
    res.path.forEach((qid) => {
      let n = existing[qid] && existing[qid].isConnected ? existing[qid] : null;
      if (!n) n = questionNode(byId[qid]);
      // only move a node when it is out of place (moving would blur a focused text box)
      const wanted = prev ? prev.nextSibling : tree.firstChild;
      if (wanted !== n) { if (prev) prev.after(n); else tree.prepend(n); }
      prev = n;
    });
    const r = $("result");
    if (res.complete && res.outcome && task.show_outcome) {
      r.textContent = `Result: ${(task.outcomes || {})[res.outcome] || res.outcome}. You can change any answer above, or click Next.`;
      r.classList.remove("hidden");
    } else r.classList.add("hidden");
    $("next-btn").disabled = !res.complete;
    $("next-btn").onclick = () => submitItem(res.outcome);
  }

  async function submitItem(outcome) {
    $("next-btn").disabled = true;
    $("next-btn").textContent = "Saving…";
    const row = { ...cur, t_end: now(), outcome, answers: { ...S.answers }, n_items: S.seq.length,
                  study: S.study, session: S.session };
    row.time_on_item = row.t_end - row.t_start;
    cur = null;
    S.rows.push(row);
    try { await postWithRetry("item", { row }); }
    catch (e) { return fail(e.message + " Please reload the page; your progress is saved."); }
    S.pos++;
    if (S.pos < S.seq.length) return renderItem();
    finish();
  }

  function csvCell(v) { const s = String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; }

  async function finish() {
    if (!S.batch) return fail("Could not load your texts. Please reload the page.");  // never redirect without a batch
    if (S.heartbeat) { clearInterval(S.heartbeat); S.heartbeat = null; }
    show("screen-done");
    $("progress").textContent = "";
    const summary = { t_total: now(), t_instr: S.tInstrDone, session_events: S.sessionEvents,
                      user_agent: navigator.userAgent, screen: [screen.width, screen.height] };
    try { await postWithRetry("complete", { summary }); }
    catch (e) { return fail(e.message + " Please contact the researcher via Prolific."); }
    if (S.cfg.backend.type === "demo") {
      const head = ["ts", "pid", "batch_id", "item_id", "pos", "label", "time_on_item", "trace", "row_json"];
      const lines = [head.join(",")].concat(S.rows.map((r) => [new Date(S.t0 + r.t_end).toISOString(), S.pid, S.batch,
        r.item_id, r.pos, r.outcome ?? "", r.time_on_item, JSON.stringify(r.answers), JSON.stringify(r)].map(csvCell).join(",")));
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([lines.join("\n") + "\n"], { type: "text/csv" }));
      a.download = `responses_${S.pid}.csv`; a.textContent = "Download your responses (CSV)";
      const p = document.createElement("p");
      p.textContent = "Demo mode: nothing was sent anywhere. The CSV has the same columns as a real export, so you can try `pannot ingest` on it.";
      $("done-msg").replaceChildren(p, a);
      return;
    }
    const url = S.cfg.completion_url;
    if (!url) { $("done-msg").innerHTML = "<p>Your responses were saved. You can close this page.</p>"; return; }
    $("done-msg").innerHTML = `<p>Your responses were saved. Redirecting to Prolific… If nothing happens, <a href="${url}">click here</a>.</p>`;
    setTimeout(() => (location.href = url), 1500);
  }

  start();
})();
