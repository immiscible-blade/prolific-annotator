// Offline test of a generated Code.gs against a mock SpreadsheetApp / LockService / ContentService.
// Usage: node tests/js/test_code_gs.js build/backend/Code.gs
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
    };
  }
}
const SS = { sheets: [new Sheet("Sheet1")], getSheetByName(n) { return this.sheets.find((s) => s.name === n) || null; },
  insertSheet(n) { const s = new Sheet(n); this.sheets.push(s); return s; }, getSheets() { return this.sheets; },
  deleteSheet(s) { this.sheets = this.sheets.filter((x) => x !== s); } };
global.SpreadsheetApp = { getActive: () => SS };
global.LockService = { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) };
global.ContentService = { MimeType: { JSON: 1 }, createTextOutput: (t) => ({ setMimeType() { return JSON.parse(t); } }) };
eval(fs.readFileSync(process.argv[2], "utf8") + ";Object.assign(global,{doGet,doPost,setup,resetTestBatches,reloadBatches,BATCHES,STALE_MIN});");

const NB = BATCHES.length, NI = BATCHES[0].items.length;
const [first, second, third] = BATCHES.map((b) => b.batch_id);
const get = (pid) => doGet({ parameter: { action: "claim", pid } });
const post = (o) => doPost({ postData: { contents: JSON.stringify(o) } });
let ok = true;
const check = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); ok = ok && c; };
const B = () => SS.getSheetByName("batches");

const a = get("P1");
check(a.ok && a.batch_id === first && a.items.length === NI, "first claim auto-creates tabs and returns the first batch");
check(["batches", "responses", "sessions"].every((n) => SS.getSheetByName(n)) && !SS.getSheetByName("Sheet1"), "tabs created, empty Sheet1 removed");
check(SS.getSheetByName("responses").fmt["F:F"] === "@" && B().fmt["D:D"] === "@", "id and label columns forced to text");
check(B().getLastRow() === NB + 1, "all batches written");
check(get("P2").batch_id === second, "second participant gets the next batch");
post({ action: "item", pid: "P1", batch_id: first, row: { item_id: "x", pos: 0, outcome: "false", time_on_item: 5, answers: { q1: "no" } } });
post({ action: "item", pid: "P1", batch_id: first, row: { item_id: "y", pos: 1, outcome: null, time_on_item: 5, answers: {} } });
check(SS.getSheetByName("responses").d[1][5] === "false" && SS.getSheetByName("responses").d[2][5] === "", "outcome stored as text, null as empty");
const r = get("P1");
check(r.batch_id === first && r.resume_pos === 2, "reload resumes the same batch at the third item");
post({ action: "complete", pid: "P1", batch_id: first, summary: {} });
check(!get("P1").ok, "a finished participant cannot claim again");
check(B().d[1][2] === "done", "batch marked done");
B().d[2][4] = new Date(Date.now() - (STALE_MIN + 30) * 60000).toISOString();
check(get("P3").batch_id === second, "a stale batch is handed to a new participant");
check(setup() === `batches: ${NB} rows` && B().d[1][2] === "done", "re-running setup() keeps data");
get("TEST_x"); post({ action: "item", pid: "TEST_x", batch_id: third, row: { item_id: "1", pos: 0, time_on_item: 1, answers: {} } });
const before = SS.getSheetByName("responses").getLastRow(); resetTestBatches();
check(B().d[3][3] === "" && B().d[1][2] === "done", "resetTestBatches frees test batches only");
check(SS.getSheetByName("responses").getLastRow() === before - 1, "resetTestBatches deletes only test rows");
let threw = false; try { reloadBatches(); } catch (e) { threw = true; }
check(threw, "reloadBatches refuses once a real participant has a batch");
B().d.forEach((row, i) => { if (i > 0) { row[2] = ""; row[3] = ""; row[4] = ""; row[5] = ""; } });
check(reloadBatches() === `batches reloaded: ${NB} rows`, "reloadBatches works before anyone real has started");
check(!doGet({ parameter: { action: "nope", pid: "Z" } }).ok && !post({ action: "zzz" }).ok, "bad requests are refused");
process.exit(ok ? 0 : 1);
