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
    assert any("other runs" in p for p in m.problems(raw([row("ashby:acme", "j1"), row("ashby:acme", "j2", run_id="9-1")])))


def test_naive_timestamps_rejected():
    naive = datetime(2026, 11, 9, 6, 0)
    assert "timestamps must be UTC-aware" in Manifest("ashby", "0", "r", "s", naive, naive, []).problems()
