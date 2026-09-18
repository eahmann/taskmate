"""Reward buttons and stale review actions use current coordinator state."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from custom_components.taskmate.button import ClaimRewardButton
from custom_components.taskmate.models import Reward, RewardClaim

from .test_jackpot_redemption import _coord


def _button(coord, child_id="kid1"):
    entry = MagicMock(entry_id="reward-test")
    return ClaimRewardButton(coord, entry, coord.get_child(child_id), coord.get_reward("prize"))


@pytest.mark.parametrize("jackpot", [False, True])
async def test_fully_funded_reward_button_works_with_empty_wallet(jackpot):
    coord = _coord(jackpot=jackpot)
    child = coord.get_child("kid1")
    child.points = 50
    coord.storage.update_child(child)
    await coord.async_allocate_points_to_pool("kid1", "prize", 50)
    button = _button(coord)

    assert button.available
    assert button.extra_state_attributes["can_afford"]
    assert button.extra_state_attributes["points_needed"] == 0
    assert button.extra_state_attributes["available_points"] == 0
    await coord.async_claim_reward("prize", "kid1")


async def test_partial_jackpot_cannot_use_wallet_funding():
    coord = _coord()
    await coord.async_allocate_points_to_pool("kid2", "prize", 40)
    button = _button(coord)

    assert not button.available
    assert not button.extra_state_attributes["can_afford"]
    assert button.extra_state_attributes["points_needed"] == 10
    assert button.extra_state_attributes["available_points"] == 100
    with pytest.raises(ValueError, match="pool is not full"):
        await coord.async_claim_reward("prize", "kid1")


@pytest.mark.parametrize("pool_points", [0, 20])
async def test_ordinary_reward_can_still_use_wallet(pool_points):
    coord = _coord(jackpot=False)
    if pool_points:
        await coord.async_allocate_points_to_pool("kid1", "prize", pool_points)
    button = _button(coord)

    assert button.available
    assert button.extra_state_attributes["can_afford"]
    assert button.extra_state_attributes["points_needed"] == 0
    await coord.async_claim_reward("prize", "kid1")


@pytest.mark.parametrize("cost", [0, 50])
async def test_wallet_deficit_is_not_hidden_by_nonexistent_pool(cost):
    coord = _coord(jackpot=False)
    child = coord.get_child("kid1")
    child.points = -10
    coord.storage.update_child(child)
    reward = coord.get_reward("prize")
    reward.cost = cost
    coord.storage.update_reward(reward)
    button = _button(coord)

    assert not button.available
    assert not button.extra_state_attributes["can_afford"]
    assert button.extra_state_attributes["points_needed"] == cost + 10
    assert button.extra_state_attributes["available_points"] == -10
    with pytest.raises(ValueError, match="Not enough points"):
        await coord.async_claim_reward("prize", "kid1")


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("assigned_to", ["kid2"], "not available to"),
        ("quantity", 0, "sold out"),
        ("expires_at", "2000-01-01", "expired"),
    ],
)
async def test_unavailable_reward_disables_funded_button(field, value, error):
    coord = _coord(jackpot=False)
    reward = coord.get_reward("prize")
    setattr(reward, field, value)
    coord.storage.update_reward(reward)

    assert not _button(coord).available
    with pytest.raises(ValueError, match=error):
        await coord.async_claim_reward("prize", "kid1")


async def test_time_lock_disables_button_until_allowed_day():
    coord = _coord(jackpot=False)
    reward = coord.get_reward("prize")
    reward.time_lock_enabled = True
    reward.available_days = [0]
    coord.storage.update_reward(reward)
    button = _button(coord)

    with patch("homeassistant.util.dt.now", return_value=datetime(2026, 9, 18, 12, tzinfo=UTC)):
        assert not button.available
        with pytest.raises(ValueError, match="not available right now"):
            await coord.async_claim_reward("prize", "kid1")
    with patch("homeassistant.util.dt.now", return_value=datetime(2026, 9, 21, 12, tzinfo=UTC)):
        assert button.available
        await coord.async_claim_reward("prize", "kid1")


@pytest.mark.parametrize("jackpot", [False, True])
async def test_pending_claim_disables_button_before_coordinator_refresh(jackpot):
    coord = _coord(jackpot=jackpot)
    button = _button(coord)
    claiming_child = "kid2" if jackpot else "kid1"
    if jackpot:
        await coord.async_allocate_points_to_pool("kid2", "prize", 50)
    claim = await coord.async_claim_reward("prize", claiming_child)

    # async_refresh is mocked, so .data is stale. Storage is authoritative.
    assert coord.data == {}
    assert not button.available
    with pytest.raises(ValueError, match="already waiting for approval"):
        await coord.async_claim_reward("prize", "kid1")
    await coord.async_reject_reward(claim.id)
    assert button.available


async def test_button_uses_current_wallet_commitments():
    coord = _coord(jackpot=False)
    coord.storage.add_reward(Reward(name="Other", cost=60, id="other"))
    claim = await coord.async_claim_reward("other", "kid1")
    button = _button(coord)

    assert not button.available
    assert button.extra_state_attributes["committed_points"] == 60
    assert button.extra_state_attributes["available_points"] == 40
    assert button.extra_state_attributes["points_needed"] == 10
    with pytest.raises(ValueError, match="Not enough points"):
        await coord.async_claim_reward("prize", "kid1")
    await coord.async_reject_reward(claim.id)
    assert button.available


async def test_pending_limit_disables_button_even_when_affordable():
    coord = _coord(jackpot=False)
    for index in range(20):
        reward = Reward(name=f"Free reward {index}", cost=0, id=f"free-{index}")
        coord.storage.add_reward(reward)
        coord.storage.add_reward_claim(RewardClaim(reward_id=reward.id, child_id="kid1", claimed_at=datetime.now(UTC)))

    assert not _button(coord).available
    with pytest.raises(ValueError, match="Too many reward claims"):
        await coord.async_claim_reward("prize", "kid1")


@pytest.mark.parametrize("removed", ["child", "reward"])
def test_deleted_record_disables_existing_button(removed):
    coord = _coord(jackpot=False)
    button = _button(coord)
    if removed == "child":
        coord.storage.remove_child("kid1")
    else:
        coord.storage.remove_reward("prize")
    assert not button.available
    assert button.extra_state_attributes == {}


async def test_stale_reject_preserves_approved_claim_and_spending_history():
    coord = _coord(jackpot=False)
    claim = await coord.async_claim_reward("prize", "kid1")
    await coord.async_approve_reward(claim.id)
    before = deepcopy(coord.storage._data)
    coord.storage.async_save.reset_mock()
    coord.async_refresh.reset_mock()
    coord.hass.bus.async_fire.reset_mock()
    coord.notifications.clear_approval.reset_mock()

    await coord.async_reject_reward(claim.id)

    assert coord.storage._data == before
    assert coord.storage.get_reward_claims()[0].approved
    assert coord._spent_in_period("kid1") == 50
    coord.storage.async_save.assert_not_awaited()
    coord.async_refresh.assert_not_awaited()
    coord.hass.bus.async_fire.assert_not_called()
    coord.notifications.clear_approval.assert_not_awaited()


@pytest.mark.parametrize("approve_first", [False, True])
async def test_concurrent_approval_and_rejection_keep_first_decision(approve_first):
    coord = _coord(jackpot=False)
    claim = await coord.async_claim_reward("prize", "kid1")
    approve = coord.async_approve_reward(claim.id)
    reject = coord.async_reject_reward(claim.id)
    await asyncio.gather(*([approve, reject] if approve_first else [reject, approve]))

    claims = coord.storage.get_reward_claims()
    if approve_first:
        assert len(claims) == 1
        assert claims[0].approved
        assert coord.get_child("kid1").points == 50
        assert coord.get_reward("prize").quantity == 2
    else:
        assert claims == []
        assert coord.get_child("kid1").points == 100
        assert coord.get_reward("prize").quantity == 3


async def test_repeated_rejection_does_not_publish_another_decision():
    coord = _coord(jackpot=False)
    claim = await coord.async_claim_reward("prize", "kid1")
    await coord.async_reject_reward(claim.id)
    coord.hass.bus.async_fire.reset_mock()
    coord.notifications.clear_approval.reset_mock()
    coord.storage.async_save.reset_mock()

    await coord.async_reject_reward(claim.id)

    coord.hass.bus.async_fire.assert_not_called()
    coord.notifications.clear_approval.assert_not_awaited()
    coord.storage.async_save.assert_not_awaited()
