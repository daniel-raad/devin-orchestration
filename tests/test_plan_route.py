"""End-to-end tests for the plan route through the orchestrator."""

from __future__ import annotations

from app.models import InteractionEvent, RemediationTask, TaskStatus
from app.orchestrator import refresh_task_from_devin
from tests.conftest import make_issue_comment_payload


def _post(client, payload):
    return client.post(
        "/webhooks/github",
        json=payload,
        headers={"X-GitHub-Event": "issue_comment"},
    )


def test_plan_request_creates_plan_task_with_plan_prompt(client, db_session, mock_devin):
    payload = make_issue_comment_payload(body="@devin can you plan a solution to this")
    res = _post(client, payload)
    assert res.status_code == 200
    assert res.json()["action"] == "session_created"

    task = db_session.query(RemediationTask).first()
    assert task is not None
    assert task.status == TaskStatus.PLANNING.value

    mock_devin.create_session.assert_called_once()
    prompt = mock_devin.create_session.call_args.kwargs["prompt"]
    # Plan prompt explicitly forbids code changes / PRs.
    assert "Do NOT" in prompt
    assert "PR" in prompt or "pull request" in prompt.lower()
    assert "## Issue summary" in prompt or "Issue summary" in prompt


def test_remediate_request_uses_remediate_prompt(client, mock_devin):
    payload = make_issue_comment_payload(body="@devin please remediate this")
    _post(client, payload)
    prompt = mock_devin.create_session.call_args.kwargs["prompt"]
    # Remediate prompt does NOT contain the plan-specific anti-PR language.
    assert "Open a focused PR" in prompt
    assert "Do NOT modify any files" not in prompt


def test_session_started_comment_differs_for_plan_mode(client, mock_github):
    _post(client, make_issue_comment_payload(body="@devin plan a fix"))
    bodies = [
        c.kwargs.get("body") or ""
        for c in mock_github.post_issue_comment.call_args_list
    ]
    assert any("drafting a plan" in b for b in bodies)
    assert not any("remediation session started" in b for b in bodies)


def test_active_remediation_refuses_plan_request(client, db_session, mock_devin, mock_github):
    # First comment kicks off a remediation.
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin remediate this"))
    assert db_session.query(RemediationTask).first().status == TaskStatus.REMEDIATING.value
    mock_devin.send_message.reset_mock()

    # Second comment asks for a plan — must be refused.
    res = _post(
        client,
        make_issue_comment_payload(comment_id=2, body="@devin can you plan a different approach"),
    )
    assert res.json()["action"] == "mode_switch_refused"
    assert res.json()["existing_mode"] == "remediate"
    assert res.json()["requested_mode"] == "plan"

    # Devin must NOT have received a forwarded message.
    mock_devin.send_message.assert_not_called()

    # The refusal comment should be on the issue.
    bodies = [
        c.kwargs.get("body") or ""
        for c in mock_github.post_issue_comment.call_args_list
    ]
    assert any("Remediate" in b and "Plan" in b for b in bodies)


def test_active_plan_remediate_request_transitions_in_place(client, db_session, mock_devin):
    """An active plan task with a `remediate` directive (or a continuation
    phrase like 'go ahead') TRANSITIONS the same task into remediating —
    no second task row is created. This is the user's mental model:
    plan → remediate is one effort, two phases."""
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin plan a solution"))
    plan_task = db_session.query(RemediationTask).first()
    assert plan_task.status == "planning"

    res = _post(
        client,
        make_issue_comment_payload(comment_id=2, body="@devin nevermind, just remediate"),
    )
    assert res.json()["action"] == "phase_transition"
    assert res.json()["from"] == "planning"
    assert res.json()["to"] == "remediating"

    # Same task — no second row created.
    tasks = db_session.query(RemediationTask).all()
    assert len(tasks) == 1
    db_session.refresh(tasks[0])
    assert tasks[0].status == "remediating"
    # And Devin got a "now implement" handoff message.
    mock_devin.send_message.assert_called_once()
    msg = mock_devin.send_message.call_args.kwargs.get("message", "")
    assert "implement" in msg.lower()


