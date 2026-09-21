"""Routines use real chore completions and a reversible daily bonus ledger."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from custom_components.taskmate.models import Routine
from tests.test_chore_award_reversal import _make_system

NOW = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)


def run(scenario):
    with patch("custom_components.taskmate.coord_routines.dt_util.now", return_value=NOW):
        asyncio.run(scenario())


async def setup(*, approval=True, bonus=4):
    coord, storage = await _make_system()
    child = await coord.async_add_child("Maggie")
    chores = [
        await coord.async_add_chore(name, points=2, assigned_to=[child.id], requires_approval=approval)
        for name in ("Make bed", "Brush teeth", "Match socks")
    ]
    rid = await coord.async_save_routine(
        name="Morning",
        bonus_points=bonus,
        members=[
            {"chore_id": chores[0].id, "required": True},
            {"chore_id": chores[1].id, "required": True},
            {"chore_id": chores[2].id, "required": False},
        ],
    )
    return coord, storage, child, chores, rid


@pytest.mark.parametrize("approval", [False, True])
def test_automatic_bonus_waits_for_required_approvals_only(approval):
    async def scenario():
        coord, store, child, chores, _ = await setup(approval=approval)
        first = await coord.async_complete_chore(chores[0].id, child.id)
        second = await coord.async_complete_chore(chores[1].id, child.id)
        if approval:
            assert store.get_child(child.id).points == 0
            await coord.async_approve_chore(second.id)
            assert store.get_child(child.id).points == 2
            await coord.async_approve_chore(first.id)
        assert store.get_child(child.id).points == 8
        assert store.get_child(child.id).total_chores_completed == 2
        progress = coord.routine_progress_for_child(child.id)[0]
        assert progress["done"] and progress["completed_count"] == 2
        await coord.async_complete_chore(chores[2].id, child.id)
        assert len([t for t in store.get_points_transactions() if t.reason.startswith("Routine complete")]) == 1

    run(scenario)


@pytest.mark.parametrize("reverse", ["async_reject_chore", "async_undo_chore_approval"])
def test_bonus_snapshot_survives_edits_delayed_approval_and_reversal(reverse):
    async def scenario():
        coord, store, child, chores, rid = await setup()
        first = await coord.async_complete_chore(chores[0].id, child.id)
        second = await coord.async_complete_chore(chores[1].id, child.id)
        await coord.async_save_routine(rid, bonus_points=100, members=[{"chore_id": chores[0].id, "required": True}])
        with patch("custom_components.taskmate.coord_routines.dt_util.now", return_value=NOW + timedelta(days=1)):
            await coord.async_approve_chore(first.id)
            await coord.async_approve_chore(second.id)
            assert store.get_child(child.id).points == 8
            await getattr(coord, reverse)(first.id)
            assert store.get_child(child.id).points == 2
            if reverse == "async_undo_chore_approval":
                await coord.async_approve_chore(first.id)
                await coord.async_approve_chore(first.id)
                assert store.get_child(child.id).points == 8

    run(scenario)


def test_due_members_include_locked_dependencies_but_not_off_day_or_other_child():
    async def scenario():
        coord, store, child, chores, rid = await setup(approval=False)
        chores[1].depends_on = [chores[0].id]
        store.update_chore(chores[1])
        chores[2].due_days = ["sunday"]
        store.update_chore(chores[2])
        other = await coord.async_add_child("Ellie")
        assert coord.routine_progress_for_child(other.id) == []
        progress = coord.routine_progress_for_child(child.id)[0]
        assert len(progress["members"]) == 2
        assert progress["required_count"] == 2
        await coord.async_complete_chore(chores[0].id, child.id)
        assert store.get_child(child.id).points == 2
        await coord.async_complete_chore(chores[1].id, child.id)
        assert store.get_child(child.id).points == 8

    run(scenario)


@pytest.mark.parametrize("bonus", [0, 4])
def test_bonus_once_per_child_per_local_day_even_for_repeatable_chore(bonus):
    async def scenario():
        coord, store, child, chores, rid = await setup(approval=False, bonus=bonus)
        chores[0].daily_limit = 5
        chores[0].assigned_to = []
        store.update_chore(chores[0])
        await coord.async_save_routine(rid, members=[{"chore_id": chores[0].id, "required": True}])
        other = await coord.async_add_child("Ellie")
        for _ in range(3):
            await coord.async_complete_chore(chores[0].id, child.id)
        await coord.async_complete_chore(chores[0].id, other.id)
        assert store.get_child(child.id).points == 6 + bonus
        assert store.get_child(other.id).points == 2 + bonus
        with patch("custom_components.taskmate.coord_routines.dt_util.now", return_value=NOW + timedelta(days=1)):
            await coord.async_complete_chore(chores[0].id, child.id)
            assert store.get_child(child.id).points == 8 + 2 * bonus

    run(scenario)


def test_empty_or_optional_only_never_awards_a_free_bonus():
    async def scenario():
        coord, store, child, chores, rid = await setup(approval=False)
        await coord.async_save_routine(rid, members=[{"chore_id": chores[0].id, "required": False}])
        await coord.async_complete_chore(chores[0].id, child.id)
        assert store.get_child(child.id).points == 2
        assert not coord.routine_progress_for_child(child.id)[0]["done"]

    run(scenario)


def test_deleted_routine_keeps_reversal_ledger_and_ordinary_chores():
    async def scenario():
        coord, store, child, chores, rid = await setup(approval=False)
        first = await coord.async_complete_chore(chores[0].id, child.id)
        await coord.async_complete_chore(chores[1].id, child.id)
        await coord.async_delete_routine(rid)
        assert len(store.get_chores()) == 3
        await coord.async_reject_chore(first.id)
        assert store.get_child(child.id).points == 2

    run(scenario)


def test_validate_duplicates_unknown_and_multiple_routine_membership():
    async def scenario():
        coord, store, child, chores, rid = await setup()
        for members in ([{"chore_id": "missing"}], [{"chore_id": chores[0].id}]):
            with pytest.raises(ValueError):
                await coord.async_save_routine(name="Evening", members=members)
        with pytest.raises(ValueError):
            await coord.async_save_routine(rid, members=[{"chore_id": chores[0].id}] * 2)
        with pytest.raises(ValueError):
            await coord.async_save_routine(rid, name=" ")
        with pytest.raises(ValueError):
            await coord.async_save_routine(rid, bonus_points=-1)

    run(scenario)


def test_daily_snapshot_uses_ha_timezone():
    async def scenario():
        coord, store, child, chores, _ = await setup(approval=False)
        instant = datetime(2026, 9, 22, 2, tzinfo=timezone.utc)
        with (
            patch("custom_components.taskmate.coord_routines.dt_util.now", return_value=instant),
            patch(
                "custom_components.taskmate.coord_routines.dt_util.as_local",
                side_effect=lambda d: d.astimezone(timezone(timedelta(hours=-5))),
            ),
        ):
            await coord.async_complete_chore(chores[0].id, child.id)
            assert store.get_routine_runs()[0]["day"] == "2026-09-21"

    run(scenario)


def test_model_round_trip_preserves_members_and_defaults_without_aliasing():
    routine = Routine(
        name="Bedtime",
        members=[{"chore_id": "a", "required": False}],
        defaults={"due_days": ["monday"], "assigned_to": ["kid"]},
    )
    assert Routine.from_dict(routine.to_dict()).to_dict() == routine.to_dict()
    exported = routine.to_dict()
    exported["defaults"]["assigned_to"].append("other")
    exported["members"][0]["required"] = True
    assert routine.defaults["assigned_to"] == ["kid"]
    assert routine.members[0]["required"] is False


@pytest.mark.parametrize("overnight", [False, "midnight_cleanup", "manual_stop"])
@pytest.mark.parametrize("approval", [False, True])
def test_timed_member_uses_original_day_and_normal_approval(overnight, approval):
    async def scenario():
        coord, store, child, chores, rid = await setup(approval=approval)
        chores[0].task_type = "timed"
        chores[0].timed_rate_minutes = 1
        chores[0].timed_rate_points = 1
        store.update_chore(chores[0])
        await coord.async_save_routine(rid, members=[{"chore_id": chores[0].id, "required": True}])
        start = NOW.replace(hour=23, minute=58)
        with patch("custom_components.taskmate.coord_routines.dt_util.now", return_value=start):
            await coord.async_start_timed_task(chores[0].id, child.id)
        stop = start + timedelta(minutes=3 if overnight else 1)
        with patch("custom_components.taskmate.coord_routines.dt_util.now", return_value=stop):
            if overnight == "midnight_cleanup":
                await coord._async_stop_stale_timed_sessions()
            else:
                await coord.async_stop_timed_task(chores[0].id, child.id)
            completion = store.get_completions()[0]
            assert completion.completed_at.date() == NOW.date()
            if approval:
                assert not store.get_routine_runs()[0]["awarded"]
                await coord.async_approve_chore(completion.id)
            assert store.get_routine_runs()[0]["awarded"]
            assert store.get_child(child.id).points == (6 if overnight else 5)

    run(scenario)


def test_pruning_preserves_pending_routine_siblings_then_discards_settled_history():
    async def scenario():
        coord, store, child, chores, _ = await setup()
        first = await coord.async_complete_chore(chores[0].id, child.id)
        second = await coord.async_complete_chore(chores[1].id, child.id)
        await coord.async_approve_chore(first.id)
        with patch("custom_components.taskmate.coord_routines.dt_util.now", return_value=NOW + timedelta(days=100)):
            await coord.async_prune_history(90)
            assert len(store.get_completions()) == 2
            await coord.async_approve_chore(second.id)
            assert store.get_child(child.id).points == 8
            await coord.async_prune_history(90)
            assert store.get_routine_runs() == []
            assert store.get_completions() == []

    run(scenario)


def test_start_after_midnight_settles_stale_timer_before_new_day():
    async def scenario():
        coord, store, child, chores, rid = await setup(approval=False)
        chores[0].task_type = "timed"
        chores[0].timed_rate_minutes = 1
        chores[0].timed_rate_points = 1
        store.update_chore(chores[0])
        await coord.async_save_routine(rid, members=[{"chore_id": chores[0].id, "required": True}])
        with patch(
            "custom_components.taskmate.coord_routines.dt_util.now", return_value=NOW.replace(hour=23, minute=58)
        ):
            await coord.async_start_timed_task(chores[0].id, child.id)
        tomorrow = NOW + timedelta(days=1)
        with patch("custom_components.taskmate.coord_routines.dt_util.now", return_value=tomorrow):
            await coord.async_start_timed_task(chores[0].id, child.id)
            assert store.get_child(child.id).points == 6
            assert store.get_active_timed_session(chores[0].id, child.id).session_date == tomorrow.date().isoformat()
            assert len(store.get_routine_runs()) == 2

    run(scenario)


def test_backup_restore_keeps_snapshot_and_reversal():
    async def scenario():
        coord, store, child, chores, _ = await setup(approval=False)
        first = await coord.async_complete_chore(chores[0].id, child.id)
        await coord.async_complete_chore(chores[1].id, child.id)
        snapshot = store.export_data()
        store.import_data(snapshot)
        await coord.async_reject_chore(first.id)
        assert store.get_child(child.id).points == 2
        assert snapshot["routine_runs"][0]["awarded"]

    run(scenario)


@pytest.mark.parametrize("damage", ["negative", "duplicate", "date", "members"])
def test_malformed_bonus_backups_do_not_replace_live_data(damage):
    async def scenario():
        coord, store, child, chores, _ = await setup(approval=False)
        await coord.async_complete_chore(chores[0].id, child.id)
        before = store.export_data()
        bad = store.export_data()
        record = bad["routine_runs"][0]
        if damage == "negative":
            record["bonus_points"] = -10
        elif damage == "duplicate":
            bad["routine_runs"].append(dict(record))
        elif damage == "date":
            record["day"] = "not-a-date"
        else:
            record["members"] = ["broken"]
        with pytest.raises(ValueError, match="Invalid routine backup"):
            store.import_data(bad)
        assert store.export_data() == before

    run(scenario)


def test_delete_child_and_chore_clean_routine_links():
    async def scenario():
        coord, store, child, chores, rid = await setup()
        await coord.async_save_routine(rid, defaults={"assigned_to": [child.id]})
        await coord.async_complete_chore(chores[0].id, child.id)
        await coord.async_remove_chore(chores[2].id)
        assert all(m["chore_id"] != chores[2].id for m in store.get_routine(rid).members)
        await coord.async_remove_child(child.id)
        assert store.get_routine_runs() == []
        assert store.get_routine(rid).defaults["assigned_to"] == []

    run(scenario)
