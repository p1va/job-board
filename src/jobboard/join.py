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


def run_family(run_id: str) -> tuple[str, int | None]:
    """'19034567890-2' -> ('19034567890', 2): attempts of one workflow run share the family."""
    base, sep, attempt = run_id.rpartition("-")
    return (base, int(attempt)) if sep and attempt.isdigit() else (run_id, None)


def expected_input(manifest: Manifest, inputs: Path, options: dict | None = None) -> str:
    """The input_sha this shard should have if collected from companies/ with the run's options
    (scope limit, Workable countries and remote), exactly as the collector plans it."""
    from .collect import plan, selection_sha
    index, _, count = manifest.shard.partition("-of-")
    o = options or {}
    return selection_sha(plan(manifest.source, inputs, int(index), int(count), o.get("limit") or 0,
                              o.get("countries") or None, o.get("remote") or "include"))


def read_shards(raw: Path, run_id: str | None = None, inputs: Path | None = None, options: dict | None = None) -> Shards:
    """Every shard folder under raw/ with a valid manifest from this run (or an earlier attempt of it),
    collected from the current companies/ inputs. The rest are reported and ignored: an old or
    unrelated artifact must never become authoritative absence."""
    out = Shards()
    family, attempt = run_family(run_id) if run_id else (None, None)
    for manifest_path in sorted(raw.glob("**/manifest.json")):     # any layout, e.g. one folder per artifact
        where = str(manifest_path.parent.relative_to(raw))
        try:
            manifest = Manifest.from_json(manifest_path.read_text(encoding="utf-8"))
            table = pq.read_table(manifest_path.parent / "jobs.parquet")
            problems = manifest.problems(table)
            if run_id is not None:
                theirs, their_attempt = run_family(manifest.run_id)
                if theirs != family or (attempt is not None and (their_attempt or 0) > attempt):
                    problems.append(f"from run {manifest.run_id}, not {run_id} or an earlier attempt of it")
            if inputs is not None and manifest.input_sha != expected_input(manifest, inputs, options):
                problems.append("collected from other inputs than companies/ with this run's options")
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


def sightings(shards: Shards, tracked: set[Key], run_id: str, now: datetime,
              previous: dict[Key, JobState] | None = None, errors: list | None = None) -> tuple[dict[Key, Sighting], dict[Key, dict]]:
    """Merge raw rows by job key across scopes; keep normalised rows only for kept or tracked jobs."""
    # Small columns only: payloads (full descriptions) are read one at a time when needed, so a day of
    # ~240k jobs doesn't become gigabytes of Python strings at once.
    scopes: dict[Key, set[str]] = {}
    latest: dict[Key, tuple] = {}                     # key -> (fetched_at, table index, row index)
    for t, table in enumerate(shards.tables):
        cols = {c: table.column(c).to_pylist() for c in ("source", "board", "native_id", "scope_id", "fetched_at")}
        for i in range(table.num_rows):
            key = (cols["source"][i], cols["board"][i], cols["native_id"][i])
            scopes.setdefault(key, set()).add(cols["scope_id"][i])
            if key not in latest or cols["fetched_at"][i] > latest[key][0]:
                latest[key] = (cols["fetched_at"][i], t, i)
    payloads = [table.column("payload") for table in shards.tables]
    seen: dict[Key, Sighting] = {}
    rows: dict[Key, dict] = {}
    for key, (fetched_at, t, i) in latest.items():
        r = dict(source=key[0], board=key[1], native_id=key[2], fetched_at=fetched_at, payload=payloads[t][i].as_py())
        if key not in tracked and role_family(json.loads(r["payload"]).get("title") or "") is None:
            # never kept without a role family: skip the full normalisation (most jobs)
            seen[key] = Sighting(key, f"{key[0]}:{key[1]}", "", frozenset(scopes[key]), False, r["fetched_at"])
            continue
        try:
            row = normalize(r["source"], r["board"], r["native_id"], r["payload"], run_id, r["fetched_at"])
        except Exception as exc:
            # one job we can't read must not stop the run: a tracked job counts as seen, unchanged
            if errors is not None:
                errors.append(f"{':'.join(key)}: {type(exc).__name__}: {exc}"[:200])
            prev = (previous or {}).get(key)
            if prev is not None:
                seen[key] = Sighting(key, prev.company_id, prev.current_hash, frozenset(scopes[key]),
                                     prev.status != "filtered_out", r["fetched_at"])
            continue
        if row["kept"] or key in tracked:
            rows[key] = row
        seen[key] = Sighting(key, row["company_id"], row["payload_hash"], frozenset(scopes[key]), row["kept"],
                             r["fetched_at"])
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


