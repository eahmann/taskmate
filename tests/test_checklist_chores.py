"""Checklist progress earns one chore award and survives normal lifecycle changes."""

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest

from custom_components.taskmate.checklist import normalize_checklist_items
from custom_components.taskmate.models import Chore, TimedSession
from custom_components.taskmate.sensor import _build_chores_list, _build_todays_completions
from tests.test_chore_award_reversal import NOW, _make_system

ITEMS = [{"id": "clothes", "name": "Put daytime clothes on"}, {"id": "pajamas", "name": "Put pajamas away"}]


def run(scenario):
    with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=NOW):
        asyncio.run(scenario())


async def setup(**kwargs):
    coord, store = await _make_system()
    child = await coord.async_add_child("Maggie")
    chore = await coord.async_add_chore(
        "Get dressed", points=2, requires_approval=False, task_type="checklist", checklist_items=ITEMS, **kwargs
    )
    return coord, store, child, chore


async def check(coord, chore, child, item="clothes", checked=True, **kwargs):
    kwargs.setdefault("occurrence_id", coord.checklist_progress_for_chore(chore, child.id)["occurrence_id"])
    return await coord.async_set_checklist_item(chore.id, child.id, item, checked, **kwargs)


def test_items_work_in_any_order_with_no_partial_awards_and_one_final_chore():
    async def scenario():
        coord, store, child, chore = await setup()
        first = await check(coord, chore, child, "pajamas")
        assert not first["completed"]
        assert store.get_child(child.id).points == 0
        assert store.get_child(child.id).total_chores_completed == 0
        assert not store.get_completions()
        assert not store.get_points_transactions()
        result = await check(coord, chore, child)
        assert result["completed"] and result["approved"]
        assert result["points_awarded"] == 2
        assert store.get_child(child.id).points == 2
        assert store.get_child(child.id).total_chores_completed == 1
        assert len(store.get_completions()) == 1
        assert store.get_completions()[0].checklist_items == ITEMS

    run(scenario)


def test_partial_uncheck_preserves_other_items_and_each_child_is_independent():
    async def scenario():
        coord, store, child, chore = await setup()
        sibling = await coord.async_add_child("Ellie")
        await check(coord, chore, child)
        assert not any(i["checked"] for i in coord.checklist_progress_for_chore(chore, sibling.id)["items"])
        await check(coord, chore, child, checked=False)
        assert not any(i["checked"] for i in coord.checklist_progress_for_chore(chore, child.id)["items"])
        assert not store.get_completions()

    run(scenario)


@pytest.mark.parametrize("daily_limit", [1, 3])
def test_concurrent_and_retried_final_taps_cannot_double_award_or_start_next_run(daily_limit):
    async def scenario():
        coord, store, child, chore = await setup(daily_limit=daily_limit)
        occurrence = coord.checklist_progress_for_chore(chore, child.id)["occurrence_id"]
        await check(coord, chore, child)
        results = await asyncio.gather(
            check(coord, chore, child, "pajamas", occurrence_id=occurrence),
            check(coord, chore, child, "pajamas", occurrence_id=occurrence),
        )
        assert sum(result["completed"] for result in results) == 1
        assert store.get_child(child.id).points == 2
        assert len(store.get_completions()) == 1
        progress = coord.checklist_progress_for_chore(chore, child.id)
        if daily_limit > 1:
            assert progress["occurrence_id"] != occurrence
            assert not any(i["checked"] for i in progress["items"])
            await check(coord, chore, child)
            await check(coord, chore, child, "pajamas")
            assert store.get_child(child.id).points == 4
            await check(coord, chore, child)
            await check(coord, chore, child, "pajamas")
            assert store.get_child(child.id).points == 6

    run(scenario)


def test_pending_checklist_uncheck_uses_undo_and_manual_approval_locks_items():
    async def scenario():
        coord, store, child, chore = await setup()
        chore.requires_approval = True
        store.update_chore(chore)
        await check(coord, chore, child)
        final = await check(coord, chore, child, "pajamas")
        assert not final["approved"]
        assert store.get_child(child.id).points == 0
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=NOW + timedelta(minutes=1)):
            await check(coord, chore, child, "clothes", False)
        progress = coord.checklist_progress_for_chore(chore, child.id)
        assert [i["checked"] for i in progress["items"]] == [False, True]
        assert not store.get_completions()
        final = await check(coord, chore, child)
        await coord.async_approve_chore(final["completion_id"])
        with pytest.raises(ValueError, match="parent has reviewed"):
            await check(coord, chore, child, "pajamas", False)
        assert store.get_child(child.id).points == 2

    run(scenario)


