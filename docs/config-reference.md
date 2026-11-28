# Config reference (`study.yaml`)

Every field, with its default. Relative paths are resolved against the folder that contains `study.yaml`. JSON (`study.json`) works too.

YAML note: unquoted `yes`, `no`, `on`, `off` stay **text** in this tool. Only `true`/`false` are booleans.

## `study`

| field | default | meaning |
|---|---|---|
| `title` | `"Annotation study"` | page title and header |
| `completion_url` | `""` | where participants go after the last text (Prolific's *redirect* URL). Empty: a thank-you message instead |
| `backend.type` | `demo` | `apps_script`, `server` or `demo` (no backend; answers offered as CSV) |
| `backend.url` | `""` | Apps Script `/exec` URL (required for `apps_script`). For `server`, empty means `/api` on the same host |
| `consent_html` | short default | HTML shown first, with an "I agree" checkbox |
| `instructions_html` | short default | HTML shown before the first text |
| `estimated_minutes` | `20` | for your records; not shown unless you put it in the HTML |
| `block_copy` | `true` | block selecting and copying the text (attempts are logged) |
| `idle_minutes` | `15` | without a Prolific token (or for ids Prolific doesn't know): a claimed batch with no heartbeat or answer for this long goes to the next participant |
| `stale_minutes` | `90` | the same, for batches claimed by an older app version that sends no heartbeat (measured from the claim) |
| `prolific_study_id` | `""` | study id for the Prolific status check; empty means the `STUDY_ID` in the participant's link. The API token itself never goes in this file |
| `heartbeat_seconds` | `60` | how often an open page tells the backend the participant is still there (at most a third of `idle_minutes`) |
| `waiting_poll_seconds` | `30` | waiting room: when every batch is taken, how often the page tries again |
| `waiting_minutes` | `15` | ...and for how long before asking the participant to return the study |

## `items`

| field | default | meaning |
|---|---|---|
| `file` | `items.csv` | `.csv`, `.jsonl` or `.json` (list of objects) |
| `id_column` | `id` | unique id per text; ids starting with `attn` are reserved |
| `text_column` | `text` | the text |
| `format` | `text` | `text`: escaped, line breaks become paragraphs. `html`: inserted as is (trusted) |
| `highlight.start` / `.end` | `<hl>` / `</hl>` | markers around the passage to highlight. The first one is tracked for "scrolled into view" |

Other columns are kept in the private manifest for your analysis and never sent to the browser.

## `task`

| field | default | meaning |
|---|---|---|
| `start` | first question | id of the first question |
| `outcomes` | `{}` | `{key: display name}`. If given, every `outcome:` must be one of these keys |
| `show_outcome` | `true` | show "Result: …" when the flow ends |
| `questions` | required | list of questions (below) |

### Question

| field | applies to | default | meaning |
|---|---|---|---|
| `id` | all | required | unique id; also the column name in `votes.csv` |
| `type` | all | `single` | `single`, `likert`, `multi`, `text` |
| `title` | all | `""` | bold heading (HTML allowed) |
| `text` | all | `""` | question text (HTML allowed) |
| `options` | single, likert, multi | required | list of `{value, label, next?, outcome?}`; `label` defaults to `value` |
| `next` | all | — | question to show after this one |
| `outcome` | all | — | end the flow with this outcome |
| `required` | likert, multi, text | `true` | `false`: may be left blank (`single` is always required) |
| `min_selected` | multi | `1` | minimum ticked boxes |
| `min_chars` | text | `1` | minimum characters |
| `placeholder` | text | — | placeholder text |

Routing: on `single` questions an option's own `next`/`outcome` wins; otherwise the question-level `next`/`outcome` applies. With neither, the flow ends without an outcome. Cycles are rejected.

## `design`

| field | default | meaning |
|---|---|---|
| `votes_per_item` | `5` | independent annotators per text |
| `items_per_session` | `10` | analysis texts per participant |
| `seed` | `1` | random seed for the design |
| `include` | all | optional list of item ids to annotate (others are ignored) |
| `gold.per_session` | `0` | gold items per session (distinct within a session, used equally often) |
| `gold.items` | `[]` | `{id, expected_outcome}` or `{id, expected_answers: {qid: value}}`; ids must be in the items file |
| `gold.hidden_instruction_html` | `""` | off-screen text inserted before the highlight of every gold item |
| `gold.hidden_answers` | `{}` | answers that mean "followed the hidden text" (reported as `gold_hidden_followed`) |
| `attention` | `[]` | list of attention items (below) |

### Attention item

| field | default | meaning |
|---|---|---|
| `id` | required | unique id, conventionally `attn1`, `attn2`, … |
| `html` | required | the text; put the instruction in `<mark id="span">…</mark>` |
| `expected_answers` | required | `{qid: value}` (a list for `multi`) |
| `position` | `any` | `first_half`, `second_half` or `any` |
| `hidden_instruction.html` | — | off-screen text |
| `hidden_instruction.answers` | — | answers that mean "followed the hidden text"; must differ from `expected_answers` |

## `flags`

| field | default | meaning |
|---|---|---|
| `threshold` | `2` | a worker is `flagged` when this many flags fire (gold and attention count once per wrong item) |
| `ignore_pid_prefixes` | `[TEST_, DEV_]` | ids ignored by ingest and cleared by reset-tests |
| `rejected_pids_file` | `null` | text file, one participant id per line (`#` comments allowed) |
| `rules.gold_wrong` | `true` | count wrong gold items |
| `rules.attention_wrong` | `true` | count failed attention items |
| `rules.fast` | `{min_seconds_per_item: 12}` | total time on texts below this × number of texts |
| `rules.skim` | `{max_words_per_second: 25, min_words: 500}` | median reading speed (visible time only) on texts of at least `min_words` |
| `rules.long_absence` | `{minutes: 5}` | one stretch away from the page longer than this |
| `rules.tab_burst` | `{away_seconds: 20, burst_seconds: 10}` | ≥2 answers right after a long stretch away |
| `rules.highlight_unseen` | `true` | Next clicked without the highlighted passage ever on screen |

Set any rule to `false` to disable it.
