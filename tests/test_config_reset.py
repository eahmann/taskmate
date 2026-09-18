"""Reset keeps the installation while safely discarding its test data."""

from __future__ import annotations

import asyncio
import copy
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.taskmate import storage as storage_module
from custom_components.taskmate.coord_badges import BUILTIN_CATALOGUE
from custom_components.taskmate.coordinator import TaskMateCoordinator
from custom_components.taskmate.models import Child, Reward
from custom_components.taskmate.storage import TaskMateStorage


@pytest.fixture(autouse=True)
def persistent_store(monkeypatch):
    """A verifier must use a different Store instance and read saved data."""
    disk = {}

    class PersistentStore:
        def __init__(self, hass, version, key, *, atomic_writes):
            assert atomic_writes
            self.key = key

        @property
        def _data(self):
            return copy.deepcopy(disk.get(self.key))

        @_data.setter
        def _data(self, data):
            disk[self.key] = copy.deepcopy(data)

        async def async_load(self):
            return self._data

        async def async_save(self, data):
            self._data = data

        def async_delay_save(self, callback, delay):
            self._data = callback()

    monkeypatch.setattr(storage_module, "Store", PersistentStore)


@pytest.mark.asyncio
async def test_reset_clears_all_data_and_settings_to_fresh_defaults(hass):
    storage = TaskMateStorage(hass, "reset")
    await storage.async_load()
    storage.add_child(Child(name="Test child", id="test", points=100))
    storage.set_points_name("Test coins")
    storage.set_points_icon("mdi:cash")
    storage.set_setting("card_design", "playroom")
    storage.set_setting("parent_user_ids", ["test-parent"])
    storage._data.update(
        {
            "audit_log": [{"action": "test"}],
            "pool_allocations": [{"child_id": "test", "reward_id": "reward", "allocated_points": 30}],
            "future_collection": [{"private": "test data"}],
            "season_points": {"2026-09": {"test": 100}},
            "quest_progress": {"quest": {"test": {"step": 2}}},
            "badges": [{"id": "custom-test-badge", "name": "Test badge"}],
        }
    )
    version = storage.data_version

    await storage.async_reset()

    assert storage.get_children() == []
    assert storage.get_pool_allocations() == []
    assert storage.get_settings() == {}
    assert storage.get_points_name() == "Stars"
    assert storage.get_points_icon() == "mdi:star"
    assert storage.get_audit_log() == []
    assert "future_collection" not in storage.data
    assert "season_points" not in storage.data
    assert "quest_progress" not in storage.data
    assert {b.id for b in storage.get_badges()} == {b.id for b in BUILTIN_CATALOGUE}
    assert storage.get_notification_config("pending_reward_claim").master_enabled
    assert storage.get_notification_config("pending_reward_claim").routes == {}
    assert storage.is_initial_setup_done()
    assert storage.data_version == version + 1
    assert storage._store._data == storage.data

    # The next load must keep the reset currency/settings and never migrate
    # fresh balances as if they used old pool semantics.
    reloaded = TaskMateStorage(hass, "reset")
    reloaded._store._data = copy.deepcopy(storage._store._data)
    await reloaded.async_load()
    assert reloaded.data == storage.data
    reloaded.add_child(Child(name="Real child", id="live"))
    await reloaded.async_save()
    assert reloaded.get_child("live") is not None


@pytest.mark.asyncio
async def test_failed_reset_keeps_live_records_and_version(hass):
    storage = TaskMateStorage(hass, "reset")
    await storage.async_load()
    storage.add_child(Child(name="Keep me", id="child", points=70))
    old_data = storage.data
    old_snapshot = storage.export_data()
    version = storage.data_version
    storage._store.async_save = AsyncMock(side_effect=OSError("disk full"))

    with pytest.raises(OSError, match="disk full"):
        await storage.async_reset()

    assert storage.data is old_data
    assert storage.data == old_snapshot
    assert storage.data_version == version
    assert storage._retired_data is None


