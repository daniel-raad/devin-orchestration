# Devin GitHub Issue Orchestrator

Turns `@devin` comments on GitHub issues into Devin remediation sessions, uses
the issue thread as the human-in-the-loop chat surface, and exposes a
dashboard so an engineering leader can answer *"How do I know Devin is
actually working?"*

```
@devin comment on issue  →  Orchestrator  →  Devin session  →  PR opened  →  Status comments back on the issue
                                                  │
                                                  └──►  React dashboard (metrics + per-task timeline)
```

---

## TL;DR for reviewers

You can demo and verify the entire orchestration without any GitHub or Devin
credentials. Two paths:

1. **Run the test suite** — fully mocked, no creds, ~1s. This is the highest-fidelity check of the orchestration logic.
2. **Run the stack + use the simulate endpoint / dashboard** — exercises the same code path a real GitHub webhook would, and you see it land in the UI.

Real GitHub-and-Devin end-to-end (`@devin` on a real issue → real PR) needs
credentials and an ngrok tunnel. See [docs/EXTENDED.md](docs/EXTENDED.md).

---

## Quick start

```bash
# 1. Run the tests (no creds required, all external APIs mocked)
make test

# 2. Bring the stack up
cp .env.example .env       # leave DEVIN_*/GITHUB_* blank for the simulate-only path
docker compose up --build
```

- Backend (FastAPI):  http://localhost:8000
- Dashboard (React):  http://localhost:5173

### Drive it from the dashboard

Open http://localhost:5173, click **Demo & diagnostics** (top right) to
reveal the tools panel, then click **Simulate @devin comment**. A task
appears in the table; click **Details** to see the interaction timeline
and send a follow-up.

### Drive it from curl

```bash
curl -X POST http://localhost:8000/api/simulate-comment \
  -H "Content-Type: application/json" \
  -d '{
    "repo_full_name": "your-org/superset",
    "issue_number": 1,
    "issue_title": "[VULN] Demo issue",
    "issue_url": "https://github.com/your-org/superset/issues/1",
    "issue_body": "Asks Devin to remediate a bounded vulnerability.",
    "comment_body": "@devin please investigate and remediate",
    "comment_author": "demo-user"
  }'
```

`/api/simulate-comment` runs the *exact same* orchestration code path as a
real GitHub webhook — only the transport differs.

> If `DEVIN_API_KEY` / `DEVIN_ORG_ID` / `GITHUB_TOKEN` are configured, the
> simulate endpoint *will* call the real APIs. To exercise the orchestration
> in pure isolation, run `make test` instead.

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

All of the above are walked through step-by-step in [docs/EXTENDED.md](docs/EXTENDED.md).

---

## Repo layout

```
app/                       FastAPI service
├── main.py                app factory + lifespan
├── webhooks.py            POST /webhooks/github
├── api.py                 /api/* (tasks, metrics, simulate, send, refresh)
├── orchestrator.py        core state machine (start here to read the logic)
├── modes.py               plan / remediate routing registry
├── prompts.py             Devin prompt templates
├── devin_client.py        Devin v3 API client + adapter
├── github_client.py       GitHub REST client + signature verification + App auth
├── poller.py              background reconciliation worker
├── metrics.py             compute_metrics()
└── models.py              SQLAlchemy: RemediationTask, InteractionEvent
tests/                     pytest suite — Devin + GitHub fully mocked
web/                       React + Vite dashboard
docker-compose.yml         app + web services
docs/EXTENDED.md           full setup, GitHub App auth, modes, hardening
```

The two files most worth reading to understand the system are
`app/orchestrator.py` (the state machine) and `app/modes.py` (how `@devin`
comments get routed to plan vs. remediate).

---

## Notes & limitations

- SQLite in a Docker volume — fine for the prototype; swap to Postgres via
  `DATABASE_URL` for production.
- Polls Devin every 45s instead of receiving webhooks; would flip when Devin
  ships outbound webhooks.
- No auto-merge — Devin opens PRs, humans review and merge. The dashboard
  distinguishes *Awaiting review* from *Completed* on purpose.
- No dashboard auth — put it behind a VPN / auth proxy in any non-toy use.

Full list, hardening characteristics, and roadmap: [docs/EXTENDED.md](docs/EXTENDED.md).
