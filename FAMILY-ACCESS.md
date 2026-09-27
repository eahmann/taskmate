# Family companion access: protocols 1 and 2

Fork version `5.6.1+eahmann.14` retains the **protocol 1 nonmutating parent-access probe** and adds **protocol 2 family snapshots and explicit workflows**. Both are in-process interfaces for a trusted Home Assistant companion backend. Repository validation does not establish deployment or live family acceptance.

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
  "provider_version": "5.6.1+eahmann.14",
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

Protocol 1 intentionally has no management API. Protocol 2 is separately requested and carries explicit targets, payloads, concurrency checks, and durable command receipts. Chore calendar publication and reward device unlock configuration are excluded from its management fields.

## Protocol 2 request and snapshot

`await async_get_family_api(hass, entry_id, protocol=2)` returns an adapter with `async_snapshot(request)` and `async_command(request)`. `PROTOCOL_VERSION` is `2`; `SUPPORTED_PROTOCOLS` is `(1, 2)`. Existing protocol 1 callers and probe responses remain protocol 1.

`FamilyWorkflowRequest`, defined in `family_workflows.py` and exported by `family_api.py`, contains the protocol 1 identity fields plus `allowed_child_ids` (an exact tuple of household-mapped provider child IDs), `command_id`, `expected_revision`, and `data`. A snapshot uses operation `snapshot` and leaves the three command fields empty. A parent-authorized snapshot or command includes the server-held parent session reference as `authorization_id`; ordinary child access leaves it empty. The companion verifier must bind the exact request object, actual Context and live connection, household revision and mapping, provider entry, and required parent grant across awaits. No browser-supplied roles or arbitrary service names are accepted.

Snapshots return `protocol`, `entry_id`, an opaque `revision`, HA-local `local_date`, `generated_at`, `points_name`, `parent_authorized`, and detached arrays of children, chore/routine/reward definitions, completions, and reward claims. Child entries contain wallet/available/committed points, `can_act`, and domain-derived chore, checklist, routine, and reward availability. Completion records retain pending submissions plus recent approved activity, with child undo metadata, note, suggested points, `can_review`, and `unsupported_reason`. Claims likewise expose review capabilities. These are display hints; commands validate authority again. Snapshot reads do not save provider storage or renew the companion PIN session.

Native linked-child restrictions remain in force for child operations, even with a parent PIN grant. Management requires a parent grant and rejects records whose assignments affect unmapped children. Reward management also considers historical claim owners because native cost edits can update legacy claim metadata. A selected child in the browser grants no authority.

## Explicit operations

| Operation | Data |
| --- | --- |
| `child.chore.complete` | `child_id`, `chore_id`, optional `note` and `suggested_points` |
| `child.checklist.set` | `child_id`, `chore_id`, stable `item_id`, boolean `checked`, `occurrence_id` |
| `child.chore.undo` | `completion_id` |
| `child.reward.request` | `child_id`, `reward_id` |
| `chore.approve` | `completion_id`, optional `points` |
| `chore.reject`, `chore.undo_approval` | `completion_id` |
| `reward.approve`, `reward.reject` | `claim_id` |
| `points.adjust` | `child_id`, signed integer `points`, required `reason` |
| `chore.create`, `routine.create`, `reward.create` | `fields` |
| `chore.update`, `routine.update`, `reward.update` | `id`, changed `fields` only |
| each kind's `.archive` or `.delete` | `id` |

Only the four `child.*` operations are available without parent authorization. Domain methods remain authoritative for points, approvals, quotas, schedules, routine awards, undo deadlines, stock, and wallet reservations. Open-ended chore submissions require a note and always await parent review. Plain/checklist chores and wallet rewards are supported. Photo/timer submissions are marked unsupported for companion completion or review; their evidence and timers are handled in TaskMate. Calendar-publishing chores cannot be managed here. Device-unlock, pool/jackpot, existing pool allocations, and automatically restocking rewards remain unsupported.