def last_generation(data: Path) -> dict | None:
    """The committed data/generation.json: which run produced the current data."""
    path = data / "generation.json"
    if not path.parent.exists():
        return None
    inside = _git(path.parent, "rev-parse", "--is-inside-work-tree")
    if inside is None or inside.returncode != 0:
        return json.loads(path.read_text()) if path.exists() else None
    shown = _git(path.parent, "show", f"HEAD:./{path.name}")
    return json.loads(shown.stdout) if shown is not None and shown.returncode == 0 else None


def older(run_id: str, than: str) -> bool:
    """A run of an earlier workflow run (GitHub run ids grow); attempts of one run aren't older."""
    mine, theirs = run_family(run_id)[0], run_family(than)[0]
    if mine.isdigit() and theirs.isdigit():
        return int(mine) < int(theirs)
    return mine < theirs if mine.isdigit() == theirs.isdigit() else False


def join(raw: Path, data: Path, inputs: Path, run_id: str, now: datetime, verify_inputs: bool = True,
         options: dict | None = None) -> dict:
    versions_path = data / "versions" / f"{now:%Y/%m/%d}" / f"{run_id}.parquet"
    replayed = [p for p in data.glob(f"versions/*/*/*/{run_id}.parquet") if committed(p)]
    if replayed:
        return dict(run_id=run_id, replay=True, versions_file=str(replayed[0]), note="run already committed: no-op")
    current = last_generation(data)
    if current and older(run_id, current["run_id"]):
        # an old run's saved collection must never overwrite or close what a newer run observed
        return dict(run_id=run_id, replay=False, superseded_by=current["run_id"],
                    note="a newer run's data is already committed: no-op")
    # Version files an interrupted attempt left uncommitted are not history: remove them, so they can't
    # be rendered or committed as part of this generation.
    leftovers = [p for p in data.glob("versions/*/*/*/*.parquet") if not committed(p)]
    for p in leftovers:
        p.unlink()
    shards = read_shards(raw, run_id, inputs if verify_inputs else None, options)
    previous = load_state(data / "state.parquet")
    errors: list[str] = []
    seen, rows = sightings(shards, set(previous), run_id, now, previous, errors)
    state, changed = step(previous, shards.outcomes, seen, now)
    changed = [k for k in changed if k in state]
    names = [f.name for f in VERSION]
    versions = pa.Table.from_pylist([{n: rows[k].get(n) for n in names} for k in changed], schema=VERSION)
    write_atomic(versions, versions_path)      # always, even empty: the run's immutable replay marker
    write_atomic(state_table(state), data / "state.parquet")
    write_atomic(boards_table(inputs, data / "boards.parquet", shards.outcomes, now), data / "boards.parquet")
    (data / "generation.json").write_text(json.dumps(dict(run_id=run_id, joined_at=now.isoformat()), indent=1) + "\n")
    return dict(run_id=run_id, replay=False, now=now.isoformat(), shards_valid=len(shards.tables),
                shards_invalid=shards.invalid, scopes=dict(Counter(s.status.value for s in shards.outcomes.values())),
                jobs_seen=len(seen), tracked=len(state), versions=versions.num_rows,
                versions_file=str(versions_path), removed_leftovers=[str(p) for p in leftovers],
                normalize_errors=len(errors), normalize_error_examples=errors[:10],
                statuses=dict(Counter(s.status for s in state.values())))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--inputs", type=Path, default=Path("companies"))
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--now", help="UTC ISO time of the run (default: now)")
    ap.add_argument("--limit-scopes", type=int, default=0, help="the collection's scope limit (0 = all)")
    ap.add_argument("--countries", default="", help="the collection's Workable countries (blank = all Europe)")
    ap.add_argument("--remote", default="include", choices=("include", "exclude", "only"))
    args = ap.parse_args(argv)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        ap.error("--now must include a UTC offset")
    options = dict(limit=args.limit_scopes, countries=args.countries or None, remote=args.remote)
    report = join(args.raw, args.data, args.inputs, args.run_id, now.astimezone(timezone.utc), options=options)
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