def test_continue_planning_iterates_does_not_transition(client, db_session, mock_devin):
    """`@devin continue planning ...` on a plan-phase task is plan-mode
    iteration, NOT a transition to remediation — even though "continue"
    is also a retry/continuation phrase. Plan-mode wins when the comment
    explicitly contains a plan verb."""
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin plan a solution"))
    plan_task = db_session.query(RemediationTask).first()
    plan_task.status = TaskStatus.PLAN_POSTED.value
    db_session.commit()

    mock_devin.create_session.reset_mock()
    mock_devin.send_message.reset_mock()

    res = _post(
        client,
        make_issue_comment_payload(
            comment_id=2,
            body="@devin continue planning please, also reject backslashes",
        ),
    )
    assert res.json()["action"] == "followup_forwarded"

    db_session.refresh(plan_task)
    assert plan_task.status == TaskStatus.PLAN_POSTED.value  # still plan_posted
    mock_devin.send_message.assert_called_once()


def test_same_mode_followup_is_forwarded(client, db_session, mock_devin):
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin plan a solution"))
    res = _post(
        client,
        make_issue_comment_payload(comment_id=2, body="@devin can you also plan an alternative"),
    )
    assert res.json()["action"] == "followup_forwarded"
    mock_devin.send_message.assert_called_once()


def test_plan_posted_plus_go_ahead_transitions_in_place(client, db_session, mock_devin):
    """The canonical happy path: Devin posts a plan, the task moves to
    plan_posted (still active), then user says `@devin go ahead`. Same task
    transitions to remediating — no new task row."""
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin plan a solution"))
    plan_task = db_session.query(RemediationTask).first()
    plan_task.status = TaskStatus.PLAN_POSTED.value
    db_session.commit()

    mock_devin.create_session.reset_mock()
    mock_devin.send_message.reset_mock()

    res = _post(
        client,
        make_issue_comment_payload(comment_id=2, body="@devin go ahead and implement this"),
    )
    assert res.json()["action"] == "phase_transition"
    assert res.json()["from"] == "plan_posted"
    assert res.json()["to"] == "remediating"

    tasks = db_session.query(RemediationTask).order_by(RemediationTask.id).all()
    assert len(tasks) == 1, "go-ahead must not spawn a second task"
    db_session.refresh(tasks[0])
    assert tasks[0].status == "remediating"

    # Devin received the handoff message; no fresh session was created.
    mock_devin.create_session.assert_not_called()
    mock_devin.send_message.assert_called_once()


def test_completed_remediate_plus_plan_keyword_creates_plan_task(client, db_session, mock_devin):
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin remediate this"))
    rem_task = db_session.query(RemediationTask).first()
    rem_task.status = TaskStatus.DONE.value
    db_session.commit()

    mock_devin.create_session.reset_mock()
    mock_devin.create_session.return_value = {
        "session_id": "devin-session-2",
        "session_url": "https://app.devin.ai/sessions/devin-session-2",
        "status": "running",
        "pr_url": None,
        "latest_message": None,
        "error": None,
    }

    res = _post(
        client,
        make_issue_comment_payload(comment_id=2, body="@devin can you plan a different approach"),
    )
    assert res.json()["action"] == "session_created"
    tasks = db_session.query(RemediationTask).order_by(RemediationTask.id).all()
    assert len(tasks) == 2
    assert tasks[1].status == TaskStatus.PLANNING.value


def test_poller_does_not_post_plan_until_devin_signals_done(
    client, db_session, mock_devin, mock_github, app
):
    _post(client, make_issue_comment_payload(body="@devin plan a fix"))
    task = db_session.query(RemediationTask).first()
    mock_github.post_issue_comment.reset_mock()

    # Devin still working — must not post the plan.
    mock_devin.get_session.return_value = {
        "session_id": "devin-session-1",
        "session_url": "https://app.devin.ai/sessions/devin-session-1",
        "status": "running",
        "pr_url": None,
        "latest_message": "Reading the codebase...",
        "error": None,
    }

    refresh_task_from_devin(
        db=db_session,
        devin=mock_devin,
        gh=app.state.github_client,
        task=task,
    )

    bodies = [
        c.kwargs.get("body") or ""
        for c in mock_github.post_issue_comment.call_args_list
    ]
    assert not any("Devin's proposed plan" in b for b in bodies)
    db_session.refresh(task)
    assert task.status != TaskStatus.PLAN_POSTED.value


