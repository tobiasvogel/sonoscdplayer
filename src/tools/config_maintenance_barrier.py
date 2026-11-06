"""OPS-01 pure offline admission barrier; not connected to the player.

The model describes a future serialized player-command gate. It has no
socket, GPIO, drive, Sonos, systemd, filesystem or writer integration.
A simulated FROZEN state is NOT production maintenance authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading


class BarrierState(str, Enum):
    OPEN = "OPEN"
    DRAINING = "DRAINING"
    FROZEN = "FROZEN"
    CLOSED = "CLOSED"


class AdmissionRefused(ValueError):
    """The offline barrier refused a state change or evidence."""


@dataclass(frozen=True)
class IdleProof:
    """Explicit controller-level booleans; none can be unknown."""

    mode_stopped: bool
    no_owned_url: bool
    no_stream: bool
    no_reader: bool
    no_joined_group: bool
    no_uncertain_group: bool
    no_group_recovery: bool
    no_pending_external_activity: bool


class OfflineMaintenanceBarrier:
    """Lock-protected *simulation* of admission and command-drain ordering.

    A caller is responsible for issuing and releasing tickets around ALL
    state-changing requests and periodic refreshes. Nothing here coordinates
    with a real service; external requests or processes remain unexcluded.
    """

    def __init__(self):
        self._guard = threading.Lock()
        self._state = BarrierState.OPEN
        self._next_ticket = 0
        self._active: set[int] = set()

    @property
    def state(self) -> BarrierState:
        with self._guard:
            return self._state

    @property
    def in_flight(self) -> int:
        with self._guard:
            return len(self._active)

    def admit(self, *, read_only: bool = False) -> int | None:
        """Issue a work ticket only while OPEN; diagnostic reads are unticketed.

        Read-only here is a caller's classification, not independent
        authorization. A real integration must not let callers mark
        state-changing commands as read-only.
        """
        with self._guard:
            if read_only:
                if self._state == BarrierState.CLOSED:
                    raise AdmissionRefused("barrier closed")
                return None
            if self._state != BarrierState.OPEN:
                raise AdmissionRefused("maintenance admission closed")
            self._next_ticket += 1
            ticket = self._next_ticket
            self._active.add(ticket)
            return ticket

    def finish(self, ticket: int) -> None:
        """Consume a genuine outstanding ticket exactly once."""
        with self._guard:
            if type(ticket) is not int or ticket not in self._active:
                raise AdmissionRefused("unknown or completed ticket")
            self._active.remove(ticket)

    def begin_draining(self) -> None:
        """Atomically refuse new simulated mutation and refresh submissions."""
        with self._guard:
            if self._state != BarrierState.OPEN:
                raise AdmissionRefused("drain already initiated")
            self._state = BarrierState.DRAINING

    def freeze(self, proof: IdleProof) -> None:
        """Move to FROZEN only with no work and strictly true idle claims."""
        with self._guard:
            if self._state != BarrierState.DRAINING:
                raise AdmissionRefused("draining required")
            if self._active:
                raise AdmissionRefused("unfinished command or refresh")
            if not isinstance(proof, IdleProof):
                raise AdmissionRefused("missing controller evidence")
            if any(type(value) is not bool or value is not True
                   for value in vars(proof).values()):
                raise AdmissionRefused("player not proven idle")
            self._state = BarrierState.FROZEN

    def close(self) -> None:
        """Irreversibly fail closed. Never replay or resume queued requests."""
        with self._guard:
            self._state = BarrierState.CLOSED
            self._active.clear()

    def report(self) -> dict:
        """Non-secret, non-authorizing diagnostic result."""
        with self._guard:
            return {
                "state": self._state.value,
                "in_flight": len(self._active),
                "write_authorized": False,
                "live_exclusion_verified": False,
            }
