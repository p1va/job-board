"""The lifecycle contract examples from docs/contract.md, as executable cases."""
from datetime import datetime, timedelta, timezone

from jobboard.contract import Kind, Scope, Status
from jobboard.lifecycle import Sighting, step

T0 = datetime(2026, 11, 9, 6, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)
ACME = ("ashby", "acme", "j1")
WJ = ("workable", "4f1c-uuid", "AB12")       # a Workable job found through searches


def board(scope_id, status, jobs=0):
    return Scope(scope_id, Kind.BOARD, Status(status), jobs=jobs, error="x" if status in ("failed", "partial") else None)


def query(scope_id, status, jobs=0):
    return Scope(scope_id, Kind.QUERY, Status(status), jobs=jobs, error="x" if status in ("failed", "partial") else None)


def outcomes(*scopes):
    return {s.scope_id: s for s in scopes}


def seen(key, *scopes, h="h1", kept=True):
    return {key: Sighting(key, "co", h, frozenset(scopes), kept)}


def test_new_kept_job_is_tracked_and_versioned():
    state, changed = step({}, outcomes(board("ashby:acme", "complete", 1)), seen(ACME, "ashby:acme"), T0)
    assert changed == [ACME]
    s = state[ACME]
    assert (s.status, s.first_seen, s.last_confirmed_seen, s.scopes) == ("open", T0, T0, ("ashby:acme",))


def test_job_never_kept_is_not_tracked():
    state, changed = step({}, outcomes(board("ashby:acme", "complete", 1)), seen(ACME, "ashby:acme", kept=False), T0)
    assert state == {} and changed == []


def test_edit_writes_a_version_and_keeps_first_seen():
    s0, _ = step({}, outcomes(board("ashby:acme", "complete", 1)), seen(ACME, "ashby:acme"), T0)
    s1, changed = step(s0, outcomes(board("ashby:acme", "complete", 1)), seen(ACME, "ashby:acme", h="h2"), T0 + DAY)
    assert changed == [ACME]
    assert (s1[ACME].first_seen, s1[ACME].last_changed, s1[ACME].current_hash) == (T0, T0 + DAY, "h2")


def test_unchanged_job_writes_no_version():
    s0, _ = step({}, outcomes(board("ashby:acme", "complete", 1)), seen(ACME, "ashby:acme"), T0)
    s1, changed = step(s0, outcomes(board("ashby:acme", "complete", 1)), seen(ACME, "ashby:acme"), T0 + DAY)
    assert changed == [] and s1[ACME].last_confirmed_seen == T0 + DAY and s1[ACME].last_changed == T0


def test_real_empty_board_closes_its_jobs():
    s0, _ = step({}, outcomes(board("ashby:acme", "complete", 1)), seen(ACME, "ashby:acme"), T0)
    s1, _ = step(s0, outcomes(board("ashby:acme", "empty")), {}, T0 + DAY)
    assert (s1[ACME].status, s1[ACME].closed_at, s1[ACME].scopes) == ("closed", T0 + DAY, ())


def test_complete_board_without_the_job_closes_it():
    s0, _ = step({}, outcomes(board("ashby:acme", "complete", 1)), seen(ACME, "ashby:acme"), T0)
    other = ("ashby", "acme", "j2")
    s1, _ = step(s0, outcomes(board("ashby:acme", "complete", 1)), seen(other, "ashby:acme"), T0 + DAY)
    assert s1[ACME].status == "closed" and s1[other].status == "open"


def test_failed_board_carries_forward_then_unverified_after_a_week():
    s, _ = step({}, outcomes(board("ashby:acme", "complete", 1)), seen(ACME, "ashby:acme"), T0)
    for day in range(1, 8):
        s, _ = step(s, outcomes(board("ashby:acme", "failed")), {}, T0 + day * DAY)
        assert s[ACME].status == "open" and s[ACME].last_confirmed_seen == T0
    s, _ = step(s, outcomes(board("ashby:acme", "failed")), {}, T0 + 8 * DAY)
    assert s[ACME].status == "unverified" and s[ACME].scopes == ("ashby:acme",)


