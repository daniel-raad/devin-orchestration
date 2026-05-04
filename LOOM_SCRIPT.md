# Loom Video Script — Devin Issue Orchestrator

**Audience:** C-suite + VP of Engineering + senior ICs
**Length target:** 5:00 (script lands at ~4:55 at ~145 wpm; 5s buffer)
**Goal:** Sell Devin to a mixed business + technical room by leading each section with the business outcome and dropping a 10–15 second technical proof point that makes ICs nod.

Stage directions are in **[brackets]**. Spoken text is plain. The demo is the **plan → implement** flow because it lands harder for a non-engineering audience: it shows Devin thinking *before* it codes, and it brings non-engineers into the loop.

---

## 0:00 — 0:30  The cold open

[Screen: a real-looking GitHub issue titled "[VULN] Path traversal in upload handler". Dashboard tab open in the next monitor or window.]

> "Here's a vulnerability ticket. In most engineering organizations, this issue sits open for days. Someone has to triage, assign, an engineer context-switches in, reads code, writes a fix, runs tests, opens a PR. Today I'll show you a system where anyone — security, a PM, a VP — types `@devin` in this thread and gets either a written plan they can review *or* a finished pull request. And critically — they can see, in real time, that Devin is actually working."

---

## 0:30 — 1:20  The leadership hook

[Switch to dashboard. The default view is metrics + tasks — no developer clutter. Cursor moves across the metric cards.]

> "Before the trigger, the dashboard. Two stories on one screen. The throughput story — total `@devin` mentions, active sessions, **PRs merged**, **closed without a fix**, average time to PR, average time to completion. And the *adoption* story, which is the one most teams can't tell — **unique issues** and **unique requesters**. How many distinct issues have used Devin, and how many distinct people have triggered it. That's the difference between one team running it as a toy and the org actually adopting it. Mentions tell you volume. Unique requesters tell you reach.
>
> Notice the metric cards distinguish *PRs merged* from *closed without a fix* from *errors*. A merged PR shipped. A closed-unmerged PR is human signal that Devin's approach didn't land. An issue closed without a PR is human signal that the work was deemed not worth doing. These are different stories — most dashboards collapse them into one 'closed' bucket and lose the operational nuance.
>
> This is the answer to the question every VP of Engineering gets in a board meeting — *is the AI actually shipping work?* Most teams that have rolled out an AI engineer in the last year cannot answer that question. They have a Slack channel and a vibe. We have receipts."

[Click into one task row, side panel opens, timeline visible.]

> "Per task, a color-coded interaction timeline. Blue is GitHub. Purple is Devin. Green is the orchestrator. Every event is event-sourced — so the dashboard is a deterministic projection of what actually happened. There's no 'I think it worked.'"

---

## 1:20 — 3:00  The live demo: plan → implement

[Back to GitHub issue. Type and post the comment.]

> "Watch the dashboard." [Type `@devin plan a fix for this vulnerability`. Submit.]

[Switch to dashboard within ~2 seconds.]

> "A new row appears. Lifecycle indicator highlights **Plan** as active. The orchestrator parsed the comment, recognized 'plan a fix' as a plan-mode directive, and started the task in `planning` — Devin will think, not change code."

[Switch to GitHub, show the orchestrator reply with the session URL. Then cut/skip ahead to a pre-staged plan reply, since plan mode takes ~30–90 seconds.]

> "About a minute later, Devin posts a structured markdown plan back into this same thread — issue summary, root cause hypothesis, proposed approach, files likely to change, risks, next steps. The dashboard task moves to `plan_posted` — and importantly, that's *not* a terminal state. The plan can still be iterated on. The user might respond with refinements, or close the issue, or — "

[Show the plan rendered on the GitHub issue.]

> "This is the on-ramp for non-engineers. A security analyst, a PM, an engineering manager can ask Devin to think about a problem and produce a structured proposal — without committing engineering capacity. The conversation about whether to fix it, and how, happens on this plan, in this issue, with the actual stakeholders. Then —"

[Type follow-up: `@devin go ahead`. Submit.]

> "— `@devin go ahead`."

[Switch to dashboard. Watch the **same row** transition.]

