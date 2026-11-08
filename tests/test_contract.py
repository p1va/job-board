from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from jobboard.contract import RAW_SCHEMA, Kind, Manifest, Scope, Status

EXAMPLES = Path(__file__).parent / "examples"
T = datetime(2026, 11, 9, 6, 0, tzinfo=timezone.utc)


def raw(rows):
    cols = {f.name: [r[f.name] for r in rows] for f in RAW_SCHEMA}
    return pa.table(cols, schema=RAW_SCHEMA)


def row(scope_id, native_id, run_id="123-1"):
    return {"schema_version": 1, "run_id": run_id, "source": "ashby", "scope_id": scope_id,
            "board": scope_id.split(":", 1)[1], "native_id": native_id, "fetched_at": T, "payload": "{}"}


def test_example_manifest_matches_its_raw_rows():
    m = Manifest.from_json((EXAMPLES / "ashby-0-of-1" / "manifest.json").read_text())
    rows = pq.read_table(EXAMPLES / "ashby-0-of-1" / "jobs.parquet")
    assert m.problems(rows) == []
    assert {s.status for s in m.scopes} == {Status.COMPLETE, Status.EMPTY, Status.FAILED}


def test_round_trip():
    m = Manifest("ashby", "0-of-1", "123-1", "sha", T, T, [Scope("ashby:acme", Kind.BOARD, Status.COMPLETE, 200, 1, 2)])
    assert Manifest.from_json(m.to_json()) == m


def test_status_rules():
    bad = [Scope("ashby:a", Kind.BOARD, Status.COMPLETE, 200, 1, 0),
           Scope("ashby:b", Kind.BOARD, Status.EMPTY, 200, 1, 3),
           Scope("ashby:c", Kind.BOARD, Status.FAILED, 503, 0, 0),
           Scope("ashby:d", Kind.BOARD, Status.FAILED, 503, 1, 4, "timeout")]
    problems = Manifest("ashby", "0", "r", "s", T, T, bad).problems()
    assert len(problems) == 4


def test_counts_must_match_raw_rows():
    m = Manifest("ashby", "0", "123-1", "s", T, T, [Scope("ashby:acme", Kind.BOARD, Status.COMPLETE, 200, 1, 2)])
    assert m.problems(raw([row("ashby:acme", "j1"), row("ashby:acme", "j2")])) == []
    assert m.problems(raw([row("ashby:acme", "j1")])) == ["ashby:acme: manifest says 2 jobs, raw has 1"]
    assert "raw rows for unlisted scope ashby:other" in m.problems(raw([row("ashby:acme", "j1"), row("ashby:acme", "j2"), row("ashby:other", "x")]))
    assert any("raw run_id" in p for p in m.problems(raw([row("ashby:acme", "j1"), row("ashby:acme", "j2", run_id="9-1")])))


def test_naive_timestamps_rejected():
    naive = datetime(2026, 11, 9, 6, 0)
    assert "timestamps must be UTC-aware" in Manifest("ashby", "0", "r", "s", naive, naive, []).problems()


def test_failed_response_cannot_pose_as_resolved():
    disguised = [Scope("ashby:a", Kind.BOARD, Status.EMPTY, 503, 0, 0),            # 503 presented as empty
                 Scope("ashby:b", Kind.BOARD, Status.COMPLETE, 429, 0, 1),         # 429 presented as complete
                 Scope("ashby:c", Kind.BOARD, Status.EMPTY, 200, 0, 0),            # no page fetched
                 Scope("ashby:d", Kind.BOARD, Status.EMPTY, 200, 1, 0, "timeout"),
                 Scope("ashby:e", Kind.BOARD, Status.COMPLETE, 200, -1, 2)]
    for s in disguised:
        assert s.problems(), s
    assert Scope("ashby:real-empty", Kind.BOARD, Status.EMPTY, 200, 1, 0).problems() == []


def test_raw_identity_and_required_values_are_checked():
    m = Manifest("ashby", "0", "123-1", "s", T, T, [Scope("ashby:acme", Kind.BOARD, Status.COMPLETE, 200, 1, 1)])
    good = row("ashby:acme", "j1")
    assert m.problems(raw([good])) == []
    for change, expected in [({"source": "greenhouse"}, "raw source"),
                             ({"schema_version": 99}, "raw schema_version"),
                             ({"native_id": None}, "native_id: 1 missing"),
                             ({"native_id": ""}, "native_id: 1 missing"),
                             ({"board": "other-co"}, "rows from another board")]:
        problems = m.problems(raw([{**good, **change}]))
        assert any(expected in p for p in problems), (change, problems)


def test_query_scope_may_span_boards_but_not_repeat_a_job():
    q = "workable:search:country=GB"
    hit = lambda board, n: {**row("x:y", n), "source": "workable", "scope_id": q, "board": board}
    m = Manifest("workable", "0", "123-1", "s", T, T, [Scope(q, Kind.QUERY, Status.COMPLETE, 200, 2, 2)])
    assert m.problems(raw([hit("u1", "a"), hit("u2", "b")])) == []
    assert any("listed twice" in p for p in m.problems(raw([hit("u1", "a"), hit("u1", "a")])))
