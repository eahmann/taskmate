"""Shared jackpots have one pending redemption per funded pool."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, call

import pytest

from custom_components.taskmate.models import Child, Reward, RewardClaim
from custom_components.taskmate.storage import TaskMateStorage

from .test_coordinator_rewards import _make_coord


def _coord(*, jackpot=True):
    coord = _make_coord()
    # Use real storage reads/writes, including fresh model copies on every read,
    # so a second request sees claims and allocations changed by the first one.
    coord.storage = TaskMateStorage(coord.hass, "jackpot-test")
    coord.storage._data = {
        "children": [Child(name=f"Kid {i}", points=100, id=f"kid{i}").to_dict() for i in (1, 2)],
        "rewards": [
            Reward(name="Prize", cost=50, is_jackpot=jackpot, pool_enabled=True, quantity=3, id="prize").to_dict()
        ],
        "reward_claims": [],
        "pool_allocations": [],
    }

    async def save():
        await asyncio.sleep(0)

    coord.storage.async_save = AsyncMock(side_effect=save)
    coord._async_notify_pending_reward_claim = AsyncMock()
    coord.async_start_unlock = AsyncMock()
    return coord


async def _fund(coord):
    await coord.async_allocate_points_to_pool("kid1", "prize", 40)
    await coord.async_allocate_points_to_pool("kid2", "prize", 10)


def _balances(coord):
    return [coord.get_child(cid).points for cid in ("kid1", "kid2")]


def _claim(child_id, reward_id="prize", *, approved=False):
    return RewardClaim(reward_id=reward_id, child_id=child_id, claimed_at=datetime.now(UTC), approved=approved)


async def test_pending_jackpot_blocks_every_participant():
    coord = _coord()
    await _fund(coord)
    first = await coord.async_claim_reward("prize", "kid1")

    with pytest.raises(ValueError, match="already waiting for approval"):
        await coord.async_claim_reward("prize", "kid2")

    assert [claim.id for claim in coord.storage.get_pending_reward_claims()] == [first.id]
    coord._async_notify_pending_reward_claim.assert_awaited_once()
    assert _balances(coord) == [60, 90]


async def test_simultaneous_jackpot_requests_only_create_one_claim():
    coord = _coord()
    await _fund(coord)
    results = await asyncio.gather(
        coord.async_claim_reward("prize", "kid1"),
        coord.async_claim_reward("prize", "kid2"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, RewardClaim) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert len(coord.storage.get_pending_reward_claims()) == 1
    coord._async_notify_pending_reward_claim.assert_awaited_once()


async def test_unfunded_jackpot_cannot_be_claimed_using_wallet_points():
    coord = _coord()
    with pytest.raises(ValueError, match="pool"):
        await coord.async_claim_reward("prize", "kid1")
    assert coord.storage.get_reward_claims() == []
    assert _balances(coord) == [100, 100]


async def test_unfunded_legacy_claim_cannot_charge_the_wallet_on_approval():
    coord = _coord()
    stale = _claim("kid2")
    coord.storage.add_reward_claim(stale)
    with pytest.raises(ValueError, match="pool"):
        await coord.async_approve_reward(stale.id)
    assert _balances(coord) == [100, 100]
    assert coord.get_reward("prize").quantity == 3
    assert not coord.storage.get_reward_claims()[0].approved
    coord.async_start_unlock.assert_not_awaited()


def test_underfunded_jackpot_pending_claim_does_not_reserve_wallet_points():
    coord = _coord()
    claim = _claim("kid1")
    assert coord.is_pool_mode_claim(claim)


async def test_underfunded_approval_preserves_allocations_and_legacy_claims():
    coord = _coord()
    await coord.async_allocate_points_to_pool("kid1", "prize", 40)
    first, duplicate = _claim("kid1"), _claim("kid2")
    coord.storage.add_reward_claim(first)
    coord.storage.add_reward_claim(duplicate)
    with pytest.raises(ValueError, match="pool"):
        await coord.async_approve_reward(first.id)
    assert _balances(coord) == [60, 100]
    assert coord.storage.get_total_allocated_for_reward("prize") == 40
    assert coord.get_reward("prize").quantity == 3
    assert {claim.id for claim in coord.storage.get_pending_reward_claims()} == {first.id, duplicate.id}
    coord.notifications.clear_approval.assert_not_awaited()
    coord.async_start_unlock.assert_not_awaited()


async def test_approval_spends_pool_once_and_next_redemption_needs_new_funding():
    coord = _coord()
    await _fund(coord)
    first = await coord.async_claim_reward("prize", "kid1")
    await coord.async_approve_reward(first.id)
    await coord.async_approve_reward(first.id)  # Repeat approval is harmless.
    assert _balances(coord) == [60, 90]
    assert coord.storage.get_total_allocated_for_reward("prize") == 0
    assert coord.get_reward("prize").quantity == 2
    with pytest.raises(ValueError, match="pool"):
        await coord.async_claim_reward("prize", "kid2")

    await _fund(coord)
    second = await coord.async_claim_reward("prize", "kid2")
    await coord.async_approve_reward(second.id)
    assert _balances(coord) == [20, 80]
    assert coord.get_reward("prize").quantity == 1
    assert len([claim for claim in coord.storage.get_reward_claims() if claim.approved]) == 2


async def test_rejecting_claim_keeps_pool_available_for_another_participant():
    coord = _coord()
    await _fund(coord)
    first = await coord.async_claim_reward("prize", "kid1")
    await coord.async_reject_reward(first.id)
    assert coord.storage.get_total_allocated_for_reward("prize") == 50
    second = await coord.async_claim_reward("prize", "kid2")
    await coord.async_approve_reward(second.id)
    assert _balances(coord) == [60, 90]
    assert coord.get_reward("prize").quantity == 2


async def test_approval_clears_legacy_duplicates_without_touching_other_claims():
    coord = _coord()
    await _fund(coord)
    first = _claim("kid1")
    duplicate = _claim("kid2")
    history = _claim("kid1", approved=True)
    other = _claim("kid2", "another-prize")
    for claim in (first, duplicate, history, other):
        coord.storage.add_reward_claim(claim)

    await asyncio.gather(coord.async_approve_reward(first.id), coord.async_approve_reward(duplicate.id))
    assert {claim.id for claim in coord.storage.get_reward_claims()} == {first.id, history.id, other.id}
    assert _balances(coord) == [60, 90]
    assert coord.get_reward("prize").quantity == 2
    coord.notifications.clear_approval.assert_has_awaits(
        [call("pending_reward_claim", first.id), call("pending_reward_claim", duplicate.id)], any_order=True
    )

    # The old duplicate must not spend a newly funded cycle, either.
    await _fund(coord)
    await coord.async_approve_reward(duplicate.id)
    assert coord.storage.get_total_allocated_for_reward("prize") == 50
    assert coord.get_reward("prize").quantity == 2


async def test_non_jackpot_savings_jars_can_be_claimed_independently():
    coord = _coord(jackpot=False)
    for cid in ("kid1", "kid2"):
        await coord.async_allocate_points_to_pool(cid, "prize", 50)
    first = await coord.async_claim_reward("prize", "kid1")
    second = await coord.async_claim_reward("prize", "kid2")
    await coord.async_approve_reward(first.id)
    assert [claim.id for claim in coord.storage.get_pending_reward_claims()] == [second.id]
    await coord.async_approve_reward(second.id)
    assert _balances(coord) == [50, 50]
    assert coord.get_reward("prize").quantity == 1
