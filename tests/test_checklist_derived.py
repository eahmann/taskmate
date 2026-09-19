"""Checklist submissions and awards keep notifications and reports honest."""

from datetime import timedelta
from unittest.mock import patch

from custom_components.taskmate.coord_notifications import NotificationCoordinator
from custom_components.taskmate.models import BonusSubTask

from .test_checklist_chores import _now, _run, _setup, _step


def _notifications(storage):
    notifications = object.__new__(NotificationCoordinator)
    notifications.storage = storage
    return notifications


def test_only_a_fully_submitted_checklist_stops_outstanding_chore_reminders():
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=True)
        notifications = _notifications(storage)
        assert notifications._has_outstanding_chores_today(child.id)
        first = await _step(coord, chore, child, 0)
        assert notifications._has_outstanding_chores_today(child.id)
        await coord.async_approve_chore(first.id)
        assert notifications._has_outstanding_chores_today(child.id)
        second = await _step(coord, chore, child, 1)
        assert not notifications._has_outstanding_chores_today(child.id)
        await coord.async_reject_chore(second.id)
        assert notifications._has_outstanding_chores_today(child.id)

    _run(scenario)


def test_submissions_do_not_combine_across_days_or_children_for_done_checks():
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=True)
        sibling = await coord.async_add_child("Bob")
        chore.assigned_to.append(sibling.id)
        storage.update_chore(chore)
        await _step(coord, chore, child, 0)
        tomorrow = _now() + timedelta(days=1)
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=tomorrow):
            await _step(coord, chore, child, 1)
            await _step(coord, chore, sibling, 0)
            assert _notifications(storage)._has_outstanding_chores_today(child.id)
            assert not coord._child_completed_today(chore.id, child.id, tomorrow.date())
            assert not coord._child_completed_today(chore.id, sibling.id, tomorrow.date())
            await _step(coord, chore, child, 0)
            assert not _notifications(storage)._has_outstanding_chores_today(child.id)
            assert coord._child_completed_today(chore.id, child.id, tomorrow.date())
            assert _notifications(storage)._has_outstanding_chores_today(sibling.id)

    _run(scenario)


def test_all_pending_steps_prevent_mandatory_miss_but_partial_steps_do_not():
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=True)
        chore.mandatory = True
        storage.update_chore(chore)
        await _step(coord, chore, child, 0)
        assert not coord._child_completed_today(chore.id, child.id, _now().date())
        second = await _step(coord, chore, child, 1)
        assert await coord.async_detect_mandatory_misses("anytime", _now().date()) == 0
        await coord.async_reject_chore(second.id)
        assert await coord.async_detect_mandatory_misses("anytime", _now().date()) == 1

    _run(scenario)


def test_points_challenge_includes_approved_steps_without_counting_them_as_chores():
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=True)
        challenge_id = await coord.async_create_challenge(
            name="Earn two points", scope="daily", metric="points", target=2, bonus_points=7
        )
        first = await _step(coord, chore, child, 0)
        assert coord._metric_value(child.id, "daily", "points") == 0
        await coord.async_approve_chore(first.id)
        assert coord._metric_value(child.id, "daily", "points") == 2
        assert coord._metric_value(child.id, "daily", "chores") == 0
        await coord._async_evaluate_challenges(child.id)
        assert storage.get_challenge_child_progress(challenge_id, child.id)["awarded"]
        second = await _step(coord, chore, child, 1)
        await coord.async_approve_chore(second.id)
        assert coord._metric_value(child.id, "daily", "points") == 15
        assert coord._metric_value(child.id, "daily", "chores") == 1

    _run(scenario)


def test_fairness_points_include_steps_and_parent_bonus_then_follow_step_undo():
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=True)
        first = await _step(coord, chore, child, 0)
        assert coord.fairness_report(1)["total_points"] == 0
        await coord.async_approve_chore(first.id)
        report = coord.fairness_report(1)
        assert report["total_points"] == 2
        assert report["total_completions"] == 0
        second = await _step(coord, chore, child, 1)
        await coord.async_approve_chore(second.id)
        report = coord.fairness_report(1)
        assert report["total_points"] == 15
        assert report["total_completions"] == 1
        await coord.async_undo_chore_approval(first.id)
        report = coord.fairness_report(1)
        assert report["total_points"] == 3
        assert report["total_completions"] == 0

    _run(scenario)


def test_optional_bonus_subtasks_keep_existing_challenge_and_fairness_semantics():
    async def scenario():
        coord, storage, child, _checklist = await _setup()
        chore = await coord.async_add_chore("Standard", points=3, requires_approval=False)
        chore.bonus_subtasks = [BonusSubTask(name="Extra", points=4)]
        storage.update_chore(chore)
        await coord.async_complete_chore(chore.id, child.id)
        await coord.async_complete_bonus_subtask(chore.id, chore.bonus_subtasks[0].id, child.id)
        assert coord._metric_value(child.id, "daily", "points") == 3
        assert coord._metric_value(child.id, "daily", "chores") == 1
        report = coord.fairness_report(1)
        assert report["total_points"] == 3
        assert report["total_completions"] == 1

    _run(scenario)