> "Watch the same row. Lifecycle indicator advances from `Plan ✓` to `Devin (active)`. **One task per issue.** The orchestrator recognized 'go ahead' as a plan-to-remediate continuation, transitioned the task in place from `plan_posted` to `remediating`, and sent Devin the same session a 'now implement the plan you just wrote' message. No new row, no second timeline, no context lost. One issue, one effort, two phases.
>
> That detail matters more than it looks. The naive design here is two tasks — one for plan, one for implement. We tried that early on and it polluted the dashboard with duplicates and split metrics across rows. The current model — *status carries the phase, the row is the effort* — is the cleaner mental model and reflects how a human thinks about the work."

[Click through to the PR Devin eventually opens.]

> "And there's the PR. The same row's lifecycle indicator now shows `PR ✓` and `Review (active)`. Time-to-PR is now in the metrics. That's the loop — *think first, ship second*, with humans deciding the gate between, all on one task."

---

## 3:00 — 3:55  The architecture, with depth

[Switch to a single architecture slide — four boxes.]

> "Four layers. **GitHub** is the collaboration layer — humans stay in the issue thread. **The FastAPI orchestrator** is the control plane. **Devin** is execution. **The React dashboard** is observability.
>
> The orchestrator is the interesting part, so let me get specific. It's not a thin proxy.
>
> **Modes are a registry.** Plan and Remediate today, but adding one — say a 'review' mode that grades incoming PRs — is a single-file change. The orchestrator picks up routing, persistence, and refusal logic for free.
>
> **The dispatcher is a transition table, not a maze of ifs.** A pure function takes `(task_status, intent_is_plan, intent_is_continuation)` and returns one of six action keys — *iterate, transition_plan_to_remediating, transition_pr_to_planning, refuse_mode_switch, previous_task_done, create_new_session*. That's the entire decision surface. Each handler is its own self-contained function. Adding a new state or intent is one row in the dispatcher.
>
> **The state machine is honest about lifecycle.** Plan and Remediate aren't separate tasks — they're phases of the same effort. A plan doesn't 'complete'; it's posted, then the user iterates, advances to remediation, or abandons. Terminal states distinguish *PR merged* from *PR closed unmerged* from *issue closed without a PR* from *system error*. Reviewers landing on the dashboard see at a glance which efforts shipped and which got dropped — and *why*.
>
> **Concurrency is correct, not just fast.** Two layers. In-process: per-`(repo, issue)` `asyncio.Lock` — same-issue retries serialize so the dedupe check can't race the task INSERT, but different issues parallelize. Cross-process: a `processed_comments` table with the GitHub comment id as primary key — concurrent workers race for the INSERT and the loser bails on `IntegrityError` before any Devin work happens. The lock is a latency optimization; the DB constraint is the durable correctness primitive. The orchestrator runs on a worker thread so slow Devin or GitHub I/O doesn't pin the event loop. SQLite is in WAL mode with a 5-second busy timeout, so the concurrent poller and the request path don't block each other.
>
> **Three guardrails.** Rate limits cap new Devin sessions at 30 per repo per rolling hour — over-limit requests are refused and don't even create a database row, so a stuck CI loop spamming `@devin` can't run up an ACU bill. Mode-mismatch refusal — ask for a plan while a remediation is mid-flight (no PR yet), and the orchestrator pushes back instead of merging two intents into one Devin session. Bot-loop protection — any sender ending in `[bot]` is filtered, so the orchestrator's own GitHub App identity can't trigger itself.
>
> **Replan-with-receipts.** When a user asks for a fresh plan after a PR is up — *'@devin replan, this approach was wrong'* — the orchestrator transitions the same task back to `planning` in place, posts a *superseded* notice on the prior PR, and tracks it in a `previous_pr_urls` ledger so the dashboard's task detail shows every PR that's been tried. The PR isn't auto-closed — humans decide its fate — but the audit trail is explicit.
>
> **One funnel.** Webhook and simulate endpoint both call the same `handle_comment_event`. New trigger sources — Slack, Linear, a Dependabot finding — construct the same shape and call that function. No second code path."

---

## 3:55 — 4:30  Why Devin specifically

[Hold on a single slide titled "Why Devin".]

> "I want to be precise. I'm not selling AI agents in general — I'm selling Devin. Three reasons.
>
> **One: Devin actually changes code.** Most bots in this space watch a queue and post a string. Devin reads the repo, runs the relevant tests, opens a reviewable PR. The orchestrator only matters because the thing on the other end ships.
>
> **Two: Devin asks good clarifying questions.** When uncertain, it pauses into an `awaiting_user` state. That's why a thread-based UX works — the conversation already has a home.
>
> **Three, the architectural payoff:** Devin sessions are *durable and addressable*. My orchestrator only uses three Devin endpoints — sometimes a fourth call to `/messages` because the v3 session endpoint doesn't include conversation history. The same handful of calls power *both* the plan flow and the remediate flow. The difference between modes is in our prompts and reconciliation logic, not in needing different Devin features. *That's what makes Devin uniquely orchestrable* — one integration surface, an arbitrary number of productized workflows on top. Most agent products don't expose that surface."

