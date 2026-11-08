"""One run's effect on tracked jobs: membership, status and which jobs need a new version.

Pure function, no I/O; docs/contract.md explains the rules, tests/test_lifecycle.py pins them.
Order per job: positive sightings win; authoritative (board) absence closes; selection (query)
absence only unlists; anything unresolved keeps the prior status and, after a week without
confirmation, becomes unverified.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .contract import RESOLVED, Kind, Scope

Key = tuple[str, str, str]          # (source, board, native_id)
UNVERIFIED_AFTER = timedelta(days=7)
HIDDEN_UNTIL_SEEN = {"closed", "unlisted"}


@dataclass(frozen=True)
class JobState:
    key: Key
    company_id: str
    first_seen: datetime
    last_confirmed_seen: datetime
    last_changed: datetime
    current_hash: str
    status: str                     # open | closed | unlisted | unverified | filtered_out
    scopes: tuple[str, ...]         # sorted membership
    closed_at: datetime | None = None


@dataclass(frozen=True)
class Sighting:
    """A job seen in this run, merged across every scope that returned it."""
    key: Key
    company_id: str
    payload_hash: str
    scopes: frozenset[str]
    kept: bool                      # passes the role x Europe/remote filter


def step(state: dict[Key, JobState], outcomes: dict[str, Scope], sightings: dict[Key, Sighting],
         now: datetime, unverified_after: timedelta = UNVERIFIED_AFTER) -> tuple[dict[Key, JobState], list[Key]]:
    """Returns the new state and the keys whose payload changed (each needs a version row).

    `outcomes` holds every scope that reported this run; a scope with no outcome (a missing shard)
    is unresolved, like a failed one. Jobs never kept are not tracked.
    """
    new_state: dict[Key, JobState] = {}
    changed: list[Key] = []
    for key in sorted(state.keys() | sightings.keys()):
        prev, seen = state.get(key), sightings.get(key)
        if prev is None:
            assert seen is not None
            if not seen.kept:
                continue
        member = set(prev.scopes) if prev else set()
        seen_in = seen.scopes if seen else frozenset()
        removed_by_board = False
        for scope in member | seen_in:
            if scope in seen_in:
                member.add(scope)
            elif (o := outcomes.get(scope)) is not None and o.status in RESOLVED:
                member.discard(scope)
                removed_by_board |= o.kind == Kind.BOARD

        if seen:
            is_new = prev is None or prev.current_hash != seen.payload_hash
            new_state[key] = JobState(
                key=key, company_id=seen.company_id,
                first_seen=prev.first_seen if prev else now,
                last_confirmed_seen=now,
                last_changed=now if is_new or prev is None else prev.last_changed,
                current_hash=seen.payload_hash,
                status="open" if seen.kept else "filtered_out",
                scopes=tuple(sorted(member)), closed_at=None)
            if is_new:
                changed.append(key)
            continue

        assert prev is not None
        if prev.status in HIDDEN_UNTIL_SEEN:
            status, closed_at = prev.status, prev.closed_at
        elif removed_by_board:
            status, closed_at = "closed", now
        elif not member:
            status, closed_at = "unlisted", None
        elif prev.status == "open" and now - prev.last_confirmed_seen > unverified_after:
            status, closed_at = "unverified", None
        else:
            status, closed_at = prev.status, prev.closed_at
        new_state[key] = replace(prev, status=status, scopes=tuple(sorted(member)), closed_at=closed_at)
    return new_state, changed