@pytest.mark.parametrize("elapsed,allowed", [(9, True), (10, False)])
def test_autoapproved_checklist_uncheck_respects_global_undo_deadline(elapsed, allowed):
    async def scenario():
        coord, store, child, chore = await setup()
        await check(coord, chore, child)
        final = await check(coord, chore, child, "pajamas")
        old_token = final["progress"]["occurrence_id"]
        with patch(
            "custom_components.taskmate.coord_chores.dt_util.now", return_value=NOW + timedelta(seconds=elapsed)
        ):
            if allowed:
                await check(coord, chore, child, "clothes", False)
                assert store.get_child(child.id).points == 0
                assert [i["checked"] for i in coord.checklist_progress_for_chore(chore, child.id)["items"]] == [
                    False,
                    True,
                ]
                with pytest.raises(ValueError, match="occurrence has changed"):
                    await check(coord, chore, child, "pajamas", occurrence_id=old_token)
            else:
                with pytest.raises(ValueError, match="undo window"):
                    await check(coord, chore, child, "clothes", False)
                assert store.get_child(child.id).points == 2

    run(scenario)


def test_whole_chore_undo_reopens_only_last_checked_item_and_reverses_routine_bonus():
    async def scenario():
        coord, store, child, chore = await setup()
        await coord.async_save_routine(name="Morning", bonus_points=3, members=[{"chore_id": chore.id}])
        await check(coord, chore, child, "pajamas")
        final = await check(coord, chore, child)
        assert store.get_child(child.id).points == 5
        await coord.async_undo_chore(final["completion_id"])
        assert store.get_child(child.id).points == 0
        assert [i["checked"] for i in coord.checklist_progress_for_chore(chore, child.id)["items"]] == [False, True]
        await check(coord, chore, child)
        assert store.get_child(child.id).points == 5

    run(scenario)


def test_photo_gate_restores_final_checkbox_and_pending_completion_has_evidence():
    async def scenario():
        coord, store, child, chore = await setup()
        chore.require_photo = True
        store.update_chore(chore)
        await check(coord, chore, child)
        with pytest.raises(ValueError, match="requires a photo"):
            await check(coord, chore, child, "pajamas", photo_url="https://example.com/foreign.jpg")
        assert [i["checked"] for i in coord.checklist_progress_for_chore(chore, child.id)["items"]] == [True, False]
        photo = "/api/taskmate/photo/00000000000000000000000000000000.jpg"
        final = await check(coord, chore, child, "pajamas", photo_url=photo)
        assert not final["approved"]
        assert store.get_completions()[0].photo_url == photo

    run(scenario)


@pytest.mark.parametrize("parent", [False, True])
def test_complete_chore_cannot_bypass_checklist(parent):
    async def scenario():
        coord, store, child, chore = await setup()
        with pytest.raises(ValueError, match="Check every"):
            await coord.async_complete_chore(chore.id, child.id, as_parent=parent)
        with pytest.raises(ValueError, match="Check every"):
            await coord.async_parent_complete_chore(chore.id)
        with pytest.raises(ValueError, match="not a timed task"):
            await coord.async_start_timed_task(chore.id, child.id)
        assert not store.get_completions()

    run(scenario)


@pytest.mark.parametrize("restriction", ["assigned", "disabled", "off_day", "limit"])
def test_item_submission_enforces_ordinary_chore_eligibility(restriction):
    async def scenario():
        coord, store, child, chore = await setup()
        if restriction == "assigned":
            chore.assigned_to = ["other-child"]
        elif restriction == "disabled":
            chore.enabled = False
        elif restriction == "off_day":
            chore.due_days = [(NOW + timedelta(days=1)).strftime("%A").lower()]
        else:
            chore.daily_limit = 0
        store.update_chore(chore)
        with pytest.raises(ValueError, match="not available"):
            await check(coord, chore, child)
        assert not store.get_checklist_progress(chore.id, child.id)

    run(scenario)