---

## 4:30 — 5:00  Next steps and the platform thesis

[Switch to a slide with two columns: "Now" and "Next".]

> "Near-term — Postgres for production deployments, OIDC auth on the dashboard, switch from polling to Devin webhooks when those land. A one-file change in our adapter. The Postgres move isn't about correctness — the dedupe primitive (`processed_comments` PK) already works on either backend — it's about running multiple uvicorn workers without fighting for SQLite's writer lock, plus connection pooling and `pg_advisory_lock` for clean leader election on the poller. `DATABASE_URL` is the only config that changes.
>
> Strategically — three extensions turn this from a remediation tool into a platform. **Scanner ingestion**: Dependabot, Semgrep, Snyk findings auto-trigger `@devin` through the same single funnel. Your existing security scanners become an autonomous remediation pipeline. **Severity-based approval gates**: critical CVEs require explicit human green-light before Devin opens the PR. That's the compliance story. **A 'review' mode**: Devin grades incoming PRs against your standards — same registry, same plumbing.
>
> So in its current form this is a vulnerability remediation accelerator. With those extensions, it's an autonomous security and quality platform with a human-in-the-loop dashboard for the people accountable for it. That's where I think Devin earns its line item on a CTO's budget."

[End on a single closing frame. Loom thumbnails the last frame, so make it a good one — your name + "Devin Issue Orchestrator" + repo URL if public.]

---

# Production tips

- **Pre-record the plan reveal.** Plan mode takes ~30–90 seconds and is dead air on a Loom. Either pre-warm a plan into the issue and "skip ahead" verbally, or record in two takes and stitch.
- **Default the dashboard to exec view.** "Demo & diagnostics" is now toggle-hidden by default — leave it that way for the recording. Don't show the simulator panel; it confuses non-technical viewers.
- **Practice once with a stopwatch.** Five minutes goes fast.
- **Pre-stage everything.** GitHub issue tab and `localhost:5173` cold-loaded. Pre-warm at least one completed task in the DB so the dashboard isn't empty on first frame.
- **Cursor matters.** Use Loom's click highlight. C-suite watches where your cursor goes more than they read.
- **Don't apologize** for SQLite, polling, or single-tenant. Frame those as deliberate scope choices.
- **What to cut if over time** (priority order): the "dispatcher is a transition table" sentence → the architecture slide → the "concurrency is correct" sentence (keep guardrails) → cut to a single-flow demo (plan only OR remediate only). **Keep at all costs:** the cold open, the metrics dashboard ("PRs merged vs closed without fix vs errors" lands hard), the *"one row, two phases"* in-place transition moment (it's the demo's strongest tell that you actually thought about the model), and the *"one integration surface, an arbitrary number of productized workflows on top"* line.

---

# Depth bank — for live Q&A, not the video

## "What's the dashboard actually showing?"

Two narratives, one screen:

- **Throughput** — total mentions, active sessions, awaiting review, PRs opened, **PRs merged**, **closed without fix**, **errors** (system failures), avg time to PR, avg time to completion, follow-ups forwarded.
- **Adoption** — `unique_issues` (distinct `(repo, issue)` pairs that ever had a task) and `unique_requesters` (distinct GitHub authors who triggered at least one task). Volume vs reach.
- The metric cards distinguish *PRs merged* (success), *closed without fix* (the human said no — this card is one bucket combining `closed_unmerged` + `closed_unfixed`), and *errors* (system failure) — three different operational signals that most dashboards collapse into one ambiguous "closed" or "completed" bucket. The state model underneath keeps the two "closed without fix" sub-states distinct because they're different audit trails (PR rejected vs. issue closed without ever opening a PR), but the dashboard surfaces them together because the operational decision — *somebody needs to look at this* — is the same.
- Per-task interaction timeline, color-coded by source. Every state change, refusal, status comment, Devin response, and *phase_transition* event is an immutable row in `interaction_events`.
- The task detail panel surfaces a *Superseded PRs* row when prior PRs have been replanned over — each linked, with a small *closed/merged at the reviewer's discretion* caption.
- Default view is exec-clean. A "Demo & diagnostics" toggle reveals the simulator panel and the system-health card for backend connectivity checks.

