"""Versioned, in-process parent-access probe for a trusted companion backend.

There is deliberately no service, WebSocket command, or mutation dispatcher.
The companion constructs a request from its authenticated HA connection and
supplies a server-held verifier. That verifier must validate the exact request,
live connection/session, policy, household, entry, and authorization reference;
it must return literal True only while all bindings remain valid. This Python
boundary trusts installed backend code, never browser-supplied authority.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import Context, HomeAssistant

PROTOCOL_VERSION = 1
PROVIDER_VERSION = "5.6.1+eahmann.13"
_OPERATION = "parent.probe"


class FamilyAPIError(Exception):
    """A safe, stable failure for the companion to map into its own transport."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _context_identity(context: Context) -> tuple[Any, Any, Any]:
    return (
        getattr(context, "user_id", None),
        getattr(context, "id", None),
        getattr(context, "parent_id", None),
    )


@dataclass(frozen=True, slots=True)
class FamilyAccessRequest:
    """Server-only request; its verifier owns session and policy authorization.

    HA Context itself is mutable, so retain its initial identity and reject
    changes rather than silently adopting a different actor after an await.
    No field represents a trusted role or client-provided capability list.
    """

    operation: str
    entry_id: str
    context: Context
    household_id: str
    authorization_id: str
    verify: Callable[[FamilyAccessRequest], Awaitable[bool]] = field(repr=False, compare=False)
    _initial_context: tuple[Any, Any, Any] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_initial_context", _context_identity(self.context))


@dataclass(slots=True)
class _Lifetime:
    active: bool = True


def bind_family_api(coordinator: Any) -> None:
    """Enable the probe only after this coordinator's setup succeeded."""
    unbind_family_api(coordinator)
    coordinator._family_api_lifetime = _Lifetime()


def unbind_family_api(coordinator: Any) -> None:
    """Invalidate held adapters before shutdown, including pending probes."""
    lifetime = getattr(coordinator, "_family_api_lifetime", None)
    if isinstance(lifetime, _Lifetime):
        lifetime.active = False


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _deny() -> FamilyAPIError:
    return FamilyAPIError("unauthorized", "Parent authorization is missing, changed, or no longer valid.")


class FamilyAPI:
    """An entry- and lifetime-bound, nonmutating protocol-1 adapter."""

    def __init__(self, hass: HomeAssistant, entry_id: str, coordinator: Any, lifetime: _Lifetime) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._coordinator = coordinator
        self._lifetime = lifetime

    def _ensure_current(self) -> None:
        coordinator = self._coordinator
        if (
            self._hass.data.get(DOMAIN, {}).get(self._entry_id) is not coordinator
            or getattr(coordinator, "_family_api_lifetime", None) is not self._lifetime
            or not self._lifetime.active
            or getattr(coordinator, "_reset_in_progress", False) is True
            or getattr(coordinator.storage, "is_retired", False) is True
        ):
            raise FamilyAPIError(
                "provider_unavailable", "TaskMate was unloaded, replaced, or reset. Try again after setup."
            )

    def _validate_request(self, request: FamilyAccessRequest) -> None:
        self._ensure_current()
        if (
            type(request) is not FamilyAccessRequest
            or request.operation != _OPERATION
            or request.entry_id != self._entry_id
            or not _identifier(request.household_id)
            or not _identifier(request.authorization_id)
            or not callable(request.verify)
            or not _identifier(request._initial_context[0])
            or _context_identity(request.context) != request._initial_context
        ):
            raise _deny()

    def _check_user(self, user: Any, user_id: str) -> None:
        if user is None or user.id != user_id or user.is_active is not True:
            raise _deny()
        if not user.is_admin and user_id in self._coordinator.storage.get_parent_user_ids():
            raise FamilyAPIError(
                "unsafe_shared_account",
                "This account already has permanent TaskMate parent access. Use a separate non-admin shared account.",
            )

    async def _verify(self, request: FamilyAccessRequest) -> None:
        self._validate_request(request)
        try:
            authorized = await request.verify(request)
        except Exception:
            # Do not expose verifier exceptions, which may include session data.
            raise _deny() from None
        self._validate_request(request)
        if authorized is not True:
            raise _deny()

    async def async_probe(self, request: FamilyAccessRequest) -> dict[str, Any]:
        """Verify delegated parent access without reading or changing records."""
        await self._verify(request)
        user_id = request._initial_context[0]
        user = await self._hass.auth.async_get_user(user_id)
        self._validate_request(request)
        self._check_user(user, user_id)
        # Recheck companion session/policy after HA authentication yielded.
        await self._verify(request)
        self._check_user(user, user_id)
        return {
            "authorized": True,
            "protocol": PROTOCOL_VERSION,
            "provider_version": PROVIDER_VERSION,
            "entry_id": self._entry_id,
            "actor_user_id": user_id,
        }

    async def _async_check_actor_eligibility(self, context: Context) -> bool:
        self._ensure_current()
        identity = _context_identity(context)
        user_id = identity[0]
        if not _identifier(user_id):
            raise _deny()
        user = await self._hass.auth.async_get_user(user_id)
        self._ensure_current()
        if _context_identity(context) != identity:
            raise _deny()
        self._check_user(user, user_id)
        return True


async def async_get_family_api(hass: HomeAssistant, entry_id: str, protocol: int = 1) -> FamilyAPI:
    """Resolve exactly one active entry, never the first configured household."""
    if type(protocol) is not int or protocol != PROTOCOL_VERSION:
        raise FamilyAPIError("unsupported_protocol", "TaskMate family access requires protocol 1.")
    coordinator = hass.data.get(DOMAIN, {}).get(entry_id) if _identifier(entry_id) else None
    lifetime = getattr(coordinator, "_family_api_lifetime", None)
    if coordinator is None or not isinstance(lifetime, _Lifetime):
        raise FamilyAPIError("provider_unavailable", "This TaskMate entry does not have an active family access API.")
    api = FamilyAPI(hass, entry_id, coordinator, lifetime)
    api._ensure_current()
    return api


async def async_check_actor_eligibility(hass: HomeAssistant, entry_id: str, context: Context) -> bool:
    """Preflight an account; success grants no capability or parent session.

    Administrators retain their native authority and are eligible for setup.
    Non-admin permanent TaskMate parents cannot safely serve as locked kiosks.
    """
    api = await async_get_family_api(hass, entry_id)
    return await api._async_check_actor_eligibility(context)
