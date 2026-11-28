// Offline test of a generated Code.gs against mocks of SpreadsheetApp, LockService,
// ContentService, PropertiesService, CacheService and UrlFetchApp.
// Usage: node tests/js/test_code_gs.js build/backend/Code.gs   (needs >= 6 batches)
const fs = require("fs");
class Sheet {
  constructor(n) { this.name = n; this.d = []; this.fmt = {}; }
  appendRow(r) { this.d.push([...r]); } setFrozenRows() {} getLastRow() { return this.d.length; }
  deleteRow(i) { this.d.splice(i - 1, 1); } deleteRows(i, n) { this.d.splice(i - 1, n); }
  getDataRange() { return { getValues: () => this.d.map((r) => [...r]) }; }
  getRange(r, c, nr = 1, nc = 1) {
    const s = this;
    if (typeof r === "string") return { setNumberFormat(f) { s.fmt[r] = f; } };
    return {
      setValues(v) { for (let i = 0; i < nr; i++) { s.d[r - 1 + i] = s.d[r - 1 + i] || []; for (let j = 0; j < nc; j++) s.d[r - 1 + i][c - 1 + j] = v[i][j]; } },
      setValue(v) { s.d[r - 1] = s.d[r - 1] || []; s.d[r - 1][c - 1] = v; },
      getValue() { return (s.d[r - 1] || [])[c - 1]; },
    };
  }
}
const SS = { sheets: [new Sheet("Sheet1")], getSheetByName(n) { return this.sheets.find((s) => s.name === n) || null; },
  insertSheet(n) { const s = new Sheet(n); this.sheets.push(s); return s; }, getSheets() { return this.sheets; },
  deleteSheet(s) { this.sheets = this.sheets.filter((x) => x !== s); } };
global.SpreadsheetApp = { getActive: () => SS };
global.LockService = { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) };
global.ContentService = { MimeType: { JSON: 1 }, createTextOutput: (t) => ({ setMimeType() { return JSON.parse(t); } }) };
const PROPS = {}; global.PropertiesService = { getScriptProperties: () => ({ getProperty: (k) => PROPS[k] || null }) };
const CACHE = {}; global.CacheService = { getScriptCache: () => ({ get: (k) => CACHE[k] || null, put: (k, v) => { CACHE[k] = v; }, remove: (k) => { delete CACHE[k]; } }) };
let API = { code: 200, results: [] }, FETCHES = 0, LAST = null;
global.UrlFetchApp = { fetch: (url, opt) => { FETCHES++; LAST = { url, opt }; return { getResponseCode: () => API.code, getContentText: () => JSON.stringify({ results: API.results }) }; } };
eval(fs.readFileSync(process.argv[2], "utf8") +
  ";Object.assign(global,{doGet,doPost,setup,resetTestBatches,reloadBatches,checkProlific,BATCHES,STALE_MIN,IDLE_MIN});");

const NB = BATCHES.length, NI = BATCHES[0].items.length;
const [first, second] = BATCHES.map((b) => b.batch_id);
const get = (pid, study) => doGet({ parameter: { action: "claim", pid, study: study || "" } });
const post = (o) => doPost({ postData: { contents: JSON.stringify(o) } });
const ago = (m) => new Date(Date.now() - m * 60000).toISOString();
let ok = true;
const check = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); ok = ok && c; };
const B = () => SS.getSheetByName("batches");
const row = (pid) => B().d.find((r) => r[3] === pid);
const fillAll = (prefix) => B().d.forEach((r, i) => { if (i > 0 && r[2] !== "done") { r[2] = "claimed"; r[3] = prefix + i; r[4] = new Date().toISOString(); r[6] = new Date().toISOString(); } });
const clearAll = () => B().d.forEach((r, i) => { if (i > 0) { r[2] = ""; r[3] = ""; r[4] = ""; r[5] = ""; r[6] = ""; } });

// --- basics
const a = get("P1");
check(a.ok && a.batch_id === first && a.items.length === NI, "first claim auto-creates tabs and returns the first batch");
check(["batches", "responses", "sessions"].every((n) => SS.getSheetByName(n)) && !SS.getSheetByName("Sheet1"), "tabs created, empty Sheet1 removed");
check(B().d[0][6] === "last_seen" && !!row("P1")[6], "last_seen column exists and is set on claim");
check(SS.getSheetByName("responses").fmt["F:F"] === "@" && B().fmt["D:D"] === "@", "id and label columns forced to text");
check(B().getLastRow() === NB + 1, "all batches written");
check(get("P2").batch_id === second, "second participant gets the next batch");
post({ action: "item", pid: "P1", batch_id: first, row: { item_id: "x", pos: 0, outcome: "false", time_on_item: 5, answers: { q1: "no" } } });
post({ action: "item", pid: "P1", batch_id: first, row: { item_id: "y", pos: 1, outcome: null, time_on_item: 5, answers: {} } });
check(SS.getSheetByName("responses").d[1][5] === "false" && SS.getSheetByName("responses").d[2][5] === "", "outcome stored as text, null as empty");
check(get("P1").resume_pos === 2, "reload resumes at the third item");
post({ action: "complete", pid: "P1", batch_id: first, summary: {} });
check(!get("P1").ok && B().d[1][2] === "done", "a finished participant cannot claim again");
check(!doGet({ parameter: { action: "nope", pid: "Z" } }).ok && !post({ action: "zzz" }).ok, "bad requests are refused");