## "Walk me through the mode system."

- `app/modes.py` defines a `Mode` dataclass: `key`, `label`, `detect(body) -> bool`, `build_prompt(...)`, `build_followup_prompt(...)`, optional `response_ready(snapshot, task) -> bool`, optional `format_response(text, task) -> str`, and `response_event_type` for dedupe.
- Two modes registered today — **Plan** and **Remediate**. Order matters: `detect_mode()` returns the first match, with Remediate as the catch-all.
- Plan detection: scan the first 8 tokens after `@devin` for plan verbs (`plan`, `planning`, `propose`, `outline`, `draft`, `design`, `replan`, `rethink`). Article-guarded — *"@devin we have a plan"* with the noun preceded by an article correctly classifies as remediate, not plan. Also matches phrases like *"without making changes"*, *"what's your plan"*, *"do not implement"*.
- Plan response handling: `response_ready()` fires when the Devin session reaches a settled state (`status_detail = waiting_for_user`, `completed`, `blocked`...) AND the latest message is ≥60 chars. Orchestrator wraps in a "Devin's proposed plan" header, posts to the issue, transitions task to `plan_posted` (still active — the user can iterate, advance, or abandon).
- **No `mode` column on the task.** The mode is *derived* from the task's current phase (`mode_for_status(status)` — plan-phase statuses → Plan, otherwise Remediate). Status carries the phase, the row is the effort. Dropping the column simplified the dispatcher and removed an entire axis of confusion.
- Adding a new mode (e.g. "review") is exactly: write a `detect()`, write two prompt builders, register above the catch-all. **Zero changes** to the orchestrator dispatcher, the API, the schema, or the dashboard.

## "Walk me through the state machine."

```
                                  ┌──── @devin go ahead ────┐
pending → planning ⇄ plan_posted ─┤                          ▼
                                  └──── @devin replan ────► remediating ⇄ awaiting_user
                                                              │
                                                              ▼
                                                          pr_opened ─── @devin replan ──► planning
                                                              │       (clears pr_url, posts a
                                                              │        "superseded" notice on
                                                              ▼        the old PR, lists it in
                                                          done           previous_pr_urls)
                                                       (PR merged)
                                                              │
                                              ╔══════════════ ╠══════════════╗
                                              ▼               ▼              ▼
                                        closed_unmerged   closed_unfixed   failed
                                        (PR rejected)     (issue closed,   (system error)
                                                           no PR)
```