def test_poller_posts_plan_when_devin_signals_done(
    client, db_session, mock_devin, mock_github, app
):
    _post(client, make_issue_comment_payload(body="@devin plan a fix"))
    task = db_session.query(RemediationTask).first()
    mock_github.post_issue_comment.reset_mock()

    plan_text = (
        "## Issue summary\n"
        "The thing is broken.\n\n"
        "## Root cause hypothesis\n"
        "Probably the input validator.\n\n"
        "## Proposed approach\n"
        "Add a length check before slicing.\n\n"
        "## Files / areas likely to change\n"
        "src/validator.py\n\n"
        "## Risks and unknowns\n"
        "Might break edge case X.\n\n"
        "## Next steps\n"
        "Comment `@devin go ahead` to implement.\n"
    )
    mock_devin.get_session.return_value = {
        "session_id": "devin-session-1",
        "session_url": "https://app.devin.ai/sessions/devin-session-1",
        "status": "awaiting_user",
        "pr_url": None,
        "latest_message": plan_text,
        "error": None,
    }

    refresh_task_from_devin(
        db=db_session,
        devin=mock_devin,
        gh=app.state.github_client,
        task=task,
    )

    bodies = [
        c.kwargs.get("body") or ""
        for c in mock_github.post_issue_comment.call_args_list
    ]
    assert any("Devin's proposed plan" in b for b in bodies)
    assert any("Add a length check before slicing." in b for b in bodies)

    db_session.refresh(task)
    # Plan tasks settle into plan_posted (still active) — they only become
    # terminal when the user converts to remediation, abandons, or the
    # issue is closed.
    assert task.status == TaskStatus.PLAN_POSTED.value


def test_plan_post_only_fires_once(
    client, db_session, mock_devin, mock_github, app
):
    _post(client, make_issue_comment_payload(body="@devin plan a fix"))
    task = db_session.query(RemediationTask).first()

    plan_text = "## Issue summary\n" + "x" * 200
    mock_devin.get_session.return_value = {
        "session_id": "devin-session-1",
        "session_url": "https://app.devin.ai/sessions/devin-session-1",
        "status": "awaiting_user",
        "pr_url": None,
        "latest_message": plan_text,
        "error": None,
    }

    # First poll posts the plan.
    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )
    first_count = sum(
        1
        for c in mock_github.post_issue_comment.call_args_list
        if "Devin's proposed plan" in (c.kwargs.get("body") or "")
    )
    # Second poll must not duplicate.
    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )
    second_count = sum(
        1
        for c in mock_github.post_issue_comment.call_args_list
        if "Devin's proposed plan" in (c.kwargs.get("body") or "")
    )

    assert first_count == 1
    assert second_count == 1


def test_pr_opened_replan_records_and_annotates_superseded_pr(
    client, db_session, mock_devin, mock_github
):
    """When a PR-bearing task is replanned, the orchestrator must:
       (a) append the superseded pr_url to the task's previous_pr_urls list,
       (b) post a 'superseded' comment on the PR itself.
    """
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin remediate this"))
    rem_task = db_session.query(RemediationTask).first()
    rem_task.status = TaskStatus.PR_OPENED.value
    rem_task.pr_url = "https://github.com/test-org/test-repo/pull/9"
    db_session.commit()
    task_id = rem_task.id

    mock_github.post_issue_comment.reset_mock()

    _post(
        client,
        make_issue_comment_payload(
            comment_id=2,
            body="@devin can you replan, this approach was wrong",
        ),
    )

    db_session.refresh(rem_task)
    # (a) ledger updated
    assert rem_task.previous_pr_urls is not None
    assert "https://github.com/test-org/test-repo/pull/9" in rem_task.previous_pr_urls
    assert rem_task.pr_url is None  # cleared

    # (b) superseded comment posted on PR #9 specifically
    pr_comments = [
        c for c in mock_github.post_issue_comment.call_args_list
        if c.kwargs.get("issue_number") == 9
        and "superseded" in (c.kwargs.get("body") or "").lower()
    ]
    assert len(pr_comments) == 1, "exactly one superseded notice on the PR"