// --- release rules without a Prolific token
clearAll(); B().d[1][2] = "done"; B().d[1][3] = "P1";
B().d[2][2] = "claimed"; B().d[2][3] = "OLD"; B().d[2][4] = ago(500); B().d[2][6] = ago(500);
check(get("N0").batch_id === BATCHES[2].batch_id, "never-claimed batches go before anyone's is reclaimed");
fillAll("F");
B().d[2][6] = ago(IDLE_MIN + 1);
check(get("N1").batch_id === second, "no free batch: one silent for IDLE_MIN is given away");
check(SS.getSheetByName("sessions").d.some((r) => r[3] === "released_stale"), "logged as released_stale");
let w = get("N2");
check(!w.ok && w.waiting === true, "everything busy -> waiting:true");
const r3 = B().d[3]; r3[6] = ago(IDLE_MIN + 2);
check(post({ action: "ping", pid: r3[3], batch_id: r3[0] }).held === true && !get("N3").ok, "a heartbeat keeps the batch");
r3[6] = ago(IDLE_MIN + 2);
post({ action: "item", pid: r3[3], batch_id: r3[0], row: { item_id: "z", pos: 0, time_on_item: 1, answers: {} } });
check(!get("N4").ok, "an answer also counts as a sign of life");
const r4 = B().d[4]; const old4 = r4[3]; r4[6] = ""; r4[4] = ago(IDLE_MIN + 5);
check(!get("N5").ok, "rows without last_seen are kept until STALE_MIN");
r4[4] = ago(STALE_MIN + 5);
check(get("N5").batch_id === r4[0] && post({ action: "ping", pid: old4, batch_id: r4[0] }).held === false,
      "...then given away, and the old holder's ping reports it");

// --- Prolific API
fillAll("Z");
B().d[3][3] = "ACT"; B().d[3][6] = ago(IDLE_MIN + 60);   // long pause but ACTIVE
B().d[4][3] = "RET";                                    // returned, heartbeat fresh
B().d[5][3] = "PREVIEW"; B().d[5][6] = ago(IDLE_MIN + 1);
PROPS.PROLIFIC_TOKEN = "tok"; PROPS.PROLIFIC_STUDY_ID = "S1";
API = { code: 200, results: [{ participant_id: "ACT", status: "ACTIVE" }, { participant_id: "RET", status: "RETURNED" }] };
FETCHES = 0;
check(get("Q1").batch_id === B().d[4][0], "Prolific RETURNED -> freed at once despite a fresh heartbeat");
check(LAST.url === "https://api.prolific.com/api/v1/studies/S1/submissions/" && LAST.opt.headers.Authorization === "Token tok",
      "documented endpoint and token header");
check(SS.getSheetByName("sessions").d.some((r) => r[3] === "released_prolific_returned"), "logged as released_prolific_returned");
check(get("Q2").batch_id === B().d[5][0], "unknown id (preview/test) falls back to the heartbeat rule");
check(row("ACT") && !get("Q3").ok, "an ACTIVE participant keeps the batch however long the pause");
check(FETCHES === 1, "statuses cached between claims");
API.results.push({ participant_id: "ACT", status: "TIMED-OUT" }); for (const k in CACHE) delete CACHE[k];
check(get("Q4").batch_id === B().d[3][0], "freed once Prolific times them out");
for (const k in CACHE) delete CACHE[k]; API = { code: 500, results: [] };
B().d[6][6] = ago(IDLE_MIN + 1);
check(get("Q5").batch_id === B().d[6][0], "API error -> heartbeat rule still works");
delete PROPS.PROLIFIC_STUDY_ID; for (const k in CACHE) delete CACHE[k];
API = { code: 200, results: [{ participant_id: "x", status: "ACTIVE" }] };
fillAll("Y"); get("Q6", "FROM_LINK");
check(LAST.url.endsWith("/studies/FROM_LINK/submissions/"), "study id falls back to the participant's STUDY_ID");
PROPS.PROLIFIC_STUDY_ID = "S1";
check(/^OK: 1 submissions/.test(checkProlific()), "checkProlific reports what it can see");
delete PROPS.PROLIFIC_TOKEN;
check(/check PROLIFIC_TOKEN/.test(checkProlific()), "checkProlific explains a missing token");

// --- editor helpers
clearAll(); B().d[1][2] = "done"; B().d[1][3] = "REAL";
get("TEST_x");
const before = SS.getSheetByName("responses").getLastRow();
post({ action: "item", pid: "TEST_x", batch_id: row("TEST_x")[0], row: { item_id: "1", pos: 0, time_on_item: 1, answers: {} } });
resetTestBatches();
check(!row("TEST_x") && B().d[1][3] === "REAL", "resetTestBatches frees test batches only");
check(SS.getSheetByName("responses").getLastRow() === before, "resetTestBatches deletes only test rows");
let threw = false; try { reloadBatches(); } catch (e) { threw = true; }
check(threw, "reloadBatches refuses once a real participant has a batch");
clearAll();
check(reloadBatches() === `batches reloaded: ${NB} rows` && setup() === `batches: ${NB} rows`, "reloadBatches / setup before anyone real started");
process.exit(ok ? 0 : 1);
