"""Mode registry — each mode defines how the orchestrator interprets and
responds to an `@devin` comment.

Adding a new mode is intentionally a single-file change:

1. Write a `detect()` function — return True for the comment phrasings that
   should activate this mode.
2. Add prompt builders for new sessions and follow-ups in `app/prompts.py`.
3. (Optionally) write a `response_ready()` function — return True when
   Devin's session state means we should post Devin's `latest_message` back
   on the GitHub issue. Return None on the Mode if the mode doesn't post
   the model's text directly (e.g. remediate mode posts PR/completion
   status comments, not Devin's prose).
4. Register the new `Mode` in `MODE_REGISTRY` *above* the catch-all
   `REMEDIATE` entry. `detect_mode()` returns the first match.

A mode then automatically picks up:

- Routing in `orchestrator.handle_comment_event` (single-funnel).
- Persistence (`task.mode` records which mode this task is running in).
- Mode-mismatch refusal when a follow-up would change modes mid-flight.
- Plan-style "post Devin's response back to the issue" semantics if
  `response_ready` is provided.

The registry is the single source of truth — there is no per-mode wiring
required in the orchestrator, the API, or the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.prompts import (
    build_plan_followup_prompt,
    build_plan_prompt,
    build_remediate_followup_prompt,
    build_remediate_prompt,
)


PromptBuilder = Callable[..., str]
ResponseReady = Callable[[dict, Any], bool]
ResponseFormatter = Callable[[str, Any], str]


@dataclass(frozen=True)
class Mode:
    key: str
    label: str
    detect: Callable[[str], bool]
    build_prompt: PromptBuilder
    build_followup_prompt: PromptBuilder
    # When non-None, the poller will call response_ready(snapshot, task) on
    # each Devin poll; if True and we haven't posted yet, the orchestrator
    # posts Devin's latest_message (run through format_response) on the
    # issue and marks the task completed.
    response_ready: Optional[ResponseReady] = None
    format_response: ResponseFormatter = field(
        default=lambda text, task: text,
    )
    # Status comment kind used to dedupe the response post (so the same plan
    # isn't posted twice if the poller fires repeatedly).
    response_event_type: str = "mode_response_posted"


# ---------------------------------------------------------------------------
# Plan mode
# ---------------------------------------------------------------------------


_PLAN_VERBS = {"plan", "planning", "replan", "rethink", "propose", "outline", "draft", "design"}

# Words that signal "the next noun is a noun, not a verb directive".
# Used to filter out false positives like "@devin we have a plan".
_PLAN_ARTICLES = {"a", "the", "my", "your", "our", "their", "its", "his", "her"}

_PLAN_PHRASE_MARKERS = (
    "plan a solution",
    "plan the fix",
    "propose a plan",
    "propose a solution",
    "outline an approach",
    "outline a plan",
    "outline a solution",
    "draft a plan",
    "what's your plan",
    "what is your plan",
    "think through",
    "without making changes",
    "without implementing",
    "do not implement",
    "don't implement",
    "just plan",
)


def _is_plan_request(body: str) -> bool:
    """Match comment phrasings that ask for planning rather than implementation.

    Scans the first ~8 tokens after `@devin` for a plan verb. To avoid
    false positives on noun usage ("a plan", "the plan", "my plan"), we
    skip plan verbs immediately preceded by an article. Catches:

      - "@devin plan a solution"
      - "@devin can you replan"
      - "@devin continue planning"
      - "@devin let's plan a different approach"

    But correctly rejects:

      - "@devin we have a plan, please remediate"
      - "@devin remediate this"
    """
    text = (body or "").lower()
    if "@devin" not in text:
        return False

    after = text.split("@devin", 1)[1]
    raw_tokens = [t.strip(",.;:!?\"'`()[]") for t in after.split()[:8]]
    tokens = [t for t in raw_tokens if t]

    for i, token in enumerate(tokens):
        if token in _PLAN_VERBS:
            prev = tokens[i - 1] if i > 0 else ""
            if prev in _PLAN_ARTICLES:
                # Noun usage like "a plan" / "the plan" — skip.
                continue
            return True

    return any(m in text for m in _PLAN_PHRASE_MARKERS)


_PLAN_READY_STATUSES = {
    "awaiting_user",
    "waiting_user",
    "needs_input",
    "completed",
    "finished",
    "succeeded",
    "blocked",
}


def _plan_response_ready(snapshot: dict, task: Any) -> bool:
    """Devin signals the plan is ready when the session moves to a settled
    state AND the latest message is substantive (not a one-line progress note).
    """
    status = (snapshot.get("status") or "").lower()
    if status not in _PLAN_READY_STATUSES:
        return False
    msg = snapshot.get("latest_message") or ""
    return len(msg) >= 60


def _plan_format_response(text: str, task: Any) -> str:
    return (
        "**Devin's proposed plan:**\n\n"
        + text.strip()
        + "\n\n---\n"
        "Reply `@devin go ahead` (or `@devin implement`) to have Devin "
        "execute this plan, or comment with refinements."
    )


PLAN = Mode(
    key="plan",
    label="Plan",
    detect=_is_plan_request,
    build_prompt=build_plan_prompt,
    build_followup_prompt=build_plan_followup_prompt,
    response_ready=_plan_response_ready,
    format_response=_plan_format_response,
    response_event_type="plan_posted",
)


# ---------------------------------------------------------------------------
# Remediate mode (default — always matches as the catch-all)
# ---------------------------------------------------------------------------


REMEDIATE = Mode(
    key="remediate",
    label="Remediate",
    detect=lambda body: True,
    build_prompt=build_remediate_prompt,
    build_followup_prompt=build_remediate_followup_prompt,
    response_ready=None,  # remediate posts PR/completion status events, not Devin's prose
)


# Order matters: detect_mode() returns the first match. Put more specific
# modes above the REMEDIATE catch-all.
MODE_REGISTRY: list[Mode] = [PLAN, REMEDIATE]


def detect_mode(comment_body: str | None) -> Mode:
    """Pick a mode from the *content* of a user comment. Used at task
    creation time to choose which prompt template to send Devin."""
    body = comment_body or ""
    for mode in MODE_REGISTRY:
        if mode.detect(body):
            return mode
    return REMEDIATE


def get_mode(key: str | None) -> Mode:
    if not key:
        return REMEDIATE
    for mode in MODE_REGISTRY:
        if mode.key == key:
            return mode
    return REMEDIATE


def mode_for_status(status: str | None) -> Mode:
    """Pick a mode from a task's *current phase*. Used at follow-up time
    so the orchestrator picks the right prompt without storing a redundant
    `mode` column on the task — the phase status already says whether
    Devin is in a plan-style or remediate-style conversation.
    """
    # Lazy import to avoid a circular dependency between modes ↔ models.
    from app.models import PLAN_PHASE_STATUSES

    if status in PLAN_PHASE_STATUSES:
        return PLAN
    return REMEDIATE
