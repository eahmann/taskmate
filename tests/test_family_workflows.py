"""Protocol-2 workflows use TaskMate rules and durable, nonreplayed commands."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.taskmate.family_api import (
    FamilyAPIError,
    async_get_family_api,
    bind_family_api,
    unbind_family_api,
)
from custom_components.taskmate.family_workflows import FamilyWorkflowRequest
from tests.test_chore_award_reversal import _make_system


def test_public_family_api_exports_the_exact_workflow_request_contract():
    from custom_components.taskmate import family_api

    assert family_api.FamilyWorkflowRequest is FamilyWorkflowRequest


@pytest.fixture
async def family():
    coord, store = await _make_system()
    child = await coord.async_add_child("Alex")
    other = await coord.async_add_child("Sam")
    chore = await coord.async_add_chore("Brush teeth", 3, assigned_to=[child.id])
    reward = await coord.async_add_reward("Movie", 5, assigned_to=[child.id])
    user = SimpleNamespace(id="tablet", is_active=True, is_admin=False)
    coord.hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=user))
    coord.hass.data = {"taskmate": {"entry": coord}}
    coord._async_notify_pending_reward_claim = AsyncMock()
    coord.entry_id = "entry"
    persisted = []

    async def confirm():
        persisted.append(deepcopy(store.data))

    store.async_confirmed_save = confirm
    bind_family_api(coord)
    api = await async_get_family_api(coord.hass, "entry", protocol=2)
    context = SimpleNamespace(user_id="tablet", id="actual-context", parent_id=None)
    verify = AsyncMock(return_value=True)
    request = FamilyWorkflowRequest(
        operation="snapshot",
        entry_id="entry",
        context=context,
        household_id="home",
        authorization_id="grant",
        allowed_child_ids=(child.id,),
        verify=verify,
    )
    return SimpleNamespace(
        coord=coord,
        store=store,
        child=child,
        other=other,
        chore=chore,
        reward=reward,
        user=user,
        api=api,
        request=request,
        persisted=persisted,
    )


async def command(family, operation, data, *, identity=None, request=None):
    request = request or family.request
    snapshot = await family.api.async_snapshot(request)
    return await family.api.async_command(
        replace(
            request,
            operation=operation,
            data=data,
            command_id=identity or f"command-{len(family.persisted)}",
            expected_revision=snapshot["revision"],
        )
    )


async def test_snapshot_is_scoped_detached_and_has_authoritative_child_affordances(family):
    before = deepcopy(family.store.data)
    snapshot = await family.api.async_snapshot(family.request)
    assert snapshot["protocol"] == 2
    assert [child["id"] for child in snapshot["children"]] == [family.child.id]
    assert snapshot["children"][0]["chores"][0]["can_complete"]
    assert snapshot["children"][0]["chores"][0]["status"] == "available"
    assert "linked_user_id" not in str(snapshot)
    snapshot["chores"][0]["name"] = "tampered"
    assert family.store.data == before
    assert family.persisted == []


async def test_complete_approve_correct_and_reward_request_use_existing_awards(family):
    completion = await command(
        family, "child.chore.complete", {"child_id": family.child.id, "chore_id": family.chore.id}
    )
    cid = completion["result"]["id"]
    assert family.store.get_child(family.child.id).points == 0
    await command(family, "chore.approve", {"completion_id": cid, "points": 7})
    assert family.store.get_child(family.child.id).points == 7
    claim = await command(family, "child.reward.request", {"child_id": family.child.id, "reward_id": family.reward.id})
    await command(family, "reward.approve", {"claim_id": claim["result"]["id"]})
    assert family.store.get_child(family.child.id).points == 2
    await command(family, "chore.undo_approval", {"completion_id": cid})
    assert not family.store.get_completions()[0].approved


async def test_duplicate_commands_and_reload_never_repeat_points(family):
    snapshot = await family.api.async_snapshot(family.request)
    request = replace(
        family.request,
        operation="points.adjust",
        command_id="durable-id",
        expected_revision=snapshot["revision"],
        data={"child_id": family.child.id, "points": 9, "reason": "Helped tidy"},
    )
    first = await family.api.async_command(request)
    assert first["status"] == "committed"
    duplicate = await family.api.async_command(request)
    assert duplicate["status"] == "duplicate"
    unbind_family_api(family.coord)
    bind_family_api(family.coord)
    api = await async_get_family_api(family.coord.hass, "entry", protocol=2)
    assert (await api.async_command(request))["status"] == "duplicate"
    assert family.store.get_child(family.child.id).points == 9
    with pytest.raises(FamilyAPIError):
        await api.async_command(replace(request, data={**request.data, "points": 10}))


@pytest.mark.parametrize("mutation", ["target", "role", "context", "payload", "inactive", "stale"])
async def test_forged_or_stale_request_never_mutates(family, mutation):
    snapshot = await family.api.async_snapshot(family.request)
    request = replace(
        family.request,
        operation="points.adjust",
        command_id="forged",
        expected_revision=snapshot["revision"],
        data={"child_id": family.child.id, "points": 9, "reason": "Change"},
    )
    if mutation == "target":
        request = replace(request, data={**request.data, "child_id": family.other.id})
    elif mutation == "role":
        request = replace(request, authorization_id="")
    elif mutation == "context":
        request.context.user_id = "somebody-else"
    elif mutation == "payload":
        request.data["points"] = 100
    elif mutation == "inactive":
        family.user.is_active = False
    else:
        request = replace(request, expected_revision="obsolete")
    with pytest.raises(FamilyAPIError):
        await family.api.async_command(request)
    assert family.store.get_child(family.child.id).points == 0


async def test_child_link_rule_still_applies_even_with_parent_session(family):
    child = family.store.get_child(family.child.id)
    child.linked_user_id = "someone-else"
    family.store.update_child(child)
    with pytest.raises(FamilyAPIError):
        await command(family, "child.chore.complete", {"child_id": child.id, "chore_id": family.chore.id})
    assert family.store.get_completions() == []


async def test_queue_revocation_and_unknown_storage_write_are_closed(family):
    lock = family.coord._family_api_lifetime.command_lock
    await lock.acquire()
    task = asyncio.create_task(
        command(family, "points.adjust", {"child_id": family.child.id, "points": 4, "reason": "Bonus"})
    )
    await asyncio.sleep(0)
    family.request.verify.return_value = False
    lock.release()
    with pytest.raises(FamilyAPIError):
        await task
    assert family.store.get_child(family.child.id).points == 0
    family.request.verify.return_value = True
    family.store.async_confirmed_save = AsyncMock(side_effect=OSError("private path"))
    with pytest.raises(FamilyAPIError) as caught:
        await command(family, "points.adjust", {"child_id": family.child.id, "points": 4, "reason": "Bonus"})
    assert caught.value.code == "command_uncertain"
    assert "private" not in str(caught.value)
    assert family.store.get_child(family.child.id).points == 0


async def test_crud_retains_hidden_fields_and_history_requires_archive(family):
    chore = family.store.get_chore(family.chore.id)
    chore.claim_allowance_minutes = 18
    family.store.update_chore(chore)
    await command(family, "chore.update", {"id": chore.id, "fields": {"name": "Teeth"}})
    assert family.store.get_chore(chore.id).claim_allowance_minutes == 18
    await command(family, "child.chore.complete", {"child_id": family.child.id, "chore_id": chore.id})
    with pytest.raises(FamilyAPIError):
        await command(family, "chore.delete", {"id": chore.id})
    await command(family, "chore.archive", {"id": chore.id})
    assert not family.store.get_chore(chore.id).enabled
    assert family.store.get_completions()


async def test_checklist_progress_and_routine_bonus_reuse_domain_methods(family):
    result = await command(
        family,
        "chore.create",
        {
            "fields": {
                "name": "Get ready",
                "points": 2,
                "assigned_to": [family.child.id],
                "requires_approval": False,
                "task_type": "checklist",
                "checklist_items": [{"name": "Get dressed"}, {"name": "Put pajamas away"}],
            }
        },
    )
    cid = result["result"]["id"]
    await command(
        family,
        "routine.create",
        {
            "fields": {
                "name": "Morning",
                "bonus_points": 4,
                "members": [{"chore_id": cid, "required": True}],
                "defaults": {"assigned_to": [family.child.id]},
            }
        },
    )
    progress = family.coord.checklist_progress_for_chore(family.store.get_chore(cid), family.child.id)
    for item in progress["items"]:
        await command(
            family,
            "child.checklist.set",
            {
                "child_id": family.child.id,
                "chore_id": cid,
                "item_id": item["id"],
                "checked": True,
                "occurrence_id": progress["occurrence_id"],
            },
        )
    assert family.store.get_child(family.child.id).points == 6


async def test_calendar_device_unlock_and_unmapped_record_scope_cannot_be_mutated(family):
    chore = family.store.get_chore(family.chore.id)
    chore.publish_calendar_entities = ["calendar.private"]
    family.store.update_chore(chore)
    for operation in ["chore.update", "chore.archive", "chore.delete"]:
        with pytest.raises(FamilyAPIError):
            await command(
                family,
                operation,
                {"id": chore.id, **({"fields": {"name": "Changed"}} if operation.endswith("update") else {})},
            )
    reward = family.store.get_reward(family.reward.id)
    reward.unlock_entity = "switch.private"
    family.store.update_reward(reward)
    with pytest.raises(FamilyAPIError):
        await command(family, "reward.update", {"id": reward.id, "fields": {"cost": 1}})
    chore.publish_calendar_entities = []
    chore.assigned_to.append(family.other.id)
    family.store.update_chore(chore)
    with pytest.raises(FamilyAPIError):
        await command(family, "chore.update", {"id": chore.id, "fields": {"assigned_to": [family.child.id]}})
    family.coord.hass.services.async_call.assert_not_awaited()


async def test_chore_creation_cannot_overwrite_native_edit_during_refresh(family):
    edited = False

    async def native_edit():
        nonlocal edited
        chore = next((c for c in family.store.get_chores() if c.name == "New chore"), None)
        if chore is not None and not edited:
            edited = True
            chore.description = "Native edit after creation"
            family.store.update_chore(chore)
            await family.store.async_save()

    family.coord.async_refresh = native_edit
    result = await command(family, "chore.create", {"fields": {"name": "New chore", "display_category": "Laundry"}})
    saved = family.store.get_chore(result["result"]["id"])
    assert saved.display_category == "Laundry"
    assert saved.description == "Native edit after creation"


async def test_deleted_actor_during_last_verifier_is_rejected(family):
    calls = 0

    async def verify(_request):
        nonlocal calls
        calls += 1
        if calls == 2:
            family.coord.hass.auth.async_get_user.return_value = None
        return True

    with pytest.raises(FamilyAPIError):
        await family.api.async_snapshot(replace(family.request, verify=verify))


async def test_open_ended_submission_and_special_chore_capabilities(family):
    family.store.data["points_name"] = "Acorns"
    result = await command(family, "chore.create", {"fields": {"name": "Help out", "open_ended": True}})
    chore_id = result["result"]["id"]
    photo = await family.coord.async_add_chore("Photo task", 1, assigned_to=[family.child.id])
    photo.require_photo = True
    family.store.update_chore(photo)
    snapshot = await family.api.async_snapshot(family.request)
    assert snapshot["points_name"] == "Acorns"
    assert next(c for c in snapshot["chores"] if c["id"] == chore_id)["open_ended"] is True
    assert next(c for c in snapshot["chores"] if c["id"] == photo.id)["require_photo"] is True
    assert not next(c for c in snapshot["children"][0]["chores"] if c["chore_id"] == photo.id)["can_complete"]
    with pytest.raises(FamilyAPIError):
        await command(family, "child.chore.complete", {"chore_id": chore_id, "child_id": family.child.id})
    completion = await command(
        family,
        "child.chore.complete",
        {"chore_id": chore_id, "child_id": family.child.id, "note": "Helped clear the table", "suggested_points": 4},
    )
    saved = next(c for c in family.store.get_completions() if c.id == completion["result"]["id"])
    assert saved.note == "Helped clear the table"
    assert saved.suggested_points == 4
    assert not saved.approved


async def test_pruned_completion_history_still_prevents_destructive_chore_delete(family):
    family.store.data["last_completed"][family.chore.id] = {family.child.id: {"current": "2026-09-27T09:00:00+00:00"}}
    with pytest.raises(FamilyAPIError) as caught:
        await command(family, "chore.delete", {"id": family.chore.id})
    assert caught.value.code == "has_history"
    assert family.store.get_chore(family.chore.id) is not None


async def test_evidence_review_and_device_claims_are_explicitly_unsupported(family):
    from custom_components.taskmate.models import ChoreCompletion, RewardClaim

    completion = ChoreCompletion(
        chore_id=family.chore.id,
        child_id=family.child.id,
        completed_at=dt_util.now(),
        photo_url="/local/taskmate/evidence.jpg",
    )
    family.store.add_completion(completion)
    reward = family.store.get_reward(family.reward.id)
    reward.unlock_entity = "switch.playroom"
    family.store.update_reward(reward)
    claim = RewardClaim(reward_id=reward.id, child_id=family.child.id, claimed_at=dt_util.now())
    family.store.add_reward_claim(claim)
    snapshot = await family.api.async_snapshot(family.request)
    record = next(c for c in snapshot["completions"] if c["id"] == completion.id)
    assert record["can_review"] is False and record["can_undo"] is False
    assert record["unsupported_reason"]
    record = next(c for c in snapshot["reward_claims"] if c["id"] == claim.id)
    assert record["can_review"] is False and record["unsupported_reason"]
    for operation in ("chore.approve", "chore.reject", "chore.undo_approval", "child.chore.undo"):
        with pytest.raises(FamilyAPIError) as caught:
            await command(family, operation, {"completion_id": completion.id})
        assert caught.value.code == "unsupported_operation"
    assert family.store.get_completions()[0].photo_url == completion.photo_url


@pytest.mark.parametrize("kind", ["chore", "routine", "reward"])
async def test_create_edit_retire_and_delete_unused_records(family, kind):
    created = await command(family, f"{kind}.create", {"fields": {"name": f"New {kind}"}})
    identity = created["result"]["id"]
    await command(family, f"{kind}.update", {"id": identity, "fields": {"description": "Edited details"}})
    saved = getattr(family.store, f"get_{kind}")(identity)
    assert saved.description == "Edited details"
    await command(family, f"{kind}.archive", {"id": identity})
    saved = getattr(family.store, f"get_{kind}")(identity)
    assert getattr(saved, {"chore": "enabled", "routine": "active", "reward": "quantity"}[kind]) == 0
    await command(family, f"{kind}.delete", {"id": identity})
    assert getattr(family.store, f"get_{kind}")(identity) is None


async def test_child_withdrawal_and_parent_rejection_keep_other_children_untouched(family):
    family.store.update_child(replace(family.other, points=22))
    first = await command(family, "child.chore.complete", {"child_id": family.child.id, "chore_id": family.chore.id})
    await command(family, "child.chore.undo", {"completion_id": first["result"]["id"]})
    second = await command(family, "child.chore.complete", {"child_id": family.child.id, "chore_id": family.chore.id})
    await command(family, "chore.reject", {"completion_id": second["result"]["id"]})
    await command(family, "points.adjust", {"child_id": family.child.id, "points": 10, "reason": "Allowance"})
    claim = await command(family, "child.reward.request", {"child_id": family.child.id, "reward_id": family.reward.id})
    snapshot = await family.api.async_snapshot(family.request)
    assert snapshot["children"][0]["available_points"] == 5
    await command(family, "reward.reject", {"claim_id": claim["result"]["id"]})
    await command(family, "points.adjust", {"child_id": family.child.id, "points": -2, "reason": "Correction"})
    assert family.store.get_child(family.child.id).points == 8
    assert family.store.get_child(family.other.id).points == 22
    assert family.store.get_reward_claims() == []
    assert family.store.get_completions() == []


async def test_schedule_limits_and_rejected_validation_do_not_poison_next_command(family):
    chore = family.store.get_chore(family.chore.id)
    chore.due_days = ["sunday"]  # Fixture clock is Wednesday.
    family.store.update_chore(chore)
    snapshot = await family.api.async_snapshot(family.request)
    assert snapshot["children"][0]["chores"][0]["can_complete"] is False
    with pytest.raises(FamilyAPIError):
        await command(family, "child.chore.complete", {"child_id": family.child.id, "chore_id": chore.id})
    with pytest.raises(FamilyAPIError):
        await command(
            family,
            "routine.create",
            {
                "fields": {
                    "name": "Invalid",
                    "members": [{"chore_id": chore.id, "required": True}, {"chore_id": chore.id, "required": True}],
                }
            },
        )
    await command(family, "chore.update", {"id": chore.id, "fields": {"due_days": []}})
    await command(family, "child.chore.complete", {"child_id": family.child.id, "chore_id": chore.id})
    assert len(family.store.get_completions()) == 1
    assert (await family.api.async_snapshot(family.request))["children"][0]["chores"][0]["can_complete"] is False


async def test_reward_claim_history_prevents_delete_and_unmapped_history_edit(family):
    from custom_components.taskmate.models import RewardClaim

    old_claim = RewardClaim(
        reward_id=family.reward.id, child_id=family.other.id, claimed_at=dt_util.now(), approved=True
    )
    family.store.add_reward_claim(old_claim)
    with pytest.raises(FamilyAPIError):
        await command(family, "reward.delete", {"id": family.reward.id})
    with pytest.raises(FamilyAPIError):
        await command(family, "reward.update", {"id": family.reward.id, "fields": {"cost": 2}})
    assert family.store.get_reward_claims()[0].approved_cost is None
    assert family.store.get_reward(family.reward.id).cost == 5


@pytest.mark.parametrize("reference", ["quest", "checklist_progress", "mandatory_misses"])
async def test_unfinished_progress_and_quest_references_prevent_chore_delete(family, reference):
    if reference == "quest":
        from custom_components.taskmate.models import Quest

        family.store.add_quest(Quest(name="Morning quest", steps=[family.chore.id]))
    elif reference == "mandatory_misses":
        from custom_components.taskmate.models import MandatoryMiss

        family.store.add_mandatory_miss(
            MandatoryMiss(
                chore_id=family.chore.id, child_id=family.child.id, due_date="2024-03-19", period_id="anytime"
            )
        )
    else:
        family.store.save_checklist_progress(
            {"chore_id": family.chore.id, "child_id": family.child.id, "checked_ids": ["first"]}
        )
    snapshot = await family.api.async_snapshot(family.request)
    assert snapshot["chores"][0]["can_delete"] is False
    with pytest.raises(FamilyAPIError) as caught:
        await command(family, "chore.delete", {"id": family.chore.id})
    assert caught.value.code == "has_history"
    assert family.store.get_chore(family.chore.id) is not None


async def test_edit_revision_stays_valid_across_time_without_record_changes(family, monkeypatch):
    initial = await family.api.async_snapshot(family.request)
    later = dt_util.now() + timedelta(minutes=5)
    monkeypatch.setattr(dt_util, "now", lambda: later)
    assert (await family.api.async_snapshot(family.request))["revision"] == initial["revision"]
    saved = await family.api.async_command(
        replace(
            family.request,
            operation="chore.update",
            command_id="long-open-form",
            expected_revision=initial["revision"],
            data={"id": family.chore.id, "fields": {"name": "Teeth"}},
        )
    )
    assert saved["status"] == "committed"
    assert family.store.get_chore(family.chore.id).name == "Teeth"