@pytest.mark.asyncio
async def test_silent_store_write_failure_is_detected_before_reset(hass):
    storage = TaskMateStorage(hass, "reset")
    await storage.async_load()
    storage.add_child(Child(name="Keep me", id="child", points=70))
    await storage.async_save()
    old_data = storage.data
    old_snapshot = storage.export_data()
    version = storage.data_version
    # HA catches disk write errors internally and only logs them.
    storage._store.async_save = AsyncMock()

    with pytest.raises(OSError, match="Could not verify"):
        await storage.async_reset()

    assert storage.data is old_data
    assert storage.data == old_snapshot
    assert storage._store._data == old_snapshot
    assert storage.data_version == version
    assert storage._retired_data is None


@pytest.mark.asyncio
async def test_reset_does_not_discard_actions_during_persistence(hass):
    storage = TaskMateStorage(hass, "reset")
    await storage.async_load()
    writes = []

    async def write(data):
        writes.append(copy.deepcopy(data))
        if len(writes) == 1:
            storage.add_child(Child(name="Concurrent child", id="new"))
            await storage.async_save()

    storage._store.async_save = write
    with pytest.raises(ValueError, match="changed during reset"):
        await storage.async_reset()

    assert storage.get_child("new") is not None
    assert writes[-1] == storage.data
    assert storage._retired_data is None


@pytest.mark.asyncio
async def test_retired_storage_cannot_persist_stale_action_or_delayed_save(hass):
    storage = TaskMateStorage(hass, "reset")
    await storage.async_load()
    pending = []
    storage._store.async_delay_save = lambda callback, delay: pending.append(callback)
    await storage.async_save()
    await storage.async_reset()
    clean = copy.deepcopy(storage._store._data)

    # An operation suspended before reset may resume with its old child object.
    storage.update_child(Child(name="Stale child", id="old", points=90))
    with pytest.raises(ValueError, match="has been reset"):
        await storage.async_save()
    await storage.async_save_now()  # old coordinator's shutdown flush
    assert pending[0]() == clean
    assert storage._store._data == clean


@pytest.mark.asyncio
async def test_reset_preserves_upload_files(hass, tmp_path):
    photo = tmp_path / ("a" * 32 + ".jpg")
    photo.write_bytes(b"uploaded evidence")
    storage = TaskMateStorage(hass, "reset")
    await storage.async_load()
    storage._data["completions"] = [{"photo_url": "/api/taskmate/photo/" + photo.name}]

    await storage.async_reset()

    assert storage.get_completions() == []
    assert photo.read_bytes() == b"uploaded evidence"


def _coordinator(hass, monkeypatch):
    coord = TaskMateCoordinator(hass, "entry")
    coord.storage.async_reset = AsyncMock()
    coord.async_shutdown = AsyncMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_reload = AsyncMock(return_value=True)
    registry_api = MagicMock()
    registry_api.async_entries_for_config_entry.return_value = []
    monkeypatch.setattr(sys.modules["homeassistant.helpers"], "entity_registry", registry_api)
    return coord, registry_api


@pytest.mark.asyncio
async def test_reset_reloads_and_removes_only_dynamic_taskmate_entities(hass, monkeypatch):
    coord, er = _coordinator(hass, monkeypatch)
    dynamic = [
        "child_points",
        "child_stats",
        "child_badges",
        "child_calendar",
        "child_todo",
        "kid_chore_complete",
        "kid_reward_claim",
    ]
    stable = [
        "overall_stats",
        "pending_approvals",
        "chores",
        "rewards",
        "activity",
        "incentives",
        "setting_history_days",
    ]
    er.async_entries_for_config_entry.return_value = [
        SimpleNamespace(platform="taskmate", unique_id=f"entry_{uid}", entity_id=f"sensor.{uid}")
        for uid in dynamic + stable
    ] + [
        SimpleNamespace(platform="other", unique_id="entry_other_points", entity_id="sensor.other"),
        SimpleNamespace(platform="taskmate", unique_id="other_child_points", entity_id="sensor.other_entry"),
    ]

    await coord.async_reset_config()

    coord.storage.async_reset.assert_awaited_once()
    coord.async_shutdown.assert_awaited_once()
    er.async_entries_for_config_entry.assert_called_once_with(er.async_get.return_value, "entry")
    assert [c.args[0] for c in er.async_get.return_value.async_remove.call_args_list] == [
        f"sensor.{uid}" for uid in dynamic
    ]
    hass.config_entries.async_reload.assert_awaited_once_with("entry")
    assert not coord._reset_in_progress


