"""Shared child-undo policy for services and dashboard completion records."""

from datetime import timedelta

from homeassistant.util import dt as dt_util


def child_undo_metadata(completion, completions, window_seconds: int) -> dict:
    """Publish a deadline, or an unlimited pending withdrawal, without granting access.

    Parent approval clears child_undo_allowed permanently. Legacy records stay
    parent-only. A main chore cannot be withdrawn through a bonus subtask that
    has already been reviewed by a parent or whose own undo window has expired.
    The caller must still enforce the linked-child rule.
    """
    if getattr(completion, "child_undo_allowed", False) is not True:
        return {}
    affected = [completion]
    if not completion.bonus_subtask_id:
        day = dt_util.as_local(completion.completed_at).date()
        affected.extend(
            c
            for c in completions
            if c.chore_id == completion.chore_id
            and c.child_id == completion.child_id
            and c.bonus_subtask_id
            and dt_util.as_local(c.completed_at).date() == day
        )
    if any(getattr(c, "child_undo_allowed", False) is not True for c in affected):
        return {}
    approved = [c for c in affected if c.approved]
    if not approved:
        return {"child_undo_pending": True}
    if window_seconds <= 0:
        return {}
    deadline = min(c.completed_at for c in approved) + timedelta(seconds=window_seconds)
    return {"child_undo_until": deadline.isoformat()}
