from __future__ import annotations

import asyncio
import logging
from typing import Callable

from app import orchestrator
from app.models import ACTIVE_STATUSES, RemediationTask

log = logging.getLogger(__name__)


def _refresh_one_in_thread(
    db_factory: Callable, devin, gh, task_id: int
) -> None:
    """Open a fresh DB session, re-fetch the task, reconcile from Devin.

    Each polled task uses its own session so they don't share SQLAlchemy
    state across threads.
    """
    db = db_factory()
    try:
        task = db.get(RemediationTask, task_id)
        if task is None:
            return
        if task.status not in ACTIVE_STATUSES:
            return
        if not task.devin_session_id:
            return
        orchestrator.refresh_task_from_devin(db=db, devin=devin, gh=gh, task=task)
    except Exception:
        log.exception("poll error for task %s", task_id)
    finally:
        db.close()


async def poll_once(db_factory: Callable, devin, gh, *, concurrency: int = 8) -> int:
    """Reconcile all active tasks against Devin in parallel (bounded).

    Returns the number of tasks polled.
    """
    db = db_factory()
    try:
        active = (
            db.query(RemediationTask.id)
            .filter(RemediationTask.status.in_(list(ACTIVE_STATUSES)))
            .filter(RemediationTask.devin_session_id.isnot(None))
            .all()
        )
        task_ids = [row[0] for row in active]
    finally:
        db.close()

    if not task_ids:
        return 0

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _bounded(task_id: int) -> None:
        async with sem:
            await asyncio.to_thread(
                _refresh_one_in_thread, db_factory, devin, gh, task_id
            )

    await asyncio.gather(*(_bounded(tid) for tid in task_ids))
    return len(task_ids)


async def poll_forever(app, interval_seconds: int) -> None:
    concurrency = getattr(app.state.settings, "poller_concurrency", 8)
    while True:
        try:
            await poll_once(
                db_factory=app.state.db.session,
                devin=app.state.devin_client,
                gh=app.state.github_client,
                concurrency=concurrency,
            )
        except Exception:
            log.exception("poller iteration failed")
        await asyncio.sleep(interval_seconds)