Field allowlists live in `family_workflow_data.py`; unknown fields are rejected. Chore fields include description, points, explicit child assignments, approval/open-ended flags, standard/checklist type, stable checklist items, schedule/days/recurrence, time/category, daily limit (1–100), difficulty, dependencies, and enabled state. Routines expose ordered normal chore references, required-for-bonus flags, bonus points, active state, and bounded defaults. Rewards expose name/description/icon, cost, explicit assignments, and nullable stock quantity. PATCH updates preserve hidden existing fields. New assignments must explicitly select at least one household child; omitted creation assignments default to the mapped children. Existing empty assignments retain their native meaning only when omitted from an update.

Chore retirement sets `enabled=False` and retains history; routine retirement sets `active=False`. Reward `.archive` sets quantity to zero and must be labeled **Make unavailable**, since no reward archive model exists. Deletion is allowed only when chore/reward history and relevant dependencies are absent; chore last-completed history still blocks deletion after completion pruning, as do unfinished checklists, quest references, and outstanding mandatory misses. Routine deletion uses its native behavior, retaining award/run snapshots and ordinary chores. No child deletion is exposed.

## Concurrency, durability, and attribution

Every command needs a fresh snapshot revision and a unique command ID. The revision changes with the provider lifetime, provider state, and external state. Time alone does not invalidate an unchanged edit form; schedules, quota availability, and undo deadlines are checked again by the domain action. Commands serialize within the provider API; fresh authorization, exact child targets, native linked-child rules, and storage state are checked after queued waits and after the durable reservation. Checklist commands also recheck after their native item lock. The final actor lookup uses HA Core 2026.9.3's in-memory auth store (no event-loop yield); changing that assumption requires renewed race coverage.

TaskMate stores a bounded `family_command_receipts` journal in its existing authoritative storage. A pending reservation is saved and read back before domain mutation. A committed receipt and resulting provider state are then saved and read back together using `async_confirmed_save`; native debounced persistence remains unchanged. Up to 1,000 receipts are retained, pruning resolved oldest entries only. Pending entries are never automatically replayed or pruned. The receipt contains operation, actor HA user ID, household ID, timestamp, request fingerprint, and safe result IDs; it never stores a PIN, parent session token, or claimed named-parent identity. `taskmate_family_command_committed` carries the actual HA Context.

Success is `{command_id, status: "committed", revision, result}`. An authorized exact retry of a retained committed command returns `status: "duplicate"` without repeating its mutation. Reused IDs with different payloads are rejected. `conflict` means refresh and review before a new explicit action. `command_uncertain` means inspect authoritative provider state and do not replay automatically; cancellation after reservation and failed or unconfirmed writes remain closed. A committed result remains committed even if the companion grant is revoked afterward. Domain rejection before mutation is recorded without poisoning later valid commands.

An in-process trusted integration can still mutate provider state directly; this API does not sandbox installed Python. The journal prevents replay within its protocol, not unrelated native requests. Native notifications, badge/quest progression, and routine awards continue through existing domain behavior. No synthetic administrator, arbitrary service forwarding, or duplicated companion domain database is used.

## Recorded validation

Protocol 2 tests first failed because its module did not exist. Additional regressions were observed failing before fixes for a deleted actor during final verification, a native edit during chore creation, retained-history deletion, reward edits affecting unmapped historical claim owners, clock-only edit conflicts, and the public request-class export. The final local Python 3.12.14 run passed **2,260 tests**, including **30 workflow tests** and the existing protocol 1 tests. Repository Ruff lint and changed-file formatting passed. The same 27 existing notification-test `AsyncMock` warnings remain.

The companion repository separately tests the real HA Context, coordinator/models, Store disk readback, queued revocation, actor deletion, native concurrent edits, cancellation, and retained receipts across reload. Its final results are recorded there; this document does not turn stub tests into live deployment evidence.

### Protocol 1 baseline

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
