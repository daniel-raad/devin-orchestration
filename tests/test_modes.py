"""Tests for the mode registry — detection, lookup, and the Mode contract."""

from __future__ import annotations

import pytest

from app.modes import MODE_REGISTRY, PLAN, REMEDIATE, detect_mode, get_mode


@pytest.mark.parametrize(
    "body",
    [
        "@devin plan a solution to this",
        "@devin can you plan a fix",
        "@devin could you plan an approach",
        "@devin please plan it out",
        "@devin propose a plan",
        "@devin propose a solution please",
        "@devin outline an approach",
        "@devin outline a plan",
        "@devin draft a plan",
        "@devin design an approach",
        "@devin think through this without making changes",
        "@devin what's your plan",
        "@devin do not implement, just plan",
        # Plan verb appearing not in first position (continuation phrasing):
        "@devin continue planning please",
        "@devin let's plan a different approach",
        "@devin keep planning, but reject backslashes",
        "@devin replan, the previous approach was wrong",
    ],
)
def test_detect_mode_picks_plan(body):
    assert detect_mode(body).key == "plan"


@pytest.mark.parametrize(
    "body",
    [
        # Noun usage with article — must NOT be classified as plan.
        "@devin we have a plan, please remediate",
        "@devin the plan is documented elsewhere; just go fix it",
        "@devin my plan worked; now implement it",
    ],
)
def test_detect_mode_rejects_noun_usage_with_article(body):
    assert detect_mode(body).key == "remediate"


@pytest.mark.parametrize(
    "body",
    [
        "@devin please remediate this",
        "@devin go ahead",
        "@devin implement the fix",
        "@devin",
        "@devin can you fix this bug",
        "@devin retry",
    ],
)
def test_detect_mode_falls_through_to_remediate(body):
    assert detect_mode(body).key == "remediate"


def test_detect_mode_requires_devin_mention_for_plan():
    # Plan keywords without @devin shouldn't match plan mode.
    # (We default to remediate, but only @devin mentions reach detect_mode
    # in real flows — this is just a guard against misroute.)
    assert detect_mode("plan a solution please").key == "remediate"


def test_get_mode_unknown_key_falls_back_to_remediate():
    assert get_mode(None).key == "remediate"
    assert get_mode("").key == "remediate"
    assert get_mode("nonsense").key == "remediate"


def test_get_mode_returns_registered_modes():
    assert get_mode("plan").key == "plan"
    assert get_mode("remediate").key == "remediate"


def test_remediate_is_catch_all_at_end_of_registry():
    # Catch-all must be last so plan / future modes get matched first.
    assert MODE_REGISTRY[-1] is REMEDIATE
    assert PLAN in MODE_REGISTRY


def test_plan_mode_response_ready_requires_settled_status_and_substantive_message():
    snap_running = {"status": "running", "latest_message": "x" * 500}
    snap_settled_short = {"status": "completed", "latest_message": "ok"}
    snap_settled_full = {
        "status": "awaiting_user",
        "latest_message": "## Issue summary\n" + "x" * 200,
    }
    assert PLAN.response_ready(snap_running, None) is False
    assert PLAN.response_ready(snap_settled_short, None) is False
    assert PLAN.response_ready(snap_settled_full, None) is True


def test_plan_mode_format_response_wraps_devin_text():
    out = PLAN.format_response("# Plan\n\nDo X then Y.", None)
    assert "Devin's proposed plan" in out
    assert "Do X then Y." in out
    assert "@devin go ahead" in out  # continuation hint


def test_remediate_mode_does_not_post_devin_prose_directly():
    assert REMEDIATE.response_ready is None
