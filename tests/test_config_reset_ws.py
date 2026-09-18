"""Reset is admin-only, explicitly confirmed, and never audits the retired coordinator."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError

from custom_components import taskmate
from custom_components.taskmate import websocket as ws
from custom_components.taskmate.coordinator import TaskMateCoordinator


@pytest.fixture
def reset_endpoint(monkeypatch):
    coord = MagicMock()
    coord._reset_in_progress = False
    coord.async_reset_config = AsyncMock()
    coord.async_record_audit = AsyncMock()
    monkeypatch.setattr(ws, "_get_coordinator", lambda hass: coord)
    connection = MagicMock()
    connection.user.is_admin = True
    return coord, connection


@pytest.mark.parametrize("confirmation", [None, "", "reset taskmate", "RESET", True])
def test_reset_schema_requires_exact_confirmation(confirmation):
    schema = vol.Schema(ws._CONFIG_RESET_SCHEMA)
    msg = {"type": ws.WS_CONFIG_RESET}
    if confirmation is not None:
        msg["confirmation"] = confirmation
    with pytest.raises(vol.Invalid):
        schema(msg)


@pytest.mark.asyncio
async def test_reset_requires_admin(reset_endpoint):
    coord, connection = reset_endpoint
    connection.user.is_admin = False
    await ws._ws_config_reset(
        MagicMock(),
        connection,
        {
            "id": 1,
            "type": ws.WS_CONFIG_RESET,
            "confirmation": "RESET TASKMATE",
        },
    )
    coord.async_reset_config.assert_not_awaited()
    connection.send_result.assert_not_called()
    assert connection.send_error.call_args.args[1] == ws.websocket_api.const.ERR_UNAUTHORIZED


@pytest.mark.asyncio
async def test_reset_validates_confirmation_at_handler(reset_endpoint):
    coord, connection = reset_endpoint
    await ws._ws_config_reset(MagicMock(), connection, {"id": 1, "type": ws.WS_CONFIG_RESET})
    coord.async_reset_config.assert_not_awaited()
    connection.send_result.assert_not_called()
    assert connection.send_error.call_args.args[1] == "invalid"


@pytest.mark.asyncio
async def test_confirmed_reset_does_not_audit_old_coordinator(reset_endpoint):
    coord, connection = reset_endpoint
    await ws._ws_config_reset(
        MagicMock(),
        connection,
        {
            "id": 1,
            "type": ws.WS_CONFIG_RESET,
            "confirmation": "RESET TASKMATE",
        },
    )
    coord.async_reset_config.assert_awaited_once()
    connection.send_result.assert_called_once_with(1, {"reset": True})
    coord.async_record_audit.assert_not_awaited()
    connection.send_error.assert_not_called()


@pytest.mark.asyncio
async def test_reset_failure_is_not_reported_as_success(reset_endpoint):
    coord, connection = reset_endpoint
    coord.async_reset_config.side_effect = ValueError("Wait for active unlocks")
    await ws._ws_config_reset(
        MagicMock(),
        connection,
        {
            "id": 1,
            "type": ws.WS_CONFIG_RESET,
            "confirmation": "RESET TASKMATE",
        },
    )
    connection.send_result.assert_not_called()
    connection.send_error.assert_called_once_with(1, "invalid", "Wait for active unlocks")


@pytest.mark.asyncio
async def test_other_mutations_are_blocked_during_reset(reset_endpoint):
    coord, connection = reset_endpoint
    coord._reset_in_progress = True
    coord.async_add_child = AsyncMock()
    await ws._ws_add_child(MagicMock(), connection, {"id": 1, "type": ws.WS_ADD_CHILD, "name": "Test"})
    coord.async_add_child.assert_not_awaited()
    connection.send_result.assert_not_called()
    assert connection.send_error.call_args.args[1] == "reset_in_progress"


def test_services_cannot_get_a_coordinator_during_reset(hass):
    coord = TaskMateCoordinator(hass, "entry")
    hass.data = {"taskmate": {"entry": coord}}
    assert taskmate._get_coordinator(hass) is coord
    coord._reset_in_progress = True
    with pytest.raises(ServiceValidationError, match="resetting"):
        taskmate._get_coordinator(hass)


@pytest.mark.asyncio
async def test_retired_coordinator_requires_reload(reset_endpoint):
    coord, connection = reset_endpoint
    coord.storage.is_retired = True
    await ws._ws_config_reset(
        MagicMock(), connection, {"id": 1, "type": ws.WS_CONFIG_RESET, "confirmation": "RESET TASKMATE"}
    )
    coord.async_reset_config.assert_not_awaited()
    assert connection.send_error.call_args.args[1] == "reload_required"


def test_services_cannot_use_retired_storage(hass):
    coord = TaskMateCoordinator(hass, "entry")
    coord.storage._retired_data = {}
    hass.data = {"taskmate": {"entry": coord}}
    with pytest.raises(ServiceValidationError, match="data was reset"):
        taskmate._get_coordinator(hass)
