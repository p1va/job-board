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


def selections(source: str, inputs: Path, countries=None, remote="include") -> list[Selection]:
    if source == "workable":
        return workable_selections(countries, remote)
    keys = set()
    for path in sorted(inputs.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                if row["provider"] == source and row["key"].strip():
                    keys.add(row["key"].strip())
    return [Selection(f"{source}:{key}", Kind.BOARD, key) for key in sorted(keys)]


def collect(source: str, selected: list[Selection], get, run_id: str, shard: str,
            out: Path, *, workers=2, max_pages=5000, progress=None) -> tuple[Manifest, dict]:
    started = datetime.now(timezone.utc)
    tick = time.monotonic()
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").unlink(missing_ok=True)
    input_sha = hashlib.sha256(json.dumps([asdict(s) for s in selected], sort_keys=True).encode()).hexdigest()
    timings = {}

    def scan(selection):
        tick = time.monotonic()
        result = retrieve(source, selection, get, run_id, max_pages=max_pages, progress=progress)
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
                  scope_seconds=timings)
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return manifest, report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, choices=("ashby", "greenhouse", "workable"))
    parser.add_argument("--inputs", type=Path, default=Path("companies"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--interval", type=float, default=None, help="seconds between requests per shard")
    parser.add_argument("--max-pages", type=int, default=5000)
    parser.add_argument("--limit-scopes", type=int, default=0, help="0 for all; positive limit for a smoke run")
    parser.add_argument("--countries", help="Workable ISO-2 codes, comma-separated; default all Europe")
    parser.add_argument("--remote", choices=("include", "exclude", "only"), default="include")
    args = parser.parse_args(argv)
    if (not 0 <= args.shard_index < args.shard_count or args.workers < 1 or args.max_pages < 1
            or args.limit_scopes < 0 or (args.interval is not None and args.interval < 0)):
        parser.error("invalid shard, worker, page, scope limit or pacing value")
    try:
        selected = selections(args.source, args.inputs,
                              args.countries.upper().split(",") if args.countries else None, args.remote)
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))
    if not selected:
        parser.error("no scopes selected; check companies/*.csv and the requested source")
    selected = selected[args.shard_index::args.shard_count]
    if args.limit_scopes:
        selected = selected[:args.limit_scopes]
    client = Client(interval=args.interval if args.interval is not None else (1 if args.source == "workable" else 0.5))
    _, report = collect(args.source, selected, client.get, args.run_id,
                        f"{args.shard_index}-of-{args.shard_count}", args.out,
                        workers=args.workers, max_pages=args.max_pages,
                        progress=lambda text: print(text, file=sys.stderr, flush=True))
    report["http"] = dict(client.metrics)
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "scope_seconds"}), flush=True)
    # Artifacts contain useful positives and per-scope failures even when a step fails.
    return int(any(s in report["statuses"] for s in (Status.FAILED, Status.PARTIAL)))


if __name__ == "__main__":
    sys.exit(main())