def test_pr_opened_replan_request_transitions_in_place(
    client, db_session, mock_devin
):
    """A replan request on a `pr_opened` task transitions the SAME task
    back to `planning` (not a sibling row). Devin gets a `revise the
    approach` message in the existing session; the previous pr_url is
    cleared from the task (kept in interaction-events history) so the
    lifecycle indicator reflects "back in planning"."""
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin remediate this"))
    rem_task = db_session.query(RemediationTask).first()
    rem_task.status = TaskStatus.PR_OPENED.value
    rem_task.pr_url = "https://github.com/test-org/test-repo/pull/9"
    db_session.commit()
    task_id = rem_task.id

    mock_devin.create_session.reset_mock()
    mock_devin.send_message.reset_mock()

    res = _post(
        client,
        make_issue_comment_payload(
            comment_id=2,
            body="@devin can you replan, this approach was wrong",
        ),
    )
    assert res.json()["action"] == "phase_transition"
    assert res.json()["from"] == "pr_opened"
    assert res.json()["to"] == "planning"
    assert res.json()["previous_pr"] == "https://github.com/test-org/test-repo/pull/9"

    tasks = db_session.query(RemediationTask).all()
    assert len(tasks) == 1, "replan must not spawn a sibling task"

    db_session.refresh(tasks[0])
    assert tasks[0].id == task_id  # same task row mutated in place
    assert tasks[0].status == TaskStatus.PLANNING.value
    assert tasks[0].pr_url is None  # cleared (lifecycle goes back to planning)

    # No fresh Devin session — we reuse the existing one with a revise message.
    mock_devin.create_session.assert_not_called()
    mock_devin.send_message.assert_called_once()
    sent_message = mock_devin.send_message.call_args.kwargs.get("message", "")
    assert "previous pr" in sent_message.lower() or "supersed" in sent_message.lower()


def test_pr_opened_same_mode_followup_still_forwards(client, db_session, mock_devin):
    """A same-mode follow-up on a pr_opened task still flows into the Devin
    session — useful for "also rename this test in the PR" type refinements."""
    _post(client, make_issue_comment_payload(comment_id=1, body="@devin remediate this"))
    rem_task = db_session.query(RemediationTask).first()
    rem_task.status = TaskStatus.PR_OPENED.value
    rem_task.pr_url = "https://github.com/test-org/test-repo/pull/9"
    db_session.commit()

    mock_devin.create_session.reset_mock()
    mock_devin.send_message.reset_mock()

    res = _post(
        client,
        make_issue_comment_payload(
            comment_id=2,
            body="@devin can you also rename the test in the PR",
        ),
    )
    assert res.json()["action"] == "followup_forwarded"
    mock_devin.create_session.assert_not_called()
    mock_devin.send_message.assert_called_once()

    tasks = db_session.query(RemediationTask).all()
    assert len(tasks) == 1


def test_replan_keyword_alone_triggers_plan_mode():
    from app.modes import detect_mode
    assert detect_mode("@devin replan").key == "plan"
    assert detect_mode("@devin can you replan this").key == "plan"
    assert detect_mode("@devin rethink the approach").key == "plan"


def test_plan_task_does_not_emit_clarification_comment(
    client, db_session, mock_devin, mock_github, app
):
    """`awaiting_user` on a plan task = plan is ready. We should NOT also
    emit the remediate-style `Devin needs clarification` comment.
    """
    _post(client, make_issue_comment_payload(body="@devin plan a fix"))
    task = db_session.query(RemediationTask).first()
    mock_github.post_issue_comment.reset_mock()

    mock_devin.get_session.return_value = {
        "session_id": "devin-session-1",
        "session_url": "https://app.devin.ai/sessions/devin-session-1",
        "status": "awaiting_user",
        "pr_url": None,
        "latest_message": "## Issue summary\n" + "x" * 300,
        "error": None,
    }
    refresh_task_from_devin(
        db=db_session, devin=mock_devin, gh=app.state.github_client, task=task
    )

    bodies = [
        c.kwargs.get("body") or ""
        for c in mock_github.post_issue_comment.call_args_list
    ]
    assert not any("Devin needs clarification" in b for b in bodies)
