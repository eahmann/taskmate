"""Notification waits/failures must never split an award from its bookkeeping."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.taskmate.models import BonusSubTask, Challenge, Quest

from .test_completion_concurrency import _make_system, _now


def _run(scenario):
    with patch("custom_components.taskmate.coord_points.dt_util.now", return_value=_now()):
        asyncio.run(scenario())


async def _setup(*, requires_approval=True, points=100):
    coord, storage = await _make_system()
    storage.set_setting("weekend_multiplier", "1")
    storage.set_setting("streak_milestones_enabled", "false")
    child = await coord.async_add_child("Alice")
    chore = await coord.async_add_chore(
        "Dishes", points=points, requires_approval=requires_approval, assigned_to=[child.id]
    )
    return coord, storage, child, chore


def _pause_first_level_notification(coord):
    entered, release = asyncio.Event(), asyncio.Event()

    async def notify(kind, _context):
        if kind == "level_up" and not entered.is_set():
            entered.set()
            await release.wait()

    coord.notifications.fire.side_effect = notify
    return entered, release


async def _wait_for_notification(entered):
    # A broken path must fail promptly instead of hanging the test runner.
    await asyncio.wait_for(entered.wait(), timeout=2)


@pytest.mark.parametrize("action", ["async_reject_chore", "async_undo_chore_approval"])
@pytest.mark.parametrize("requires_approval", [False, True])
def test_reversal_during_award_notification_cannot_restore_undone_points(action, requires_approval):
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=requires_approval)
        entered, release = _pause_first_level_notification(coord)
        if requires_approval:
            completion = await coord.async_complete_chore(chore.id, child.id)
            award = asyncio.create_task(coord.async_approve_chore(completion.id))
        else:
            award = asyncio.create_task(coord.async_complete_chore(chore.id, child.id))
        await _wait_for_notification(entered)
        saved = storage.get_completions()[0]
        assert saved.approved and saved.points_awarded == 100
        assert storage.get_child(child.id).points == 100

        await getattr(coord, action)(saved.id)
        release.set()
        await award

        after = storage.get_child(child.id)
        assert after.points == 0
        assert after.total_points_earned == 0
        assert after.total_chores_completed == 0
        assert after.career_score == 0
        if action == "async_reject_chore":
            assert storage.get_completions() == []
            assert not storage.get_last_completed(chore.id, child.id)
        else:
            assert len(storage.get_pending_completions()) == 1

    _run(scenario)


def test_two_different_approvals_during_level_notification_keep_both_awards():
    async def scenario():
        coord, storage, child, first = await _setup()
        second = await coord.async_add_chore("Tidy", points=25, requires_approval=True, assigned_to=[child.id])
        a = await coord.async_complete_chore(first.id, child.id)
        b = await coord.async_complete_chore(second.id, child.id)
        entered, release = _pause_first_level_notification(coord)
        award = asyncio.create_task(coord.async_approve_chore(a.id))
        await _wait_for_notification(entered)

        await coord.async_approve_chore(b.id)
        release.set()
        await award

        after = storage.get_child(child.id)
        assert after.points == 125
        assert after.total_points_earned == 125
        assert after.total_chores_completed == 2
        assert sum(c.points_awarded for c in storage.get_completions()) == 125

    _run(scenario)


@pytest.mark.parametrize("requires_approval", [False, True])
@pytest.mark.parametrize("failure", ["level_up", "streak_milestone", "clear_approval"])
def test_notification_failure_does_not_lose_award_or_allow_retry_payout(requires_approval, failure):
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=requires_approval)
        if failure == "streak_milestone":
            storage.set_setting("streak_milestones_enabled", "true")
            storage.set_setting("streak_milestones", "1:5")

        async def fail_notification(kind, _context):
            if kind == failure:
                raise RuntimeError("Notification service unavailable")

        coord.notifications.fire.side_effect = fail_notification
        if failure == "clear_approval":
            coord.notifications.clear_approval.side_effect = RuntimeError("Notification service unavailable")
        completion = await coord.async_complete_chore(chore.id, child.id)
        if requires_approval:
            await coord.async_approve_chore(completion.id)
        await coord.async_approve_chore(completion.id)
        assert await coord.async_complete_chore(chore.id, child.id) is None

        after = storage.get_child(child.id)
        assert after.points == (105 if failure == "streak_milestone" else 100)
        assert after.total_chores_completed == 1
        assert len(storage.get_completions()) == 1
        assert storage.get_completions()[0].points_awarded == 100

    _run(scenario)


def test_manual_credits_during_notification_keep_both_transactions_and_balances():
    async def scenario():
        coord, storage, child, _ = await _setup()
        entered, release = _pause_first_level_notification(coord)
        award = asyncio.create_task(coord.async_add_points(child.id, 100, "First credit"))
        await _wait_for_notification(entered)
        await coord.async_add_points(child.id, 7, "Second credit")
        release.set()
        await award

        after = storage.get_child(child.id)
        assert after.points == 107
        assert after.total_points_earned == 107
        assert after.career_score == 107
        assert [t.points for t in storage.get_points_transactions()] == [100, 7]

    _run(scenario)


def test_manual_credit_is_committed_when_level_notification_fails():
    async def scenario():
        coord, storage, child, _ = await _setup()
        coord.notifications.fire.side_effect = RuntimeError("Notification service unavailable")
        await coord.async_add_points(child.id, 100, "Credit")
        assert storage.get_child(child.id).points == 100
        assert storage.get_child(child.id).level == 2
        assert [t.points for t in storage.get_points_transactions()] == [100]

    _run(scenario)


def test_bonus_reversal_during_notification_reverses_the_whole_chore():
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=False, points=10)
        chore.bonus_subtasks = [BonusSubTask(id="bonus", name="Dry dishes", points=100)]
        storage.update_chore(chore)
        parent = await coord.async_complete_chore(chore.id, child.id)
        entered, release = _pause_first_level_notification(coord)
        award = asyncio.create_task(coord.async_complete_bonus_subtask(chore.id, "bonus", child.id))
        await _wait_for_notification(entered)
        await coord.async_reject_chore(parent.id)
        release.set()
        await award
        assert storage.get_completions() == []
        assert storage.get_child(child.id).points == 0
        assert storage.get_child(child.id).total_chores_completed == 0

    _run(scenario)


def test_timed_stop_during_notification_cannot_pay_the_same_session_twice():
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=False)
        chore.task_type = "timed"
        chore.timed_rate_minutes = 1
        chore.timed_rate_points = 100
        storage.update_chore(chore)
        with patch("custom_components.taskmate.coord_timed.dt_util.now", return_value=_now() - timedelta(minutes=1)):
            await coord.async_start_timed_task(chore.id, child.id)
        entered, release = _pause_first_level_notification(coord)
        award = asyncio.create_task(coord.async_stop_timed_task(chore.id, child.id))
        await _wait_for_notification(entered)
        with pytest.raises(ValueError, match="No active timer"):
            await coord.async_stop_timed_task(chore.id, child.id)
        release.set()
        await award
        assert len(storage.get_completions()) == 1
        assert storage.get_child(child.id).points == 100

    _run(scenario)


@pytest.mark.parametrize("kind", ["quest", "challenge"])
def test_progress_bonus_notification_cannot_overwrite_a_concurrent_manual_credit(kind):
    async def scenario():
        coord, storage, child, chore = await _setup()
        if kind == "quest":
            storage.add_quest(Quest(name="Clean up", steps=[chore.id], bonus_points=100))
            progress = coord._async_advance_quests(child.id, chore.id)
        else:
            storage.add_challenge(Challenge(name="One chore", target=1, bonus_points=100))
            coord._metric_value = lambda *_args: 1
            progress = coord._async_evaluate_challenges(child.id)
        entered, release = _pause_first_level_notification(coord)
        award = asyncio.create_task(progress)
        await _wait_for_notification(entered)
        await coord.async_add_points(child.id, 7, "Concurrent credit")
        release.set()
        await award
        assert storage.get_child(child.id).points == 107
        assert storage.get_child(child.id).total_points_earned == 107

    _run(scenario)


@pytest.mark.parametrize("boundary", ["refresh", "badge"])
@pytest.mark.parametrize("requires_approval", [False, True])
def test_undo_at_first_refresh_or_badge_check_reverses_quest_progress(boundary, requires_approval):
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=requires_approval, points=10)
        quest = Quest(name="Clean up", steps=[chore.id], bonus_points=25)
        storage.add_quest(quest)
        if requires_approval:
            completion = await coord.async_complete_chore(chore.id, child.id)
        reversed_once = False

        async def undo_at_boundary(*_args):
            nonlocal reversed_once
            if not reversed_once:
                reversed_once = True
                saved = storage.get_completions()[0]
                await coord.async_undo_chore_approval(saved.id)

        if boundary == "refresh":
            coord.async_refresh.side_effect = undo_at_boundary
        else:
            coord.badges = AsyncMock()
            coord.badges.evaluate_for_child.side_effect = undo_at_boundary
        if requires_approval:
            await coord.async_approve_chore(completion.id)
        else:
            await coord.async_complete_chore(chore.id, child.id)

        assert storage.get_child(child.id).points == 0
        assert storage.get_child(child.id).total_points_earned == 0
        assert len(storage.get_pending_completions()) == 1
        progress = storage.get_quest_child_progress(quest.id, child.id)
        assert progress["step"] == 0
        assert progress["completed_count"] == 0

    _run(scenario)
