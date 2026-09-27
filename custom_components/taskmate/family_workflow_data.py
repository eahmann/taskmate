"""Narrow family projections and input validation over authoritative TaskMate models."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime

from homeassistant.util import dt as dt_util

from .chore_undo import child_undo_metadata
from .family_api import FamilyAPIError

CHORE_FIELDS = frozenset(
    (
        "name",
        "description",
        "points",
        "assigned_to",
        "requires_approval",
        "open_ended",
        "time_category",
        "display_category",
        "task_type",
        "checklist_items",
        "schedule_mode",
        "due_days",
        "enabled",
        "daily_limit",
        "difficulty",
        "recurrence",
        "recurrence_day",
        "recurrence_start",
        "first_occurrence_mode",
        "depends_on",
    )
)
ROUTINE_FIELDS = frozenset(("name", "description", "icon", "bonus_points", "active", "members", "defaults"))
REWARD_FIELDS = frozenset(("name", "description", "icon", "cost", "assigned_to", "quantity"))
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def fail(message, code="invalid_request"):
    raise FamilyAPIError(code, message)


def identifier(value):
    if not isinstance(value, str) or not value or len(value) > 128 or value.strip() != value:
        fail("Use an existing record identifier.")
    return value


def integer(value, low=0, high=100000):
    if type(value) is not int or not low <= value <= high:
        fail(f"Use a whole number between {low} and {high}.")
    return value


def text(value, *, required=False, limit=2000):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        fail("Enter a valid name or description within the field limit.")
    return value.strip()


def exact(data, required, optional=()):
    if not isinstance(data, dict) or not set(required) <= set(data) or set(data) - set(required) - set(optional):
        fail("The command has missing or unsupported fields.")


def child_allowed(coord, child_id, allowed):
    identifier(child_id)
    if child_id not in allowed or coord.storage.get_child(child_id) is None:
        fail("The requested child is not linked to this household.", "unauthorized")


def scope(coord, record, allowed):
    assigned = set(record.assigned_to) or {c.id for c in coord.storage.get_children()}
    if not assigned or not assigned <= set(allowed):
        fail("This record also affects children outside this household. Manage it in TaskMate.", "outside_household")
    # Reward edits can freeze legacy claim prices and remove pending claims.
    # Historical children therefore remain part of its mutation scope even
    # after the reward's current assignments change.
    if hasattr(record, "cost") and any(
        claim.reward_id == record.id and claim.child_id not in allowed for claim in coord.storage.get_reward_claims()
    ):
        fail("This reward has claims outside this household. Manage it in TaskMate.", "outside_household")


def chore_reason(chore):
    if chore.publish_calendar_entities:
        return "Calendar publishing is configured. Manage this chore in TaskMate."
    if chore.task_type == "timed":
        return "This chore uses TaskMate timers. Manage it in TaskMate."
    return ""


def reward_reason(coord, reward):
    if reward.unlock_entity or reward.unlock_minutes:
        return "This reward controls a device. Manage it in TaskMate."
    if (
        reward.pool_enabled
        or reward.is_jackpot
        or any(a.reward_id == reward.id for a in coord.storage.get_pool_allocations())
    ):
        return "This reward uses saved pool allocations. Manage it in TaskMate."
    if reward.restock_enabled:
        return "This reward automatically restocks. Manage it in TaskMate."
    return ""


def completion_reason(coord, completion):
    chore = coord.storage.get_chore(completion.chore_id)
    if chore is None:
        return "The original chore is missing. Review this submission in TaskMate."
    if completion.photo_url or completion.timed_duration_seconds or chore.require_photo or chore.task_type == "timed":
        return "Review photo evidence or timer details in TaskMate."
    return ""


def routine_scope(coord, routine, allowed):
    for child_id in routine.defaults.get("assigned_to", []):
        child_allowed(coord, child_id, allowed)
    for member in routine.members:
        chore = coord.storage.get_chore(member["chore_id"])
        if chore is None:
            fail("A routine member is missing. Repair the routine in TaskMate.")
        scope(coord, chore, allowed)


def deletable(coord, kind, record):
    store = coord.storage
    if kind == "chore":
        return not (
            record.image_url
            or store.data.get("last_completed", {}).get(record.id)
            or any(c.chore_id == record.id for c in store.get_completions())
            or any(record.id in c.depends_on for c in store.get_chores())
            or any(record.id in quest.steps for quest in store.get_quests())
            or any(any(m["chore_id"] == record.id for m in r.members) for r in store.get_routines())
            or any(getattr(t, "chore_id", None) == record.id for t in store.get_timed_sessions())
            or any(record.id in str(t.to_dict()) for t in store.get_points_transactions())
            or any(
                record.id in str(item)
                for key, rows in store.data.items()
                if key
                in (
                    "routine_runs",
                    "scheduled_changes",
                    "task_groups",
                    "swap_requests",
                    "checklist_progress",
                    "mandatory_misses",
                )
                for item in (rows.values() if isinstance(rows, dict) else rows if isinstance(rows, list) else [])
            )
        )
    if kind == "reward":
        return not any(c.reward_id == record.id for c in store.get_reward_claims())
    return True


def fields(coord, kind, values, allowed, *, existing=None):
    whitelist = {"chore": CHORE_FIELDS, "routine": ROUTINE_FIELDS, "reward": REWARD_FIELDS}[kind]
    exact(values, (), whitelist)
    if not values:
        fail("Choose at least one field to save.")
    result = deepcopy(values)
    if existing is None and "name" not in result:
        fail("A name is required.")
    for key in ("name", "description", "icon", "display_category"):
        if key in result:
            result[key] = text(result[key], required=key == "name", limit=120 if key != "description" else 2000)
    for key in ("points", "bonus_points", "cost"):
        if key in result:
            integer(result[key])
    if "quantity" in result and result["quantity"] is not None:
        integer(result["quantity"])
    if "daily_limit" in result:
        integer(result["daily_limit"], 1, 100)
    for key in ("enabled", "active", "requires_approval", "open_ended"):
        if key in result and type(result[key]) is not bool:
            fail("Enabled and approval fields must be true or false.")
    if "assigned_to" not in result and existing is None and kind in ("chore", "reward"):
        result["assigned_to"] = list(allowed)
    if "assigned_to" in result:
        if not isinstance(result["assigned_to"], list) or not result["assigned_to"] or len(result["assigned_to"]) > 30:
            fail("Choose at least one household child.")
        if len(set(result["assigned_to"])) != len(result["assigned_to"]):
            fail("Choose each child only once.")
        for child_id in result["assigned_to"]:
            child_allowed(coord, child_id, allowed)
    enums = {
        "task_type": ("standard", "checklist"),
        "time_category": ("anytime", "morning", "afternoon", "evening", "night"),
        "difficulty": ("easy", "medium", "hard"),
        "schedule_mode": ("specific_days", "recurring", "one_shot"),
        "recurrence": ("every_2_days", "weekly", "every_2_weeks", "monthly", "every_3_months", "every_6_months"),
        "recurrence_day": ("", *DAYS),
        "first_occurrence_mode": ("available_immediately", "wait_for_first_occurrence"),
    }
    for key, choices in enums.items():
        if key in result and result[key] not in choices:
            fail(f"Choose a supported {key.replace('_', ' ')}.")
    if "due_days" in result and (
        not isinstance(result["due_days"], list) or any(day not in DAYS for day in result["due_days"])
    ):
        fail("Choose valid days of the week.")
    if result.get("recurrence_start"):
        try:
            date.fromisoformat(result["recurrence_start"])
        except (ValueError, TypeError):
            fail("Use an ISO recurrence start date.")
    if "checklist_items" in result:
        if not isinstance(result["checklist_items"], list):
            fail("Checklist items must be a list.")
        for item in result["checklist_items"]:
            exact(item, ("name",), ("id",))
    if "depends_on" in result:
        if not isinstance(result["depends_on"], list) or len(result["depends_on"]) > 30:
            fail("Choose valid dependency chores.")
        for identity in result["depends_on"]:
            chore = coord.storage.get_chore(identifier(identity))
            if chore is None or (existing and identity == existing.id):
                fail("A dependency must be another existing chore.")
            scope(coord, chore, allowed)
    if "members" in result:
        if not isinstance(result["members"], list) or len(result["members"]) > 100:
            fail("Choose at most 100 routine members.")
        for member in result["members"]:
            exact(member, ("chore_id", "required"))
            if type(member["required"]) is not bool:
                fail("Required for bonus must be true or false.")
            chore = coord.storage.get_chore(identifier(member["chore_id"]))
            if chore is None:
                fail("Choose an existing chore for this routine.")
            scope(coord, chore, allowed)
    if "defaults" in result:
        exact(result["defaults"], (), ("assigned_to", "due_days", "time_category", "requires_approval"))
        if result["defaults"]:
            result["defaults"] = fields(coord, "chore", result["defaults"], allowed, existing=object())
    return result


def snapshot(coord, allowed, can_act, parent):
    """Projection is deliberately detached; no provider config or user links leak."""
    store = coord.storage
    now = dt_util.now()
    today = dt_util.as_local(now).date()
    chores = [c for c in store.get_chores() if not c.assigned_to or set(c.assigned_to) & set(allowed)]
    rewards = [r for r in store.get_rewards() if not r.assigned_to or set(r.assigned_to) & set(allowed)]
    completions = [c for c in store.get_completions() if c.child_id in allowed]
    claims = [c for c in store.get_reward_claims() if c.child_id in allowed]
    definitions = {"chores": [], "routines": [], "rewards": []}
    for kind, records, keys in [
        ("chore", chores, CHORE_FIELDS),
        ("reward", rewards, REWARD_FIELDS),
        ("routine", store.get_routines(), ROUTINE_FIELDS),
    ]:
        for record in records:
            permitted = True
            reason = ""
            try:
                if kind == "routine":
                    routine_scope(coord, record, allowed)
                else:
                    scope(coord, record, allowed)
                reason = (
                    chore_reason(record)
                    if kind == "chore"
                    else reward_reason(coord, record)
                    if kind == "reward"
                    else ""
                )
            except FamilyAPIError as error:
                permitted = False
                reason = error.message
            if kind == "routine" and not permitted:
                continue
            data = record.to_dict()
            projected = {key: deepcopy(data[key]) for key in keys if key in data}
            if kind == "chore":
                projected["require_photo"] = record.require_photo
            if "assigned_to" in projected:
                projected["assigned_to"] = [cid for cid in projected["assigned_to"] if cid in allowed]
            projected.update(
                id=record.id,
                can_edit=parent and permitted and not reason,
                can_delete=parent and permitted and not reason and deletable(coord, kind, record),
                unsupported_reason=reason,
            )
            definitions[{"chore": "chores", "routine": "routines", "reward": "rewards"}[kind]].append(projected)
    children = []
    for identity in allowed:
        child = store.get_child(identity)
        if child is None:
            continue
        committed = sum(
            coord.get_reward(c.reward_id).cost
            for c in claims
            if c.child_id == identity
            and not c.approved
            and coord.get_reward(c.reward_id)
            and not coord.is_pool_mode_claim(c)
        )
        child_chores = []
        for chore in chores:
            if chore.assigned_to and identity not in chore.assigned_to:
                continue
            own = [
                c
                for c in completions
                if c.child_id == identity
                and c.chore_id == chore.id
                and not c.bonus_subtask_id
                and dt_util.as_local(c.completed_at).date() == today
            ]
            complete = sum(c.approved for c in own)
            pending = len(own) - complete
            available = coord._checklist_can_start(chore, identity)
            reason = (
                "Use TaskMate to provide the required photo or timer."
                if chore.require_photo or chore.task_type == "timed"
                else ""
            )
            checklist = coord.checklist_progress_for_chore(chore, identity) if chore.task_type == "checklist" else None
            child_chores.append(
                {
                    "chore_id": chore.id,
                    "available": available,
                    "can_complete": can_act[identity] and available and not reason,
                    "reason": reason,
                    "checklist": checklist,
                    "pending_count": pending,
                    "completed_count": complete,
                    "latest_completion_id": own[-1].id if own else None,
                    "status": "pending"
                    if pending
                    else "completed"
                    if complete
                    else "available"
                    if available
                    else "unavailable",
                }
            )
        child_rewards = []
        for reward in rewards:
            if not coord._reward_is_for_child(reward, identity):
                continue
            reason = reward_reason(coord, reward)
            if not reason:
                try:
                    coord._validate_reward_claim(reward.id, identity)
                except ValueError as error:
                    reason = str(error)
            child_rewards.append(
                {"reward_id": reward.id, "can_request": can_act[identity] and not reason, "reason": reason}
            )
        children.append(
            {
                "id": identity,
                "name": child.name,
                "avatar": child.avatar,
                "color": child.color,
                "points": child.points,
                "available_points": max(0, child.points - committed),
                "committed_points": committed,
                "can_act": can_act[identity],
                "chores": child_chores,
                "routines": coord.routine_progress_for_child(identity),
                "rewards": child_rewards,
            }
        )
    recent = sorted(completions, key=lambda c: c.completed_at, reverse=True)
    completion_records = []
    for index, completion in enumerate(recent):
        if index >= 100 and completion.approved:
            continue
        policy = child_undo_metadata(completion, completions, store.get_chore_undo_seconds())
        until = datetime.fromisoformat(policy["child_undo_until"]) if policy.get("child_undo_until") else None
        reason = completion_reason(coord, completion)
        completion_records.append(
            {
                "id": completion.id,
                "chore_id": completion.chore_id,
                "child_id": completion.child_id,
                "completed_at": completion.completed_at.isoformat(),
                "approved": completion.approved,
                "points_awarded": completion.points_awarded,
                "note": completion.note,
                "suggested_points": completion.suggested_points,
                "can_review": parent and not reason,
                "unsupported_reason": reason,
                "can_undo": can_act[completion.child_id]
                and not reason
                and bool(policy.get("child_undo_pending") or (until and now < until)),
                **policy,
            }
        )
    return {
        "generated_at": now.isoformat(),
        "local_date": today.isoformat(),
        "parent_authorized": parent,
        "points_name": store.get_points_name(),
        "children": children,
        **definitions,
        "completions": completion_records,
        "reward_claims": [
            {
                "id": c.id,
                "reward_id": c.reward_id,
                "child_id": c.child_id,
                "claimed_at": c.claimed_at.isoformat(),
                "approved": c.approved,
                "can_review": parent
                and not c.approved
                and bool(store.get_reward(c.reward_id))
                and not reward_reason(coord, store.get_reward(c.reward_id)),
                "unsupported_reason": reward_reason(coord, store.get_reward(c.reward_id))
                if store.get_reward(c.reward_id)
                else "The reward is missing. Review this request in TaskMate.",
            }
            for c in claims
            if not c.approved or c in claims[-100:]
        ],
    }
