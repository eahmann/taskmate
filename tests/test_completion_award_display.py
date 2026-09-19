"""Approval previews and completion history show saved awards after chore edits."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from custom_components.taskmate.models import Child, Chore, ChoreCompletion
from custom_components.taskmate.sensor import (
    PendingApprovalsSensor,
    _build_recent_completions,
    _build_todays_completions,
)

from .test_award_notification_atomicity import _run, _setup
from .test_completion_concurrency import _now


def _display_points(source, child, chore, completion):
    common = {
        "child_lookup": {child.id: child},
        "chore_lookup": {chore.id: chore},
        "all_completions": [completion],
    }
    if source == "today":
        with patch("custom_components.taskmate.sensor.dt_util.now", return_value=_now()):
            return _build_todays_completions(common)[0]["points"]
    if source == "recent":
        return _build_recent_completions(common)[0]["points"]
    coordinator = MagicMock()
    coordinator.data = {"pending_completions": [completion], "pending_reward_claims": []}
    coordinator.get_child = lambda child_id: child if child_id == child.id else None
    coordinator.get_chore = lambda chore_id: chore if chore_id == chore.id else None
    coordinator.mandatory_misses_state.return_value = []
    entry = MagicMock(entry_id="award-display")
    return PendingApprovalsSensor(coordinator, entry).extra_state_attributes["chore_completions"][0]["points"]


@pytest.mark.parametrize("source", ["today", "recent", "pending"])
@pytest.mark.parametrize("submitted,expected", [(6, 6), (0, 0), (None, 18)])
def test_pending_timer_preview_preserves_snapshot_or_legacy_fallback(source, submitted, expected):
    child = Child(name="Alice")
    # This timer paid 3/min at submission, then the parent edited it to 9/min.
    chore = Chore(name="Reading", points=50, task_type="timed", timed_rate_minutes=1, timed_rate_points=9)
    completion = ChoreCompletion(
        chore_id=chore.id,
        child_id=child.id,
        completed_at=_now(),
        timed_duration_seconds=120,
        submitted_points=submitted,
    )
    assert _display_points(source, child, chore, completion) == expected


@pytest.mark.parametrize("source", ["today", "recent", "pending"])
def test_pending_bonus_preview_preserves_award_after_subtask_removal(source):
    child = Child(name="Alice")
    chore = Chore(name="Dishes", points=50)
    completion = ChoreCompletion(
        chore_id=chore.id,
        child_id=child.id,
        completed_at=_now(),
        bonus_subtask_id="removed-bonus",
        submitted_points=7,
    )
    assert _display_points(source, child, chore, completion) == 7


@pytest.mark.parametrize("source", ["today", "recent"])
@pytest.mark.parametrize("submitted", [None, 6])
@pytest.mark.parametrize("awarded", [0, 25])
def test_approved_display_uses_actual_award_including_legacy_records(source, submitted, awarded):
    child = Child(name="Alice")
    chore = Chore(name="Reading", points=50, task_type="timed", timed_rate_minutes=1, timed_rate_points=9)
    completion = ChoreCompletion(
        chore_id=chore.id,
        child_id=child.id,
        completed_at=_now(),
        approved=True,
        timed_duration_seconds=120,
        submitted_points=submitted,
        points_awarded=awarded,
    )
    assert _display_points(source, child, chore, completion) == awarded


@pytest.mark.parametrize("source", ["today", "pending"])
def test_accepting_unchanged_review_preview_cannot_reprice_a_pending_timer(source):
    async def scenario():
        coord, storage, child, chore = await _setup(points=50)
        chore.task_type = "timed"
        chore.timed_rate_minutes = 1
        chore.timed_rate_points = 3
        storage.update_chore(chore)
        with patch("custom_components.taskmate.coord_timed.dt_util.now", return_value=_now() - timedelta(minutes=2)):
            await coord.async_start_timed_task(chore.id, child.id)
        await coord.async_stop_timed_task(chore.id, child.id)
        completion = storage.get_completions()[0]
        assert completion.submitted_points == 6

        chore.timed_rate_points = 9
        storage.update_chore(chore)
        preview = _display_points(source, child, chore, completion)
        assert preview == 6
        # Upstream's Review-and-award dialog submits its default as an explicit
        # override even when the parent accepts it without editing the input.
        await coord.async_approve_chore(completion.id, points=preview)
        assert storage.get_child(child.id).points == 6
        assert storage.get_completions()[0].points_awarded == 6

    _run(scenario)


def test_actual_parent_override_is_shown_in_completion_history():
    async def scenario():
        coord, storage, child, chore = await _setup(points=10)
        completion = await coord.async_complete_chore(chore.id, child.id)
        await coord.async_approve_chore(completion.id, points=25)
        saved = storage.get_completions()[0]
        assert saved.submitted_points == 10
        assert saved.points_awarded == 25
        for source in ("today", "recent"):
            assert _display_points(source, child, chore, saved) == 25

    _run(scenario)
