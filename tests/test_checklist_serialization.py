"""Checklist definitions survive sensor, websocket, and saved-template boundaries."""

from __future__ import annotations

import importlib.util
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol

from custom_components.taskmate import websocket as ws
from custom_components.taskmate.models import BonusSubTask, Chore, ChoreCompletion
from custom_components.taskmate.sensor import _build_chores_list, _build_todays_completions, _compute_common
from custom_components.taskmate.storage import TaskMateStorage

from .test_coordinator_logic import _make_coord


def _checklist(*, sequential=True):
    return Chore(
        id="ready",
        name="Get ready",
        description="Our morning checklist",
        task_type="checklist",
        checklist_sequential=sequential,
        points=0,
        bonus_subtasks=[
            BonusSubTask(
                id="teeth", name="Brush teeth", description="Brush every tooth", icon="mdi:toothbrush", points=0
            ),
            BonusSubTask(
                id="dress", name="Get dressed", description="Choose clean clothes", icon="mdi:tshirt-crew", points=3
            ),
        ],
    )


def _assert_checklist_fields(record, *, sequential=True):
    expected = _checklist(sequential=sequential)
    assert record["task_type"] == "checklist"
    assert record["checklist_sequential"] is sequential
    assert record["points"] == 0
    assert record["description"] == expected.description
    assert record["bonus_subtasks"] == [step.to_dict() for step in expected.bonus_subtasks]


@pytest.mark.parametrize("sequential", [False, True])
def test_chores_sensor_preserves_checklist_picture_steps_and_zero_points(sequential):
    (record,) = _build_chores_list(_make_coord(), {"chores": [_checklist(sequential=sequential)]})
    _assert_checklist_fields(record, sequential=sequential)


def test_pending_points_count_saved_steps_instead_of_repeating_parent_bonus():
    chore = _checklist()
    chore.points = 10
    coord = _make_coord()
    coord.data = {
        "chores": [chore],
        "pending_completions": [
            ChoreCompletion(
                chore_id=chore.id,
                child_id="kid",
                bonus_subtask_id="teeth",
                submitted_points=0,
                completed_at=datetime.now(timezone.utc),
            ),
            ChoreCompletion(
                chore_id=chore.id,
                child_id="kid",
                bonus_subtask_id="dress",
                submitted_points=2,
                completed_at=datetime.now(timezone.utc),
            ),
        ],
    }
    common = _compute_common(coord)
    assert common["pending_points_by_child"] == {"kid": 2}


@pytest.mark.parametrize("bonus", [0, 10])
def test_pending_checklist_sensor_exposes_saved_completion_bonus(bonus):
    chore = _checklist()
    chore.points = 99
    completion = ChoreCompletion(
        chore_id=chore.id,
        child_id="kid",
        bonus_subtask_id="dress",
        completed_at=datetime.now(timezone.utc),
        submitted_points=3,
        checklist_bonus_points=bonus,
    )
    with patch("custom_components.taskmate.sensor.dt_util.now", return_value=completion.completed_at):
        (record,) = _build_todays_completions(
            {
                "all_completions": [completion],
                "child_lookup": {},
                "chore_lookup": {chore.id: chore},
            }
        )
    assert record["points"] == 3
    assert record["checklist_bonus_points"] == bonus


@pytest.fixture(scope="module")
def websocket_schemas():
    """Capture the real command dictionaries before HA's test stub discards them.

    Load under a separate module name so replacing the decorator cannot alter
    handlers already imported by other tests. Voluptuous validates the exact
    add/update/apply command schemas, including their nested step records.
    """
    schemas = {}

    def capture(schema):
        def decorate(handler):
            schemas[handler.__name__] = vol.Schema(schema)
            return handler

        return decorate

    spec = importlib.util.spec_from_file_location("custom_components.taskmate._checklist_schema_probe", ws.__file__)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with patch.object(ws.websocket_api, "websocket_command", capture):
        spec.loader.exec_module(module)
    return schemas


