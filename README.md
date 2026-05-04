# Devin GitHub Issue Orchestrator

An event-driven system that turns `@devin` comments on GitHub issues into Devin
remediation sessions, uses the issue thread as the human-in-the-loop
collaboration surface, and exposes a dashboard so an engineering leader can
answer the question: *"How do I know Devin is actually working?"*

---

## Problem

Vulnerability and bug remediation work usually starts in a GitHub issue —
but the path from "issue filed" to "PR shipped" is mostly manual:

- Someone has to triage the issue.
- Someone has to assign an engineer.
- The engineer has to context-switch, read code, write a fix, test, and PR.
- Stakeholders have no real-time visibility into progress.

This is slow, easy to drop, and gives leadership no signal about where the
work actually is.

## Solution

This service watches a GitHub repo for `@devin` comments. When one shows up:

1. The orchestrator opens a Devin session for that issue.
2. The Devin session URL is posted back into the issue thread.
3. Subsequent `@devin` comments on the same issue are forwarded into the
   *same* Devin session — so the GitHub thread becomes the chat with Devin.
4. The orchestrator posts back meaningful state changes: PR opened, completed,
   failed, clarification needed.
5. A dashboard shows live metrics (mentions, active sessions, PRs, time-to-PR,
   etc.) and per-task interaction timelines.

---

## Architecture

```
GitHub issue comment "@devin"
        ▼
  POST /webhooks/github   ──┐
                            ├──► FastAPI orchestrator (this repo)
  POST /api/simulate-comment │       │
        ▲                   │       │  SQLite (tasks + interaction_events)
  React + Vite dashboard ───┘       │
                                    ▼
                             Devin API (create / message / poll)
                                    │
                                    ▼
                              PR opened on GitHub
                                    │
                                    ▼
                       Status comments back on the issue thread
```

Layers:

- **GitHub** is the *collaboration* layer. Humans talk to Devin where they
  already work — in the issue thread.
- **FastAPI** is the *orchestration / control* layer. It enforces idempotency
  (one active session per issue), de-duplicates noisy comments, and reconciles
  Devin state with our own.
- **Devin** is the *execution* layer. It can read the repo, change code, run
  tests, ask questions, and open PRs.
- **The React dashboard** is the *observability* layer. Metrics, task list,
  interaction timeline, and a simulate-comment panel for demos.

