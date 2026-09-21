"""Daily routines built from normal chores, with reversible automatic bonuses."""

from __future__ import annotations

from dataclasses import replace
from functools import partial

from homeassistant.util import dt as dt_util

from .models import PointsTransaction, Routine

_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class RoutinesMixin:
    """Group chores without replacing their approval, scheduling or award paths."""

    def _validate_routine(self, routine: Routine) -> None:
        if not routine.name.strip():
            raise ValueError("Routine name is required")
        if routine.bonus_points < 0:
            raise ValueError("Routine bonus cannot be negative")
        seen = set()
        occupied = {
            m["chore_id"] for other in self.storage.get_routines() if other.id != routine.id for m in other.members
        }
        for member in routine.members:
            cid = member.get("chore_id")
            if not self.get_chore(cid):
                raise ValueError("Every routine item must link to an existing chore")
            if cid in seen:
                raise ValueError("A chore can appear only once in a routine")
            if cid in occupied:
                raise ValueError(
                    "This chore already belongs to a routine. Create a separate chore for another occasion."
                )
            seen.add(cid)
        defaults = routine.defaults
        if set(defaults) - {"assigned_to", "due_days", "time_category", "requires_approval"}:
            raise ValueError("Unsupported routine default")
        children = {c.id for c in self.storage.get_children()}
        if any(cid not in children for cid in defaults.get("assigned_to", [])):
            raise ValueError("Routine default refers to an unknown child")
        if any(day not in _DAYS for day in defaults.get("due_days", [])):
            raise ValueError("Invalid routine default day")

    async def async_save_routine(self, routine_id: str | None = None, **fields) -> str:
        existing = self.storage.get_routine(routine_id) if routine_id else None
        if routine_id and not existing:
            raise ValueError("Routine not found")
        data = existing.to_dict() if existing else {}
        data.update(fields)
        if routine_id:
            data["id"] = routine_id
        routine = Routine.from_dict(data)
        self._validate_routine(routine)
        self.storage.save_routine(routine)
        await self.storage.async_save()
        await self.async_refresh()
        return routine.id

    async def async_delete_routine(self, routine_id: str) -> None:
        if not self.storage.get_routine(routine_id):
            raise ValueError("Routine not found")
        self.storage.remove_routine(routine_id)
        # Keep award snapshots: undo must still reverse an old bonus, and a
        # submitted day's promised bonus survives later configuration changes.
        await self.storage.async_save()
        await self.async_refresh()

    def _routine_day_completions(self, child_id: str, day: str) -> list:
        return [
            c
            for c in self.storage.get_completions()
            if c.child_id == child_id
            and not c.bonus_subtask_id
            and dt_util.as_local(c.completed_at).date().isoformat() == day
        ]

    def _routine_due_members(self, routine: Routine, child_id: str) -> list[dict]:
        """Use normal chore eligibility, including tasks already submitted today.

        Dependencies affect when a chore may be performed, not whether it is
        part of today's routine. They must not shrink the bonus requirement.
        """
        today = dt_util.as_local(dt_util.now()).date().isoformat()
        submitted = {c.chore_id for c in self._routine_day_completions(child_id, today)}
        members = []
        for member in routine.members:
            chore = self.get_chore(member["chore_id"])
            if not chore:
                continue
            if chore.id in submitted or self._is_chore_completable_by_child(replace(chore, depends_on=[]), child_id):
                members.append(dict(member))
        return members

    def _prepare_routine_runs(self, chore_id: str, child_id: str) -> None:
        """Freeze today's requirements and bonus before the first submission.

        No await here: a second submission cannot reserve a duplicate bonus.
        Each linked chore counts once per HA-local day, even with daily_limit > 1.
        """
        day = dt_util.as_local(dt_util.now()).date().isoformat()
        for routine in self.storage.get_routines():
            if not routine.active or not any(m["chore_id"] == chore_id for m in routine.members):
                continue
            if self.storage.get_routine_run(routine.id, child_id, day):
                continue
            members = self._routine_due_members(routine, child_id)
            if not any(m["chore_id"] == chore_id for m in members):
                continue
            self.storage.save_routine_run(
                {
                    "routine_id": routine.id,
                    "child_id": child_id,
                    "day": day,
                    "name": routine.name,
                    "members": members,
                    "bonus_points": routine.bonus_points,
                    "awarded": False,
                }
            )

    async def _async_sync_routine_awards(self, completion, *, deferred_notifications=None) -> None:
        """Settle or reverse the original submission day's bonus, exactly once."""
        if completion.bonus_subtask_id:
            return
        day = dt_util.as_local(completion.completed_at).date().isoformat()
        approved = {c.chore_id for c in self._routine_day_completions(completion.child_id, day) if c.approved}
        notifications = []
        for run in self.storage.get_routine_runs():
            if run["child_id"] != completion.child_id or run["day"] != day:
                continue
            required = {m["chore_id"] for m in run["members"] if m.get("required", True)}
            done = bool(required) and required <= approved
            if done == run["awarded"]:
                continue
            child = self.get_child(completion.child_id)
            if not child:
                continue
            bonus = run["bonus_points"] * (1 if done else -1)
            # Reserve the award state before any effect can yield. Fixed bonus
            # deliberately does not increment chore/streak counters or multiply.
            run["awarded"] = done
            self.storage.save_routine_run(run)
            child.points = max(0, child.points + bonus)
            child.total_points_earned = max(0, child.total_points_earned + bonus)
            child.career_score = child.total_points_earned - child.total_penalties_received
            if bonus:
                self.storage.add_points_transaction(
                    PointsTransaction(
                        child_id=child.id,
                        points=bonus,
                        reason=f"Routine {'complete' if done else 'reversed'}: {run['name']}",
                        created_at=dt_util.now(),
                    )
                )
            if done and bonus:
                await self._maybe_level_up(child, deferred_notifications=notifications)
            self.storage.update_child(child)
            self.storage.append_career_score_snapshot(
                child.id, dt_util.as_local(dt_util.now()).date().isoformat(), child.career_score
            )
            if done:
                details = {"routine_id": run["routine_id"], "day": day, "bonus": run["bonus_points"]}
                self.hass.bus.async_fire("taskmate_routine_completed", {"child_id": child.id, **details})
                notifications.append(
                    partial(
                        self._celebrate,
                        child,
                        "routine_completed",
                        f"{child.name} completed {run['name']}!",
                        tier=2,
                        extra=details,
                    )
                )
        if deferred_notifications is not None:
            deferred_notifications.extend(notifications)
        else:
            # Reversal callers only reach this branch (no celebration to send).
            await self._async_deliver_award_notifications(notifications)

    def routine_progress_for_child(self, child_id: str) -> list[dict]:
        day = dt_util.as_local(dt_util.now()).date().isoformat()
        completions = self._routine_day_completions(child_id, day)
        approved = {c.chore_id for c in completions if c.approved}
        pending = {c.chore_id for c in completions if not c.approved} - approved
        out = []
        for routine in self.storage.get_routines():
            if not routine.active:
                continue
            run = self.storage.get_routine_run(routine.id, child_id, day)
            members = run["members"] if run else self._routine_due_members(routine, child_id)
            if not members:
                continue
            required = {m["chore_id"] for m in members if m.get("required", True)}
            out.append(
                {
                    "id": routine.id,
                    "name": routine.name,
                    "icon": routine.icon,
                    "description": routine.description,
                    "members": members,
                    "required_count": len(required),
                    "completed_count": len(required & approved),
                    "pending_count": len(required & pending),
                    "done": bool(run and run["awarded"]),
                    "bonus_points": run["bonus_points"] if run else routine.bonus_points,
                }
            )
        return out
