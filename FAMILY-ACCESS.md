# Family companion access: protocol 1

Fork version `5.6.1+eahmann.13` adds an **in-process, nonmutating parent-access probe** for a trusted Home Assistant companion integration. It does not add chore, routine, reward, point, calendar, or entity-control operations. The change is prepared for review; these tests do not establish deployment or live family acceptance.

## Boundary

The API lives in `custom_components/taskmate/family_api.py`. It has no Home Assistant service, HTTP endpoint, or WebSocket command. An installed companion backend constructs a `FamilyAccessRequest` from its authenticated HA connection and supplies a server-held asynchronous verifier. Browser messages must never supply the verifier, a trusted role, or an already-verified request.

This boundary trusts installed Python integrations. It is not a sandbox against another integration that can execute arbitrary code in Home Assistant.

```python
api = await async_get_family_api(hass, taskmate_entry_id, protocol=1)
request = FamilyAccessRequest(
    operation="parent.probe",
    entry_id=taskmate_entry_id,
    context=connection.context(msg),
    household_id=household_entry_id,
    authorization_id=server_session_reference,
    verify=verify_exact_server_request,
)
result = await api.async_probe(request)
```

The companion's verifier must check the exact request object, actual connection and acting HA user, live parent session, session reference, household and policy revision, exact TaskMate entry, and requested operation. It returns literal `True` only while those bindings are valid. It must check current state after any await it performs. A successful account eligibility check is not a session or an authorization grant.

The frozen request has exactly these constructor fields: `operation`, `entry_id`, `context`, `household_id`, `authorization_id`, and `verify`. It accepts no targets or mutation payload. TaskMate snapshots the original HA Context identity and rejects changes across awaits. The verifier runs before the fresh HA user lookup and again afterward. TaskMate also checks the loaded entry, integration lifetime, reset state, actor activity, and permanent parent policy before returning.

Success contains only:

```json
{
  "authorized": true,
  "protocol": 1,
  "provider_version": "5.6.1+eahmann.13",
  "entry_id": "the-requested-entry",
  "actor_user_id": "the-actual-HA-user"
}
```

The probe does not read the administrative state snapshot, return household records, save provider storage, persist an audit record, call a service, or enable any native TaskMate privilege. Unloading invalidates held adapters before coordinator shutdown. Reloading creates a fresh lifetime; an adapter retained from the old lifetime is rejected even if the coordinator object is reused.

`FamilyAPIError` exposes safe `code` and `message` fields. Codes are `unauthorized`, `unsafe_shared_account`, `provider_unavailable`, and `unsupported_protocol`. Verifier exception details are not exposed. Cancellation propagates normally.

## Shared-account eligibility

`await async_check_actor_eligibility(hass, entry_id, context)` returns `True` for an active, eligible actor or raises `FamilyAPIError`. It performs no mutation and grants no capability.

A shared display must use a non-admin HA account that is **not** in TaskMate's permanent parent list. Non-admin permanent parents are rejected with `unsafe_shared_account`, because locking the companion could not revoke their native permissions. HA administrators remain eligible for setup and diagnostics; their existing administrator privileges continue independently of the probe. The companion must not describe an administrator account as a securely locked shared account.

TaskMate owns its parent-account policy. The helper reads it through TaskMate storage's existing `get_parent_user_ids()` method; companions should use the helper instead of inspecting provider storage or assuming that policy lives in config-entry options.

## Existing authorization and limits

Native service, admin WebSocket, entity, child self-service, and notification gates are unchanged. A successful probe does not bypass any of them. Genuine administrators, permanently configured parent accounts, eligible children, and existing contextless automations retain their existing policy.

Existing TaskMate authorization trusts calls without a `Context.user_id`. The new probe always requires an identified active actor and never exploits that trust. User-triggerable automations remain a separate configuration boundary: source inspection of HA Core 2026.9.3 shows automation execution constructing a new Context with the triggering context as `parent_id`, without copying its `user_id`; script service actions preserve their supplied context. Therefore these tests do not certify that every configured automation, script, notification source, or other installed integration is inaccessible to a shared account. Audit those routes before claiming that a companion PIN protects every privileged household action.

Protocol 1 intentionally has no management API. Future provider operations need their own capability and target validation, domain validation, caller/parent audit attribution, queued-revocation tests, and readback handling. Chore calendar publication and reward device unlock fields can cause additional provider/entity writes and must not silently enter a general management capability.

## Recorded validation

Tests were written before the module existed; the first run failed with `ModuleNotFoundError`. A later contract regression test failed on the missing safe error `message` attribute before its implementation.

On Python 3.12.14, the local repository suite passed **2,230 tests**, including **37** new family API cases. The focused family/API and native authorization set passed **98 tests**:

```sh
python -m pytest tests/test_family_api.py tests/test_service_admin_gate.py tests/test_service_parent_gate.py tests/test_service_linked_child_gate.py tests/test_websocket_admin.py tests/test_security_hardening.py -q
python -m pytest -q
ruff check custom_components/taskmate tests scripts
ruff format --check custom_components/taskmate/family_api.py custom_components/taskmate/__init__.py tests/test_family_api.py
```

The new cases cover strict request shape, context mutation, nonliteral verifier results, revocation, missing/inactive users, permanent-parent rejection, eligibility, exact entry and protocol, unload/reload/reset, no provider writes, and native gate denial after a successful probe. Existing targeted tests cover linked-child permissions, number/select controls, child buttons/to-do, parent notifications, and legitimate admin/parent/contextless service behavior.

These repository tests use `tests/conftest.py` Home Assistant module stubs. A separate import check loaded the actual new module and real HA Context on Core 2026.9.3/Python 3.14.7; an import check alone is not a real transport or lifecycle test. The companion's actual-HA test results must be recorded in its own repository.

The full local run emitted 27 notification-test `AsyncMock` warnings. Ruff lint passed. Changed-file formatting passed; repository-wide formatting also reports pre-existing mixed line endings in `custom_components/taskmate/frontend.py`, which this change leaves untouched. No household data or live HA configuration was changed.
