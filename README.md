# Indeed auto-apply

Applies to jobs in your Indeed "Jobs for you" feed when the application is a simple click-through
(Apply now → Continue/Next → **Submit your application**). Everything else is skipped or paused, and
every job's outcome is saved to `data/applications.csv`.

## What it does and does not do

| Situation | Result | Recorded as |
|---|---|---|
| Apply → Continue/Next → Submit, confirmation seen | applies | `submitted` |
| Apply leads to the company's own site | closes that tab, moves on | `skipped` (`external_company_site`) |
| Application asks questions / needs info | moves on, nothing submitted | `skipped` (`requires_questions`) |
| Already applied / no Apply button | moves on | `skipped` |
| **CAPTCHA** | **pauses the whole run**, leaves the tab open and brings it forward | `needs_manual` (`captcha`) |
| Logged out | pauses | `needs_manual` (`login_required`) |
| Unexpected page, a step that won't advance, submit with no confirmation | pauses or moves on (see below) | `needs_manual` + reason |

- A job is only `submitted` when the confirmation page is actually seen. Submit is clicked **once**, never retried.
- It never tries to solve or bypass a CAPTCHA. Solve it in the browser window, press **Resume**.
- It never fills in forms, never touches the "job alert" toggle, and never answers questions.
- Before applying it clicks the job card and checks the details pane shows *that* job; if not, it does not apply.
- Any job that has a row in the CSV is never retried automatically. Delete its row to retry it.

## Setup (once)

Requires Python 3.11+ and Google Chrome.

```bash
cd Applyapp   # this repo’s folder on your machine
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt                       # Playwright Python client only (uses your Chrome via CDP)
python -m autoapply launch-browser                    # opens a dedicated Chrome window
```

Log in to Indeed **in that Chrome window**. The login lives in the `.browser-profile/` folder, so no password
is ever stored by this tool. (This profile is separate from your everyday Chrome, which is never touched.)

## Verify the selectors (do this first, it clicks nothing)

I could not open Indeed while building this, so the page selectors are educated guesses (see
"Known unknowns"). With the dedicated Chrome logged in:

```bash
python -m autoapply doctor
```

It prints which card/pane selectors matched on your real feed, the jobs it parsed, and what it would do
for the currently shown job. If something shows 0 matches, edit `config/portals/indeed.toml`. No code changes needed.

## Run

```bash
python -m autoapply run            # opens the dashboard at http://127.0.0.1:8765
python -m autoapply run --start    # ...and starts applying immediately
```

Dashboard: **Start / Pause / Resume / Stop**, current state, counters (attempted, submitted, skipped, need you),
and a table of every job with the reason. A paused run shows a banner with why and what to do.

- **Pause** takes effect at the next safe point (between steps, never mid-click).
- **Stop** ends the run; a tab left open for manual work stays open.
- After a CAPTCHA: the run **pauses** (it does not stop). You get a **desktop notification** and a dashboard popup; solve it in Chrome, then press **Resume** to continue the same run.

The first run is limited by `max_applications = 25` in `config/settings.toml`. Start small and watch it.

## Background use: what to expect

- It drives a **separate Chrome window** and only touches tabs it opened itself. Your other tabs/windows are left alone
  (the end-to-end test checks this).
- Keep that window **open and not minimized**: Chrome throttles minimized/hidden windows. Put it behind other windows or
  on another desktop. The launcher already disables Chrome's background-tab throttling.
- It does not use your mouse or keyboard, so you can keep working. New tabs opening may occasionally flash that window to the front.
- Remote debugging is bound to `127.0.0.1`. Don't browse arbitrary sites in the automation profile.

## Data file

