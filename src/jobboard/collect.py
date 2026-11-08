"""Collect one deterministic provider shard: python -m jobboard.collect --help."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .contract import RAW_SCHEMA, Kind, Manifest, Status
from .http import Client
from .retrievers import Selection, retrieve, workable_selections
from .personio import board_slug


def selections(source: str, inputs: Path, countries=None, remote="include") -> list[Selection]:
    if source == "workable":
        return workable_selections(countries, remote)
    keys = set()
    for path in sorted(inputs.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                if row["provider"] == source and row["key"].strip():
                    keys.add(board_slug(row["key"]) if source == "personio" else row["key"].strip())
    return [Selection(f"{source}:{key}", Kind.BOARD, key) for key in sorted(keys)]


def shard_selections(source: str, selected: list[Selection], index: int, count: int) -> list[Selection]:
    if source == "workable" and count > 1 and any(s.scope_id == "workable:search:remote" for s in selected):
        if index == count - 1:
            return [s for s in selected if s.scope_id == "workable:search:remote"]
        countries = [s for s in selected if s.scope_id != "workable:search:remote"]
        return countries[index::count - 1]
    return selected[index::count]


def plan(source: str, inputs: Path, shard_index: int = 0, shard_count: int = 1, limit: int = 0,
         countries: str | None = None, remote: str = "include") -> list[Selection]:
    """The exact scopes one shard collects for these options; the join recomputes it to check inputs."""
    selected = selections(source, inputs, countries.upper().split(",") if countries else None, remote)
    selected = shard_selections(source, selected, shard_index, shard_count)
    return selected[:limit] if limit else selected


def selection_sha(selected: list[Selection]) -> str:
    return hashlib.sha256(json.dumps([asdict(s) for s in selected], sort_keys=True).encode()).hexdigest()


def collect(source: str, selected: list[Selection], get, run_id: str, shard: str,
            out: Path, *, workers=2, max_pages=5000, progress=None, deadline=None) -> tuple[Manifest, dict]:
    started = datetime.now(timezone.utc)
    tick = time.monotonic()
    deadline = tick + 6000 if deadline is None else deadline
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").unlink(missing_ok=True)
    input_sha = selection_sha(selected)
    timings = {}

    def scan(selection):
        tick = time.monotonic()
        result = retrieve(source, selection, get, run_id, max_pages=max_pages,
                          progress=progress, deadline=deadline)
        timings[selection.scope_id] = round(time.monotonic() - tick, 2)
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(scan, selected))
    table = pa.Table.from_pylist([r for result in results for r in result.rows], schema=RAW_SCHEMA)
    manifest = Manifest(source, shard, run_id, input_sha, started, datetime.now(timezone.utc),
                        [r.scope for r in results])
    problems = manifest.problems(table)
    if problems:
        raise ValueError("invalid collection: " + "; ".join(problems))
    # The manifest is the completion marker and is published only after the raw rows.
    pq.write_table(table, out / "jobs.parquet.tmp", compression="zstd")
    os.replace(out / "jobs.parquet.tmp", out / "jobs.parquet")
    (out / "manifest.json.tmp").write_text(manifest.to_json(), encoding="utf-8")
    os.replace(out / "manifest.json.tmp", out / "manifest.json")
    report = dict(source=source, shard=shard, run_id=run_id, scopes=len(results), jobs=table.num_rows,
                  statuses=dict(Counter(r.scope.status for r in results)),
                  seconds=round(time.monotonic() - tick, 2), parquet_bytes=(out / "jobs.parquet").stat().st_size,
                  scope_seconds=timings,
                  scope_totals={r.scope.scope_id: r.totals for r in results if r.totals},
                  outcomes=[dict(status=status, http_status=code, scopes=n) for (status, code), n in
                            sorted(Counter((r.scope.status, r.scope.http_status) for r in results).items(), key=str)])
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return manifest, report


def write_summary(manifest: Manifest, report: dict) -> str:
    text = (f"### {manifest.source} {manifest.shard}\n\n"
            f"{report['jobs']:,} jobs · {report['scopes']} scopes · {report['seconds']} seconds\n\n"
            "| Outcome | HTTP status | Scopes |\n|---|---|---:|\n")
    for row in report["outcomes"]:
        text += f"| {row['status']} | {row['http_status'] or '—'} | {row['scopes']} |\n"
    if any(s.status in (Status.FAILED, Status.PARTIAL) for s in manifest.scopes):
        text += "\nFailed/partial scopes remain unresolved; their absence cannot close jobs.\n"
        print(f"::warning::{manifest.source} {manifest.shard} has unresolved scopes; inspect the artifact report.")
    if manifest.source == "personio":
        totals = report["scope_totals"].values()
        filled = sum(t.get("descriptions_filled", 0) for t in totals)
        missing = sum(t.get("descriptions_missing", 0) for t in report["scope_totals"].values())
        text += f"\nEnglish fallback filled {filled} descriptions; {missing} jobs still have no description.\n"
        if missing:
            print(f"::warning::Personio {manifest.shard}: {missing} jobs have no description after the English fallback.")
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(text + "\n")
    return text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, choices=("ashby", "greenhouse", "workable", "personio"))
    parser.add_argument("--inputs", type=Path, default=Path("companies"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--interval", type=float, default=None, help="seconds between requests per shard")
    parser.add_argument("--max-pages", type=int, default=5000)
    parser.add_argument("--deadline", type=int, default=6000, help="seconds to collect before flushing artifacts")
    parser.add_argument("--limit-scopes", type=int, default=0, help="0 for all; positive limit for a smoke run")
    parser.add_argument("--countries", help="Workable ISO-2 codes, comma-separated; default all Europe")
    parser.add_argument("--remote", choices=("include", "exclude", "only"), default="include")
    args = parser.parse_args(argv)
    if (not 0 <= args.shard_index < args.shard_count or args.workers < 1 or args.max_pages < 1
            or args.limit_scopes < 0 or args.deadline < 1 or (args.interval is not None and args.interval < 0)):
        parser.error("invalid shard, worker, page, scope limit or pacing value")
    try:
        selected = plan(args.source, args.inputs, args.shard_index, args.shard_count, args.limit_scopes,
                        args.countries, args.remote)
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))
    if not selected and not plan(args.source, args.inputs, 0, 1, 0, args.countries, args.remote):
        parser.error("no scopes selected; check companies/*.csv and the requested source")
    # a shard can be legitimately empty after splitting (e.g. --remote only): it writes an empty artifact
    deadline = time.monotonic() + args.deadline
    interval = args.interval if args.interval is not None else {"workable": 2, "personio": 1}.get(args.source, 0.5)
    client = Client(interval=interval, deadline=deadline)
    manifest, report = collect(args.source, selected, client.get, args.run_id,
                        f"{args.shard_index}-of-{args.shard_count}", args.out,
                        workers=args.workers, max_pages=args.max_pages, deadline=deadline,
                        progress=lambda text: print(text, file=sys.stderr, flush=True))
    report["http"] = dict(client.metrics)
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("scope_seconds", "scope_totals")}), flush=True)
    write_summary(manifest, report)
    # A valid artifact is a successful collection, even if some scopes are unresolved.
    return 0


if __name__ == "__main__":
    sys.exit(main())
