"""Child identity colors and display categories round-trip without affecting awards."""

import asyncio
from unittest.mock import MagicMock

import pytest
import voluptuous as vol

from custom_components.taskmate import websocket as ws
from custom_components.taskmate.const import DOMAIN
from custom_components.taskmate.models import Child, Chore
from tests.test_chore_award_reversal import _make_system


def test_old_data_defaults_and_new_data_round_trip():
    assert Child.from_dict({"name": "Kid"}).color == ""
    assert Chore.from_dict({"name": "Socks"}).display_category == ""
    assert Child.from_dict(Child(name="Kid", color="#4ec9bb").to_dict()).color == "#4ec9bb"
    assert Chore.from_dict(Chore(name="Socks", display_category="Laundry").to_dict()).display_category == "Laundry"


def test_color_schema_rejects_non_hex_and_category_trims():
    for value in ("#4ec9bb", ""):
        assert ws._CHILD_COLOR(value) == value
    for value in ("red", "#fff", "#123456;display:none", None):
        with pytest.raises(vol.Invalid):
            ws._CHILD_COLOR(value)
    schema = vol.Schema(ws._chore_payload_schema(require_name=False))
    assert schema({"display_category": "  Laundry  "})["display_category"] == "Laundry"
    with pytest.raises(vol.Invalid):
        schema({"display_category": "x" * 81})


def test_admin_save_export_restore_and_clear():
    async def scenario():
        coord, storage = await _make_system()
        coord.entry_id = "test"
        coord.hass.data = {DOMAIN: {"test": coord}}
        connection = MagicMock()
        connection.user.is_admin = True
        await ws._ws_add_child(coord.hass, connection, {"id": 1, "name": "Kid", "color": "#4ec9bb"})
        child = storage.get_children()[0]
        await ws._ws_add_chore(coord.hass, connection, {"id": 2, "name": "Socks", "display_category": "Laundry"})
        chore = storage.get_chores()[0]
        backup = storage.export_data()
        assert backup["children"][0]["color"] == "#4ec9bb"
        assert backup["chores"][0]["display_category"] == "Laundry"
        await ws._ws_update_child(coord.hass, connection, {"id": 3, "child_id": child.id, "color": ""})
        assert storage.get_child(child.id).color == ""
        await ws._ws_update_chore(coord.hass, connection, {"id": 4, "chore_id": chore.id, "display_category": ""})
        assert storage.get_chore(chore.id).display_category == ""
        storage.import_data(backup)
        assert storage.get_child(child.id).color == "#4ec9bb"
        assert storage.get_chore(chore.id).display_category == "Laundry"
        assert storage.get_child(child.id).points == 0
        assert storage.get_completions() == []

    asyncio.run(scenario())