`data/applications.csv` (Excel-friendly UTF-8) and `data/applications_latest.xlsx` (one row per job, latest status):
`timestamp, job_key, title, company, location, outcome, reason, portal, job_url, apply_url, url`.
Skipped jobs (questions, external site, no button, etc.) still get **company, platform, and links** so you can apply manually.
Append-only CSV; the **latest row per `job_key`** is the job's status. A row is written as `in_progress` *before*
each attempt, so a crash can never cause a second application. `JobStore` (in `autoapply/store.py`) is the
interface; to use a database, implement its 3 methods (`seen`, `record`, `counts`) and pass your store to `AutoApplyApp`.

## Architecture

```
autoapply/
  engine.py        portal-independent loop: next job → select → apply → run flow → record → clean up
  flows.py         ApplicationFlow; ClickThroughFlow = Continue/Next/Submit, verifies confirmation
  portals/base.py  PortalAdapter: the contract a job portal must implement
  portals/indeed.py Indeed specifics; every selector/text comes from config/portals/indeed.toml
  browser.py       Playwright over CDP: tabs, click-and-follow-new-tab (only file importing Playwright)
  launcher.py      starts the dedicated Chrome
  store.py         JobStore interface + CSV implementation
  control.py       run state, pause/resume/stop, counters (shared with the UI)
  config.py        typed settings from TOML (typos are rejected)
  app.py, ui/      wiring, worker thread, tiny local dashboard (stdlib http.server)
config/            settings.toml (general) and portals/indeed.toml (selectors, texts, URLs)
tests/             unit tests with scripted fakes + a real-Chromium end-to-end test against a mock portal
```

Why Playwright over CDP: it reuses your logged-in profile (no credentials), handles new tabs reliably, auto-waits for
elements, and works on a window you're not looking at.

### Extending

- **Another portal**: subclass `PortalAdapter` (`portals/base.py`), add `config/portals/<name>.toml`, register it in
  `portals/__init__.py`, set `portal = "<name>"`. The engine, flow runner, store, UI and tests of the core are reused.
- **Answering questions**: write an `ApplicationFlow` (`flows.py`) that handles `PageState.QUESTIONS` instead of skipping,
  and pass it to `Engine` in `app.py`. CAPTCHA pausing, tab handling and verification are inherited.
- **Database**: implement `JobStore`.

## Config reference

`config/settings.toml`: run limits and pacing, flow timeouts, `pause_on_unknown_page`, storage path, dashboard port.
`config/portals/indeed.toml`: `start_url` (change for non-India sites), `trusted_domains`, card/pane selectors,
button names, success/question/login/CAPTCHA markers (all case-insensitive regexes).

## Tests

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium   # only needed for the end-to-end test (not for normal runs)
pytest                       # 55 tests
```

Covered: click-through submit, new tabs, unexpected pages, question pages, company-site redirects, CAPTCHA
pause/resume/stop, unconfirmed submit, tab closed mid-flow, de-duplication across restarts, stale detail pane,
Indeed page classification, and dashboard security (Host/header checks).

## Known unknowns (not verifiable from the screenshots)

These are the parts that talk to the real Indeed and need your confirmation with `doctor` and a small first run:

1. **Card and details-pane selectors** (`card_selectors`, `pane_selectors`, `pane_title_selectors`) are from memory.
   If the pane-title selector is wrong, the tool applies to **nothing** (it refuses when it can't confirm the job), which is the safe failure.
2. **Whether Apply opens a new tab or navigates in place**. Both are handled.
3. **Application-step URLs and the confirmation page** (`/post-apply`, "your application has been submitted", `/questions`).
   If the confirmation marker is wrong, jobs will be recorded `needs_manual / submit_unverified` rather than `submitted`:
   check one by hand, then fix `success_url_markers` / `success_text`.
4. **What the CAPTCHA looks like.** Detection covers common challenge frames and wording; if one is missed, the
   step won't advance and the run still pauses on the unrecognized page.
5. Question detection is heuristic (required-but-empty fields + URL marker). A missed question page is harmless:
   Indeed itself will not let an incomplete step advance, and it is recorded as `step_did_not_advance`.

Note: automated applying may be restricted by Indeed's terms of service. Keep the limits modest and review your account's rules.
# Autoapply
