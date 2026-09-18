"""Reward edits preserve deposited points and the price of completed purchases."""

from datetime import UTC, datetime

import pytest

from custom_components.taskmate.models import Reward, RewardClaim
from custom_components.taskmate.sensor import _build_recent_transactions

from .test_jackpot_redemption import _balances, _coord


def _history(coord, *, cost=None):
    claim = RewardClaim(
        reward_id="prize", child_id="kid1", claimed_at=datetime.now(UTC), approved=True, approved_cost=cost
    )
    coord.storage.add_reward_claim(claim)
    return claim


@pytest.mark.parametrize("jackpot", [False, True])
async def test_unassigning_saver_refunds_their_deposit_and_cancels_their_pending_claim(jackpot):
    coord = _coord(jackpot=jackpot)
    history = _history(coord, cost=50)
    await coord.async_allocate_points_to_pool("kid1", "prize", 40 if jackpot else 50)
    await coord.async_allocate_points_to_pool("kid2", "prize", 10)
    pending = await coord.async_claim_reward("prize", "kid1")
    coord.storage.add_reward(Reward(name="Other", cost=5, id="other"))
    unrelated = await coord.async_claim_reward("other", "kid1")

    reward = coord.get_reward("prize")
    reward.assigned_to = ["kid2"]
    await coord.async_update_reward(reward)

    assert _balances(coord) == [100, 90]
    assert coord.storage.get_pool_allocation("kid1", "prize") is None
    assert coord.storage.get_pool_allocation("kid2", "prize").allocated_points == 10
    assert {claim.id for claim in coord.storage.get_reward_claims()} == {history.id, unrelated.id}
    assert coord.get_reward("prize").quantity == 3
    coord.notifications.clear_approval.assert_awaited_once_with("pending_reward_claim", pending.id)


async def test_unassignment_also_releases_wallet_claim_reservations():
    coord = _coord(jackpot=False)
    claim = await coord.async_claim_reward("prize", "kid1")
    reward = coord.get_reward("prize")
    reward.assigned_to = ["kid2"]
    await coord.async_update_reward(reward)

    assert coord.storage.get_pending_reward_claims() == []
    assert _balances(coord) == [100, 100]
    coord.notifications.clear_approval.assert_awaited_once_with("pending_reward_claim", claim.id)


async def test_assignment_refund_precedes_shared_pool_cost_reduction():
    coord = _coord()
    await coord.async_allocate_points_to_pool("kid1", "prize", 40)
    await coord.async_allocate_points_to_pool("kid2", "prize", 10)
    reward = coord.get_reward("prize")
    reward.assigned_to = ["kid2"]
    reward.cost = 10
    await coord.async_update_reward(reward)

    assert _balances(coord) == [100, 90]
    assert coord.storage.get_total_allocated_for_reward("prize") == 10
    claim = await coord.async_claim_reward("prize", "kid2")
    await coord.async_approve_reward(claim.id)
    assert _balances(coord) == [100, 90]


@pytest.mark.parametrize("transition", ["shared-to-individual", "individual-to-shared", "disable-pool"])
async def test_funding_mode_change_refunds_deposits_and_requires_new_claim(transition):
    coord = _coord(jackpot=transition == "shared-to-individual")
    history = _history(coord, cost=50)
    await coord.async_allocate_points_to_pool("kid1", "prize", 50)
    claim = await coord.async_claim_reward("prize", "kid1")
    reward = coord.get_reward("prize")
    if transition == "disable-pool":
        reward.pool_enabled = False
    else:
        reward.is_jackpot = not reward.is_jackpot
    await coord.async_update_reward(reward)

    assert _balances(coord) == [100, 100]
    assert coord.storage.get_pool_allocations() == []
    assert [saved.id for saved in coord.storage.get_reward_claims()] == [history.id]
    assert coord.get_reward("prize").quantity == 3
    coord.notifications.clear_approval.assert_awaited_once_with("pending_reward_claim", claim.id)
    await coord.async_approve_reward(claim.id)
    assert _balances(coord) == [100, 100]


async def test_full_payload_with_unchanged_mode_preserves_deposits_and_claim():
    coord = _coord()
    await coord.async_allocate_points_to_pool("kid1", "prize", 50)
    claim = await coord.async_claim_reward("prize", "kid1")
    reward = coord.get_reward("prize")
    reward.name = "Renamed prize"
    await coord.async_update_reward(Reward.from_dict(reward.to_dict()))

    assert _balances(coord) == [50, 100]
    assert coord.storage.get_total_allocated_for_reward("prize") == 50
    assert [pending.id for pending in coord.storage.get_pending_reward_claims()] == [claim.id]
    coord.notifications.clear_approval.assert_not_awaited()