@pytest.mark.asyncio
async def test_reset_rejects_active_unlock_before_mutating(hass, monkeypatch):
    coord, er = _coordinator(hass, monkeypatch)
    coord.storage.set_setting("active_unlocks", [{"entity_id": "switch.tv", "revert_at": "later"}])

    with pytest.raises(ValueError, match="active timed reward unlocks"):
        await coord.async_reset_config()

    coord.storage.async_reset.assert_not_awaited()
    coord.async_shutdown.assert_not_awaited()
    er.async_get.assert_not_called()


@pytest.mark.asyncio
async def test_failed_reset_does_not_cancel_timers_or_remove_entities(hass, monkeypatch):
    coord, er = _coordinator(hass, monkeypatch)
    coord.storage.async_reset.side_effect = OSError("disk full")

    with pytest.raises(ValueError, match="Existing TaskMate data has been kept"):
        await coord.async_reset_config()

    coord.async_shutdown.assert_not_awaited()
    er.async_get.assert_not_called()
    hass.config_entries.async_reload.assert_not_awaited()
    assert not coord._reset_in_progress


@pytest.mark.asyncio
async def test_reset_reports_when_data_cleared_but_reload_failed(hass, monkeypatch):
    coord, _ = _coordinator(hass, monkeypatch)
    hass.config_entries.async_reload.return_value = False

    with pytest.raises(ValueError, match="data was reset, but the integration could not reload"):
        await coord.async_reset_config()

    coord.storage.async_reset.assert_awaited_once()
    assert not coord._reset_in_progress


@pytest.mark.asyncio
async def test_reset_rejects_simultaneous_reset(hass, monkeypatch):
    coord, _ = _coordinator(hass, monkeypatch)
    coord._reset_in_progress = True

    with pytest.raises(ValueError, match="already resetting"):
        await coord.async_reset_config()

    coord.storage.async_reset.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_waits_for_an_unlock_whose_device_call_is_in_flight(hass, monkeypatch):
    coord, _ = _coordinator(hass, monkeypatch)
    coord.storage.set_setting("unlock_allowlist", ["switch.tv"])
    coord._schedule_revert = MagicMock()
    entered, release = asyncio.Event(), asyncio.Event()

    async def turn_on(*_args, **_kwargs):
        entered.set()
        await release.wait()

    hass.services.async_call = turn_on
    start = asyncio.create_task(
        coord.async_start_unlock(
            Reward(name="TV time", unlock_entity="switch.tv", unlock_minutes=30), Child(name="Kid")
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=2)
    try:
        assert coord.active_unlocks() == []
        with pytest.raises(ValueError, match="active timed reward unlocks"):
            await coord.async_reset_config()
        coord.storage.async_reset.assert_not_awaited()
    finally:
        release.set()
        await start
    assert len(coord.active_unlocks()) == 1
    coord._schedule_revert.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("retired", [False, True])
async def test_old_approval_cannot_start_a_device_unlock_after_reset(hass, monkeypatch, retired):
    coord, _ = _coordinator(hass, monkeypatch)
    coord.storage.set_setting("unlock_allowlist", ["switch.tv"])
    if retired:
        coord.storage._retired_data = {}
    else:
        coord._reset_in_progress = True
    result = await coord.async_start_unlock(
        Reward(name="TV time", unlock_entity="switch.tv", unlock_minutes=30), Child(name="Kid")
    )
    assert result is None
    hass.services.async_call.assert_not_awaited()