def test_daily_progress_expires_and_old_device_token_is_rejected():
    async def scenario():
        coord, store, child, chore = await setup()
        await check(coord, chore, child)
        old_token = coord.checklist_progress_for_chore(chore, child.id)["occurrence_id"]
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=NOW + timedelta(days=1)):
            progress = coord.checklist_progress_for_chore(chore, child.id)
            assert not any(i["checked"] for i in progress["items"])
            with pytest.raises(ValueError, match="occurrence has changed"):
                await check(coord, chore, child, "pajamas", occurrence_id=old_token)
        assert not store.get_completions()

    run(scenario)


def test_rolling_recurrence_keeps_partial_work_until_submission_then_resets_when_due():
    async def scenario():
        coord, store, child, chore = await setup(schedule_mode="recurring", recurrence="weekly")
        await check(coord, chore, child)
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=NOW + timedelta(days=1)):
            assert coord.checklist_progress_for_chore(chore, child.id)["items"][0]["checked"]
            await check(coord, chore, child, "pajamas")
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=NOW + timedelta(days=8)):
            assert not any(i["checked"] for i in coord.checklist_progress_for_chore(chore, child.id)["items"])
        assert len(store.get_completions()) == 1

    run(scenario)


@pytest.mark.parametrize("mode", ["one_shot", "aligned"])
def test_occurrences_keep_progress_until_next_scheduled_opportunity(mode):
    async def scenario():
        settings = {"schedule_mode": "one_shot"}
        if mode == "aligned":
            settings = {
                "schedule_mode": "recurring",
                "recurrence": "every_2_days",
                "recurrence_start": NOW.date().isoformat(),
            }
        coord, _, child, chore = await setup(**settings)
        await check(coord, chore, child)
        token = coord.checklist_progress_for_chore(chore, child.id)["occurrence_id"]
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=NOW + timedelta(days=1)):
            progress = coord.checklist_progress_for_chore(chore, child.id)
            assert progress["occurrence_id"] == token
            assert progress["items"][0]["checked"]
        if mode == "aligned":
            with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=NOW + timedelta(days=2)):
                progress = coord.checklist_progress_for_chore(chore, child.id)
                assert progress["occurrence_id"] != token
                assert not any(i["checked"] for i in progress["items"])

    run(scenario)


def test_renaming_and_reordering_items_preserves_checked_ids():
    async def scenario():
        coord, _, child, chore = await setup()
        await check(coord, chore, child)
        token = coord.checklist_progress_for_chore(chore, child.id)["occurrence_id"]
        chore.checklist_items = list(reversed(chore.checklist_items))
        chore.checklist_items[1]["name"] = "Put on today's clothes"
        await coord.async_update_chore(chore)
        progress = coord.checklist_progress_for_chore(chore, child.id)
        assert progress["occurrence_id"] == token
        assert [i["checked"] for i in progress["items"]] == [False, True]

    run(scenario)


def test_roundtrip_backup_clone_and_delete_keep_configuration_separate_from_progress():
    async def scenario():
        coord, store, child, chore = await setup()
        await check(coord, chore, child)
        backup = store.export_data()
        store.import_data(backup)
        assert coord.checklist_progress_for_chore(chore, child.id)["items"][0]["checked"]
        clone = await coord.async_clone_chore(chore.id)
        assert [i["name"] for i in clone.checklist_items] == [i["name"] for i in ITEMS]
        assert not ({i["id"] for i in clone.checklist_items} & {i["id"] for i in ITEMS})
        assert not any(i["checked"] for i in coord.checklist_progress_for_chore(clone, child.id)["items"])
        store.remove_chore(chore.id)
        assert not store.get_checklist_progress(chore.id, child.id)
        await check(coord, clone, child, clone.checklist_items[0]["id"])
        store.remove_child(child.id)
        assert not store.get_checklist_progress(clone.id, child.id)

    run(scenario)


def test_type_change_is_blocked_until_active_timer_stops():
    async def scenario():
        coord, store, child, chore = await setup()
        chore.task_type = "timed"
        store.update_chore(chore)
        store.save_timed_session(TimedSession(chore_id=chore.id, child_id=child.id))
        chore.task_type = "checklist"
        with pytest.raises(ValueError, match="Stop active timers"):
            await coord.async_update_chore(chore)
        assert store.get_chore(chore.id).task_type == "timed"

    run(scenario)


