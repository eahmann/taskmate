"""The delegated family boundary is a probe, never a new mutation door.

These use the repository's isolated HA stubs. They do not establish real HA
transport or script/automation context propagation.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import custom_components.taskmate as tm
from custom_components.taskmate import authz, websocket
from custom_components.taskmate.family_api import (
    PROVIDER_VERSION,
    FamilyAccessRequest,
    FamilyAPIError,
    async_check_actor_eligibility,
    async_get_family_api,
    bind_family_api,
    unbind_family_api,
)


@pytest.fixture
def provider():
    user = SimpleNamespace(id="tablet", is_active=True, is_admin=False)
    coordinator = SimpleNamespace(
        entry_id="entry-1",
        storage=SimpleNamespace(
            get_parent_user_ids=lambda: ["parent"],
            is_retired=False,
            async_save=AsyncMock(side_effect=AssertionError("probe must not save")),
        ),
        _reset_in_progress=False,
    )
    hass = SimpleNamespace(
        data={"taskmate": {"entry-1": coordinator}},
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user)),
        services=SimpleNamespace(async_call=AsyncMock(side_effect=AssertionError("probe must not call services"))),
    )
    bind_family_api(coordinator)
    context = SimpleNamespace(user_id="tablet", id="context-1", parent_id=None)
    verify = AsyncMock(return_value=True)
    request = FamilyAccessRequest(
        operation="parent.probe",
        entry_id="entry-1",
        context=context,
        household_id="household-1",
        authorization_id="grant-1",
        verify=verify,
    )
    return SimpleNamespace(
        hass=hass, coordinator=coordinator, user=user, context=context, verify=verify, request=request
    )


async def _probe(provider, request=None):
    api = await async_get_family_api(provider.hass, "entry-1")
    return await api.async_probe(request if request is not None else provider.request)


async def test_probe_returns_only_identity_and_contract_without_mutation(provider):
    before = provider.hass.data.copy()
    assert await _probe(provider) == {
        "authorized": True,
        "protocol": 1,
        "provider_version": PROVIDER_VERSION,
        "entry_id": "entry-1",
        "actor_user_id": "tablet",
    }
    assert provider.verify.await_count == 2
    assert all(call.args == (provider.request,) for call in provider.verify.await_args_list)
    provider.hass.auth.async_get_user.assert_awaited_once_with("tablet")
    provider.coordinator.storage.async_save.assert_not_awaited()
    provider.hass.services.async_call.assert_not_awaited()
    assert provider.hass.data == before
    manifest = Path(__file__).parents[1] / "custom_components/taskmate/manifest.json"
    assert json.loads(manifest.read_text(encoding="utf-8"))["version"] == PROVIDER_VERSION


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": "chore.approve"},
        {"entry_id": "other-entry"},
        {"household_id": ""},
        {"authorization_id": ""},
        {"verify": None},
    ],
)
async def test_malformed_or_out_of_scope_request_is_denied_before_verifier(provider, changes):
    with pytest.raises(FamilyAPIError):
        await _probe(provider, replace(provider.request, **changes))
    provider.verify.assert_not_awaited()
    provider.hass.auth.async_get_user.assert_not_awaited()


async def test_plain_browser_payload_cannot_be_a_verified_request(provider):
    with pytest.raises(FamilyAPIError):
        await _probe(provider, {"parent": True, "entry_id": "entry-1", "operation": "parent.probe"})
    provider.verify.assert_not_awaited()


def test_request_is_frozen_and_does_not_accept_targets(provider):
    with pytest.raises(FrozenInstanceError):
        provider.request.entry_id = "other"
    with pytest.raises(TypeError):
        replace(provider.request, target="child-1")


@pytest.mark.parametrize("verdict", [False, None, "true", 1])
async def test_verifier_requires_literal_true(provider, verdict):
    provider.verify.return_value = verdict
    with pytest.raises(FamilyAPIError, match="authorization"):
        await _probe(provider)
    provider.hass.auth.async_get_user.assert_not_awaited()


async def test_verifier_failure_is_a_closed_generic_error(provider):
    provider.verify.side_effect = RuntimeError("private grant material")
    with pytest.raises(FamilyAPIError) as caught:
        await _probe(provider)
    assert "private" not in str(caught.value)
    assert caught.value.message == str(caught.value)


async def test_authorization_revoked_during_auth_lookup_is_denied(provider):
    provider.verify.side_effect = [True, False]
    with pytest.raises(FamilyAPIError):
        await _probe(provider)
    assert provider.verify.await_count == 2


@pytest.mark.parametrize("user", [None, SimpleNamespace(id="tablet", is_active=False, is_admin=False)])
async def test_missing_or_inactive_actor_denied(provider, user):
    provider.hass.auth.async_get_user.return_value = user
    with pytest.raises(FamilyAPIError):
        await _probe(provider)


async def test_contextless_automation_is_not_a_delegated_actor(provider):
    provider.context.user_id = None
    with pytest.raises(FamilyAPIError):
        await _probe(provider)
    provider.verify.assert_not_awaited()


@pytest.mark.parametrize("field,value", [("user_id", "admin"), ("id", "other-context"), ("parent_id", "other-parent")])
async def test_context_cannot_change_across_an_await(provider, field, value):
    async def change_context(_request):
        setattr(provider.context, field, value)
        return True

    provider.verify.side_effect = change_context
    with pytest.raises(FamilyAPIError):
        await _probe(provider)


async def test_permanent_parent_cannot_claim_secure_shared_account(provider):
    provider.coordinator.storage.get_parent_user_ids = lambda: ["tablet"]
    with pytest.raises(FamilyAPIError) as caught:
        await _probe(provider)
    assert caught.value.code == "unsafe_shared_account"


async def test_eligibility_is_fresh_and_does_not_grant_parent_access(provider):
    assert await async_check_actor_eligibility(provider.hass, "entry-1", provider.context) is True
    provider.coordinator.storage.get_parent_user_ids = lambda: ["tablet"]
    with pytest.raises(FamilyAPIError) as caught:
        await async_check_actor_eligibility(provider.hass, "entry-1", provider.context)
    assert caught.value.code == "unsafe_shared_account"
    provider.user.is_admin = True
    assert await async_check_actor_eligibility(provider.hass, "entry-1", provider.context) is True
    provider.verify.assert_not_awaited()


@pytest.mark.parametrize("change", ["unload", "replace", "reset", "retire", "parent", "deactivate"])
async def test_provider_and_actor_changes_while_verifying_fail_closed(provider, change):
    api = await async_get_family_api(provider.hass, "entry-1")

    async def changed(_request):
        if change == "unload":
            unbind_family_api(provider.coordinator)
        elif change == "replace":
            provider.hass.data["taskmate"]["entry-1"] = SimpleNamespace()
        elif change == "reset":
            provider.coordinator._reset_in_progress = True
        elif change == "retire":
            provider.coordinator.storage.is_retired = True
        elif change == "parent":
            provider.coordinator.storage.get_parent_user_ids = lambda: ["tablet"]
        else:
            provider.user.is_active = False
        return True

    calls = 0

    async def verify(request):
        nonlocal calls
        calls += 1
        return True if calls == 1 else await changed(request)

    request = replace(provider.request, verify=verify)
    with pytest.raises(FamilyAPIError):
        await api.async_probe(request)


async def test_unload_reload_invalidates_previously_obtained_api(provider):
    old_api = await async_get_family_api(provider.hass, "entry-1")
    unbind_family_api(provider.coordinator)
    bind_family_api(provider.coordinator)
    with pytest.raises(FamilyAPIError):
        await old_api.async_probe(provider.request)
    assert (await _probe(provider))["authorized"] is True


@pytest.mark.parametrize("protocol", [0, 2, True, "1"])
async def test_unknown_protocol_is_rejected(provider, protocol):
    with pytest.raises(FamilyAPIError) as caught:
        await async_get_family_api(provider.hass, "entry-1", protocol=protocol)
    assert caught.value.code == "unsupported_protocol"


async def test_missing_or_not_yet_bound_provider_is_unavailable(provider):
    with pytest.raises(FamilyAPIError):
        await async_get_family_api(provider.hass, "other-entry")
    unbind_family_api(provider.coordinator)
    with pytest.raises(FamilyAPIError):
        await async_get_family_api(provider.hass, "entry-1")


async def test_successful_probe_does_not_unlock_native_services_or_websocket(provider, monkeypatch):
    assert (await _probe(provider))["authorized"] is True
    monkeypatch.setattr(tm, "_get_coordinator", lambda _hass: provider.coordinator)
    call = SimpleNamespace(context=provider.context)
    with pytest.raises(tm.Unauthorized):
        await tm._async_require_admin(provider.hass, call)
    with pytest.raises(tm.Unauthorized):
        await tm._async_require_parent(provider.hass, call)
    assert await authz.async_context_is_admin(provider.hass, provider.context) is False
    assert await authz.async_context_is_parent(provider.hass, provider.coordinator, provider.context) is False
    connection = MagicMock(user=provider.user)
    await websocket._ws_add_child(
        provider.hass, connection, {"id": 1, "type": "taskmate/add_child", "name": "forbidden"}
    )
    connection.send_error.assert_called_once()
    connection.send_result.assert_not_called()


async def test_native_admin_parent_and_automation_policy_stays_unchanged(provider, monkeypatch):
    monkeypatch.setattr(tm, "_get_coordinator", lambda _hass: provider.coordinator)
    provider.user.is_admin = True
    await tm._async_require_admin(provider.hass, SimpleNamespace(context=provider.context))
    await tm._async_require_parent(provider.hass, SimpleNamespace(context=provider.context))
    provider.user.is_admin = False
    parent_context = SimpleNamespace(user_id="parent")
    await tm._async_require_parent(provider.hass, SimpleNamespace(context=parent_context))
    for user_id in (None, ""):
        call = SimpleNamespace(context=SimpleNamespace(user_id=user_id))
        await tm._async_require_admin(provider.hass, call)
        await tm._async_require_parent(provider.hass, call)


async def test_setup_binds_only_after_success_and_unload_invalidates_before_shutdown(provider, monkeypatch):
    class Coordinator:
        def __init__(self, *_args):
            self.entry_id = "entry-1"
            self.storage = MagicMock()
            self.storage.is_retired = False
            self.storage.async_save = AsyncMock()
            self.storage.get_parent_user_ids.return_value = []
            self.async_initialize = AsyncMock()
            self.async_shutdown = AsyncMock(side_effect=self.assert_inactive)
            self.async_add_listener = MagicMock()
            self.notifications = MagicMock()

        async def assert_inactive(self):
            with pytest.raises(FamilyAPIError):
                await async_get_family_api(hass, "entry-1")

    hass = MagicMock()
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.async_add_executor_job = AsyncMock()
    for name in ("async_register_frontend", "async_register_cards", "async_register_panel", "_async_register_services"):
        monkeypatch.setattr(tm, name, AsyncMock())
    for name in (
        "async_register_websocket_commands",
        "_async_update_service_descriptions",
        "_async_unregister_services",
    ):
        monkeypatch.setattr(tm, name, MagicMock())
    monkeypatch.setattr(tm, "TaskMateCoordinator", Coordinator)
    entry = SimpleNamespace(entry_id="entry-1", data={})
    assert await tm.async_setup_entry(hass, entry) is True
    await async_get_family_api(hass, "entry-1")
    assert await tm.async_unload_entry(hass, entry) is True
    with pytest.raises(FamilyAPIError):
        await async_get_family_api(hass, "entry-1")
