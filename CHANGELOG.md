# Changelog

## 0.2.0

* **Fix:** the consent page is only shown once a batch has arrived, and the app never redirects to Prolific without one. Before, a participant who clicked through quickly could reach the completion page with an empty session.
* Freeing abandoned batches:
  * optional Prolific API check: a batch is freed as soon as its holder has returned, timed out, been rejected or screened out, while active participants keep theirs however long they pause;
  * otherwise, a heartbeat from the open page plus `idle_minutes` (default 15) instead of a fixed 90 minutes after the claim;
  * never-claimed batches are always handed out first.
* Waiting room: when every batch is busy, the page retries and continues by itself instead of showing an error.
* New `ping` API action, `last_seen` batch column (existing sheets and databases are upgraded), `checkProlific` editor helper, and new `study` settings: `idle_minutes`, `prolific_study_id`, `heartbeat_seconds`, `waiting_poll_seconds`, `waiting_minutes`.

## 0.1.0

First release:
* configurable question flows (single, likert, multi, text, optional questions, branching outcomes);
* balanced batching with gold and attention items and hidden-instruction checks;
* Google Apps Script and self-hosted SQLite backends with the same API;
* ingest with configurable quality flags (no automatic exclusion), votes, distributions, answer counts;
* exchangeability comparison against earlier labels;
* simulator, CLI, docs, CI and a GitHub Pages demo.
