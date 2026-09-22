"""Checklist API boundaries preserve identity, validation, and template data."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol

import custom_components.taskmate as tm
from custom_components.taskmate import websocket as ws
from tests.test_chore_award_reversal import _make_system
from tests.test_service_parent_gate import _service_call


class Services:
    def __init__(self):
        self.handlers = {}
        self.schemas = {}
        self.responses = {}

    def async_register(self, domain, name, handler, schema=None, **kwargs):
        self.handlers[name] = handler
        self.schemas[name] = schema
        self.responses[name] = kwargs.get("supports_response")


async def setup_service(monkeypatch):
    coord, store = await _make_system()
    child = await coord.async_add_child("Maggie")
    child.linked_user_id = "maggie"
    store.update_child(child)
    coord.hass.auth = MagicMock()
    coord.hass.auth.async_get_user = AsyncMock(return_value=MagicMock(is_admin=False))
    coord.hass.services = Services()
    monkeypatch.setattr(tm, "_get_coordinator", lambda hass: coord)
    await tm._async_register_services(coord.hass)
    return coord, child


def test_checklist_service_preserves_response_and_linked_child_gate(monkeypatch):
    async def scenario():
        coord, child = await setup_service(monkeypatch)
        response = {"completed": True, "approved": False, "completion_id": "completion"}
        coord.async_set_checklist_item = AsyncMock(return_value=response)
        coord.async_record_audit = AsyncMock()
        data = {
            "chore_id": "dressed",
            "child_id": child.id,
            "item_id": "pajamas",
            "checked": True,
            "occurrence_id": "today",
            "photo_url": "",
        }
        handler = coord.hass.services.handlers["set_checklist_item"]
        assert await handler(_service_call("maggie", data)) == response
        coord.async_set_checklist_item.assert_awaited_once_with(
            "dressed", child.id, "pajamas", True, photo_url="", occurrence_id="today"
        )
        coord.async_record_audit.assert_awaited_once()
        with pytest.raises(tm.Unauthorized):
            await handler(_service_call("ellie", data))
        assert coord.async_set_checklist_item.await_count == 1

    asyncio.run(scenario())


def test_checklist_service_rejects_missing_occurrence_and_parent_override(monkeypatch):
    async def scenario():
        # The shared HA stubs do not implement config_validation coercers.
        monkeypatch.setattr(tm.cv, "string", str)
        monkeypatch.setattr(tm.cv, "boolean", bool)
        coord, child = await setup_service(monkeypatch)
        schema = coord.hass.services.schemas["set_checklist_item"]
        data = {"chore_id": "dressed", "child_id": child.id, "item_id": "pajamas", "checked": True}
        with pytest.raises(vol.Invalid):
            schema(data)
        assert schema({**data, "occurrence_id": "today"})["checked"] is True
        with pytest.raises(vol.Invalid):
            schema({**data, "occurrence_id": "today", "as_parent": True})
        assert coord.hass.services.responses["set_checklist_item"] == tm.SupportsResponse.OPTIONAL

    asyncio.run(scenario())


def test_checklist_service_converts_coordinator_errors(monkeypatch):
    async def scenario():
        coord, child = await setup_service(monkeypatch)
        coord.async_set_checklist_item = AsyncMock(side_effect=ValueError("The undo window has ended"))
        with pytest.raises(tm.ServiceValidationError, match="undo window"):
            await coord.hass.services.handlers["set_checklist_item"](
                _service_call(
                    "maggie",
                    {
                        "chore_id": "dressed",
                        "child_id": child.id,
                        "item_id": "pajamas",
                        "checked": False,
                        "occurrence_id": "today",
                    },
                )
            )

    asyncio.run(scenario())


def test_admin_add_rejects_invalid_checklist_without_leaving_a_chore(monkeypatch):
    async def scenario():
        coord, store = await _make_system()
        monkeypatch.setattr(ws, "_get_coordinator", lambda hass: coord)
        conn = MagicMock()
        conn.user.is_admin = True
        await ws._ws_add_chore(
            coord.hass,
            conn,
            {
                "id": 1,
                "type": "taskmate/add_chore",
                "name": "Get dressed",
                "task_type": "checklist",
                "checklist_items": [],
            },
        )
        conn.send_error.assert_called_once()
        assert store.get_chores() == []

    asyncio.run(scenario())


def test_admin_create_and_failed_edit_preserve_checklist(monkeypatch):
    async def scenario():
        coord, store = await _make_system()
        monkeypatch.setattr(ws, "_get_coordinator", lambda hass: coord)
        conn = MagicMock()
        conn.user.is_admin = True
        await ws._ws_add_chore(
            coord.hass,
            conn,
            {
                "id": 1,
                "type": "taskmate/add_chore",
                "name": "Get dressed",
                "points": 2,
                "task_type": "checklist",
                "checklist_items": [{"name": "Day clothes"}, {"name": "Pajamas away"}],
            },
        )
        conn.send_error.assert_not_called()
        chore = store.get_chores()[0]
        assert chore.task_type == "checklist"
        items = chore.to_dict()["checklist_items"]
        assert len({item["id"] for item in items}) == 2
        await ws._ws_update_chore(
            coord.hass,
            conn,
            {
                "id": 2,
                "type": "taskmate/update_chore",
                "chore_id": chore.id,
                "checklist_items": [],
            },
        )
        conn.send_error.assert_called_once()
        assert store.get_chore(chore.id).checklist_items == items

    asyncio.run(scenario())


def test_websocket_checklist_schema_rejects_step_points_and_oversize_lists():
    schema = vol.Schema(ws._chore_payload_schema(require_name=True))
    base = {"name": "Get dressed", "task_type": "checklist"}
    assert schema({**base, "checklist_items": [{"name": "Day clothes", "id": "a"}]})
    for items in ([{"name": "Day clothes", "points": 1}], [{"name": "x"}] * 31):
        with pytest.raises(vol.Invalid):
            schema({**base, "checklist_items": items})


def test_template_roundtrip_keeps_steps_and_creates_fresh_item_ids():
    async def scenario():
        coord, store = await _make_system()
        original = await coord.async_add_chore(
            "Get dressed",
            task_type="checklist",
            points=2,
            checklist_items=[{"id": "clothes", "name": "Day clothes"}, {"id": "pajamas", "name": "Pajamas away"}],
        )
        template_id = await coord.async_save_template_from_chores([original.id], "Morning", "mdi:shirt")
        template = coord.get_template(template_id)
        assert template["chores"][0]["checklist_items"] == original.checklist_items
        ids = await coord.async_apply_template(template["chores"])
        created = store.get_chore(ids[0])
        assert created.task_type == "checklist"
        assert [item["name"] for item in created.checklist_items] == ["Day clothes", "Pajamas away"]
        assert not {item["id"] for item in created.checklist_items} & {"clothes", "pajamas"}

    asyncio.run(scenario())


def test_invalid_checklist_template_does_not_partially_create_chores():
    async def scenario():
        coord, store = await _make_system()
        with pytest.raises(ValueError):
            await coord.async_apply_template(
                [
                    {"name": "Valid normal chore"},
                    {"name": "Invalid checklist", "task_type": "checklist", "checklist_items": []},
                ]
            )
        assert store.get_chores() == []

    asyncio.run(scenario())
