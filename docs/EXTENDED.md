# Extended setup & reference

The README covers the reviewer path. This document is the long-form reference:
real GitHub webhook setup, GitHub App auth, mode internals, dashboard tour, and
hardening notes.

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

---

## Environment variables

| Variable                  | Required | Purpose                                                                                  |
|---------------------------|----------|------------------------------------------------------------------------------------------|
| `DEVIN_API_KEY`           | yes      | Bearer token for the Devin API.                                                          |
| `DEVIN_ORG_ID`            | yes      | Devin organization ID (used in `POST /organizations/{org_id}/sessions`).                 |
| `DEVIN_API_BASE`          | no       | Default `https://api.devin.ai/v3`.                                                       |
| `GITHUB_TOKEN`            | yes*     | PAT used to post issue comments. Not needed when using GitHub App auth.                  |
| `GITHUB_APP_ID`           | no       | GitHub App ID. If set, App auth is used instead of PAT.                                  |
| `GITHUB_APP_PRIVATE_KEY_PATH` | no   | Path (inside container) to the App's `.pem`.                                             |
| `GITHUB_WEBHOOK_SECRET`   | no       | If set, the webhook verifies `X-Hub-Signature-256` against it.                           |
| `GITHUB_API_BASE`         | no       | Default `https://api.github.com`.                                                        |
| `APP_BASE_URL`            | no       | Public URL of the FastAPI service (cosmetic; reported by the dashboard).                 |
| `DATABASE_URL`            | no       | Defaults to `sqlite:////data/devin.db` inside the container.                             |
| `TARGET_REPO`             | no       | Optional convenience for filtering by `owner/repo`.                                      |
| `BOT_GITHUB_LOGIN`        | no       | If set, comments authored by this login are ignored to avoid loops.                      |
| `POLLER_ENABLED`          | no       | Default `true`. Set `false` to disable the background poller.                            |
| `POLLER_INTERVAL_SECONDS` | no       | Default `45`. How often the poller reconciles each active session.                       |
| `POLLER_CONCURRENCY`      | no       | Default `8`. Max active tasks polled in parallel each cycle.                             |
| `RATE_LIMIT_SESSIONS_PER_HOUR` | no  | Default `30`. Cap on new Devin sessions per repo per rolling 60 min. `0` = unlimited.    |

---

## Going live against a real repo

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
   pick your account → "Only select repositories" → `<owner>/<repo>`.

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

### 5. Smoke-test

```bash
curl -sS https://<ngrok-host>/healthz
# {"ok":true}
```

Then comment `@devin please investigate` on an issue — within ~2s a new
task appears on the dashboard and the bot replies on the issue.

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

---

## Dashboard

The dashboard is at **http://localhost:5173** when running in Docker.

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

### Task table

Columns: Lifecycle, Issue, Requested by, Trigger, Latest interaction, Devin,
PR, Last updated, Actions.

### Task detail (side panel)

Opens via the per-row **Details** button. Shows summary, lifecycle indicator,
and a color-coded interaction timeline (GitHub blue, Devin purple,
Orchestrator green). A follow-up box forwards messages into the existing
Devin session via `POST /api/tasks/{id}/send`.

If no events have been recorded for a task yet, a *derived* timeline is
shown (Issue detected → Devin session started → PR opened → Awaiting review
→ Completed/Failed) with a small note indicating it's a fallback.

### System health card

Webhook endpoint, Devin API, GitHub API, Database, last refresh time —
sourced from `/api/health`.

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

## Load characteristics & hardening

- **Webhook handler runs the orchestrator on a worker thread**
  (`asyncio.to_thread(handle_comment_event, ...)`). With uvicorn's default
  ~40-thread pool, a single worker absorbs ~40 concurrent webhook deliveries
  before GitHub's 10s retry timeout becomes a risk.
- **Per-repo session-creation rate limit.** `RATE_LIMIT_SESSIONS_PER_HOUR`
  (default `30`) caps brand-new Devin sessions per `(repo, rolling 60 min)`.
  Comments over the limit get a clear refusal comment and are *not* recorded
  as a task. Follow-ups into existing sessions are unaffected.
- **Bounded-concurrency poller.** `POLLER_CONCURRENCY` (default `8`)
  governs how many active tasks the poller reconciles in parallel each tick.

These three together raise the safe operating envelope from "1 webhook at a
time" to roughly "tens of concurrent webhooks, hundreds of active tasks,
predictable spend". Beyond that you want Postgres + multiple uvicorn workers
+ a real queue.

---

## Limitations

- **Storage** is SQLite in a Docker volume. Switching to Postgres is a
  `DATABASE_URL` change plus `psycopg` in `requirements.txt` — no code changes.
- **Polling, not push.** The orchestrator polls Devin every 45s. A real
  production version would want webhook callbacks from Devin.
- **Devin response shape.** The v3 adapter handles the documented shape and a
  couple of fallbacks; if Devin changes its payload, see
  `app/devin_client.py::_adapt_session`.
- **No dashboard auth.** Put it behind a VPN / auth proxy in any non-toy
  environment.
- **No auto-merge.** Devin opens PRs; humans review and merge.
- **No scanner / scheduled discovery.** Purely event-driven from `@devin`
  mentions.
- **Single repo, single org.** No multi-tenant scoping.
