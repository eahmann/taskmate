"""Children can correct mistakes without gaining parent approval privileges."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

import custom_components.taskmate as tm
from custom_components.taskmate.chore_undo import child_undo_metadata
from custom_components.taskmate.models import BonusSubTask, ChoreCompletion
from custom_components.taskmate.sensor import _build_todays_completions
from custom_components.taskmate.websocket import _UPDATE_SETTINGS_SCHEMA, _ws_update_settings
from tests.test_chore_award_reversal import NOW, _make_system
from tests.test_service_parent_gate import _service_call, _Services


def run(scenario):
    with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=NOW):
        asyncio.run(scenario())


async def setup(*, approval=False, seconds=10, **kwargs):
    coord, store = await _make_system()
    store.set_setting("chore_undo_seconds", seconds)
    child = await coord.async_add_child("Maggie")
    chore = await coord.async_add_chore("Make bed", points=2, requires_approval=approval, **kwargs)
    completion = await coord.async_complete_chore(chore.id, child.id)
    return coord, store, child, chore, completion


@pytest.mark.parametrize("seconds,elapsed,allowed", [(10, 9.999, True), (10, 10, False), (30, 29, True), (0, 0, False)])
def test_global_deadline_enforced_at_exact_boundary(seconds, elapsed, allowed):
    async def scenario():
        coord, store, child, _, completion = await setup(seconds=seconds)
        with patch(
            "custom_components.taskmate.coord_chores.dt_util.now", return_value=NOW + timedelta(seconds=elapsed)
        ):
            if allowed:
                await coord.async_undo_chore(completion.id)
                assert not store.get_completions()
                assert store.get_child(child.id).points == 0
                assert store.get_child(child.id).total_chores_completed == 0
            else:
                with pytest.raises(ValueError, match="undo window"):
                    await coord.async_undo_chore(completion.id)
                assert store.get_child(child.id).points == 2

    run(scenario)


def test_pending_withdrawal_has_no_time_limit_even_with_zero_window():
    async def scenario():
        coord, store, _, _, completion = await setup(approval=True, seconds=0)
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=NOW + timedelta(days=2)):
            await coord.async_undo_chore(completion.id)
        assert not store.get_completions()

    run(scenario)


def test_manual_approval_locks_submission_even_if_parent_later_reverts_to_pending():
    async def scenario():
        coord, store, _, _, completion = await setup(approval=True)
        await coord.async_approve_chore(completion.id)
        for pending_again in (False, True):
            if pending_again:
                await coord.async_undo_chore_approval(completion.id)
            with pytest.raises(ValueError, match="parent has reviewed"):
                await coord.async_undo_chore(completion.id)
        assert len(store.get_completions()) == 1
        # The unrestricted parent correction route remains available.
        await coord.async_reject_chore(completion.id)
        assert not store.get_completions()

    run(scenario)


def test_parent_on_behalf_and_legacy_completions_are_not_child_undoable():
    async def scenario():
        coord, store, child, chore, completion = await setup(approval=True)
        await coord.async_reject_chore(completion.id)
        completion = await coord.async_complete_chore(chore.id, child.id, as_parent=True)
        with pytest.raises(ValueError):
            await coord.async_undo_chore(completion.id)
        raw = completion.to_dict()
        raw.pop("child_undo_allowed")
        raw["approved"] = False
        assert not ChoreCompletion.from_dict(raw).child_undo_allowed

    run(scenario)


def test_new_submissions_keep_eligibility_through_storage_round_trip_and_setting_changes():
    async def scenario():
        coord, store, _, _, completion = await setup(seconds=10)
        persisted = ChoreCompletion.from_dict(completion.to_dict())
        assert persisted.child_undo_allowed
        store.set_setting("chore_undo_seconds", 60)
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=NOW + timedelta(seconds=30)):
            await coord.async_undo_chore(completion.id)
        assert not store.get_completions()

    run(scenario)


def test_routine_bonus_and_chore_award_reverse_once_and_redo_is_not_double_paid():
    async def scenario():
        coord, store, child, chore, first = await setup()
        await coord.async_reject_chore(first.id)
        await coord.async_save_routine(name="Morning", bonus_points=4, members=[{"chore_id": chore.id}])
        completion = await coord.async_complete_chore(chore.id, child.id)
        assert store.get_child(child.id).points == 6
        results = await asyncio.gather(
            coord.async_undo_chore(completion.id), coord.async_undo_chore(completion.id), return_exceptions=True
        )
        assert sum(isinstance(r, ValueError) for r in results) == 1
        assert store.get_child(child.id).points == 0
        assert not coord.routine_progress_for_child(child.id)[0]["done"]
        await coord.async_complete_chore(chore.id, child.id)
        assert store.get_child(child.id).points == 6

    run(scenario)


@pytest.mark.parametrize("review_bonus", [False, True])
def test_main_undo_cannot_remove_parent_reviewed_bonus(review_bonus):
    async def scenario():
        coord, store, child, chore, completion = await setup()
        chore.bonus_subtasks = [BonusSubTask(id="bonus", name="Pillow", points=1)]
        chore.requires_approval = review_bonus
        store.update_chore(chore)
        bonus = await coord.async_complete_bonus_subtask(chore.id, "bonus", child.id)
        if review_bonus:
            await coord.async_approve_chore(bonus.id)
            with pytest.raises(ValueError):
                await coord.async_undo_chore(completion.id)
            assert store.get_child(child.id).points == 3
        else:
            await coord.async_undo_chore(completion.id)
            assert not store.get_completions()
            assert store.get_child(child.id).points == 0

    run(scenario)


def test_bonus_undo_preserves_main_chore_and_one_shot_undo_reenables_it():
    async def scenario():
        coord, store, child, chore, completion = await setup(schedule_mode="one_shot")
        assert not store.get_chore(chore.id).enabled
        chore = store.get_chore(chore.id)
        chore.bonus_subtasks = [BonusSubTask(id="bonus", name="Pillow", points=1)]
        store.update_chore(chore)
        bonus = await coord.async_complete_bonus_subtask(chore.id, "bonus", child.id)
        await coord.async_undo_chore(bonus.id)
        assert store.get_child(child.id).points == 2
        assert len(store.get_completions()) == 1
        await coord.async_undo_chore(completion.id)
        assert store.get_chore(chore.id).enabled
        assert store.get_child(child.id).points == 0

    run(scenario)


def test_todays_sensor_publishes_server_deadline_and_hides_parent_reviewed_policy():
    async def scenario():
        coord, store, child, chore, completion = await setup()
        common = {
            "all_completions": [completion],
            "child_lookup": {child.id: child},
            "chore_lookup": {chore.id: chore},
            "chore_undo_seconds": 45,
        }
        record = _build_todays_completions(common)[0]
        assert record["child_undo_until"] == (NOW + timedelta(seconds=45)).isoformat()
        completion.child_undo_allowed = False
        assert "child_undo_until" not in _build_todays_completions(common)[0]

    run(scenario)


def test_expired_cascade_deadline_is_shared_with_frontend():
    main = ChoreCompletion("c", "kid", NOW, child_undo_allowed=True)
    bonus = ChoreCompletion(
        "c", "kid", NOW - timedelta(seconds=30), approved=True, bonus_subtask_id="bonus", child_undo_allowed=True
    )
    assert child_undo_metadata(main, [main, bonus], 10) == {
        "child_undo_until": (NOW - timedelta(seconds=20)).isoformat()
    }


@pytest.mark.parametrize(
    "actor,linked,allowed", [("kid", "kid", True), ("sibling", "kid", False), ("kiosk", "", True), ("parent", "", True)]
)
def test_service_derives_child_identity_and_restricts_even_parent_accounts(monkeypatch, actor, linked, allowed):
    async def scenario():
        coord, store, child, _, completion = await setup()
        child.linked_user_id = linked
        store.update_child(child)
        coord.hass.auth = MagicMock()
        coord.hass.auth.async_get_user = AsyncMock(return_value=MagicMock(is_admin=actor == "parent"))
        coord.hass.services = _Services()
        coord.async_record_audit = AsyncMock()
        monkeypatch.setattr(tm, "_get_coordinator", lambda hass: coord)
        await tm._async_register_services(coord.hass)
        call = _service_call(actor, {"completion_id": completion.id})
        handler = coord.hass.services.handlers["undo_chore"]
        if allowed:
            await handler(call)
            assert not store.get_completions()
        else:
            with pytest.raises(tm.Unauthorized):
                await handler(call)
            assert len(store.get_completions()) == 1

    run(scenario)


def test_approval_during_service_authorization_is_rechecked(monkeypatch):
    async def scenario():
        coord, store, child, _, completion = await setup(approval=True)
        coord.hass.services = _Services()
        monkeypatch.setattr(tm, "_get_coordinator", lambda hass: coord)

        async def approve_during_auth(*args):
            await coord.async_approve_chore(completion.id)

        monkeypatch.setattr(tm, "_async_require_linked_child", approve_during_auth)
        await tm._async_register_services(coord.hass)
        with pytest.raises(tm.ServiceValidationError):
            await coord.hass.services.handlers["undo_chore"](_service_call("kiosk", {"completion_id": completion.id}))
        assert store.get_completions()[0].approved
        assert store.get_child(child.id).points == 2

    run(scenario)


def test_settings_schema_rejects_out_of_range_and_fractional_values():
    schema = vol.Schema(_UPDATE_SETTINGS_SCHEMA)
    for value in (-1, 3601, 1.5, "30", True, False):
        with pytest.raises(vol.Invalid):
            schema({"type": "taskmate/update_settings", "chore_undo_seconds": value})
    for value in (0, 10, 60, 3600):
        assert schema({"type": "taskmate/update_settings", "chore_undo_seconds": value})["chore_undo_seconds"] == value


def test_setting_saves_and_refreshes_through_admin_endpoint(monkeypatch):
    async def scenario():
        coord, store = await _make_system()
        monkeypatch.setattr(tm, "_get_coordinator", lambda hass: coord)
        import custom_components.taskmate.websocket as ws

        monkeypatch.setattr(ws, "_get_coordinator", lambda hass: coord)
        conn = MagicMock()
        conn.user.is_admin = True
        await _ws_update_settings(
            coord.hass, conn, {"id": 1, "type": "taskmate/update_settings", "chore_undo_seconds": 60}
        )
        assert store.get_chore_undo_seconds() == 60
        coord.async_refresh.assert_awaited()

    run(scenario)


def test_timed_stop_has_same_undo_policy_and_restores_points():
    async def scenario():
        coord, store = await _make_system()
        child = await coord.async_add_child("Maggie")
        chore = await coord.async_add_chore("Tidy up", requires_approval=False)
        chore.task_type = "timed"
        chore.timed_rate_points = 2
        chore.timed_rate_minutes = 1
        store.update_chore(chore)
        await coord.async_start_timed_task(chore.id, child.id)
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=NOW + timedelta(minutes=2)):
            await coord.async_stop_timed_task(chore.id, child.id)
            completion = store.get_completions()[0]
            assert completion.child_undo_allowed
            assert store.get_child(child.id).points == 4
            await coord.async_undo_chore(completion.id)
        assert store.get_child(child.id).points == 0
        assert not store.get_completions()

    run(scenario)


def test_parent_account_cannot_bypass_expiry_via_child_service(monkeypatch):
    async def scenario():
        coord, store, _, _, completion = await setup(seconds=0)
        coord.hass.auth = MagicMock()
        coord.hass.auth.async_get_user = AsyncMock(return_value=MagicMock(is_admin=True))
        coord.hass.services = _Services()
        monkeypatch.setattr(tm, "_get_coordinator", lambda hass: coord)
        await tm._async_register_services(coord.hass)
        with pytest.raises(tm.ServiceValidationError):
            await coord.hass.services.handlers["undo_chore"](_service_call("parent", {"completion_id": completion.id}))
        assert len(store.get_completions()) == 1

    run(scenario)
