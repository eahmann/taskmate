"""A pending completion retains the effective points earned when submitted."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest

from custom_components.taskmate.models import BonusSubTask, ChoreCompletion, TimedSession
from custom_components.taskmate.storage import TaskMateStorage

from .test_award_notification_atomicity import _run, _setup
from .test_completion_concurrency import _now


@pytest.mark.parametrize("speed,roulette,expected", [(5, 1, 15), (0, 2, 20), (5, 2, 30)])
def test_pending_award_preserves_speed_and_roulette_after_they_expire(speed, roulette, expected):
    async def scenario():
        coord, storage, child, chore = await _setup(points=10)
        chore.deadline_at = (_now() + timedelta(hours=1)).isoformat()
        chore.speed_bonus_points = speed
        storage.update_chore(chore)
        storage.set_setting(
            "roulette_state",
            {child.id: {"date": _now().date().isoformat(), "chore_id": chore.id, "multiplier": roulette, "spins": 1}},
        )
        completion = await coord.async_complete_chore(chore.id, child.id)
        saved = storage.get_completions()[0]
        assert saved.submitted_points == expected
        assert saved.points_awarded == 0
        assert storage.get_child(child.id).points == 0

        # Simulate later approval after the roulette pick expired and the
        # parent edited the chore's award/deadline while it was pending.
        chore.points = 99
        chore.speed_bonus_points = 50
        storage.update_chore(chore)
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=_now() + timedelta(days=1)):
            await coord.async_prune_roulette_state()
            await coord.async_approve_chore(completion.id)

        assert storage.get_child(child.id).points == expected
        assert storage.get_completions()[0].points_awarded == expected

    _run(scenario)


def test_autoapproval_undo_and_reapproval_reuse_the_original_award():
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=False, points=10)
        chore.deadline_at = (_now() + timedelta(hours=1)).isoformat()
        chore.speed_bonus_points = 5
        storage.update_chore(chore)
        completion = await coord.async_complete_chore(chore.id, child.id)
        await coord.async_undo_chore_approval(completion.id)
        chore.speed_bonus_points = 0
        chore.points = 25
        storage.update_chore(chore)
        await coord.async_approve_chore(completion.id)
        assert storage.get_child(child.id).points == 15
        assert storage.get_completions()[0].submitted_points == 15

    _run(scenario)


def test_snapshot_receives_weekend_multiplier_once_using_submission_date():
    async def scenario():
        coord, storage, child, chore = await _setup(points=10)
        saturday = _now() + timedelta(days=3)
        chore.deadline_at = (saturday + timedelta(hours=1)).isoformat()
        chore.speed_bonus_points = 5
        storage.update_chore(chore)
        storage.set_setting("weekend_multiplier", "2")
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=saturday):
            completion = await coord.async_complete_chore(chore.id, child.id)
        assert storage.get_completions()[0].submitted_points == 15
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=saturday + timedelta(days=2)):
            await coord.async_approve_chore(completion.id)
        assert storage.get_child(child.id).points == 30
        assert storage.get_completions()[0].points_awarded == 30

    _run(scenario)


def test_pending_bonus_keeps_its_award_after_subtask_removal():
    async def scenario():
        coord, storage, child, chore = await _setup(points=10)
        chore.bonus_subtasks = [BonusSubTask(id="bonus", name="Dry dishes", points=7)]
        storage.update_chore(chore)
        await coord.async_complete_chore(chore.id, child.id)
        bonus = await coord.async_complete_bonus_subtask(chore.id, "bonus", child.id)
        chore.bonus_subtasks = []
        storage.update_chore(chore)
        await coord.async_approve_chore(bonus.id)
        assert storage.get_child(child.id).points == 7
        saved = next(c for c in storage.get_completions() if c.id == bonus.id)
        assert saved.submitted_points == 7

    _run(scenario)


@pytest.mark.parametrize("seconds,expected", [(30, 0), (120, 6)])
def test_pending_timer_keeps_its_award_after_rate_changes(seconds, expected):
    async def scenario():
        coord, storage, child, chore = await _setup(points=50)
        chore.task_type = "timed"
        chore.timed_rate_minutes = 1
        chore.timed_rate_points = 3
        storage.update_chore(chore)
        with patch(
            "custom_components.taskmate.coord_timed.dt_util.now", return_value=_now() - timedelta(seconds=seconds)
        ):
            await coord.async_start_timed_task(chore.id, child.id)
        await coord.async_stop_timed_task(chore.id, child.id)
        saved = storage.get_completions()[0]
        assert saved.submitted_points == expected
        chore.timed_rate_points = 99
        storage.update_chore(chore)
        await coord.async_approve_chore(saved.id)
        assert storage.get_child(child.id).points == expected

    _run(scenario)


def test_stale_timer_cleanup_records_submitted_points():
    async def scenario():
        coord, storage, child, chore = await _setup()
        chore.task_type = "timed"
        chore.timed_rate_minutes = 1
        chore.timed_rate_points = 3
        storage.update_chore(chore)
        end = _now() - timedelta(days=1)
        storage.save_timed_session(
            TimedSession(
                chore_id=chore.id,
                child_id=child.id,
                state="paused",
                session_date=end.date().isoformat(),
                segments=[{"start": (end - timedelta(minutes=2)).isoformat(), "end": end.isoformat()}],
            )
        )
        await coord._async_stop_stale_timed_sessions()
        assert storage.get_completions()[0].submitted_points == 6

    _run(scenario)


@pytest.mark.parametrize("kind,expected", [("standard", 10), ("bonus", 7), ("timed", 6)])
def test_legacy_pending_completions_keep_the_existing_approval_calculation(kind, expected):
    async def scenario():
        coord, storage, child, chore = await _setup(points=10)
        chore.bonus_subtasks = [BonusSubTask(id="bonus", name="Dry dishes", points=7)]
        chore.task_type = "timed" if kind == "timed" else "standard"
        chore.timed_rate_minutes = 1
        chore.timed_rate_points = 3
        storage.update_chore(chore)
        completion = ChoreCompletion(
            chore_id=chore.id,
            child_id=child.id,
            completed_at=_now(),
            bonus_subtask_id="bonus" if kind == "bonus" else "",
            timed_duration_seconds=120 if kind == "timed" else 0,
        )
        legacy_record = completion.to_dict()
        legacy_record.pop("submitted_points")
        storage._data["completions"] = [legacy_record]
        await coord.async_approve_chore(completion.id)
        assert storage.get_child(child.id).points == expected

    _run(scenario)


@pytest.mark.parametrize("points", [None, 0, 15])
def test_submitted_points_round_trip(points):
    completion = ChoreCompletion(chore_id="chore", child_id="child", completed_at=_now(), submitted_points=points)
    assert ChoreCompletion.from_dict(completion.to_dict()).submitted_points == points


@pytest.mark.parametrize("value", [None, {}, "bad", float("inf"), float("nan"), -1, True])
def test_invalid_submitted_points_load_as_legacy(value):
    assert ChoreCompletion.from_dict({"submitted_points": value}).submitted_points is None


@pytest.mark.asyncio
async def test_import_sanitizes_submitted_points_and_preserves_legacy_none(hass):
    storage = TaskMateStorage(hass, "snapshot-import")
    await storage.async_load()
    storage.import_data(
        {
            "completions": [
                {"id": "absent"},
                {"id": "none", "submitted_points": None},
                {"id": "numeric", "submitted_points": "15"},
                {"id": "invalid", "submitted_points": "bad"},
            ]
        }
    )
    records = {c["id"]: c for c in storage._data["completions"]}
    assert "submitted_points" not in records["absent"]
    assert records["none"]["submitted_points"] is None
    assert records["numeric"]["submitted_points"] == 15
    assert records["invalid"]["submitted_points"] == 0