- **One task per issue.** The status field carries the phase. Iteration on a phase doesn't spawn a new row; transitions mutate the same row.
- **Plan and remediate are phases, not separate tasks.** That mental model is enforced by the dispatcher's transition table.
- **Terminal states are distinguished.** `done` (PR merged), `closed_unmerged` (PR closed without merge — human signal Devin's approach didn't land), `closed_unfixed` (issue closed without ever opening a PR — human signal the work was deemed not worth doing), `failed` (system error). The dashboard groups `closed_unmerged` + `closed_unfixed` under one *Closed without fix* card (operationally they're the same "needs a human" signal), while the underlying timeline events keep them separate so reviewers can see *which* terminal path each task took.
- **Terminal transitions are webhook-driven**, not Devin-status-driven. `pull_request.closed merged=true` → `done`. `pull_request.closed merged=false` → `closed_unmerged`. `issues.closed` (no PR on the task) → `closed_unfixed`. The orchestrator subscribes to all three event types.
- **Replan from `pr_opened` is a single in-place transition** — the same task moves back to `planning`, the prior PR's URL is appended to a `previous_pr_urls` ledger on the task, and a *superseded* comment is posted on the old PR (additive, not destructive). The dashboard's task detail surfaces every prior PR with a link, so reviewers landing on the issue can find them.

## "How does the dispatcher work?"

`handle_comment_event` is now a small, flat orchestrator. The decision logic is one pure function:

```python
def _route(*, task, is_plan, is_continuation) -> str:
    if task is None: return "create_new_session"
    if task.status in TERMINAL_STATUSES:
        return "create_new_session" if (is_plan or is_continuation) else "previous_task_done"
    if task.status == "pr_opened":
        return "transition_pr_to_planning" if is_plan else "iterate"
    if task.status in PLAN_PHASE_STATUSES:
        return "transition_plan_to_remediating" if (not is_plan and is_continuation) else "iterate"
    if task.status in REMEDIATE_PHASE_STATUSES:
        return "refuse_mode_switch" if is_plan else "iterate"
    return "iterate"
```

Six action keys cover the entire decision space. Each handler is its own function. Adding a new state or intent is one row in `_route()` plus one entry in the action dispatch — no nested ifs, no special cases scattered across the file.

## "How is concurrency correct, not just fast?"

Four layers, all visible in the codebase:

1. **Per-`(repo, issue_number)` `asyncio.Lock`** (`main.py::IssueLocks`) wraps every call into `handle_comment_event` from both the webhook and the simulate endpoint. Same-issue retries serialize through one lock; different issues parallelize. This handles the in-process race where GitHub retries a slow webhook within a second and both attempts arrive on the same node.
2. **DB-level claim INSERT** (`processed_comments`, primary key on `github_comment_id`) is the cross-process correctness primitive. Multiple uvicorn workers / replicas race for the row; the loser bails on `IntegrityError` before any Devin/GitHub API call. The lock above is a latency optimization on top of this — the DB constraint is what makes the dedupe correct under multi-process deployments. SQLite enforces it just as Postgres does.
3. **`asyncio.to_thread`** runs the orchestrator on a worker thread so the event loop is never blocked by slow Devin or GitHub I/O.
4. **SQLite WAL mode** (`database.py`) — `journal_mode=WAL` enables concurrent reads while a write is in flight; `busy_timeout=5000` makes concurrent writers wait 5 seconds for the lock instead of failing immediately. The poller (concurrent across active tasks via `asyncio.Semaphore`) and the request path don't block each other.

Net effect: at-least-once webhook delivery is safe whether the cluster has one process or twelve, the event loop never stalls, the DB is never the bottleneck within a process.

## "How is this scalable?"

- **Stateless app process.** All state in DB and external APIs. Add replicas behind a load balancer; webhooks land on any node.
- **Idempotency is enforced at the DB**, not in the app. `processed_comments.github_comment_id` is a PK; concurrent workers handling a retried webhook race for the INSERT and exactly one wins. The in-process per-issue lock is on top of that for latency, not correctness — it's safe to run multiple workers immediately.
- **Concurrency boundary is `(repo, issue)`** — same-issue serialization with cross-issue parallelism. Bursts across distinct issues scale linearly.
- **Concurrent poller** — `asyncio.Semaphore(concurrency=8)` + `asyncio.to_thread`, each polled task gets its own DB session. Configurable via `POLLER_CONCURRENCY`.
- **Per-status comment dedupe** (`maybe_post_status_update`) — polling frequency can rise without N× spam.
- **GitHub App installation tokens cached in-memory per repo** with 60-second pre-expiry refresh.

### Production move: SQLite → Postgres

SQLite is the deliberate scope choice for the demo (single file, reviewer running in 30 seconds), and the dedupe primitive works on it just as well as on Postgres. The reasons to migrate are operational, not correctness:

- **Multiple uvicorn workers contend on SQLite's writer lock** — WAL + `busy_timeout` softens it but doesn't make concurrent writes parallel. Postgres lets the API workers actually parallelize.
- **`pg_advisory_lock` is the cleanest leader-election primitive** for the background poller, so only one replica runs the reconciliation loop instead of N× duplicating Devin API calls. The alternative — running the poller in its own container with `POLLER_ENABLED=false` on the API workers — also works on SQLite, and is the smaller change.
- **Hosted Postgres** brings connection pooling, backups, observability, replicas — none of which SQLite provides.

The migration itself is a `DATABASE_URL` change plus `psycopg` in `requirements.txt`. No code changes — SQLAlchemy abstracts the rest, and the `processed_comments` constraint is portable.

## "What does the orchestrator actually use the Devin API for?"

Three primary endpoints, all on the v3 organizations namespace, **plus a fourth fallback**:

1. **`POST /organizations/{org_id}/sessions` — create.** Send a mode-specific structured prompt plus the `repos` array so Devin scopes to the right codebase.
2. **`POST /organizations/{org_id}/sessions/{devin_id}/messages` — forward.** Every `@devin` follow-up on the issue and every dashboard message becomes a `send_message` into the same session.
3. **`GET /organizations/{org_id}/sessions/{devin_id}` — reconcile.** Polls every 45 seconds for status, latest_message, pr_url, error.
4. **`GET /organizations/{org_id}/sessions/{devin_id}/messages` — fallback.** The v3 session endpoint sometimes doesn't include conversation messages. When the session is settled and we have no `latest_message`, the client falls back to `/messages`, filters to Devin-authored entries, and pulls the latest. Without this, plan responses, clarification questions, and PR narratives wouldn't be retrievable.

`_adapt_session` in `devin_client.py` is the single seam between Devin's response shape and our internal contract. It also handles the v3 quirk where top-level `status="running"` while `status_detail="waiting_for_user"` actually means the session is awaiting a human — we map that onto our internal `awaiting_user` so plan-mode response detection just works. **Modes are an orchestrator concern, not a Devin concern** — same handful of API calls power both flows.

## "How do you prevent runaway costs?"

- `RATE_LIMIT_SESSIONS_PER_HOUR` (default 30) caps new sessions per repo over a rolling 60 minutes. Implementation in `_sessions_created_in_last_hour` + `_refuse_rate_limited`.
- Refused requests get a polite GitHub comment and **don't create a task row** — they don't pollute the dashboard or storage.
- Idempotency on `github_comment_id` plus the orchestrator lock means retried webhooks can't double-bill.
- Single active session per `(repo, issue)` — follow-ups forward into the existing session instead of creating new ones. Big multiplier on cost control for noisy issues.
- Mode-mismatch refusal prevents the failure mode where a confused user mixes intents and Devin gets stuck in an unresolvable session.
- `[bot]` auto-filter prevents the orchestrator's own status comments from looping back as new triggers.

## "Where can we extend?"

In priority order:

- **New modes** — `app/modes.py` registry. A 'review' mode that grades PRs is the obvious next one; same plumbing.
- **New trigger sources** — Slack `/devin`, Linear webhook, scheduled scanner — call `handle_comment_event` with the same args. The simulate endpoint is the proof.
- **Prompts in one file** (`app/prompts.py`). Per-mode session and follow-up prompts isolated and tunable.
- **State machine is one enum** (`TaskStatus`). Adding states like `needs_security_review` or `awaiting_approval` is additive.
- **Devin status mapping** lives in `refresh_task_from_devin` — extend to handle new Devin statuses.
- **Notification channels** — every state transition already calls `maybe_post_status_update`. Wrap that with a dispatcher to also fire Slack / Jira / Linear webhooks.
- **Approval gates** — drop a check in `_create_new_session` that holds in `pending` until a human approves via a dashboard endpoint. Schema and event timeline already support it.

## "Why event-sourced timeline?"

The question *"is Devin actually working?"* is unanswerable from a row. It's answerable from a stream. Every interaction — GitHub trigger, orchestrator reply, Devin response, dashboard follow-up, mode-switch refusal, rate-limit refusal — is an immutable row in `interaction_events`. The `RemediationTask` row is a denormalized projection for fast list views, but the source of truth is the events. Metrics are trivial; the timeline is auditable.

## "What about security?"

- HMAC-SHA256 verification of every GitHub webhook (`X-Hub-Signature-256`).
- Two-layer bot-loop protection: an automatic filter on any sender ending in `[bot]` (covers the GitHub App identity that posts our status comments), plus an optional `BOT_GITHUB_LOGIN` for legacy non-`[bot]` cases.
- GitHub App auth (recommended for production) — status comments authored by a bot identity, not impersonating a human. Installation tokens minted on demand and cached in-memory with pre-expiry refresh.
- No dashboard auth yet — explicit limitation, fix is OIDC. Documented as a known gap.

## "How is it tested?"

- **73 tests, ~1,960 lines of pytest across nine test files.** All external Devin and GitHub calls mocked, so the suite runs **with no credentials** — anyone can clone and `make test`.
- Coverage hits webhook parsing + `[bot]` filter, signature verification, idempotent session creation, follow-up forwarding, mode detection including article-guarded false-positive rejection (`test_modes.py`), the plan→implement *in-place transition* end-to-end (`test_plan_route.py`), the `pr_opened`-replan path with superseded-PR ledger and PR-side annotation, rate limiting, mode-mismatch refusal, the orchestrator-lock race scenarios (`test_hardening.py`), GitHub comment posting + dedupe-on-failure (transient post failures don't dedupe so the next poll retries), metrics, and the simulation endpoint.
- Built test-first — the test names read like a spec for the orchestrator's behavior.
