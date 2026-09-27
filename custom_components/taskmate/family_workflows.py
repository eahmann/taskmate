"""Protocol 2: explicit scoped operations, real domain methods, durable receipts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field, replace

from homeassistant.util import dt as dt_util

from .authz import async_context_allows_child, user_allows_child
from .family_api import FamilyAPI, FamilyAPIError, _context_identity
from .family_workflow_data import (
    child_allowed,
    chore_reason,
    completion_reason,
    deletable,
    exact,
    fail,
    fields,
    identifier,
    integer,
    reward_reason,
    routine_scope,
    scope,
    snapshot,
    text,
)

CHILD_OPERATIONS = frozenset(
    ("child.chore.complete", "child.checklist.set", "child.chore.undo", "child.reward.request")
)
PARENT_OPERATIONS = frozenset(
    (
        "chore.approve",
        "chore.reject",
        "chore.undo_approval",
        "reward.approve",
        "reward.reject",
        "points.adjust",
        *(
            f"{kind}.{action}"
            for kind in ("chore", "routine", "reward")
            for action in ("create", "update", "archive", "delete")
        ),
    )
)
OPERATIONS = CHILD_OPERATIONS | PARENT_OPERATIONS
JOURNAL_KEY = "family_command_receipts"
MAX_RECEIPTS = 1000


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class FamilyWorkflowRequest:
    operation: str
    entry_id: str
    context: object
    household_id: str
    authorization_id: str
    allowed_child_ids: tuple[str, ...]
    verify: object = field(repr=False, compare=False)
    command_id: str = ""
    expected_revision: str = ""
    data: dict = field(default_factory=dict)
    _identity: tuple = field(init=False, repr=False)
    _payload: str = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "data", deepcopy(self.data))
        object.__setattr__(self, "_identity", _context_identity(self.context))
        object.__setattr__(self, "_payload", canonical(self.data))


class FamilyWorkflowAPI(FamilyAPI):
    def _validate_workflow(self, request):
        self._ensure_current()
        if (
            type(request) is not FamilyWorkflowRequest
            or request.entry_id != self._entry_id
            or request.operation not in OPERATIONS | {"snapshot"}
            or not callable(request.verify)
            or not isinstance(request.household_id, str)
            or not request.household_id
            or not isinstance(request.authorization_id, str)
            or type(request.allowed_child_ids) is not tuple
            or not 1 <= len(request.allowed_child_ids) <= 30
            or len(set(request.allowed_child_ids)) != len(request.allowed_child_ids)
            or any(not isinstance(cid, str) or not cid for cid in request.allowed_child_ids)
            or not request._identity[0]
            or _context_identity(request.context) != request._identity
            or canonical(request.data) != request._payload
        ):
            fail("The family request is missing or its identity changed.", "unauthorized")
        if request.operation in PARENT_OPERATIONS and not request.authorization_id:
            fail("Unlock parent access before changing provider records.", "parent_locked")
        if request.operation != "snapshot":
            identifier(request.command_id)
            if not isinstance(request.expected_revision, str) or not request.expected_revision:
                fail("Refresh the provider snapshot before submitting a command.", "conflict")
        for cid in request.allowed_child_ids:
            child_allowed(self._coordinator, cid, request.allowed_child_ids)

    async def _authorized(self, request):
        self._validate_workflow(request)
        try:
            authorized = await request.verify(request)
        except Exception:
            fail("Family authorization could not be confirmed.", "unauthorized")
        self._validate_workflow(request)
        if authorized is not True:
            fail("Family authorization has expired or changed.", "unauthorized")
        user = await self._hass.auth.async_get_user(request._identity[0])
        self._validate_workflow(request)
        self._check_user(user, request._identity[0])
        try:
            authorized = await request.verify(request)
        except Exception:
            fail("Family authorization could not be confirmed.", "unauthorized")
        self._validate_workflow(request)
        if authorized is not True:
            fail("Family authorization has expired or changed.", "unauthorized")
        user = await self._hass.auth.async_get_user(request._identity[0])
        self._validate_workflow(request)
        self._check_user(user, request._identity[0])
        return user

    def _revision(self):
        return digest(
            [
                self._lifetime.nonce,
                self._coordinator.storage.data_version,
                getattr(self._coordinator, "external_state_version", 0),
            ]
        )

    def _domain_signature(self):
        return digest({key: value for key, value in self._coordinator.storage.data.items() if key != JOURNAL_KEY})

    async def async_snapshot(self, request):
        user = await self._authorized(request)
        if request.operation != "snapshot" or request.data or request.command_id or request.expected_revision:
            fail("Use the snapshot operation without command fields.")
        permissions = {cid: user_allows_child(self._coordinator, user, cid) for cid in request.allowed_child_ids}
        result = snapshot(self._coordinator, request.allowed_child_ids, permissions, bool(request.authorization_id))
        return {"protocol": 2, "entry_id": self._entry_id, "revision": self._revision(), **result}

    def _journal(self):
        journal = self._coordinator.storage.data.get(JOURNAL_KEY, [])
        if not isinstance(journal, list) or len(journal) > MAX_RECEIPTS:
            fail("The provider command journal needs recovery.", "command_uncertain")
        required = {
            "command_id",
            "fingerprint",
            "status",
            "actor_user_id",
            "household_id",
            "operation",
            "created_at",
            "result",
        }
        seen = set()
        for item in journal:
            if (
                not isinstance(item, dict)
                or set(item) != required
                or item.get("status") not in ("pending", "committed", "rejected")
                or not isinstance(item.get("command_id"), str)
                or item["command_id"] in seen
                or not isinstance(item.get("fingerprint"), str)
                or len(item["fingerprint"]) != 64
                or not isinstance(item.get("result"), dict)
            ):
                fail("The provider command journal needs recovery.", "command_uncertain")
            seen.add(item["command_id"])
        return journal

    async def _confirm(self):
        try:
            await self._coordinator.storage.async_confirmed_save()
        except (Exception, asyncio.CancelledError):
            self._lifetime.uncertain = True
            raise

    async def async_command(self, request):
        await self._authorized(request)
        if request.operation not in OPERATIONS:
            fail("Choose a supported family operation.")
        async with self._lifetime.command_lock:
            user = await self._authorized(request)
            if self._lifetime.uncertain:
                fail(
                    "A previous write could not be confirmed. Reload and inspect TaskMate before another command.",
                    "command_uncertain",
                )
            signature = digest(
                [
                    request.operation,
                    request.entry_id,
                    request.household_id,
                    request._identity[0],
                    request.allowed_child_ids,
                    request.expected_revision,
                    request.data,
                ]
            )
            journal = self._journal()
            prior = next((r for r in journal if r["command_id"] == request.command_id), None)
            if prior:
                if prior["fingerprint"] != signature:
                    fail("That command identifier was already used for a different request.")
                if prior["status"] != "committed":
                    fail(
                        "This command was previously attempted. Refresh and review its outcome; it will not be replayed.",
                        "command_uncertain",
                    )
                return {
                    "command_id": request.command_id,
                    "status": "duplicate",
                    "revision": self._revision(),
                    "result": deepcopy(prior["result"]),
                }
            if request.expected_revision != self._revision():
                fail("TaskMate changed. Refresh and review before trying again.", "conflict")
            # Validate target, native child permissions and fields before reserving.
            action = await self._prepare(request)
            await self._authorized(request)
            if request.expected_revision != self._revision():
                fail("TaskMate changed during validation. Refresh before trying again.", "conflict")
            baseline = self._domain_signature()
            if len(journal) >= MAX_RECEIPTS:
                removable = next((item for item in journal if item["status"] != "pending"), None)
                if removable is None:
                    fail(
                        "Unresolved commands fill the journal. Inspect TaskMate before continuing.", "command_uncertain"
                    )
                journal.remove(removable)
            receipt = {
                "command_id": request.command_id,
                "fingerprint": signature,
                "status": "pending",
                "actor_user_id": user.id,
                "household_id": request.household_id,
                "operation": request.operation,
                "created_at": dt_util.now().isoformat(),
                "result": {},
            }
            journal.append(receipt)
            self._coordinator.storage.data[JOURNAL_KEY] = journal
            started = False
            try:
                await self._confirm()
                await self._authorized(request)
                if baseline != self._domain_signature():
                    fail("TaskMate changed while the command was queued. Refresh before trying again.", "conflict")
                # Prepare again after all reservation/auth awaits. Existing domain
                # operations mutate synchronously before their first I/O, except
                # checklist's queue, which receives its own verifier below.
                action = await self._prepare(request)
                user = await self._authorized(request)
                if baseline != self._domain_signature():
                    fail("TaskMate changed during authorization. Refresh before trying again.", "conflict")
                if request.operation in CHILD_OPERATIONS:
                    child_id = request.data.get("child_id")
                    if request.operation == "child.chore.undo":
                        completion = next(
                            (
                                c
                                for c in self._coordinator.storage.get_completions()
                                if c.id == request.data["completion_id"]
                            ),
                            None,
                        )
                        child_id = completion.child_id if completion else None
                    if not child_id or not user_allows_child(self._coordinator, user, child_id):
                        fail("Child access changed before the command could run.", "unauthorized")
                started = True
                result = await action()
                receipt["result"] = result
                # This journal is also the durable actor/operation attribution.
                # It contains no PIN, grant token or claimed named-parent identity.
                receipt["status"] = "committed"
                await self._confirm()
                # The companion's actual Context accompanies the explicit audit
                # event. No synthetic administrator or contextless service call.
                self._hass.bus.async_fire(
                    "taskmate_family_command_committed",
                    {
                        "command_id": request.command_id,
                        "operation": request.operation,
                        "actor_user_id": user.id,
                        "household_id": request.household_id,
                    },
                    context=request.context,
                )
                return {
                    "command_id": request.command_id,
                    "status": "committed",
                    "revision": self._revision(),
                    "result": deepcopy(result),
                }
            except asyncio.CancelledError:
                self._lifetime.uncertain = True
                raise
            except Exception as error:
                if (not started or baseline == self._domain_signature()) and not self._lifetime.uncertain:
                    receipt["status"] = "rejected"
                    try:
                        await self._confirm()
                    except Exception:
                        pass
                    if isinstance(error, FamilyAPIError) and not self._lifetime.uncertain:
                        raise
                self._lifetime.uncertain = True
                fail(
                    "The command outcome could not be confirmed. Refresh TaskMate and review its records; do not repeat it automatically.",
                    "command_uncertain",
                )

    async def _prepare(self, request):
        coord, store, data, op, allowed = (
            self._coordinator,
            self._coordinator.storage,
            request.data,
            request.operation,
            request.allowed_child_ids,
        )
        child_id = None
        if op in ("child.chore.complete", "child.checklist.set", "child.reward.request", "points.adjust"):
            child_id = data.get("child_id")
        record = None
        if op in ("child.chore.undo", "chore.approve", "chore.reject", "chore.undo_approval"):
            record = next((c for c in store.get_completions() if c.id == data.get("completion_id")), None)
            if record is None:
                fail("This chore submission no longer exists. Refresh TaskMate.", "conflict")
            reason = completion_reason(coord, record)
            if reason:
                fail(reason, "unsupported_operation")
            child_id = record.child_id
        elif op in ("reward.approve", "reward.reject"):
            record = next((c for c in store.get_reward_claims() if c.id == data.get("claim_id")), None)
            if record is None:
                fail("This reward request no longer exists. Refresh TaskMate.", "conflict")
            child_id = record.child_id
        if child_id is not None:
            child_allowed(coord, child_id, allowed)
        if op in CHILD_OPERATIONS and not await async_context_allows_child(
            self._hass, coord, request.context, child_id
        ):
            fail("This Home Assistant account cannot act as the selected child.", "unauthorized")

        async def invoke(method, *args, **kwargs):
            try:
                result = await method(*args, **kwargs)
            except ValueError as error:
                # A domain error after mutation is conservatively handled by the
                # journal as uncertain; no partially completed action is replayed.
                fail(str(error))
            if result is None:
                return {}
            if hasattr(result, "id"):
                return {"id": result.id}
            if isinstance(result, str):
                return {"id": result}
            if isinstance(result, dict):
                return {"id": result.get("completion_id")} if result.get("completion_id") else {}
            return {}

        if op == "points.adjust":
            exact(data, ("child_id", "points", "reason"))
            points = integer(data["points"], -100000, 100000)
            if not points:
                fail("Choose a nonzero points adjustment.")
            reason = text(data["reason"], required=True, limit=200)
            return lambda: invoke(
                coord.async_add_points if points > 0 else coord.async_remove_points, child_id, abs(points), reason
            )
        if op == "child.chore.complete" or op == "child.checklist.set":
            chore = store.get_chore(identifier(data.get("chore_id")))
            withdrawing = op == "child.checklist.set" and data.get("checked") is False
            if chore is None or (not withdrawing and not coord._checklist_can_start(chore, child_id)):
                fail("This chore is not available for the selected child.", "conflict")
            if chore.require_photo or chore.task_type == "timed":
                fail("Use TaskMate for photo proof or timers.", "unsupported_operation")
            if op == "child.chore.complete":
                exact(data, ("child_id", "chore_id"), ("note", "suggested_points"))
                if chore.task_type == "checklist":
                    fail("Complete each checklist item instead.")
                note = text(data.get("note", ""), limit=500)
                suggested = integer(data.get("suggested_points", 0))
                return lambda: invoke(
                    coord.async_complete_chore, chore.id, child_id, note=note, suggested_points=suggested
                )
            exact(data, ("child_id", "chore_id", "item_id", "checked", "occurrence_id"))
            if type(data["checked"]) is not bool:
                fail("Checked must be true or false.")
            identifier(data["item_id"])
            identifier(data["occurrence_id"])
            checklist_baseline = self._domain_signature()

            async def revalidate():
                user = await self._authorized(request)
                if not user_allows_child(coord, user, child_id):
                    fail("Child access changed.", "unauthorized")
                if checklist_baseline != self._domain_signature():
                    fail(
                        "TaskMate changed while the checklist command was queued. Refresh before trying again.",
                        "conflict",
                    )
                child_allowed(coord, child_id, allowed)

            return lambda: invoke(
                coord.async_set_checklist_item,
                chore.id,
                child_id,
                data["item_id"],
                data["checked"],
                occurrence_id=data["occurrence_id"],
                _family_validate=revalidate,
            )
        if op == "child.chore.undo":
            exact(data, ("completion_id",))
            return lambda: invoke(coord.async_undo_chore, record.id)
        if op.startswith("chore.") and op in ("chore.approve", "chore.reject", "chore.undo_approval"):
            exact(data, ("completion_id",), ("points",) if op == "chore.approve" else ())
            if "points" in data:
                integer(data["points"])
            method = {
                "chore.approve": coord.async_approve_chore,
                "chore.reject": coord.async_reject_chore,
                "chore.undo_approval": coord.async_undo_chore_approval,
            }[op]
            return lambda: invoke(method, record.id, **({"points": data["points"]} if "points" in data else {}))
        if op == "child.reward.request" or op in ("reward.approve", "reward.reject"):
            exact(data, ("child_id", "reward_id") if op == "child.reward.request" else ("claim_id",))
            reward = store.get_reward(data.get("reward_id") if op == "child.reward.request" else record.reward_id)
            if reward is None:
                fail("The reward no longer exists.", "conflict")
            reason = reward_reason(coord, reward)
            if reason:
                fail(reason, "unsupported_operation")
            if op == "child.reward.request":
                try:
                    coord._validate_reward_claim(reward.id, child_id)
                except ValueError as error:
                    fail(str(error))
                return lambda: invoke(coord.async_claim_reward, reward.id, child_id)
            if record.approved:
                fail("That reward request is already approved. Refresh TaskMate.", "conflict")
            return lambda: invoke(
                coord.async_approve_reward if op == "reward.approve" else coord.async_reject_reward, record.id
            )
        kind, action = op.split(".")
        exact(data, ("fields",) if action == "create" else ("id", "fields") if action == "update" else ("id",))
        existing = None
        if action != "create":
            identity = identifier(data["id"])
            existing = getattr(store, f"get_{kind}")(identity)
            if existing is None:
                fail("The requested record no longer exists.", "conflict")
            if kind == "routine":
                routine_scope(coord, existing, allowed)
            else:
                scope(coord, existing, allowed)
                reason = chore_reason(existing) if kind == "chore" else reward_reason(coord, existing)
                if reason:
                    fail(reason, "unsupported_operation")
        if action == "delete":
            if not deletable(coord, kind, existing):
                fail("This record has history or dependencies. Make it unavailable instead.", "has_history")
            return lambda: invoke(
                getattr(coord, f"async_delete_{kind}" if kind == "routine" else f"async_remove_{kind}"), existing.id
            )
        values = (
            fields(coord, kind, data["fields"], allowed, existing=existing)
            if action in ("create", "update")
            else {"chore": {"enabled": False}, "routine": {"active": False}, "reward": {"quantity": 0}}[kind]
        )
        if kind == "routine":
            return lambda: invoke(coord.async_save_routine, existing.id if existing else None, **values)
        if existing:
            updated = replace(existing, **values)
            return lambda: invoke(getattr(coord, f"async_update_{kind}"), updated)
        if kind == "chore":
            return lambda: invoke(coord.async_add_chore, **values)
        return lambda: invoke(coord.async_add_reward, **values)
