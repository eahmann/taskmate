"""Persistent checklist occurrences and automatic whole-chore submission."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, timedelta

from homeassistant.util import dt as dt_util

from .chore_undo import child_undo_metadata
from .models import generate_id


class ChecklistsMixin:
    """Keep item progress separate from paid chore completions."""

    @staticmethod
    def _checklist_signature(chore) -> str:
        # Wording and display order do not invalidate a child's checked items.
        return hashlib.sha256(json.dumps(sorted(item["id"] for item in chore.checklist_items)).encode()).hexdigest()[
            :24
        ]

    def _checklist_period(self, chore, when=None) -> str:
        today = dt_util.as_local(when or dt_util.now()).date()
        if chore.schedule_mode == "one_shot":
            return f"one_shot:{chore.created_date}"
        # Rolling recurring chores remain the same occurrence while overdue;
        # aligned recurrences reset on the next scheduled opportunity, not on
        # off-days between those opportunities.
        if chore.schedule_mode == "recurring":
            if chore.recurrence == "every_2_days" and chore.recurrence_start:
                try:
                    anchor = date.fromisoformat(chore.recurrence_start)
                    return f"recurring:{anchor}:{(today - anchor).days // 2}"
                except ValueError:
                    pass
            if chore.recurrence in ("weekly", "every_2_weeks") and chore.recurrence_day:
                weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
                if chore.recurrence_day.lower() in weekdays:
                    weekday = weekdays.index(chore.recurrence_day.lower())
                    opportunity = today - timedelta(days=(today.weekday() - weekday) % 7)
                    return f"recurring:{opportunity.isoformat()}"
            return "recurring"
        return f"{chore.schedule_mode}:{today.isoformat()}"

    def _checklist_can_start(self, chore, child_id: str) -> bool:
        if not self._is_chore_completable_by_child(chore, child_id):
            return False
        today = dt_util.as_local(dt_util.now()).date()
        count = sum(
            c.chore_id == chore.id
            and c.child_id == child_id
            and not c.bonus_subtask_id
            and dt_util.as_local(c.completed_at).date() == today
            for c in self.storage.get_completions()
        )
        return count < chore.daily_limit

    def _checklist_state(self, chore, child_id: str) -> dict:
        """Read a current occurrence, deriving fresh state without a write."""
        period = self._checklist_period(chore)
        signature = self._checklist_signature(chore)
        state = self.storage.get_checklist_progress(chore.id, child_id)
        if state and state.get("period") == period and state.get("signature") == signature:
            if not state.get("completion_id") or not self._checklist_can_start(chore, child_id):
                return state
        completions = [
            c
            for c in self.storage.get_completions()
            if c.chore_id == chore.id and c.child_id == child_id and not c.bonus_subtask_id
        ]
        # Include the last completion in the token: a delayed final-item retry
        # cannot accidentally count as the first check of the next repeat.
        previous = max(reversed(completions), key=lambda c: c.completed_at).id if completions else ""
        token = hashlib.sha256(json.dumps([chore.id, child_id, period, signature, previous]).encode()).hexdigest()[:32]
        return {
            "chore_id": chore.id,
            "child_id": child_id,
            "period": period,
            "signature": signature,
            "occurrence_id": token,
            "checked_ids": [],
            "completion_id": "",
            "final_item_id": "",
        }

    def checklist_progress_for_chore(self, chore, child_id: str) -> dict:
        """Public card state, including server-authoritative undo metadata."""
        state = self._checklist_state(chore, child_id)
        completions = self.storage.get_completions()
        completion = next((c for c in completions if c.id == state.get("completion_id")), None)
        return {
            "items": [{**item, "checked": item["id"] in state["checked_ids"]} for item in chore.checklist_items],
            "occurrence_id": state["occurrence_id"],
            "completion_id": state.get("completion_id", ""),
            **({"approved": completion.approved} if completion else {}),
            **(
                child_undo_metadata(completion, completions, self.storage.get_chore_undo_seconds())
                if completion
                else {}
            ),
        }

    def checklist_progress_for_child(self, child_id: str) -> dict:
        return {
            chore.id: self.checklist_progress_for_chore(chore, child_id)
            for chore in self.storage.get_chores()
            if chore.task_type == "checklist" and (not chore.assigned_to or child_id in chore.assigned_to)
        }

    def _checklist_response_progress(self, chore_id: str, child_id: str) -> dict:
        """Re-read definitions after awaits; a parent may have edited/deleted them."""
        chore = self.storage.get_chore(chore_id)
        if chore and chore.task_type == "checklist" and self.storage.get_child(child_id):
            return self.checklist_progress_for_chore(chore, child_id)
        return {"items": [], "occurrence_id": "", "completion_id": ""}

    async def async_set_checklist_item(
        self,
        chore_id: str,
        child_id: str,
        item_id: str,
        checked: bool,
        photo_url: str = "",
        occurrence_id: str = "",
    ) -> dict:
        """Change one item, submitting exactly once when the last one is checked."""
        if not isinstance(checked, bool):
            raise ValueError("Checklist checked must be true or false")
        if not occurrence_id:
            raise ValueError("Checklist occurrence is required; refresh the card and try again")
        # Do not allocate permanent lock entries for arbitrary invalid IDs.
        if not self.storage.get_chore(chore_id) or not self.storage.get_child(child_id):
            raise ValueError("Chore or child not found")
        # Serialise the full award/undo operation, including refreshes and
        # notifications, so two devices cannot both submit the last item.
        locks = self.__dict__.setdefault("_checklist_locks", {})
        async with locks.setdefault((chore_id, child_id), asyncio.Lock()):
            chore = self.storage.get_chore(chore_id)
            if not chore or chore.task_type != "checklist":
                raise ValueError("This chore is not a checklist")
            if not self.storage.get_child(child_id):
                raise ValueError("Child not found")
            if item_id not in {item["id"] for item in chore.checklist_items}:
                raise ValueError("Checklist item not found")
            state = self._checklist_state(chore, child_id)
            completion = next(
                (
                    c
                    for c in self.storage.get_completions()
                    if c.chore_id == chore_id and c.child_id == child_id and c.checklist_occurrence_id == occurrence_id
                ),
                None,
            )
            if completion:
                if checked:
                    return {"completed": False, "progress": self.checklist_progress_for_chore(chore, child_id)}
                if state["occurrence_id"] != occurrence_id and state["checked_ids"]:
                    raise ValueError("A newer checklist has already started; undo the earlier completion instead")
                # Reverse the award and reopen the selected item together,
                # before reject/undo can yield to a configuration edit.
                await self.async_undo_chore(completion.id, _checklist_item_id=item_id)
                return {"completed": False, "progress": self._checklist_response_progress(chore_id, child_id)}
            if occurrence_id != state["occurrence_id"]:
                raise ValueError("This checklist occurrence has changed; refresh the card and try again")
            if not self._checklist_can_start(chore, child_id):
                raise ValueError("This chore is not available for this child right now")
            if (item_id in state["checked_ids"]) == checked:
                return {"completed": False, "progress": self.checklist_progress_for_chore(chore, child_id)}
            old_state = {**state, "checked_ids": list(state["checked_ids"])}
            if checked:
                state["checked_ids"].append(item_id)
            else:
                state["checked_ids"].remove(item_id)
            state["final_item_id"] = item_id if checked else ""
            if checked:
                self._prepare_routine_runs(chore_id, child_id)
            self.storage.save_checklist_progress(state)
            if checked and all(item["id"] in state["checked_ids"] for item in chore.checklist_items):
                try:
                    completion = await self.async_complete_chore(
                        chore_id, child_id, photo_url=photo_url, _checklist_occurrence_id=occurrence_id
                    )
                except Exception:
                    # Validation errors (for example missing evidence) leave
                    # the final checkbox ready to retry. Never unwind a record
                    # that has already awarded while a notification failed.
                    if not any(c.checklist_occurrence_id == occurrence_id for c in self.storage.get_completions()):
                        self.storage.save_checklist_progress(old_state)
                    raise
                if completion is None:
                    self.storage.save_checklist_progress(old_state)
                    await self.storage.async_save()
                    await self.async_refresh()
                    raise ValueError("This chore is no longer available; refresh the card")
                return {
                    "completed": True,
                    "completion_id": completion.id,
                    "approved": completion.approved,
                    "points_awarded": completion.points_awarded,
                    "progress": self._checklist_response_progress(chore_id, child_id),
                }
            await self.storage.async_save()
            await self.async_refresh()
            return {"completed": False, "progress": self._checklist_response_progress(chore_id, child_id)}

    def _restore_rejected_checklist(self, completion, *, unchecked_item_id: str = "") -> None:
        """Undo reopens only the final item, retaining the child's earlier work."""
        if not completion.checklist_occurrence_id:
            return
        chore = self.storage.get_chore(completion.chore_id)
        if not chore or chore.task_type != "checklist":
            return
        state = self.storage.get_checklist_progress(chore.id, completion.child_id)
        # An older rejection must not erase progress in a newer occurrence.
        if state and state["occurrence_id"] != completion.checklist_occurrence_id and state["checked_ids"]:
            return
        state = {
            "chore_id": chore.id,
            "child_id": completion.child_id,
            "period": self._checklist_period(chore, completion.completed_at),
            "signature": self._checklist_signature(chore),
            # A late retry of the original final tap must not re-submit the
            # chore after its completion has deliberately been undone.
            "occurrence_id": generate_id(),
            "checked_ids": [
                item["id"]
                for item in chore.checklist_items
                if item["id"] != (unchecked_item_id or completion.checklist_final_item_id)
                and item["id"] in {item["id"] for item in completion.checklist_items}
            ],
            "completion_id": "",
            "final_item_id": "",
        }
        self.storage.save_checklist_progress(state)