async def test_changing_assignment_to_everyone_keeps_existing_savings():
    coord = _coord(jackpot=False)
    reward = coord.get_reward("prize")
    reward.assigned_to = ["kid1"]
    coord.storage.update_reward(reward)
    await coord.async_allocate_points_to_pool("kid1", "prize", 40)
    reward.assigned_to = []
    await coord.async_update_reward(reward)

    assert _balances(coord) == [60, 100]
    assert coord.storage.get_total_allocated_for_reward("prize") == 40


@pytest.mark.parametrize("paid_cost", [0, 50])
async def test_approval_records_paid_cost_which_survives_later_price_edits(paid_cost):
    coord = _coord(jackpot=False)
    reward = coord.get_reward("prize")
    reward.cost = paid_cost
    coord.storage.update_reward(reward)
    claim = await coord.async_claim_reward("prize", "kid1")
    await coord.async_approve_reward(claim.id)
    for new_price in (20, 90):
        reward = coord.get_reward("prize")
        reward.cost = new_price
        await coord.async_update_reward(reward)
        assert coord.storage.get_reward_claims()[0].approved_cost == paid_cost
        assert coord._spent_in_period("kid1") == paid_cost
    assert coord.get_child("kid1").points == 100 - paid_cost


async def test_first_price_edit_snapshots_legacy_history_without_rewriting_it_again():
    coord = _coord(jackpot=False)
    legacy = _history(coord)
    for price in (20, 1):
        reward = coord.get_reward("prize")
        reward.cost = price
        await coord.async_update_reward(reward)
        claim = next(c for c in coord.storage.get_reward_claims() if c.id == legacy.id)
        assert claim.approved_cost == 50
        assert coord._spent_in_period("kid1") == 50


async def test_price_cut_does_not_create_room_under_spending_cap():
    coord = _coord(jackpot=False)
    coord.storage.set_setting("spend_cap_enabled", True)
    coord.storage.set_setting("spend_cap_amount", 60)
    first = await coord.async_claim_reward("prize", "kid1")
    await coord.async_approve_reward(first.id)
    reward = coord.get_reward("prize")
    reward.cost = 20
    await coord.async_update_reward(reward)
    second = await coord.async_claim_reward("prize", "kid1")

    with pytest.raises(ValueError, match="Spending cap reached"):
        await coord.async_approve_reward(second.id)
    assert coord.get_child("kid1").points == 50
    assert coord.get_reward("prize").quantity == 2


async def test_pending_claim_is_priced_at_approval_and_only_then_frozen():
    coord = _coord(jackpot=False)
    claim = await coord.async_claim_reward("prize", "kid1")
    reward = coord.get_reward("prize")
    reward.cost = 20
    await coord.async_update_reward(reward)
    assert coord.storage.get_reward_claims()[0].approved_cost is None
    await coord.async_approve_reward(claim.id)
    assert coord.storage.get_reward_claims()[0].approved_cost == 20
    assert coord.get_child("kid1").points == 80


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, None),
        (0, 0),
        (50, 50),
        ("50", 50),
        (2.5, 2.5),
        (True, None),
        (-1, None),
        ("bad", None),
        (float("nan"), None),
        (float("inf"), None),
    ],
)
def test_paid_cost_storage_round_trip_and_invalid_backup_values(raw, expected):
    claim = RewardClaim.from_dict({"approved_cost": raw})
    assert claim.approved_cost == expected
    assert RewardClaim.from_dict(claim.to_dict()).approved_cost == expected


@pytest.mark.parametrize(
    "approved, paid, expected", [(True, 50, -50), (True, 0, 0), (True, None, -20), (False, None, -20)]
)
def test_activity_sensor_uses_paid_cost_for_approved_claim(approved, paid, expected):
    coord = _coord(jackpot=False)
    reward = coord.get_reward("prize")
    reward.cost = 20
    claim = RewardClaim(
        reward_id=reward.id, child_id="kid1", claimed_at=datetime.now(UTC), approved=approved, approved_cost=paid
    )
    events = _build_recent_transactions(
        {
            "data": {"reward_claims": [claim]},
            "child_lookup": {"kid1": coord.get_child("kid1")},
            "reward_lookup": {reward.id: reward},
        }
    )
    assert events[0]["points"] == expected
