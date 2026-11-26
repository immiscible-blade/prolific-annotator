/* Question flow — the browser twin of prolific_annotator/routing.py.
 * Keep the two identical: tests/test_flow_parity.py runs both on every answer
 * combination of the example tasks. Works in the browser (window.Flow) and in
 * Node (module.exports) so it can be tested.
 */
(function (root) {
  "use strict";

  function isAnswered(q, a) {
    if (q.required === false && (a === undefined || a === null || a === "" || (Array.isArray(a) && a.length === 0))) return true;
    const vals = (q.options || []).map((o) => o.value);
    switch (q.type) {
      case "single":
      case "likert":
        return typeof a === "string" && vals.includes(a);
      case "multi":
        return Array.isArray(a) && a.length >= (q.min_selected || 1) && a.every((x) => vals.includes(x));
      case "text":
        return typeof a === "string" && a.trim().length >= (q.min_chars || 1);
      default:
        return false;
    }
  }

  function walk(task, answers) {
    const byId = {};
    task.questions.forEach((q) => (byId[q.id] = q));
    let node = task.start;
    const path = [];
    for (let i = 0; i <= task.questions.length; i++) {
      const q = byId[node];
      path.push(node);
      const a = answers[node];
      if (!isAnswered(q, a)) return { path, outcome: null, complete: false };
      let target = q;
      if (q.type === "single") {
        const opt = q.options.find((o) => o.value === a);
        if ("next" in opt || "outcome" in opt) target = opt;
      }
      if ("next" in target) { node = target.next; continue; }
      return { path, outcome: "outcome" in target ? target.outcome : null, complete: true };
    }
    throw new Error("question flow did not terminate");
  }

  function prune(task, answers) {
    const keep = new Set(walk(task, answers).path);
    const out = {};
    Object.keys(answers).forEach((k) => { if (keep.has(k)) out[k] = answers[k]; });
    return out;
  }

  const Flow = { isAnswered, walk, prune };
  if (typeof module !== "undefined" && module.exports) module.exports = Flow;
  else root.Flow = Flow;
})(this);
