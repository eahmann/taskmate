"""Checklist steps pay separately; the parent completes once all are approved."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.taskmate.models import BonusSubTask, Chore, ChoreCompletion, Quest, Reward

from .test_completion_concurrency import _make_system, _now


def _run(scenario):
    with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=_now()):
        asyncio.run(scenario())


async def _setup(*, requires_approval=False, points=10, step_points=(2, 3), sequential=False):
    coord, storage = await _make_system()
    storage.set_setting("weekend_multiplier", "1")
    storage.set_setting("streak_milestones_enabled", "false")
    child = await coord.async_add_child("Alice")
    chore = await coord.async_add_chore(
        "Morning checklist", points=points, requires_approval=requires_approval, assigned_to=[child.id]
    )
    chore.task_type = "checklist"
    chore.checklist_sequential = sequential
    chore.bonus_subtasks = [
        BonusSubTask(id=f"step-{index}", name=f"Step {index + 1}", points=value)
        for index, value in enumerate(step_points)
    ]
    storage.update_chore(chore)
    return coord, storage, child, chore


def _records(storage, chore, child, *, day=None, parent=False):
    return [
        completion
        for completion in storage.get_completions()
        if completion.chore_id == chore.id
        and completion.child_id == child.id
        and bool(completion.bonus_subtask_id) != parent
        and (day is None or completion.completed_at.date() == day)
    ]


def _approved_parents(storage, chore, child, *, day=None):
    return [c for c in _records(storage, chore, child, day=day, parent=True) if c.approved]


async def _step(coord, chore, child, index):
    return await coord.async_complete_bonus_subtask(chore.id, chore.bonus_subtasks[index].id, child.id)


async def _attempt_blocked(call):
    """Both service validation errors and the existing no-op convention block a claim."""
    try:
        result = await call
    except ValueError:
        return
    assert result is None, "an ineligible completion was accepted"


def test_steps_pay_before_parent_and_last_step_completes_it_once():
    async def scenario():
        coord, storage, child, chore = await _setup()
        first = await _step(coord, chore, child, 0)
        assert first.approved and first.points_awarded == 2
        assert storage.get_child(child.id).points == 2
        assert storage.get_child(child.id).total_chores_completed == 0
        assert storage.get_child(child.id).current_streak == 0
        assert _approved_parents(storage, chore, child) == []

        await _step(coord, chore, child, 1)
        parents = _approved_parents(storage, chore, child)
        assert len(parents) == 1
        assert parents[0].points_awarded == 10
        assert storage.get_child(child.id).points == 15
        assert storage.get_child(child.id).total_chores_completed == 1
        assert storage.get_child(child.id).current_streak == 1
        assert storage.get_child(child.id).last_completion_date == _now().date().isoformat()

        await _attempt_blocked(_step(coord, chore, child, 1))
        await coord.async_approve_chore(parents[0].id)
        assert storage.get_child(child.id).points == 15
        assert len(_approved_parents(storage, chore, child)) == 1

    _run(scenario)


@pytest.mark.parametrize("as_parent", [False, True])
def test_parent_cannot_be_claimed_directly_before_or_after_steps(as_parent):
    async def scenario():
        coord, storage, child, chore = await _setup()
        await _attempt_blocked(coord.async_complete_chore(chore.id, child.id, as_parent=as_parent))
        assert storage.get_completions() == []
        assert storage.get_child(child.id).points == 0
        for index in range(2):
            await _step(coord, chore, child, index)
        before = storage.get_child(child.id).to_dict()
        await _attempt_blocked(coord.async_complete_chore(chore.id, child.id, as_parent=as_parent))
        assert storage.get_child(child.id).to_dict() == before
        assert len(_approved_parents(storage, chore, child)) == 1

    _run(scenario)


def test_pending_steps_do_not_pay_or_complete_parent_until_individually_approved():
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=True)
        first, second = [await _step(coord, chore, child, index) for index in range(2)]
        assert not first.approved and not second.approved
        assert storage.get_child(child.id).points == 0
        assert _approved_parents(storage, chore, child) == []
        await coord.async_approve_chore(second.id)
        assert storage.get_child(child.id).points == 3
        assert storage.get_child(child.id).current_streak == 0
        assert _approved_parents(storage, chore, child) == []
        await coord.async_approve_chore(first.id)
        assert storage.get_child(child.id).points == 15
        assert storage.get_child(child.id).current_streak == 1
        assert len(_approved_parents(storage, chore, child)) == 1

    _run(scenario)


@pytest.mark.parametrize("requires_approval", [False, True])
def test_zero_point_steps_and_zero_completion_bonus_still_complete_and_count_streak(requires_approval):
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=requires_approval, points=0, step_points=(0, 0))
        for index in range(2):
            completion = await _step(coord, chore, child, index)
            if requires_approval:
                await coord.async_approve_chore(completion.id)
        assert storage.get_child(child.id).points == 0
        assert storage.get_child(child.id).current_streak == 1
        assert storage.get_child(child.id).total_chores_completed == 1
        assert len(_approved_parents(storage, chore, child)) == 1
        assert all(c.points_awarded == 0 for c in storage.get_completions())

    _run(scenario)


def test_completed_checklist_resets_on_next_day_and_rewards_once_again():
    async def scenario():
        coord, storage, child, chore = await _setup()
        for index in range(2):
            await _step(coord, chore, child, index)
        tomorrow = _now() + timedelta(days=1)
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=tomorrow):
            await _step(coord, chore, child, 0)
            assert _approved_parents(storage, chore, child, day=tomorrow.date()) == []
            assert storage.get_child(child.id).points == 17
            await _step(coord, chore, child, 1)
        assert storage.get_child(child.id).points == 30
        assert storage.get_child(child.id).current_streak == 2
        assert storage.get_child(child.id).total_chores_completed == 2
        assert len(_approved_parents(storage, chore, child)) == 2
        assert len(_records(storage, chore, child, day=tomorrow.date())) == 2

    _run(scenario)


def test_unfinished_yesterday_steps_cannot_finish_todays_checklist():
    async def scenario():
        coord, storage, child, chore = await _setup()
        await _step(coord, chore, child, 0)
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=_now() + timedelta(days=1)):
            await _step(coord, chore, child, 1)
            assert storage.get_child(child.id).points == 5
            assert _approved_parents(storage, chore, child) == []
            await _step(coord, chore, child, 0)
        assert storage.get_child(child.id).points == 17
        assert len(_approved_parents(storage, chore, child)) == 1

    _run(scenario)


def test_shared_checklist_tracks_each_child_independently():
    async def scenario():
        coord, storage, alice, chore = await _setup()
        bob = await coord.async_add_child("Bob")
        chore.assigned_to = [alice.id, bob.id]
        storage.update_chore(chore)
        await _step(coord, chore, alice, 0)
        await _step(coord, chore, bob, 1)
        assert _approved_parents(storage, chore, alice) == []
        assert _approved_parents(storage, chore, bob) == []
        await _step(coord, chore, alice, 1)
        assert storage.get_child(alice.id).points == 15
        assert storage.get_child(bob.id).points == 3
        assert _approved_parents(storage, chore, bob) == []
        await _step(coord, chore, bob, 0)
        assert storage.get_child(bob.id).points == 15
        assert len(_approved_parents(storage, chore, alice)) == 1
        assert len(_approved_parents(storage, chore, bob)) == 1

    _run(scenario)


def test_unordered_checklist_allows_any_step_first():
    async def scenario():
        coord, storage, child, chore = await _setup()
        await _step(coord, chore, child, 1)
        assert storage.get_child(child.id).points == 3
        assert _approved_parents(storage, chore, child) == []
        await _step(coord, chore, child, 0)
        assert storage.get_child(child.id).points == 15

    _run(scenario)


def test_sequential_checklist_requires_prior_step_approval():
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=True, sequential=True)
        await _attempt_blocked(_step(coord, chore, child, 1))
        assert storage.get_completions() == []
        first = await _step(coord, chore, child, 0)
        await _attempt_blocked(_step(coord, chore, child, 1))
        assert len(storage.get_completions()) == 1
        await coord.async_approve_chore(first.id)
        second = await _step(coord, chore, child, 1)
        await coord.async_approve_chore(second.id)
        assert storage.get_child(child.id).points == 15
        assert len(_approved_parents(storage, chore, child)) == 1

    _run(scenario)


@pytest.mark.parametrize("blocked_by", ["assignment", "disabled", "disabled_for", "schedule", "dependency"])
def test_checklist_steps_enforce_parent_eligibility(blocked_by):
    async def scenario():
        coord, storage, child, chore = await _setup()
        if blocked_by == "assignment":
            other = await coord.async_add_child("Bob")
            chore.assigned_to = [other.id]
        elif blocked_by == "disabled":
            chore.enabled = False
        elif blocked_by == "disabled_for":
            chore.disabled_for = [child.id]
        elif blocked_by == "schedule":
            chore.due_days = ["monday"]  # The frozen clock is Wednesday.
        else:
            prerequisite = await coord.async_add_chore("Get ready", assigned_to=[child.id])
            chore.depends_on = [prerequisite.id]
        storage.update_chore(chore)
        await _attempt_blocked(_step(coord, chore, child, 0))
        assert storage.get_completions() == []
        assert storage.get_child(child.id).points == 0

    _run(scenario)


@pytest.mark.parametrize("action", ["async_reject_chore", "async_undo_chore_approval"])
def test_reversing_one_step_reopens_parent_and_preserves_other_steps(action):
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=True)
        first, second = [await _step(coord, chore, child, index) for index in range(2)]
        await coord.async_approve_chore(first.id)
        await coord.async_approve_chore(second.id)
        assert storage.get_child(child.id).points == 15
        await getattr(coord, action)(first.id)
        assert storage.get_child(child.id).points == 3
        assert storage.get_child(child.id).total_points_earned == 3
        assert storage.get_child(child.id).current_streak == 0
        assert storage.get_child(child.id).total_chores_completed == 0
        assert _approved_parents(storage, chore, child) == []
        remaining = next(c for c in storage.get_completions() if c.id == second.id)
        assert remaining.approved and remaining.points_awarded == 3

        if action == "async_reject_chore":
            first = await _step(coord, chore, child, 0)
        await coord.async_approve_chore(first.id)
        await coord.async_approve_chore(first.id)
        assert storage.get_child(child.id).points == 15
        assert storage.get_child(child.id).current_streak == 1
        assert storage.get_child(child.id).total_chores_completed == 1
        assert len(_approved_parents(storage, chore, child)) == 1

    _run(scenario)


def test_undoing_whole_checklist_makes_steps_pending_and_removes_derived_parent():
    async def scenario():
        coord, storage, child, chore = await _setup()
        steps = [await _step(coord, chore, child, index) for index in range(2)]
        parent = _approved_parents(storage, chore, child)[0]
        await coord.async_undo_chore_approval(parent.id)
        assert storage.get_child(child.id).points == 0
        assert storage.get_child(child.id).total_chores_completed == 0
        assert storage.get_child(child.id).current_streak == 0
        assert _records(storage, chore, child, parent=True) == []
        pending_steps = _records(storage, chore, child)
        assert len(pending_steps) == 2
        assert all(not c.approved and c.points_awarded == 0 for c in pending_steps)
        await _attempt_blocked(coord.async_complete_chore(chore.id, child.id))
        await coord.async_approve_chore(steps[0].id)
        assert _approved_parents(storage, chore, child) == []
        await coord.async_approve_chore(steps[1].id)
        assert storage.get_child(child.id).points == 15
        assert storage.get_child(child.id).total_chores_completed == 1
        assert len(_approved_parents(storage, chore, child)) == 1

    _run(scenario)


def test_reversing_checklist_preserves_streak_earned_by_another_chore():
    async def scenario():
        coord, storage, child, chore = await _setup()
        other = await coord.async_add_chore("Dishes", points=7, requires_approval=False, assigned_to=[child.id])
        await coord.async_complete_chore(other.id, child.id)
        first = await _step(coord, chore, child, 0)
        await _step(coord, chore, child, 1)
        await coord.async_undo_chore_approval(first.id)
        assert storage.get_child(child.id).points == 10
        assert storage.get_child(child.id).current_streak == 1
        assert _approved_parents(storage, chore, child) == []

    _run(scenario)


@pytest.mark.parametrize("override", [0, 7])
def test_step_submission_snapshot_override_reversal_and_reapproval(override):
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=True)
        first, second = [await _step(coord, chore, child, index) for index in range(2)]
        chore.bonus_subtasks[0].points = 99
        chore.bonus_subtasks[1].points = 99
        storage.update_chore(chore)
        await coord.async_approve_chore(first.id, points=override)
        await coord.async_approve_chore(second.id)
        saved = {c.id: c for c in storage.get_completions()}
        assert saved[first.id].submitted_points == 2
        assert saved[first.id].points_awarded == override
        assert saved[second.id].submitted_points == 3
        assert saved[second.id].points_awarded == 3
        assert storage.get_child(child.id).points == 13 + override

        await coord.async_undo_chore_approval(first.id)
        assert storage.get_child(child.id).points == 3
        await coord.async_approve_chore(first.id)
        assert storage.get_child(child.id).points == 15
        assert len(_approved_parents(storage, chore, child)) == 1

    _run(scenario)


def test_late_step_approvals_complete_the_original_day_without_using_todays_steps():
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=True)
        first, second = [await _step(coord, chore, child, index) for index in range(2)]
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=_now() + timedelta(days=1)):
            await coord.async_approve_chore(first.id)
            await coord.async_approve_chore(second.id)
            parents = _approved_parents(storage, chore, child)
            assert len(parents) == 1
            assert parents[0].completed_at.date() == _now().date()
            assert storage.get_child(child.id).last_completion_date == _now().date().isoformat()
            await _step(coord, chore, child, 0)
            assert len(_approved_parents(storage, chore, child)) == 1

    _run(scenario)


def test_late_approval_after_newer_checklist_keeps_latest_date_and_backfills_streak():
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=True)
        older_steps = [await _step(coord, chore, child, index) for index in range(2)]
        tomorrow = _now() + timedelta(days=1)
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=tomorrow):
            for index in range(2):
                completion = await _step(coord, chore, child, index)
                await coord.async_approve_chore(completion.id)
            assert storage.get_child(child.id).current_streak == 1
            for completion in older_steps:
                await coord.async_approve_chore(completion.id)
        assert storage.get_child(child.id).points == 30
        assert storage.get_child(child.id).total_chores_completed == 2
        assert storage.get_child(child.id).last_completion_date == tomorrow.date().isoformat()
        assert storage.get_child(child.id).current_streak == 2
        assert storage.get_last_completed(chore.id, child.id)["current"] == tomorrow.isoformat()
        assert len(_approved_parents(storage, chore, child)) == 2

    _run(scenario)


@pytest.mark.parametrize("action", ["async_reject_chore", "async_undo_chore_approval"])
def test_reversing_older_checklist_preserves_newer_anchor_then_clears_removed_history(action):
    async def scenario():
        coord, storage, child, chore = await _setup()
        older = [await _step(coord, chore, child, index) for index in range(2)]
        tomorrow = _now() + timedelta(days=1)
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=tomorrow):
            newer = [await _step(coord, chore, child, index) for index in range(2)]
            await getattr(coord, action)(older[0].id)
            assert storage.get_child(child.id).points == 18
            assert storage.get_child(child.id).total_chores_completed == 1
            assert storage.get_child(child.id).current_streak == 1
            assert storage.get_child(child.id).last_completion_date == tomorrow.date().isoformat()
            assert storage.get_last_completed(chore.id, child.id)["current"] == tomorrow.isoformat()
            assert len(_approved_parents(storage, chore, child, day=tomorrow.date())) == 1

            # The removed older parent must not survive as a recurrence fallback.
            await getattr(coord, action)(newer[0].id)
            assert storage.get_child(child.id).points == 6
            assert storage.get_child(child.id).total_chores_completed == 0
            assert storage.get_child(child.id).current_streak == 0
            assert storage.get_child(child.id).last_completion_date is None
            assert not storage.get_last_completed(chore.id, child.id).get("current")

    _run(scenario)


@pytest.mark.parametrize("action", ["async_reject_chore", "async_undo_chore_approval"])
def test_checklist_quest_and_streak_rewards_reverse_and_repay_exactly_once(action):
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=True)
        storage.set_setting("streak_milestones_enabled", "true")
        storage.set_setting("streak_milestones", "1:5")
        quest = Quest(name="Morning quest", steps=[chore.id], bonus_points=25, repeatable=True)
        storage.add_quest(quest)
        first, second = [await _step(coord, chore, child, index) for index in range(2)]
        await coord.async_approve_chore(first.id)
        assert storage.get_child(child.id).points == 2
        assert storage.get_child(child.id).total_chores_completed == 0
        assert storage.get_quest_child_progress(quest.id, child.id).get("completed_count", 0) == 0
        await coord.async_approve_chore(second.id)
        await coord.async_approve_chore(second.id)
        assert storage.get_child(child.id).points == 45
        assert storage.get_child(child.id).total_points_earned == 45
        assert storage.get_child(child.id).career_score == 45
        assert storage.get_child(child.id).total_chores_completed == 1
        assert storage.get_quest_child_progress(quest.id, child.id)["completed_count"] == 1

        await getattr(coord, action)(first.id)
        assert storage.get_child(child.id).points == 3
        assert storage.get_child(child.id).total_points_earned == 3
        assert storage.get_child(child.id).career_score == 3
        assert storage.get_child(child.id).total_chores_completed == 0
        assert storage.get_child(child.id).streak_milestones_achieved == []
        assert storage.get_quest_child_progress(quest.id, child.id)["completed_count"] == 0
        if action == "async_reject_chore":
            first = await _step(coord, chore, child, 0)
        await coord.async_approve_chore(first.id)
        await coord.async_approve_chore(first.id)
        assert storage.get_child(child.id).points == 45
        assert storage.get_child(child.id).total_chores_completed == 1
        assert storage.get_quest_child_progress(quest.id, child.id)["completed_count"] == 1
        assert sum(t.points for t in storage.get_points_transactions()) == 30

    _run(scenario)


def test_step_points_can_buy_rewards_before_checklist_finishes_without_changing_earned_counts():
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=True)
        coord._async_notify_pending_reward_claim = AsyncMock()
        reward = Reward(name="Sticker", cost=2)
        storage.add_reward(reward)
        first = await _step(coord, chore, child, 0)
        with pytest.raises(ValueError, match="Not enough points"):
            await coord.async_claim_reward(reward.id, child.id)
        await coord.async_approve_chore(first.id)
        claim = await coord.async_claim_reward(reward.id, child.id)
        await coord.async_approve_reward(claim.id)
        assert storage.get_child(child.id).points == 0
        assert storage.get_child(child.id).total_points_earned == 2
        assert storage.get_child(child.id).total_chores_completed == 0
        assert storage.get_child(child.id).current_streak == 0
        second = await _step(coord, chore, child, 1)
        await coord.async_approve_chore(second.id)
        assert storage.get_child(child.id).points == 13
        assert storage.get_child(child.id).total_points_earned == 15
        assert storage.get_child(child.id).career_score == 15
        assert storage.get_child(child.id).total_chores_completed == 1
        assert storage.get_child(child.id).current_streak == 1
        assert len(storage.get_reward_claims()) == 1
        assert storage.get_reward_claims()[0].approved

    _run(scenario)


def _pause_first_notification(coord):
    entered, release = asyncio.Event(), asyncio.Event()

    async def notify(kind, _context):
        if kind == "level_up" and not entered.is_set():
            entered.set()
            await release.wait()

    coord.notifications.fire.side_effect = notify
    return entered, release


@pytest.mark.parametrize("requires_approval", [False, True])
def test_last_step_duplicate_during_notification_cannot_double_pay_parent(requires_approval):
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=requires_approval, points=100, step_points=(0, 0))
        first = await _step(coord, chore, child, 0)
        if requires_approval:
            await coord.async_approve_chore(first.id)
            last = await _step(coord, chore, child, 1)
        entered, release = _pause_first_notification(coord)
        award = asyncio.create_task(
            coord.async_approve_chore(last.id) if requires_approval else _step(coord, chore, child, 1)
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            assert len(_approved_parents(storage, chore, child)) == 1
            assert storage.get_child(child.id).points == 100
            if requires_approval:
                await coord.async_approve_chore(last.id)
            else:
                await _attempt_blocked(_step(coord, chore, child, 1))
            await _attempt_blocked(coord.async_complete_chore(chore.id, child.id))
        finally:
            release.set()
            await award
        assert storage.get_child(child.id).points == 100
        assert storage.get_child(child.id).current_streak == 1
        assert len(_records(storage, chore, child)) == 2
        assert len(_approved_parents(storage, chore, child)) == 1

    _run(scenario)


def test_step_reversal_during_parent_notification_cannot_restore_its_award():
    async def scenario():
        coord, storage, child, chore = await _setup(points=100, step_points=(2, 3))
        first = await _step(coord, chore, child, 0)
        entered, release = _pause_first_notification(coord)
        award = asyncio.create_task(_step(coord, chore, child, 1))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            await coord.async_undo_chore_approval(first.id)
        finally:
            release.set()
            await award
        assert storage.get_child(child.id).points == 3
        assert storage.get_child(child.id).current_streak == 0
        assert _approved_parents(storage, chore, child) == []
        assert next(c for c in _records(storage, chore, child) if c.bonus_subtask_id == "step-1").approved

    _run(scenario)


def test_another_step_can_finish_while_first_step_notification_is_waiting():
    async def scenario():
        coord, storage, child, chore = await _setup(step_points=(100, 3))
        entered, release = _pause_first_notification(coord)
        award = asyncio.create_task(_step(coord, chore, child, 0))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            await _step(coord, chore, child, 1)
        finally:
            release.set()
            await award
        assert storage.get_child(child.id).points == 113
        assert storage.get_child(child.id).total_points_earned == 113
        assert storage.get_child(child.id).total_chores_completed == 1
        assert storage.get_child(child.id).current_streak == 1
        assert len(_approved_parents(storage, chore, child)) == 1
        assert sum(c.points_awarded for c in storage.get_completions()) == 113

    _run(scenario)


def test_stored_partial_checklist_survives_reload_and_does_not_repay_finished_steps():
    async def scenario():
        coord, storage, child, chore = await _setup(sequential=True)
        first = await _step(coord, chore, child, 0)
        await storage.async_save()
        reloaded_coord, reloaded_storage = await _make_system()
        reloaded_storage._store._data = json.loads(json.dumps(storage._store._data))
        await reloaded_storage.async_load()
        saved_chore = reloaded_storage.get_chore(chore.id)
        assert saved_chore.task_type == "checklist"
        assert saved_chore.checklist_sequential is True
        assert reloaded_storage.get_child(child.id).points == 2
        await reloaded_coord.async_approve_chore(first.id)
        await _attempt_blocked(_step(reloaded_coord, saved_chore, child, 0))
        await _step(reloaded_coord, saved_chore, child, 1)
        assert reloaded_storage.get_child(child.id).points == 15
        assert reloaded_storage.get_child(child.id).total_chores_completed == 1
        assert len(_approved_parents(reloaded_storage, saved_chore, child)) == 1

    _run(scenario)


def test_optional_bonus_subtasks_keep_parent_first_behavior():
    async def scenario():
        coord, storage, child, chore = await _setup()
        chore.task_type = "standard"
        storage.update_chore(chore)
        await _attempt_blocked(_step(coord, chore, child, 0))
        assert storage.get_completions() == []
        await coord.async_complete_chore(chore.id, child.id)
        assert storage.get_child(child.id).points == 10
        for index in range(2):
            await _step(coord, chore, child, index)
        assert storage.get_child(child.id).points == 15
        assert storage.get_child(child.id).current_streak == 1
        assert len(_approved_parents(storage, chore, child)) == 1

    _run(scenario)


def test_checklist_model_roundtrip_preserves_sequence_zero_points_and_step_icon():
    raw = {
        "id": "morning",
        "name": "Morning checklist",
        "task_type": "checklist",
        "checklist_sequential": True,
        "points": 0,
        "bonus_subtasks": [{"id": "teeth", "name": "Brush teeth", "points": 0, "icon": "mdi:toothbrush"}],
    }
    saved = Chore.from_dict(raw).to_dict()
    restored = Chore.from_dict(saved)
    assert restored.task_type == "checklist"
    assert restored.checklist_sequential is True
    assert restored.points == 0
    assert restored.bonus_subtasks[0].points == 0
    assert restored.bonus_subtasks[0].icon == "mdi:toothbrush"
    assert restored.to_dict() == saved
    legacy = Chore.from_dict({"name": "Dishes"})
    assert legacy.task_type == "standard"
    assert legacy.checklist_sequential is False
    assert BonusSubTask.from_dict({"name": "Dry dishes"}).icon == ""


@pytest.mark.parametrize(
    "source,target",
    [("standard", "checklist"), ("timed", "checklist"), ("checklist", "standard"), ("checklist", "timed")],
)
@pytest.mark.parametrize("approved", [False, True])
def test_chore_type_cannot_change_to_or_from_checklist_after_any_history(source, target, approved):
    async def scenario():
        coord, storage, child, chore = await _setup()
        chore.task_type = source
        storage.update_chore(chore)
        storage.add_completion(
            ChoreCompletion(
                chore_id=chore.id,
                child_id=child.id,
                completed_at=_now() - timedelta(days=1),
                approved=approved,
            )
        )
        before = storage.get_chore(chore.id).to_dict()
        chore.task_type = target
        with pytest.raises(ValueError, match="new chore"):
            await coord.async_update_chore(chore)
        assert storage.get_chore(chore.id).to_dict() == before
        assert len(storage.get_completions()) == 1

    _run(scenario)


@pytest.mark.parametrize("history", ["today_approved", "yesterday_pending"])
@pytest.mark.parametrize("change", ["replace_id", "reorder", "add", "remove"])
def test_step_structure_cannot_change_during_today_progress_or_pending_review(history, change):
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=history == "yesterday_pending")
        when = _now() - timedelta(days=1) if history == "yesterday_pending" else _now()
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=when):
            await _step(coord, chore, child, 0)
        before = storage.get_chore(chore.id).to_dict()
        if change == "replace_id":
            chore.bonus_subtasks[0].id = "replacement"
        elif change == "reorder":
            chore.bonus_subtasks.reverse()
        elif change == "add":
            chore.bonus_subtasks.append(BonusSubTask(id="new", name="New step", points=0))
        else:
            chore.bonus_subtasks.pop(0)
        with pytest.raises(ValueError, match="reviewing.*tomorrow"):
            await coord.async_update_chore(chore)
        assert storage.get_chore(chore.id).to_dict() == before
        assert len(storage.get_completions()) == 1

    _run(scenario)


def test_step_structure_can_change_after_prior_days_history_is_fully_reviewed():
    async def scenario():
        coord, storage, child, chore = await _setup()
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=_now() - timedelta(days=1)):
            for index in range(2):
                await _step(coord, chore, child, index)
        history = [c.to_dict() for c in storage.get_completions()]
        chore.bonus_subtasks = [BonusSubTask(id="new", name="New step", points=0)]
        await coord.async_update_chore(chore)
        assert [s.id for s in storage.get_chore(chore.id).bonus_subtasks] == ["new"]
        assert [c.to_dict() for c in storage.get_completions()] == history
        assert storage.get_child(child.id).points == 15

    _run(scenario)


def test_name_icon_and_points_can_change_with_pending_steps_without_repricing_them():
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=True)
        steps = [await _step(coord, chore, child, index) for index in range(2)]
        chore.name = "Updated routine"
        chore.icon = "mdi:weather-sunny"
        chore.points = 25
        for index, step in enumerate(chore.bonus_subtasks):
            step.name = f"Updated step {index}"
            step.icon = "mdi:toothbrush"
            step.points = 99
        await coord.async_update_chore(chore)
        saved = storage.get_chore(chore.id)
        assert saved.name == "Updated routine"
        assert saved.icon == "mdi:weather-sunny"
        assert saved.points == 25
        assert all(s.icon == "mdi:toothbrush" and s.points == 99 for s in saved.bonus_subtasks)
        for completion in steps:
            await coord.async_approve_chore(completion.id)
        assert storage.get_child(child.id).points == 15
        assert [c.points_awarded for c in _records(storage, chore, child)] == [2, 3]
        assert _approved_parents(storage, chore, child)[0].points_awarded == 10

    _run(scenario)


@pytest.mark.parametrize("all_mode", [False, True])
@pytest.mark.parametrize("requires_approval", [False, True])
def test_partial_checklist_each_day_does_not_earn_perfect_week(all_mode, requires_approval):
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=requires_approval)
        storage.set_setting("perfect_week_enabled", "true")
        storage.set_setting("perfect_week_bonus", "50")
        storage.set_setting("perfect_week_requires_all_chores", str(all_mode).lower())
        monday = _now() + timedelta(days=5)
        for offset in range(7, 0, -1):
            with patch(
                "custom_components.taskmate.coord_chores.dt_util.now", return_value=monday - timedelta(days=offset)
            ):
                await _step(coord, chore, child, 0)
        before = storage.get_child(child.id).points
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=monday):
            await coord._async_check_perfect_week()
        assert storage.get_child(child.id).points == before
        assert storage.get_child(child.id).awarded_perfect_weeks == []
        assert storage.get_child(child.id).total_chores_completed == 0
        assert _approved_parents(storage, chore, child) == []

    _run(scenario)


@pytest.mark.parametrize("all_mode", [False, True])
def test_fully_completed_checklist_each_day_earns_perfect_week_once(all_mode):
    async def scenario():
        coord, storage, child, chore = await _setup()
        storage.set_setting("perfect_week_enabled", "true")
        storage.set_setting("perfect_week_bonus", "50")
        storage.set_setting("perfect_week_requires_all_chores", str(all_mode).lower())
        monday = _now() + timedelta(days=5)
        for offset in range(7, 0, -1):
            with patch(
                "custom_components.taskmate.coord_chores.dt_util.now", return_value=monday - timedelta(days=offset)
            ):
                for index in range(2):
                    await _step(coord, chore, child, index)
        assert storage.get_child(child.id).points == 105
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=monday):
            await coord._async_check_perfect_week()
            await coord._async_check_perfect_week()
        assert storage.get_child(child.id).points == 155
        assert storage.get_child(child.id).total_points_earned == 155
        assert storage.get_child(child.id).total_chores_completed == 7
        assert storage.get_child(child.id).awarded_perfect_weeks == ["2024-03-18"]

    _run(scenario)
