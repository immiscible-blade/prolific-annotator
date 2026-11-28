# Deployment

A study has two parts: the **site** (`build/site/`, static files) and the **backend** (stores answers). Build first:

```bash
pannot build study.yaml --out build
```

Never deploy `build/private/`: it contains the gold answers and item roles.

## Backend option A: Google Apps Script (free, no server)

1. Create a **blank** Google Sheet. If your institution approves Google Workspace for research data, use that account.
2. **Extensions → Apps Script**. Delete the placeholder code, paste the whole of `build/backend/Code.gs`, and save.
3. In the function dropdown pick **`setup`** and click **Run**. Approve the permissions; if Google says the app isn't verified, choose *Advanced → Go to project*. You get three tabs, `batches`, `responses` and `sessions`, and one row per session in `batches`.
4. **Deploy → New deployment →** gear icon **→ Web app**. Set *Execute as*: **Me** and *Who has access*: **Anyone**, then Deploy.
5. **Deploy → Manage deployments** shows the **Web app URL** ending in `/exec`. Put it in `study.yaml`:

   ```yaml
   study:
     backend: {type: apps_script, url: "https://script.google.com/macros/s/…/exec"}
   ```

6. Rebuild (`pannot build …`) and deploy the site.

**Recommended: let Prolific say who has left.** Without it, a batch whose participant returned the study is only freed after `idle_minutes` with no heartbeat (default 15). With it, the batch is freed as soon as Prolific reports the participant as *returned*, *timed out*, *rejected* or *screened out*, and participants who are still active keep their batch however long they pause.

1. On Prolific, open [API Tokens](https://app.prolific.com/researcher/tokens/), click *Create API Token* and copy it. The token can act on your whole researcher account, so keep it out of `study.yaml`, git and chat.
2. In the Apps Script editor, go to *Project Settings* (gear icon) → *Script Properties* → *Add script property*:
   * `PROLIFIC_TOKEN` = the token;
   * `PROLIFIC_STUDY_ID` = your study id (the `STUDY_ID` in the study link, or set `study.prolific_study_id`).
3. Run **`checkProlific`** from the function dropdown. Google asks for permission to contact `api.prolific.com`. The result appears in the **Execution log** panel below the code, e.g. `OK: 12 submissions {"ACTIVE":3,"AWAITING REVIEW":9}`. If the panel is hidden, click *Execution log* in the toolbar.

Only people with edit access to the script can see Script Properties. Participants never see the token.

Helpers you run from the Apps Script editor (no redeploy needed):

* `resetTestBatches`: frees batches claimed by test ids and deletes their rows.
* `reloadBatches`: replaces the batch list after you paste a regenerated `Code.gs`. It refuses once a real participant has a batch.
* `checkProlific`: tests the Prolific token and study id.

**Changing the backend code** (not just running a helper) needs *Deploy → Manage deployments → Edit → New version*. The `/exec` URL stays the same.

Freeing one batch by hand (e.g. before rejecting its participant): in `batches`, clear that row's `status`, `pid`, `claimed_at` and `completed_at` cells.

Export: click the `responses` tab, then File → Download → Comma-separated values.

## Backend option B: `pannot serve` (self-hosted)

```bash
pannot serve build --host 0.0.0.0 --port 8765 --db /srv/study/responses.sqlite
```

This one process serves both the site and the API at `/api`, so set `backend: {type: server}` (leave `url` empty) and you don't need a separate static host. To free returned batches through Prolific's API, start it with the token in the environment: `PROLIFIC_TOKEN=… pannot serve build …`. On startup it prints whether the Prolific check is on. Prolific needs **HTTPS**, so put it behind a reverse proxy, e.g. [Caddy](https://caddyserver.com):

```
study.example.org {
    reverse_proxy 127.0.0.1:8765
}
```

Administration:

```bash
pannot export build --db /srv/study/responses.sqlite --out exports   # CSVs like the Sheet tabs
pannot reset-tests build --db …                                       # undo test sessions
pannot release build s-004 --db …                                     # reopen one batch
```

The database is a single SQLite file; back it up while the study runs.

## Hosting the site

The site is plain static files; any host works. Re-upload after every rebuild.

### Netlify

```bash
npx netlify-cli login
cd build/site
npx netlify-cli deploy --prod --dir . --site-name my-study-x7k2
```

* Pick a neutral site name. Participants see the URL, so a name like "fallacy-study" can prime them.
* **Check that visitors can open it.** Some Netlify accounts protect every deploy with a team login by default, which returns 401 to participants. In the site's access-control (visitor protection) settings, limit protection to non-production deploys. Then check that the production URL opens in a private browser window.
* The `_headers` file in the site tells search engines not to index it and stops browsers caching the data files.

### GitHub Pages

Push `build/site/` to a branch or use an Actions workflow (see `.github/workflows/pages.yml` in this repo, which publishes the demo). Note that GitHub Pages ignores `_headers`. The site's `<meta name="robots">` tag still asks search engines not to index it.

## The Prolific link

Study URL:

```
https://<your-site>/index.html?PROLIFIC_PID={{%PROLIFIC_PID%}}&STUDY_ID={{%STUDY_ID%}}&SESSION_ID={{%SESSION_ID%}}
```

Choosing *URL parameters* in Prolific adds these for you.

Test before publishing:
1. Open the link with `?PROLIFIC_PID=TEST_you` and finish a session.
2. Check that the rows arrived.
3. Run `resetTestBatches` or `pannot reset-tests`.