> **"If it's running in Docker, why do I need a tunnel?"**
> Docker just runs the containers locally with port forwarding;
> `localhost:8000` is only reachable from your laptop. GitHub's webhook
> system is on the public internet and needs to make an HTTP POST *to* you.
> See [Going Live Against a Real Repo](#going-live-against-a-real-repo) for
> the laptop-demo path (ngrok) and the production path (deploy the image).
> The `/api/simulate-comment` endpoint runs the same orchestration code path
> with no tunnel required — useful for development and reviewer demos.

## Why Devin

We don't want yet another "watch a queue, print a string" bot. The interesting
part of vulnerability remediation is *actually fixing the code*. Devin is the
only piece in this stack that does that — it inspects the repo, makes a
focused change, runs the relevant tests, and opens a reviewable PR. Everything
else here exists to give Devin a clean event stream and to give humans a clear
picture of what Devin is doing.

---

## Features

- GitHub `issue_comment` webhook trigger filtered to `@devin` mentions.
- One active Devin session per `(repo, issue)` — follow-up `@devin` comments
  forward into that same session.
- Webhook signature verification (HMAC-SHA256) when a secret is configured.
- Bot self-comment loop protection.
- Status comments posted back to GitHub on key state changes (session started,
  follow-up forwarded, PR opened, completed, failed, clarification requested),
  with per-status dedupe so polls don't spam the issue.
- Background polling worker that reconciles Devin session state into the
  `remediation_tasks` table.
- Local simulation endpoint (`POST /api/simulate-comment`) so reviewers can
  exercise the full path *without* configuring a public GitHub webhook.
- React + Vite dashboard with metric cards, task table, side-panel detail view,
  interaction timeline, and a follow-up message box.
- SQLite for storage; easy to inspect with any sqlite client.
- Containerized with `docker-compose` (separate `app` + `web` services).
- Built test-first with `pytest`; all external API calls are mocked.

---

## Prerequisites

- A Devin API key + Devin organization ID (the v3 API requires both).
- A GitHub Personal Access Token with `issues:write` on the target repo.
- A target GitHub repo or fork you control.
- Docker + Docker Compose. (Local Python 3.11+ is only required if you want
  to run tests outside Docker.)

---

## Environment Variables

Copy `.env.example` to `.env` and fill in the values:

| Variable                  | Required | Purpose                                                                                  |
|---------------------------|----------|------------------------------------------------------------------------------------------|
| `DEVIN_API_KEY`           | yes      | Bearer token for the Devin API.                                                          |
| `DEVIN_ORG_ID`            | yes      | Devin organization ID (used in `POST /organizations/{org_id}/sessions`).                 |
| `DEVIN_API_BASE`          | no       | Default `https://api.devin.ai/v3`.                                                       |
| `GITHUB_TOKEN`            | yes      | PAT used to post issue comments.                                                         |
| `GITHUB_WEBHOOK_SECRET`   | no       | If set, the webhook verifies `X-Hub-Signature-256` against it.                           |
| `GITHUB_API_BASE`         | no       | Default `https://api.github.com`.                                                        |
| `APP_BASE_URL`            | no       | Public URL of the FastAPI service (used when telling reviewers where the webhook lives). |
| `DATABASE_URL`            | no       | Defaults to `sqlite:////data/devin.db` inside the container.                             |
| `TARGET_REPO`             | no       | Optional convenience for filtering by `owner/repo`.                                      |
| `BOT_GITHUB_LOGIN`        | no       | If set, comments authored by this login are ignored to avoid loops.                      |
| `POLLER_ENABLED`          | no       | Default `true`. Set `false` to disable the background poller.                            |
| `POLLER_INTERVAL_SECONDS` | no       | Default `45`. How often the poller reconciles each active session.                       |
| `POLLER_CONCURRENCY`      | no       | Default `8`. Max active tasks polled in parallel each cycle.                              |
| `RATE_LIMIT_SESSIONS_PER_HOUR` | no  | Default `30`. Cap on new Devin sessions per repo per rolling 60 min. `0` = unlimited.    |

---

## Running Locally

```bash
cp .env.example .env
# fill in DEVIN_API_KEY, DEVIN_ORG_ID, GITHUB_TOKEN at minimum

docker compose up --build
```

Two services come up:

- `app` — FastAPI orchestrator on http://localhost:8000
- `web` — Vite dev server (React dashboard) on http://localhost:5173

Open http://localhost:5173 to use the dashboard.

If you'd rather run the backend without Docker:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

…and the frontend:

```bash
cd web && npm install && npm run dev
```

---

## Running Tests

The project was built test-first. All external Devin and GitHub calls are
mocked, so **no real credentials are required to run the suite**.

```bash
make test
# or:
PYTHONPATH=. pytest
```

Inside the container:

```bash
docker compose run --rm app pytest -q
# or:
make docker-test
```

### Test coverage

| Area                                     | File                                                          |
|------------------------------------------|---------------------------------------------------------------|
| Webhook parsing                          | `tests/test_webhook_parsing.py`                               |
| `@devin` trigger detection               | `tests/test_webhook_parsing.py`, `tests/test_simulation.py`   |
| Idempotent session creation              | `tests/test_idempotency.py`                                   |
| Follow-up forwarding                     | `tests/test_idempotency.py`, `tests/test_orchestration.py`    |
| Devin API failure handling               | `tests/test_orchestration.py`                                 |
| GitHub issue comment posting + dedupe    | `tests/test_github_comments.py`                               |
| Metrics calculation                      | `tests/test_metrics.py`                                       |
| Local simulation                         | `tests/test_simulation.py`                                    |
| Devin client adapter shape               | `tests/test_clients_and_api.py`                               |
| Webhook signature                        | `tests/test_clients_and_api.py`                               |
| API endpoints (list/detail/refresh/send) | `tests/test_clients_and_api.py`                               |

---

## Reviewer-Friendly Simulation

You don't need to expose this service to the public internet or configure a
real webhook to demo it. The orchestrator exposes a simulation endpoint that
runs the *exact same code path* as a real GitHub webhook:

```bash
curl -X POST http://localhost:8000/api/simulate-comment \
  -H "Content-Type: application/json" \
  -d '{
    "repo_full_name": "your-org/superset",
    "issue_number": 1,
    "issue_title": "[VULN] Demo vulnerability remediation issue",
    "issue_url": "https://github.com/your-org/superset/issues/1",
    "issue_body": "This issue asks Devin to remediate a bounded vulnerability.",
    "comment_body": "@devin please investigate and remediate this issue",
    "comment_author": "demo-user"
  }'
```

Then open http://localhost:5173 to see the new task, metrics, and timeline.
You can also fire the simulation from the dashboard's "Simulate workflow"
panel.

> Note: the simulate endpoint *will* call the real Devin and GitHub APIs if
> credentials are configured. To exercise the orchestration logic in
> isolation, run the test suite — it mocks both.

---

## Going Live Against a Real Repo

`docker compose up` runs FastAPI on `localhost:8000` — only your machine can
reach it. GitHub's webhook delivery system runs on the public internet and
needs a hostname it can resolve. So either:

- expose `localhost:8000` with a tunnel (laptop demo), or
- deploy the backend to a real public host (production).

The rest of this section is the laptop / demo path.

### 1. Start a tunnel pointing at the backend

[ngrok](https://ngrok.com/download) is the path of least resistance:

```bash
ngrok http 8000
```

ngrok prints a public URL like `https://3ed7-2a02-…ngrok-free.app`. Note it.

> **ngrok free tier caveats**
> - The hostname changes each time ngrok restarts; you'll have to update the
>   webhook URL on GitHub when that happens. Pin a domain on a paid tier or
>   use `cloudflared tunnel --url` with a named tunnel for stability.
> - The free tier shows a browser warning page on GETs. Webhook POSTs from
>   GitHub go through cleanly — this only affects you if you try to open the
>   tunneled URL in a browser.

### 2. Wire the public URL into `.env`

```env
APP_BASE_URL=https://3ed7-2a02-….ngrok-free.app
```

(Cosmetic — the orchestrator doesn't actually call out to itself — but it
keeps the dashboard's reported base URL accurate.)

### 3. Add the GitHub webhook

In your repo: **Settings → Webhooks → Add webhook**.

| Field           | Value                                                                |
|-----------------|----------------------------------------------------------------------|
| Payload URL     | `<ngrok URL>/webhooks/github`                                        |
| Content type    | `application/json`                                                   |
| Secret          | the same value as `GITHUB_WEBHOOK_SECRET` in your `.env`             |
| SSL verification| Enable                                                               |
| Which events    | *Let me select individual events* → **Issue comments**, **Issues**, **Pull requests** |
| Active          | ✓                                                                    |

All three event types are needed: `issue_comment` carries the `@devin`
trigger and follow-ups; `issues.closed` lets the orchestrator mark a
task `closed_unfixed` when an issue is closed without a PR; and
`pull_request.closed` is what flips a task to `done` (merged) or
`closed_unmerged` (closed without merging). Without the `Issues` and
`Pull requests` subscriptions, those terminal transitions never happen
and the dashboard's "PRs merged" / "Closed without fix" metrics stay at
zero.

GitHub fires a `ping` event immediately after creation. The orchestrator
returns `{"action": "ignored", "reason": "unsupported_event"}` — that's
a green check on GitHub's "Recent Deliveries" tab, not an error.

### 4. PAT permissions

The runtime PAT (`GITHUB_TOKEN`) needs:

- **Issues: Read and write** (to post status comments back).
- **Pull requests: Read** (to surface PR URLs Devin opens).

To *manage webhooks via the GitHub API* (not required if you create the hook
via the UI as above), the PAT additionally needs:

- **Webhooks: Read and write** (fine-grained), or `admin:repo_hook` (classic).

If you see `403 "Resource not accessible by personal access token"`, that's
the missing permission.

### 4a. Bot-loop protection

The orchestrator filters two kinds of senders to avoid processing its own
output:

1. **Anything that ends with `[bot]`** — that's GitHub's canonical suffix
   for App identities (e.g. `daniel-raad-devin-orchestrator[bot]`). This is
   the primary safeguard, applied unconditionally.
2. **`BOT_GITHUB_LOGIN`** (optional) — if you're using a regular user
   account as your bot, set this to its login so its comments get filtered
   even though they don't have the `[bot]` suffix.

> Why both layers: the orchestrator's own status comments include literal
> `@devin` text in a few places (e.g. *"Reply with `@devin <answer>`"*).
> The `@devin` mention filter alone would *not* prevent loops — those
> comments would round-trip and re-trigger the orchestrator. The `[bot]`
> suffix check is what actually closes the loop in App-auth setups.

For demo simplicity, `BOT_GITHUB_LOGIN` is optional. With App auth (4b
below), you don't need it at all.

> **After changing `.env`, recreate the container** so the new env file is
> re-read:
>
> ```bash
> docker compose up -d --force-recreate app
> ```
>
> A plain `docker compose restart` keeps the old environment.

### 4b. Authenticate as a GitHub App (recommended for any non-toy use)

Using a PAT means every status comment the orchestrator posts is authored by
*you*. That's fine for a demo and confusing in production. The right answer
is a **GitHub App**: the App has its own bot identity (`<app-slug>[bot]`),
its own avatar, and is installable per-repo.

#### Create the App

https://github.com/settings/apps/new

- **GitHub App name:** anything unique (e.g. `daniel-raad-devin-orchestrator`).
- **Homepage URL:** anything (your repo or the dashboard URL).
- **Webhook:** *uncheck "Active"* — we use a repo-level webhook instead, so
  enabling the App webhook would cause double-delivery. (If you'd rather use
  the App webhook, do that and delete the repo-level one — but only one.)
- **Repository permissions:**
  - Issues: **Read and write**
  - Pull requests: **Read**
  - Metadata: **Read** (auto)
- **Where can this App be installed?** "Only on this account" is fine.

Create the App. On the App's page:

1. Note the **App ID** (numeric, top of page).
2. Scroll to **Private keys** → "Generate a private key". Browser downloads a
   `.pem` file. Save it into the project root as
   `devin-orchestrator.private-key.pem` (the `.pem` extension is gitignored).
3. **Install the App** on your repo: from the App's page → Install App →
   pick your account → "Only select repositories" → `daniel-raad/superset`.

#### Wire it into `.env`

```env
# Comment out or empty out GITHUB_TOKEN — it's no longer used.
# GITHUB_TOKEN=
GITHUB_APP_ID=123456
GITHUB_APP_PRIVATE_KEY_PATH=/app/devin-orchestrator.private-key.pem
```

The path `/app/...` is the path *inside the container*. Since the project
directory is mounted at `/app`, dropping the `.pem` in the project root makes
it visible at `/app/devin-orchestrator.private-key.pem`.

You can also set `BOT_GITHUB_LOGIN=<app-slug>[bot]` for the second layer of
loop protection (e.g. `BOT_GITHUB_LOGIN=daniel-raad-devin-orchestrator[bot]`).

#### Apply

```bash
docker compose up -d --force-recreate app
```

Trigger an `@devin` comment. The orchestrator's reply will now show as
`<app-slug>[bot]` with the App's avatar.

#### How it works (one paragraph)

The client signs a short-lived JWT (`RS256`) with the App's private key,
calls `/repos/{owner}/{repo}/installation` to find the installation ID for
that repo, then exchanges the JWT for an *installation access token* via
`POST /app/installations/{id}/access_tokens`. The installation token is what
authorizes API calls (post comments, read issue), and it's cached
in-memory until ~60 seconds before expiry. PAT auth is still supported as a
fallback when `GITHUB_APP_ID` is not set, which keeps the test suite
credential-free.

### 5. Smoke-test through the tunnel

Without leaving your terminal:

```bash
# Stack up
docker compose up --build -d

# Tunnel up (separate terminal)
ngrok http 8000

# Hit the backend through the tunnel:
curl -sS https://<ngrok-host>/healthz
# {"ok":true}
```

Then on any issue in your target repo, comment:

```
@devin please investigate
```

Within ~2 seconds:
- The dashboard at http://localhost:5173 shows a new task.
- A `Devin remediation session started: …` reply appears on the issue.
- The interaction timeline records the GitHub trigger and the orchestrator's
  reply.

### 6. Stopping cleanly

```bash
# stop ngrok with Ctrl-C in its terminal, then:
docker compose down
```

The webhook on GitHub stays put; just disable it (or change the URL when ngrok
gives you a new hostname next time).

---

## Triggering from GitHub

Once the webhook is wired up, comment on any issue in the configured repo:

```
@devin please remediate this vulnerability
```

What you should see:

1. The orchestrator receives the webhook.
2. A Devin session is created (or, if one already exists for the issue, the
   comment is forwarded to it).
3. The orchestrator posts a comment back on the issue with the Devin session
   URL.
4. The dashboard updates: the new task appears, metric cards tick up, and the
   task's interaction timeline shows the GitHub trigger and the orchestrator's
   response.
5. Subsequent `@devin` comments on the same issue go to the same session.

---

## Modes — what `@devin` actually does

The orchestrator routes every `@devin` comment through a registry of
**modes**. The mode is determined by the comment's wording. Each mode owns:

- a *detection* function (does this comment match this mode?),
- *prompt builders* for new sessions and follow-ups,
- a *response policy* (when to post Devin's reply back, if at all).

The current modes:

| Mode        | Triggered by (examples)                                           | What it does                                                                  |
|-------------|-------------------------------------------------------------------|-------------------------------------------------------------------------------|
| `plan`      | `@devin plan a solution` · `@devin propose a fix` · `@devin outline an approach` · `@devin draft a plan` · `@devin think through this without making changes` | Devin produces a written plan (issue summary, root cause, approach, files, risks, next steps). The orchestrator posts the plan on the issue when Devin signals it's done, and marks the task `completed` without opening any PR. |
| `remediate` | Anything else (default), e.g. `@devin please remediate this`       | Devin investigates the code, makes a focused change, runs tests, and opens a PR. The orchestrator posts session-start, PR-opened, completion, and clarification updates. |

### Mode rules

- **One active task per issue, one mode per task.** While a remediation is in
  flight on an issue, asking for a plan (or vice versa) is *refused* with a
  comment explaining how to switch — the orchestrator won't quietly mix
  intents on the same Devin session.
- **Continuation across modes works on terminal tasks.** Once a plan is
  posted (`completed`) and you comment `@devin go ahead and implement`, a
  *new* `mode=remediate` task is created on the same issue. Two timelines,
  clean metrics. The same applies in reverse: a completed remediate task
  plus `@devin can you plan a different approach` opens a fresh `plan` task.
- **Continuation phrases:** `go ahead`, `implement`, `build it`, `ship it`,
  `do it`, `proceed`, plus the original retry words (`retry`, `continue`,
  `redo`, `try again`, `reopen`).

### Adding a new mode

`app/modes.py` is the single source of truth. To add e.g. an `audit` mode:

1. Add prompt templates + builders in `app/prompts.py`.
2. In `app/modes.py`:
   - Write `_is_audit_request(body) -> bool`.
   - Define `AUDIT = Mode(key="audit", label="Audit", detect=_is_audit_request,
     build_prompt=build_audit_prompt, build_followup_prompt=build_audit_followup_prompt,
     response_ready=..., format_response=...)`.
   - Insert `AUDIT` *above* `REMEDIATE` in `MODE_REGISTRY` (the registry is
     priority-ordered; `REMEDIATE` is the catch-all and stays last).
3. Add tests under `tests/test_plan_route.py` style.

You don't need to touch the orchestrator, the API, the schema, or the UI —
mode behavior flows through the registry.

---

## Dashboard

The dashboard is at **http://localhost:5173** when running in Docker.

### Architecture strip

The strip near the top of the dashboard is a one-line picture of the system:

```
GitHub Issue Comment → Orchestrator → Devin Session → Pull Request → Dashboard
```

It exists so a reviewer can read the system in three seconds: GitHub is the
collaboration surface, the orchestrator manages Devin, Devin produces PRs, and
the dashboard is observability.

### Metric cards

- **Total @devin mentions** — every `user_instruction` event recorded.
- **Active sessions** — tasks currently in `pending`, `planning`,
  `plan_posted`, `remediating`, `awaiting_user`, or `pr_opened`.
- **Awaiting review** — tasks where a Devin PR is open and waiting on a human.
- **PRs opened** — tasks with a non-null `pr_url`.
- **PRs merged / Errors** — terminal counts (`done` and `failed`).
- **Closed without fix** — issues that were closed without a merged PR
  (`closed_unmerged` + `closed_unfixed`).
- **Follow-ups forwarded** — count of `followup_forwarded` interaction events.
- **Avg time to PR / completion** — averages across tasks where Devin has
  reached that state.

If a metric isn't present in the API response, the card shows `—` rather than
breaking the UI.

### Lifecycle indicator

Each task displays its lifecycle as a compact strip:

```
Issue ✓ → Devin ● → PR ○ → Review ○ → Done ○
```

- `✓` done, `●` active (pulsing), `○` pending, `✕` failed.
- A task with `pr_opened` status is shown as **Awaiting review**, not Completed
  — a PR opened by Devin is *not* the end of the workflow. A human still has
  to review and merge.
- An `awaiting_user` task additionally renders an "Awaiting user" flag.

The same indicator is shown in the table row and at the top of the task detail
panel, so the visual is consistent across the dashboard.

### Task table

Columns: Lifecycle, Issue, Requested by, Trigger, Latest interaction, Devin,
PR, Last updated, Actions.

- The Issue cell links to the GitHub issue and shows the repo and the task's
  mode (Plan or Remediate).
- Trigger reads `@devin comment`, `Simulated comment`, or
  `Manual UI instruction` based on how the task was started.
- Latest interaction is a human-readable label derived from the latest
  recorded event (or from status if there are no events yet).
- Devin and PR cells use clear "Open Devin ↗" / "Open PR ↗" labels rather
  than truncated IDs.

### Task detail (side panel)

Opens via the per-row **Details** button.

- Header shows repo, task ID, issue number, and title.
- Lifecycle indicator at the top mirrors the row indicator.
- Summary section: repo, issue link, requester, trigger, status, mode, Devin
  session, PR, created/updated timestamps, time-to-PR, time-to-completion,
  and any error.
- Interaction timeline, color-coded by source: GitHub (blue), Devin (purple),
  Orchestrator (green). Each event shows source pill, event type, timestamp,
  and body.
- If no events have been recorded for a task yet, a *derived* timeline is
  shown (Issue detected → Devin session started → PR opened → Awaiting review
  → Completed/Failed) with a small note indicating it's a fallback.
- Follow-up instruction box: type a message, click **Send to Devin**. The
  message is forwarded into the existing Devin session via
  `POST /api/tasks/{id}/send`. Success / error feedback is shown inline.

### System health card

A small panel that shows:

- **Webhook endpoint:** Ready (the orchestrator is up).
- **Devin API:** Configured / Missing API key (from `/api/health`).
- **GitHub API:** Configured / Missing token (PAT or App credentials).
- **Database:** Connected (the dashboard successfully loaded tasks/metrics).
- **Last refresh time** — from the most recent successful poll.

### Simulate panel

A collapsible form on the dashboard that POSTs to `/api/simulate-comment`.
Defaults are pre-filled for instant demoability. After a successful submit
the panel:

1. Shows a success message describing the orchestrator's action.
2. Refreshes metrics and the task table.
3. Offers a **View task details** button if the simulation created a task.

---

## Human-in-the-loop review

Devin opens PRs; *humans* review and merge them. The dashboard reinforces this
by deliberately distinguishing *Awaiting review* from *Completed*:

- A task with a PR but no terminal Devin status is shown as
  **Awaiting review** — the lifecycle indicator's `Review` step is `●`
  (active), and the `Done` step stays `○` (pending).
- A task only becomes **Completed** when Devin reports completion (which, in
  a human-in-the-loop flow, only happens once a reviewer has approved/merged
  and Devin's session reports `completed`).

There is no auto-merge, by design. The orchestrator does not approve, merge,
or close PRs. It only routes events between GitHub and Devin and records
state.

---

## Reviewer-friendly simulation (UI)

You can demo the full path from the dashboard without a real GitHub webhook:

1. Open the dashboard at http://localhost:5173.
2. Expand **Simulate workflow**.
3. Click **Simulate @devin comment** (defaults are pre-filled).
4. The new task appears in the table; click **Details** (or the
   "View task details" button shown after submit) to open the side panel.
5. In the side panel, type a follow-up message in
   *Send follow-up instruction to Devin* and click **Send to Devin**.
6. Watch metrics ("Follow-ups forwarded", "Total @devin mentions") and the
   lifecycle indicator update.

### Manual UI verification checklist

The frontend has no automated test framework wired up. To verify the
dashboard manually:

- [ ] Dashboard loads at http://localhost:5173.
- [ ] Architecture strip renders.
- [ ] Metric cards render. Missing metrics show `—`, not a crash.
- [ ] System health card shows configured/unknown status.
- [ ] Task table renders with the new columns.
- [ ] Lifecycle indicator appears in each row and in the detail panel.
- [ ] **Details** button opens the side panel (and Esc / overlay click
      closes it).
- [ ] Tasks with `pr_opened` show as **Awaiting review** (not Completed).
- [ ] Follow-up instruction form sends to `POST /api/tasks/{id}/send`.
- [ ] Simulation form runs `POST /api/simulate-comment` and refreshes the
      table.
- [ ] Empty state ("No remediation tasks yet…") shows when the table is
      empty.
- [ ] Loading state ("Loading remediation dashboard…") shows on first load.
- [ ] Error state ("Unable to load dashboard data.") shows when the backend
      is down.

---

## Plan-route demo (60 seconds)

1. On any issue in your target repo, comment:
   ```
   @devin can you plan a solution to this issue
   ```
2. Within seconds the bot replies: *"Devin is drafting a plan for this issue: <session url>…"*
3. Wait for Devin to finish (status moves to `awaiting_user` / `completed`).
   The poller picks that up on the next 45s tick and posts the plan back to
   the issue, prefixed with `**Devin's proposed plan:**`.
4. The dashboard now shows the task with `mode=plan`, `status=completed`,
   `time_to_completion_seconds` populated. No PR was opened.
5. Reply on the issue: `@devin go ahead and implement this plan`. A *new*
   task is created with `mode=remediate` and Devin starts working a PR.

---

## 5-Minute Demo Flow

1. Open the dashboard at http://localhost:5173 next to a GitHub issue.
2. On the issue, comment `@devin please remediate this vulnerability`.
3. Watch the orchestrator find or create a Devin session (timeline event
   appears within seconds).
4. Show the new task on the dashboard, the bumped metric cards, and the
   `Devin remediation session started` comment posted back on the issue.
5. From the dashboard task detail, send a follow-up instruction
   (`@devin also add a regression test for invalid filenames`).
6. Show the PR link populating once Devin opens one (or click Refresh on the
   row to force a poll).
7. Land it: *"This is how an engineering leader knows the system is working —
   total mentions, active sessions, PRs opened, average time to PR, and a
   per-task timeline that shows exactly where each remediation is."*

---

## Layout

```
.
├── app/                       FastAPI service
│   ├── main.py                app factory + lifespan + landing page
│   ├── webhooks.py            POST /webhooks/github
│   ├── api.py                 /api/* (tasks, metrics, simulate, send, refresh)
│   ├── orchestrator.py        core state machine
│   ├── devin_client.py        Devin v3 API client + adapter
│   ├── github_client.py       GitHub REST client + signature verification
│   ├── poller.py              background reconciliation worker
│   ├── metrics.py             compute_metrics()
│   ├── models.py              SQLAlchemy models (RemediationTask, InteractionEvent)
│   ├── schemas.py             Pydantic request/response models
│   ├── prompts.py             Devin prompt templates
│   ├── database.py            engine + session factory
│   ├── deps.py                FastAPI dependencies
│   └── config.py              Settings (pydantic-settings)
├── tests/                     pytest suite (mocks Devin + GitHub)
├── web/                       React + Vite dashboard
│   ├── src/                   App, components, lib, types
│   ├── Dockerfile             dev server image
│   └── package.json
├── Dockerfile                 backend image
├── docker-compose.yml         app + web services
├── Makefile                   test / run / docker shortcuts
└── .env.example
```

---

## Load characteristics & hardening

The orchestrator has a few cheap mitigations for moderate load. They keep the
demo robust without dragging in queue infrastructure:

- **Webhook handler runs the orchestrator on a worker thread**
  (`asyncio.to_thread(handle_comment_event, ...)`). The Devin and GitHub HTTP
  calls inside don't pin the event loop. With uvicorn's default ~40-thread
  pool, a single worker absorbs ~40 concurrent webhook deliveries before
  GitHub's 10s retry timeout becomes a risk — well above the laptop-demo
  failure point of one.
- **Per-repo session-creation rate limit.** `RATE_LIMIT_SESSIONS_PER_HOUR`
  (default `30`) caps brand-new Devin sessions per `(repo, rolling 60 min)`.
  Comments over the limit get a clear refusal comment on the issue and are
  *not* recorded as a task (so they don't pollute the dashboard or burn
  ACUs). Follow-ups into existing sessions are unaffected.
- **Bounded-concurrency poller.** `POLLER_CONCURRENCY` (default `8`)
  governs how many active tasks the poller reconciles in parallel each
  tick. Each poll uses its own DB session in a worker thread; an
  `asyncio.Semaphore` keeps simultaneous Devin requests bounded.

These three together raise the safe operating envelope from "1 webhook at a
time" to roughly "tens of concurrent webhooks, hundreds of active tasks,
predictable spend". Beyond that you want Postgres + multiple uvicorn workers
+ a real queue.

## Limitations

- **Storage** is SQLite in a Docker volume. Fine for a prototype; not for
  production. Switching to Postgres is a `DATABASE_URL` change plus
  `psycopg` in `requirements.txt` — no code changes.
- **Polling, not push.** The orchestrator polls Devin every 45s for state
  changes. A real production version would want webhook callbacks from Devin.
- **Devin response shape.** The v3 adapter handles the documented shape and a
  couple of fallbacks, but if Devin changes its payload, the adapter in
  `app/devin_client.py::_adapt_session` is the single place to touch.
- **No dashboard auth.** Anyone with network access to the dashboard can
  view tasks and send follow-up messages to Devin. Put it behind your VPN
  or an auth proxy in any non-toy environment.
- **No auto-merge.** Devin opens PRs; humans review and merge.
- **No scanner / scheduled discovery yet** — this version is purely
  event-driven from `@devin` mentions. Adding a Dependabot/Semgrep/Snyk
  ingest would be a natural next step.
- **Single repo, single org.** No multi-tenant scoping.

## Future Improvements

- Scheduled codebase audit ("scan repo X every Monday").
- Scanner ingestion from Dependabot, Semgrep, Snyk: convert findings into
  `@devin`-style triggers automatically.
- Slack / Jira / Linear notifications for state changes.
- Severity-based approval gates (e.g., critical findings require explicit
  green-light before Devin opens the PR).
- Policy checks before PR (license, scope, sensitive paths).
- Dashboard charts: time-series of mentions vs. PRs vs. completions.
- Postgres + a managed deployment, multi-worker uvicorn behind it.
- Auth (OIDC) on both the dashboard and the simulate endpoint.
- Per-installation GitHub API rate-budget tracking (currently we rely on
  the App's 5000 req/hr ceiling without self-throttling).
- Exponential backoff with retry on transient Devin/GitHub 5xx and 429.
- Devin webhooks instead of polling, when those land.
