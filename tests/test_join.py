"""The join's file contract: shards in, versions/state/boards out, replay and retry rules."""
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from jobboard.contract import RAW_SCHEMA, Kind, Manifest, Scope, Status
from jobboard.join import DEAD_AFTER, join, load_state

FIXTURES = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 11, 9, 6, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)
JOB = json.loads((FIXTURES / "ashby.json").read_text())[0] | {
    "title": "Senior Backend Engineer", "location": "Berlin", "secondaryLocations": [], "address": None}


def shard(raw: Path, run_id: str, scopes: dict[str, tuple[str, int | None, list[dict]]], when=T0) -> None:
    """scopes: scope_id -> (status, http_status, jobs). Writes one valid Ashby shard."""
    out = raw / "ashby" / "0-of-1"
    out.mkdir(parents=True, exist_ok=True)
    rows, outcomes = [], []
    for scope_id, (status, code, jobs) in scopes.items():
        board = scope_id.split(":", 1)[1]
        rows += [dict(schema_version=1, run_id=run_id, source="ashby", scope_id=scope_id, board=board,
                      native_id=j["id"], fetched_at=when, payload=json.dumps(j)) for j in jobs]
        outcomes.append(Scope(scope_id, Kind.BOARD, Status(status), code, 1 if code == 200 else 0, len(jobs),
                              None if status in ("complete", "empty") else f"HTTP {code}"))
    pq.write_table(pa.Table.from_pylist(rows, schema=RAW_SCHEMA), out / "jobs.parquet")
    (out / "manifest.json").write_text(Manifest("ashby", "0-of-1", run_id, "s", when, when, outcomes).to_json())


def inputs(tmp: Path, *keys: str) -> Path:
    d = tmp / "companies"
    d.mkdir(exist_ok=True)
    (d / "seed.csv").write_text("provider,key,origins\n" + "".join(f"ashby,{k},test\n" for k in keys))
    return d


def run(tmp, run_id, scopes, now):
    raw = tmp / f"raw-{run_id}"
    shard(raw, run_id, scopes, now)
    return join(raw, tmp / "data", inputs(tmp, "acme", "gone-co"), run_id, now)


def test_first_run_writes_versions_state_and_boards(tmp_path):
    report = run(tmp_path, "1-1", {"ashby:acme": ("complete", 200, [JOB])}, T0)
    assert report["versions"] == 1 and report["statuses"] == {"open": 1}
    assert Path(report["versions_file"]).name == "1-1.parquet" and "2026/11/09" in report["versions_file"]
    versions = pq.read_table(report["versions_file"]).to_pylist()
    assert versions[0]["title"] == "Senior Backend Engineer" and versions[0]["countries"] == ["DE"]
    boards = {b["board_id"]: b for b in pq.read_table(tmp_path / "data" / "boards.parquet").to_pylist()}
    assert boards["ashby:acme"]["status"] == "live" and boards["ashby:gone-co"]["status"] == "unchecked"


def test_unchanged_rerun_writes_no_version(tmp_path):
    run(tmp_path, "1-1", {"ashby:acme": ("complete", 200, [JOB])}, T0)
    report = run(tmp_path, "2-1", {"ashby:acme": ("complete", 200, [JOB])}, T0 + DAY)
    assert report["versions"] == 0 and report["versions_file"] is None
    s = load_state(tmp_path / "data" / "state.parquet")
    assert next(iter(s.values())).last_confirmed_seen == T0 + DAY


def test_edit_writes_a_new_version(tmp_path):
    run(tmp_path, "1-1", {"ashby:acme": ("complete", 200, [JOB])}, T0)
    report = run(tmp_path, "2-1", {"ashby:acme": ("complete", 200, [JOB | {"title": "Staff Backend Engineer"}])}, T0 + DAY)
    assert report["versions"] == 1


def test_empty_board_closes_and_invalid_shard_is_ignored(tmp_path):
    run(tmp_path, "1-1", {"ashby:acme": ("complete", 200, [JOB])}, T0)
    raw = tmp_path / "raw-bad"
    shard(raw, "2-1", {"ashby:acme": ("empty", 200, [])}, T0 + DAY)
    manifest = json.loads((raw / "ashby/0-of-1/manifest.json").read_text())
    manifest["scopes"][0]["jobs"] = 5                                # now disagrees with the raw rows
    (raw / "ashby/0-of-1/manifest.json").write_text(json.dumps(manifest))
    report = join(raw, tmp_path / "data", inputs(tmp_path, "acme"), "2-1", T0 + DAY)
    assert report["shards_invalid"] and report["statuses"] == {"open": 1}     # nothing closed
    report = run(tmp_path, "3-1", {"ashby:acme": ("empty", 200, [])}, T0 + 2 * DAY)
    assert report["statuses"] == {"closed": 1}


def test_repeated_404_marks_a_board_dead(tmp_path):
    for day in range(DEAD_AFTER):
        run(tmp_path, f"{day}-1", {"ashby:gone-co": ("failed", 404, [])}, T0 + day * DAY)
    boards = {b["board_id"]: b for b in pq.read_table(tmp_path / "data" / "boards.parquet").to_pylist()}
    assert boards["ashby:gone-co"]["status"] == "dead" and boards["ashby:gone-co"]["consecutive_404"] == DEAD_AFTER


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_replay_after_commit_is_a_no_op_but_retry_before_commit_replaces(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    first = run(tmp_path, "1-1", {"ashby:acme": ("complete", 200, [JOB])}, T0)
    path = Path(first["versions_file"])
    path.write_bytes(b"not parquet: an interrupted staging file")      # uncommitted: a retry replaces it
    retry = run(tmp_path, "1-1", {"ashby:acme": ("complete", 200, [JOB])}, T0)
    assert retry["replay"] is False and pq.read_table(path).num_rows in (0, 1)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "run 1-1")
    before = path.read_bytes()
    replay = run(tmp_path, "1-1", {"ashby:acme": ("complete", 200, [JOB | {"title": "Changed"}])}, T0)
    assert replay["replay"] is True and path.read_bytes() == before
