"""The auto-completion bonus is promised when the last step is submitted."""

from datetime import timedelta
from unittest.mock import patch

import pytest

from .test_checklist_chores import _approved_parents, _now, _run, _setup, _step


@pytest.mark.parametrize("bonus", [0, 10])
def test_delayed_approval_preserves_completion_bonus_after_chore_edit(bonus):
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=True, points=bonus)
        first = await _step(coord, chore, child, 0)
        last = await _step(coord, chore, child, 1)
        assert storage.get_completions()[-1].checklist_bonus_points == bonus
        chore.points = 99
        await coord.async_update_chore(chore)
        with patch("custom_components.taskmate.coord_chores.dt_util.now", return_value=_now() + timedelta(days=1)):
            await coord.async_approve_chore(last.id)
            await coord.async_approve_chore(first.id)
        assert _approved_parents(storage, chore, child)[0].points_awarded == bonus
        assert storage.get_child(child.id).points == 5 + bonus

    _run(scenario)


def test_time_and_roulette_bonus_saved_on_last_step_not_recalculated_on_review():
    async def scenario():
        coord, storage, child, chore = await _setup(requires_approval=True, points=10)
        first = await _step(coord, chore, child, 0)
        with (
            patch.object(coord, "_apply_speed_bonus", return_value=15) as speed,
            patch.object(coord, "_apply_roulette_multiplier", side_effect=lambda c, cid, p: p * 2),
        ):
            last = await _step(coord, chore, child, 1)
        speed.assert_called_once()
        assert last.checklist_bonus_points == 30
        await coord.async_approve_chore(first.id)
        await coord.async_approve_chore(last.id)
        assert _approved_parents(storage, chore, child)[0].points_awarded == 30
        assert storage.get_child(child.id).points == 35

    _run(scenario)