def test_missing_shard_carries_forward():
    s0, _ = step({}, outcomes(board("ashby:acme", "complete", 1)), seen(ACME, "ashby:acme"), T0)
    s1, changed = step(s0, {}, {}, T0 + DAY)            # no outcome at all for ashby:acme
    assert changed == [] and s1[ACME].status == "open" and s1[ACME].last_confirmed_seen == T0


def test_workable_overlap_with_failed_queries_never_hides_a_live_job():
    gb, remote = "workable:search:country=GB", "workable:search:remote"
    s, _ = step({}, outcomes(query(gb, "complete", 1), query(remote, "complete", 1)), seen(WJ, gb, remote), T0)
    # run 1: GB fails, remote still sees the job
    s, _ = step(s, outcomes(query(gb, "failed"), query(remote, "complete", 1)), seen(WJ, remote), T0 + DAY)
    assert s[WJ].status == "open" and s[WJ].scopes == (gb, remote)
    # run 2: the job became UK-onsite: remote completes without it, GB fails again
    s, _ = step(s, outcomes(query(gb, "failed"), query(remote, "complete", 5)), {}, T0 + 2 * DAY)
    assert s[WJ].status == "open" and s[WJ].scopes == (gb,) and s[WJ].last_confirmed_seen == T0 + DAY
    # run 3: GB works again and sees it
    s, _ = step(s, outcomes(query(gb, "complete", 1), query(remote, "complete", 5)), seen(WJ, gb, h="h2"), T0 + 3 * DAY)
    assert s[WJ].status == "open" and s[WJ].scopes == (gb,) and s[WJ].last_confirmed_seen == T0 + 3 * DAY


def test_search_only_absence_unlists_but_never_closes():
    gb, remote = "workable:search:country=GB", "workable:search:remote"
    s, _ = step({}, outcomes(query(gb, "complete", 1), query(remote, "complete", 1)), seen(WJ, gb, remote), T0)
    s, _ = step(s, outcomes(query(gb, "complete", 3), query(remote, "complete", 3)), {}, T0 + DAY)
    assert (s[WJ].status, s[WJ].closed_at, s[WJ].scopes) == ("unlisted", None, ())


def test_reopened_job_keeps_first_seen():
    s, _ = step({}, outcomes(board("ashby:acme", "complete", 1)), seen(ACME, "ashby:acme"), T0)
    s, _ = step(s, outcomes(board("ashby:acme", "empty")), {}, T0 + DAY)
    s, changed = step(s, outcomes(board("ashby:acme", "complete", 1)), seen(ACME, "ashby:acme"), T0 + 2 * DAY)
    assert (s[ACME].status, s[ACME].first_seen, s[ACME].closed_at) == ("open", T0, None)
    assert changed == []                                 # same payload as before closing: no new version


def test_job_leaving_the_filter_is_filtered_out_not_closed():
    s, _ = step({}, outcomes(board("ashby:acme", "complete", 1)), seen(ACME, "ashby:acme"), T0)
    s, changed = step(s, outcomes(board("ashby:acme", "complete", 1)), seen(ACME, "ashby:acme", h="h2", kept=False), T0 + DAY)
    assert s[ACME].status == "filtered_out" and changed == [ACME]


def test_replaying_the_same_run_changes_nothing():
    run = (outcomes(board("ashby:acme", "complete", 1)), seen(ACME, "ashby:acme"), T0 + DAY)
    s0, _ = step({}, outcomes(board("ashby:acme", "complete", 1)), seen(ACME, "ashby:acme", h="h0"), T0)
    s1, changed1 = step(s0, *run)
    s2, changed2 = step(s1, *run)
    assert changed1 == [ACME] and changed2 == [] and s2 == s1