def _command_payload(command, *, sequential=True):
    definition = {
        "name": "Get ready",
        "description": "Our morning checklist",
        "points": 0,
        "task_type": "checklist",
        "checklist_sequential": sequential,
        "bonus_subtasks": [step.to_dict() for step in _checklist().bonus_subtasks],
    }
    if command == "apply":
        return {"type": ws.WS_TEMPLATES_APPLY, "chores": [definition]}
    payload = {"type": ws.WS_ADD_CHORE if command == "add" else ws.WS_UPDATE_CHORE, **definition}
    if command == "update":
        payload["chore_id"] = "ready"
        del payload["name"]  # A partial edit must not start requiring a name.
    return payload


_HANDLERS = {"add": "_ws_add_chore", "update": "_ws_update_chore", "apply": "_ws_templates_apply"}


@pytest.mark.parametrize("command", ["add", "update", "apply"])
@pytest.mark.parametrize("sequential", [False, True])
def test_actual_websocket_command_schemas_accept_checklists(websocket_schemas, command, sequential):
    payload = _command_payload(command, sequential=sequential)
    output = websocket_schemas[_HANDLERS[command]](payload)
    definition = output["chores"][0] if command == "apply" else output
    _assert_checklist_fields(definition, sequential=sequential)
    # A schema pass alone must not hide a field the actual handlers drop.
    assert {"task_type", "checklist_sequential", "bonus_subtasks"} <= ws._CHORE_EDITABLE_FIELDS


@pytest.mark.parametrize("command", ["add", "update", "apply"])
@pytest.mark.parametrize(
    ("field", "value"),
    [("checklist_sequential", "yes"), ("task_type", "unsupported"), ("step_points", -1), ("step_icon", 12)],
)
def test_checklist_websocket_fields_are_validated_not_accepted_as_extra(websocket_schemas, command, field, value):
    payload = _command_payload(command)
    definition = payload["chores"][0] if command == "apply" else payload
    if field.startswith("step_"):
        definition["bonus_subtasks"][0][field.removeprefix("step_")] = value
    else:
        definition[field] = value
    with pytest.raises(vol.Invalid):
        websocket_schemas[_HANDLERS[command]](payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("sequential", [False, True])
async def test_saved_checklist_template_passes_apply_schema_and_recreates_steps(hass, websocket_schemas, sequential):
    coordinator = _make_coord()
    coordinator.storage = TaskMateStorage(hass, "checklist_serialization")
    await coordinator.storage.async_load()
    coordinator.async_refresh = AsyncMock()
    original = _checklist(sequential=sequential)
    coordinator.storage.add_chore(original)

    template_id = await coordinator.async_save_template_from_chores([original.id], "Morning", "mdi:weather-sunny")
    template = coordinator.storage.get_custom_template(template_id)
    assert template is not None
    _assert_checklist_fields(template["chores"][0], sequential=sequential)

    # The panel passes the entire saved definition through when Apply is
    # pressed, so validate that actual payload before invoking the coordinator.
    payload = websocket_schemas["_ws_templates_apply"](
        {"type": ws.WS_TEMPLATES_APPLY, "chores": deepcopy(template["chores"])}
    )
    created_ids = await coordinator.async_apply_template(payload["chores"])
    assert len(created_ids) == 1
    assert created_ids[0] != original.id
    recreated = coordinator.storage.get_chore(created_ids[0])
    assert recreated is not None
    _assert_checklist_fields(recreated.to_dict(), sequential=sequential)
    coordinator.async_refresh.assert_awaited_once()

    # Round-trip through the sensor too: the child receives the same pictures,
    # descriptions, order and zero-point amounts as the original chore.
    (sensor_record,) = _build_chores_list(coordinator, {"chores": [recreated]})
    _assert_checklist_fields(sensor_record, sequential=sequential)