@pytest.mark.parametrize("edit", ["type", "delete", "rename"])
def test_parent_edit_during_undo_does_not_resurrect_progress_or_return_old_definition(edit):
    async def scenario():
        coord, store, child, chore = await setup()
        await check(coord, chore, child)
        await check(coord, chore, child, "pajamas")
        reached = asyncio.Event()
        release = asyncio.Event()

        async def refresh():
            reached.set()
            await release.wait()

        coord.async_refresh = refresh
        operation = asyncio.create_task(check(coord, chore, child, "clothes", False))
        await reached.wait()
        if edit == "type":
            chore.task_type = "standard"
            store.update_chore(chore)
            store.remove_checklist_progress(chore_id=chore.id)
        elif edit == "delete":
            store.remove_chore(chore.id)
        else:
            chore.checklist_items[0]["name"] = "New wording"
            store.update_chore(chore)
        release.set()
        result = await operation
        assert not result["completed"]
        assert store.get_child(child.id).points == 0
        if edit != "rename":
            assert not result["progress"]["items"]
            assert not store.get_checklist_progress(chore.id, child.id)
        else:
            assert result["progress"]["items"][0]["name"] == "New wording"
            assert [i["checked"] for i in result["progress"]["items"]] == [False, True]

    run(scenario)


def test_sensor_and_routine_wire_state_contains_items_progress_and_completion_snapshot():
    async def scenario():
        coord, store, child, chore = await setup()
        await coord.async_save_routine(name="Morning", members=[{"chore_id": chore.id}])
        await check(coord, chore, child)
        member = coord.routine_progress_for_child(child.id)[0]["members"][0]
        assert member["task_type"] == "checklist"
        assert [i["checked"] for i in member["checklist_items"]] == [True, False]
        common = {"chores": [chore], "hass": coord.hass}
        assert _build_chores_list(coord, common)[0]["checklist_items"] == ITEMS
        await check(coord, chore, child, "pajamas")
        common = {
            "child_lookup": {child.id: child},
            "chore_lookup": {chore.id: chore},
            "all_completions": store.get_completions(),
        }
        record = _build_todays_completions(common)[0]
        assert record["checklist_occurrence_id"]
        assert all(i["checked"] for i in record["checklist_items"])

    run(scenario)


@pytest.mark.parametrize("items", [[], [{"name": " "}], [{"name": "a" * 201}], ITEMS * 2, [{"name": "a"}] * 31])
def test_invalid_checklist_configuration_is_rejected_without_creating_chore(items):
    async def scenario():
        coord, store = await _make_system()
        with pytest.raises(ValueError):
            await coord.async_add_chore("Invalid", task_type="checklist", checklist_items=items)
        assert not store.get_chores()

    run(scenario)


def test_checklist_open_ended_is_invalid_and_names_roundtrip():
    async def scenario():
        coord, store = await _make_system()
        with pytest.raises(ValueError, match="cannot be open-ended"):
            await coord.async_add_chore("Invalid", task_type="checklist", checklist_items=ITEMS, open_ended=True)
        generated = normalize_checklist_items([{"name": "  A named item  "}], required=True)
        assert generated[0]["id"] and generated[0]["name"] == "A named item"
        assert Chore.from_dict(Chore(name="Test", checklist_items=generated).to_dict()).checklist_items == generated
        assert not store.get_chores()

    run(scenario)


@pytest.mark.parametrize(
    "corrupt", ["missing_id", "null_items", "progress_type", "checked_type", "duplicate_progress", "completion_final"]
)
def test_invalid_checklist_backup_is_rejected_before_live_data_is_replaced(corrupt):
    async def scenario():
        coord, store, child, chore = await setup()
        await check(coord, chore, child)
        await check(coord, chore, child, "pajamas")
        original = store.export_data()
        backup = store.export_data()
        if corrupt == "missing_id":
            del backup["chores"][0]["checklist_items"][0]["id"]
        elif corrupt == "null_items":
            backup["chores"][0]["checklist_items"] = None
        elif corrupt == "progress_type":
            backup["checklist_progress"] = {}
        elif corrupt == "checked_type":
            backup["checklist_progress"][0]["checked_ids"] = [False]
        elif corrupt == "duplicate_progress":
            backup["checklist_progress"] *= 2
        else:
            backup["completions"][0]["checklist_final_item_id"] = "missing"
        with pytest.raises(ValueError, match="Invalid checklist backup"):
            store.import_data(backup)
        assert store.export_data() == original

    run(scenario)
