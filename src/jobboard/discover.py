"""Import job boards found by the Athena URL-index query (.notes/athena/ats-boards.sql).

    uv run python -m jobboard.discover RESULTS.csv --out companies/athena.csv --report reports/athena.json

Boards already in companies/*.csv are skipped. New Ashby and Greenhouse boards are checked against the
provider's public API (paced) and kept only when they answer HTTP 200, so dead slugs from old crawls
don't enter the scan. Boards on providers we don't read yet are only counted, to choose the next one.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote

from .http import Client
from .registry import COLUMNS

CHECK = {
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{key}",
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{key}/jobs",
}


def known_boards(inputs: Path, skip: Path | None = None) -> set[tuple[str, str]]:
    known = set()
    for path in sorted(inputs.glob("*.csv")):
        if skip is not None and path.resolve() == skip.resolve():
            continue
        with path.open(newline="", encoding="utf-8") as stream:
            known |= {(r["provider"], r["key"].lower()) for r in csv.DictReader(stream)}
    return known


def candidates(results: Path) -> list[dict]:
    with results.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    missing = {"ats", "board_key", "captures"} - set(rows[0] if rows else {})
    if missing:
        raise ValueError(f"{results}: missing columns {sorted(missing)}")
    return rows


def valid_board(ats: str, data) -> bool:
    """A real board response, not an error object: a jobs list of objects with ids (and Greenhouse's total)."""
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list) or not all(isinstance(j, dict) and j.get("id") for j in jobs):
        return False
    if ats == "greenhouse":
        total = (data.get("meta") or {}).get("total")
        return isinstance(total, int) and total == len(jobs)
    return True


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def discover(rows: list[dict], known: set[tuple[str, str]], get, skip: set[tuple[str, str]] = frozenset()) -> tuple[list[dict], dict]:
    found, new, live, failed = Counter(), Counter(), Counter(), Counter()
    out, jobs = [], {}
    for r in sorted(rows, key=lambda r: (r["ats"], -int(r.get("captures") or 0), r["board_key"])):
        ats, key = r["ats"], r["board_key"].strip()
        found[ats] += 1
        if not key or (ats, key.lower()) in known:
            continue
        if (ats, key.lower()) in skip:
            failed[f"{ats} skipped (reposts another board)"] += 1
            continue
        new[ats] += 1
        if ats not in CHECK:
            continue
        try:
            data = get(CHECK[ats].format(key=quote(key, safe="")))
        except HTTPError as exc:
            failed[f"{ats} HTTP {exc.code}"] += 1
            continue
        except Exception as exc:                          # network trouble: not proof the board is dead
            failed[f"{ats} {type(exc).__name__}"] += 1
            continue
        if not valid_board(ats, data):
            failed[f"{ats} invalid response"] += 1
            continue
        live[ats] += 1
        known.add((ats, key.lower()))
        out.append({c: "" for c in COLUMNS} | {"provider": ats, "key": key, "origins": "athena"})
        jobs[f"{ats}:{key}"] = len(data.get("jobs") or [])
    report = dict(found=dict(found), new=dict(new), live_added=dict(live), not_added=dict(failed),
                  next_providers={a: n for a, n in new.most_common() if a not in CHECK}, jobs_on_added=jobs)
    return out, report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results", type=Path)
    ap.add_argument("--inputs", type=Path, default=Path("companies"))
    ap.add_argument("--out", type=Path, default=Path("companies/athena.csv"))
    ap.add_argument("--report", type=Path, default=Path("reports/athena.json"))
    ap.add_argument("--skip", type=Path, default=Path("discovery/skip.csv"), help="provider,key,reason to leave out")
    ap.add_argument("--interval", type=float, default=0.5)
    args = ap.parse_args(argv)
    skip = {(r["provider"], r["key"].lower()) for r in read_csv(args.skip)}
    previous = read_csv(args.out)                    # earlier discoveries stay, even if a check now fails
    known = known_boards(args.inputs, args.out) | {(r["provider"], r["key"].lower()) for r in previous}
    rows, report = discover(candidates(args.results), known, Client(interval=args.interval).get, skip)
    merged = {(r["provider"], r["key"].lower()): r for r in previous + rows}
    report["kept_from_before"] = len(previous)
    with args.out.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(merged[k] for k in sorted(merged))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
