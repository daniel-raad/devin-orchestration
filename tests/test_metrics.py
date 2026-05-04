from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import InteractionEvent, RemediationTask, TaskStatus


def _seed(db_session):
    now = datetime.now(timezone.utc)

    # Active session, awaiting devin.
    t1 = RemediationTask(
        repo_full_name="o/r",
        issue_number=1,
        issue_title="i1",
        issue_url="u1",
        status=TaskStatus.REMEDIATING.value,
        devin_session_id="s1",
        devin_session_url="https://app.devin.ai/sessions/s1",
        requested_by="u",
        created_at=now - timedelta(minutes=20),
        updated_at=now - timedelta(minutes=10),
    )
    # Awaiting user.
    t2 = RemediationTask(
        repo_full_name="o/r",
        issue_number=2,
        issue_title="i2",
        issue_url="u2",
        status=TaskStatus.AWAITING_USER.value,
        devin_session_id="s2",
        devin_session_url="https://app.devin.ai/sessions/s2",
        requested_by="u",
        created_at=now - timedelta(minutes=15),
        updated_at=now - timedelta(minutes=5),
    )
    # PR opened, time_to_pr_seconds=600 (10 min).
    t3 = RemediationTask(
        repo_full_name="o/r",
        issue_number=3,
        issue_title="i3",
        issue_url="u3",
        status=TaskStatus.PR_OPENED.value,
        devin_session_id="s3",
        devin_session_url="https://app.devin.ai/sessions/s3",
        pr_url="https://github.com/o/r/pull/3",
        requested_by="u",
        created_at=now - timedelta(minutes=30),
        updated_at=now - timedelta(minutes=20),
        time_to_pr_seconds=600,
    )
    # Completed: time_to_pr=1200, time_to_completion=2400.
    t4 = RemediationTask(
        repo_full_name="o/r",
        issue_number=4,
        issue_title="i4",
        issue_url="u4",
        status=TaskStatus.DONE.value,
        devin_session_id="s4",
        devin_session_url="https://app.devin.ai/sessions/s4",
        pr_url="https://github.com/o/r/pull/4",
        requested_by="u",
        created_at=now - timedelta(hours=1),
        updated_at=now,
        time_to_pr_seconds=1200,
        time_to_completion_seconds=2400,
    )
    # Failed.
    t5 = RemediationTask(
        repo_full_name="o/r",
        issue_number=5,
        issue_title="i5",
        issue_url="u5",
        status=TaskStatus.FAILED.value,
        requested_by="u",
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=1),
        error="upstream",
    )
    db_session.add_all([t1, t2, t3, t4, t5])
    db_session.flush()

    # Mentions: 6 user_instruction events spread across tasks.
    for tid in [t1.id, t1.id, t2.id, t3.id, t4.id, t5.id]:
        db_session.add(
            InteractionEvent(
                task_id=tid,
                source="github",
                event_type="user_instruction",
                body="@devin do thing",
            )
        )
    db_session.commit()


def test_metrics_endpoint_returns_expected_counts(client, db_session):
    _seed(db_session)

    res = client.get("/api/metrics")
    assert res.status_code == 200
    m = res.json()

    assert m["total_devin_mentions"] == 6
    assert m["total_sessions"] == 5
    assert m["active_sessions"] == 3  # awaiting_devin, awaiting_user, pr_opened
    assert m["awaiting_user"] == 1
    assert m["prs_opened"] == 2  # t3 (pr_opened) + t4 (completed has pr_url)
    assert m["completed_tasks"] == 1
    assert m["failed_tasks"] == 1

    # Average time-to-pr across t3 (600) and t4 (1200) = 900s = 15 min.
    assert abs(m["average_time_to_pr_minutes"] - 15.0) < 0.1
    # Avg time-to-completion across t4 only (2400s = 40 min).
    assert abs(m["average_time_to_completion_minutes"] - 40.0) < 0.1


def test_metrics_handles_empty_db(client):
    res = client.get("/api/metrics")
    assert res.status_code == 200
    m = res.json()
    assert m["total_devin_mentions"] == 0
    assert m["total_sessions"] == 0
    assert m["active_sessions"] == 0
    assert m["prs_opened"] == 0
    assert m["completed_tasks"] == 0
    assert m["failed_tasks"] == 0
    assert m["average_time_to_pr_minutes"] is None
    assert m["average_time_to_completion_minutes"] is None
