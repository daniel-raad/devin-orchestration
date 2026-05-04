# Devin GitHub Issue Orchestrator

Turns `@devin` comments on GitHub issues into Devin remediation sessions, uses
the issue thread as the human-in-the-loop chat surface, and exposes a
metrics and tracking dashboard. 

📹 **Walkthrough video:** https://www.loom.com/share/9dc9826f731f416c90144d3ea776e7ee

```
@devin comment on issue  →  Orchestrator  →  Devin session  →  PR opened  →  Status comments back on the issue
                                                  │
                                                  └──►  React dashboard (metrics + per-task timeline)
```

---

Two ways to verify the orchestration:

1. **Run the test suite** — fully mocked, **no creds required**, ~1s. Highest-fidelity check of the orchestration logic.
2. **Run the stack + use the simulate endpoint / dashboard** — exercises the *same* code path a real GitHub webhook would, and you see it land in the UI. **Requires `DEVIN_API_KEY` + `DEVIN_ORG_ID`** because the live server constructs a real Devin client and the simulate endpoint calls it. `GITHUB_TOKEN` is optional (without it, status comments back to the issue are skipped). No webhook/ngrok needed.

Real GitHub-and-Devin end-to-end (`@devin` on a real issue → real PR) additionally
needs an ngrok tunnel and a GitHub webhook. See [docs/EXTENDED.md](docs/EXTENDED.md).

---

## Quick start

### Prerequisites

- **Python 3.11+** (for `make test` / `make run`) — `python3 --version`
- **Docker** with Compose (for `make docker-up` / `make docker-test`)

Nothing gets installed into your system Python. `make test` and `make run`
create a local `.venv/` in the project directory and install dependencies
inside it. Remove with `make clean-venv`.

### 1. Run the tests (no creds required, all external APIs mocked)

```bash
make test
```

First run creates `.venv/` and installs `requirements.txt` into it
(~30s); subsequent runs reuse the venv and only re-install if
`requirements.txt` changes.

If you'd rather not install Python at all, use the Docker-only path —
this builds the app image and runs the suite inside it:

```bash
make docker-test
```

### 2. Bring the stack up

```bash
cp .env.example .env
# Edit .env and set:
#   DEVIN_API_KEY=<your devin api key>     (required for simulate / dashboard)
#   DEVIN_ORG_ID=<your devin org id>       (required for simulate / dashboard)
#   GITHUB_TOKEN=<a PAT>                   (optional — without it, status
#                                           comments back to the issue are skipped)
docker compose up --build
```

- Backend (FastAPI):  http://localhost:8000
- Dashboard (React):  http://localhost:5173

> Without `DEVIN_API_KEY` and `DEVIN_ORG_ID`, the simulate endpoint will
> still create a task row and exercise the orchestration wiring, but the
> Devin call fails immediately and the task lands as `failed` with
> `error: org_id is required for v3 API`. If you want a credential-free
> end-to-end pass through orchestrator logic, run `make test` instead.

### Drive it from the dashboard

Open http://localhost:5173, click **Demo & diagnostics** (top right) to
reveal the tools panel, then click **Simulate @devin comment**. A task
appears in the table; click **Details** to see the interaction timeline
and send a follow-up.

### Drive it from curl

Replace `<your-org>/<your-repo>` with a real GitHub repo that your Devin
org has access to — Devin will try to clone it and operate on real code.
Pointing it at a non-existent repo will start a session, but Devin won't
have anything to work on.

```bash
curl -X POST http://localhost:8000/api/simulate-comment \
  -H "Content-Type: application/json" \
  -d '{
    "repo_full_name": "<your-org>/<your-repo>",
    "issue_number": 1,
    "issue_title": "[VULN] Demo issue",
    "issue_url": "https://github.com/<your-org>/<your-repo>/issues/1",
    "issue_body": "Asks Devin to remediate a bounded vulnerability.",
    "comment_body": "@devin please investigate and remediate",
    "comment_author": "demo-user"
  }'
```

`/api/simulate-comment` runs the *exact same* orchestration code path as a
real GitHub webhook — only the transport differs. The real-webhook path
extracts `repo_full_name` from `payload.repository.full_name` automatically
([`app/webhooks.py`](app/webhooks.py)); the simulate endpoint takes it from
the request body since there's no webhook payload to read it from.

It calls the real Devin API when `DEVIN_API_KEY` + `DEVIN_ORG_ID` are set;
without them the call fails immediately. For a fully mocked credential-free
pass through the orchestration, run `make test` instead.

---

## What you can test without external setup

| Capability                                        | How                                                |
|---------------------------------------------------|----------------------------------------------------|
| Webhook parsing, `@devin` detection, signature    | `make test` (`tests/test_webhook_parsing.py`)      |
| Idempotent session creation per `(repo, issue)`   | `make test` (`tests/test_idempotency.py`)          |
| Follow-up forwarding into the same session        | `make test` (`tests/test_orchestration.py`)        |
| Plan vs. remediate mode routing                   | `make test` (`tests/test_modes.py`, `test_plan_route.py`) |
| Devin failure handling, status reconciliation     | `make test` (`tests/test_orchestration.py`)        |
| Per-status comment dedupe                         | `make test` (`tests/test_github_comments.py`)      |
| Rate limit + bounded-concurrency poller           | `make test` (`tests/test_hardening.py`)            |
| Metrics calculation                               | `make test` (`tests/test_metrics.py`)              |
| Full code path via `/api/simulate-comment`        | `make test` (`tests/test_simulation.py`)           |
| Dashboard rendering, lifecycle indicator, Demo & diagnostics panel, follow-up box | docker compose + browser at http://localhost:5173 |

## What requires external setup

| Capability                                  | What you need                                              |
|---------------------------------------------|------------------------------------------------------------|
| Real `@devin` comment on a real issue       | A target repo + ngrok tunnel + GitHub webhook              |
| Real Devin session opening a real PR        | `DEVIN_API_KEY` + `DEVIN_ORG_ID`                           |
| Status comments authored as `<app>[bot]`    | A GitHub App + private key                                 |
| `pull_request.closed` → `done` transition   | The webhook subscribed to **Issues** and **Pull requests** |

All of the above have further instructions in [docs/EXTENDED.md](docs/EXTENDED.md).

---

## Notes & limitations

- SQLite in a Docker volume — fine for the prototype; swap to Postgres via
  `DATABASE_URL` for production.
- Polls Devin every 45s instead of receiving webhooks - ideally would be Devin webhook events we can track. 
- No dashboard auth 

