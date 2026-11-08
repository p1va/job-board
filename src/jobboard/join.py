"""Combine retriever shards with the previous state: versions, state and boards in data/.

    uv run python -m jobboard.join --raw raw --data data --inputs companies --run-id <id>

Per docs/contract.md: invalid shards count as missing; the lifecycle step decides statuses; a
version file is written only for new or changed tracked jobs, and a run already committed to git
is a no-op. Writes nothing else; rendering and committing happen in later steps.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .classify import role_family
from .contract import RESOLVED, Manifest, Scope, Status
from .lifecycle import JobState, Key, Sighting, step
from .normalize import normalize
from .tables import BOARD, STATE, VERSION

DEAD_AFTER = 3           # consecutive HTTP 404s before a board is marked dead


@dataclass
class Shards:
    outcomes: dict[str, Scope] = field(default_factory=dict)
    tables: list[pa.Table] = field(default_factory=list)
    invalid: dict[str, list[str]] = field(default_factory=dict)


def read_shards(raw: Path) -> Shards:
    """Every raw/<source>/<shard>/ with a valid manifest; the rest are reported and ignored."""
    out = Shards()
    for manifest_path in sorted(raw.glob("*/*/manifest.json")):
        where = str(manifest_path.parent.relative_to(raw))
        try:
            manifest = Manifest.from_json(manifest_path.read_text(encoding="utf-8"))
            table = pq.read_table(manifest_path.parent / "jobs.parquet")
            problems = manifest.problems(table)
        except Exception as exc:              # unreadable shard: missing, never resolving
            problems = [f"{type(exc).__name__}: {exc}"]
        if problems:
            out.invalid[where] = problems
            continue
        for scope in manifest.scopes:
            prior = out.outcomes.get(scope.scope_id)
            # the same scope reported twice: keep the less resolved outcome, so nothing closes by accident
            if prior is None or (prior.status in RESOLVED and scope.status not in RESOLVED):
                out.outcomes[scope.scope_id] = scope
        out.tables.append(table)
    return out


def sightings(shards: Shards, tracked: set[Key], run_id: str, now: datetime) -> tuple[dict[Key, Sighting], dict[Key, dict]]:
    """Merge raw rows by job key across scopes; keep normalised rows only for kept or tracked jobs."""
    scopes: dict[Key, set[str]] = {}
    latest: dict[Key, dict] = {}
    for table in shards.tables:
        for r in table.to_pylist():
            key = (r["source"], r["board"], r["native_id"])
            scopes.setdefault(key, set()).add(r["scope_id"])
            if key not in latest or r["fetched_at"] > latest[key]["fetched_at"]:
                latest[key] = r
    seen: dict[Key, Sighting] = {}
    rows: dict[Key, dict] = {}
    for key, r in latest.items():
        if key not in tracked and role_family(json.loads(r["payload"]).get("title") or "") is None:
            # never kept without a role family: skip the full normalisation (most jobs)
            seen[key] = Sighting(key, f"{key[0]}:{key[1]}", "", frozenset(scopes[key]), False)
            continue
        row = normalize(r["source"], r["board"], r["native_id"], r["payload"], run_id, now)
        if row["kept"] or key in tracked:
            rows[key] = row
        seen[key] = Sighting(key, row["company_id"], row["payload_hash"], frozenset(scopes[key]), row["kept"])
    return seen, rows


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    except OSError:
        return None


def last_committed(path: Path) -> pa.Table | None:
    """The committed copy of a data file. Retries start from the last committed generation, never from
    files an interrupted attempt left behind. Outside git, the working file is the committed one."""
    if not path.parent.exists():
        return None
    inside = _git(path.parent, "rev-parse", "--is-inside-work-tree")
    if inside is None or inside.returncode != 0:
        return pq.read_table(path) if path.exists() else None
    shown = _git(path.parent, "show", f"HEAD:./{path.name}")
    return pq.read_table(pa.BufferReader(shown.stdout)) if shown is not None and shown.returncode == 0 else None


def load_state(path: Path) -> dict[Key, JobState]:
    table = last_committed(path)
    if table is None:
        return {}
    state = {}
    for r in table.to_pylist():
        key = (r["source"], r["board"], r["native_id"])
        state[key] = JobState(key, r["company_id"], r["first_seen"], r["last_confirmed_seen"], r["last_changed"],
                              r["current_hash"], r["status"], tuple(r["scopes"]), r["closed_at"])
    return state


def state_table(state: dict[Key, JobState]) -> pa.Table:
    rows = [dict(source=s.key[0], board=s.key[1], native_id=s.key[2], company_id=s.company_id,
                 first_seen=s.first_seen, last_confirmed_seen=s.last_confirmed_seen, last_changed=s.last_changed,
                 current_hash=s.current_hash, status=s.status, scopes=list(s.scopes), closed_at=s.closed_at)
            for s in (state[k] for k in sorted(state))]
    return pa.Table.from_pylist(rows, schema=STATE)


def boards_table(inputs: Path, previous: Path, outcomes: dict[str, Scope], now: datetime) -> pa.Table:
    """One row per input board, with this run's outcome folded into its check history."""
    table = last_committed(previous)
    prior = {r["board_id"]: r for r in table.to_pylist()} if table is not None else {}
    boards: dict[str, dict] = {}
    for path in sorted(inputs.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                board_id = f"{row['provider']}:{row['key']}"
                b = boards.setdefault(board_id, prior.get(board_id) or dict(
                    board_id=board_id, provider=row["provider"], key=row["key"], company_id=board_id,
                    api_region=None, origins=[], status="unchecked", consecutive_404=0,
                    last_checked=None, last_complete=None))
                b["origins"] = sorted(set(b["origins"]) | set(filter(None, (row.get("origins") or "").split("|"))))
    for board_id, b in boards.items():
        scope = outcomes.get(board_id)
        if scope is None:
            continue
        b["last_checked"] = now
        if scope.status in RESOLVED:
            b.update(status="live" if scope.status == Status.COMPLETE else "empty", consecutive_404=0, last_complete=now)
        elif scope.http_status == 404:
            b["consecutive_404"] += 1
            if b["consecutive_404"] >= DEAD_AFTER:
                b["status"] = "dead"
    return pa.Table.from_pylist([boards[k] for k in sorted(boards)], schema=BOARD)


def committed(path: Path) -> bool:
    """True if this file is in the last commit: a committed generation must never be rewritten.
    Outside git, an existing file counts as committed."""
    inside = _git(path.parent, "rev-parse", "--is-inside-work-tree")
    if inside is None or inside.returncode != 0:
        return path.exists()
    shown = _git(path.parent, "cat-file", "-e", f"HEAD:./{path.name}")
    return shown is not None and shown.returncode == 0


def write_atomic(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)


def join(raw: Path, data: Path, inputs: Path, run_id: str, now: datetime) -> dict:
    versions_path = data / "versions" / f"{now:%Y/%m/%d}" / f"{run_id}.parquet"
    replayed = [p for p in data.glob(f"versions/*/*/*/{run_id}.parquet") if committed(p)]
    if replayed:
        return dict(run_id=run_id, replay=True, versions_file=str(replayed[0]), note="run already committed: no-op")
    shards = read_shards(raw)
    previous = load_state(data / "state.parquet")
    seen, rows = sightings(shards, set(previous), run_id, now)
    state, changed = step(previous, shards.outcomes, seen, now)
    changed = [k for k in changed if k in state]
    names = [f.name for f in VERSION]
    versions = pa.Table.from_pylist([{n: rows[k].get(n) for n in names} for k in changed], schema=VERSION)
    if versions.num_rows:
        write_atomic(versions, versions_path)
    write_atomic(state_table(state), data / "state.parquet")
    write_atomic(boards_table(inputs, data / "boards.parquet", shards.outcomes, now), data / "boards.parquet")
    return dict(run_id=run_id, replay=False, now=now.isoformat(), shards_valid=len(shards.tables),
                shards_invalid=shards.invalid, scopes=dict(Counter(s.status.value for s in shards.outcomes.values())),
                jobs_seen=len(seen), tracked=len(state), versions=versions.num_rows,
                versions_file=str(versions_path) if versions.num_rows else None,
                statuses=dict(Counter(s.status for s in state.values())))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--inputs", type=Path, default=Path("companies"))
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--now", help="UTC ISO time of the run (default: now)")
    args = ap.parse_args(argv)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        ap.error("--now must include a UTC offset")
    report = join(args.raw, args.data, args.inputs, args.run_id, now.astimezone(timezone.utc))
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
