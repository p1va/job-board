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


def discover(rows: list[dict], known: set[tuple[str, str]], get) -> tuple[list[dict], dict]:
    found, new, live, failed = Counter(), Counter(), Counter(), Counter()
    out = []
    for r in sorted(rows, key=lambda r: (r["ats"], -int(r.get("captures") or 0), r["board_key"])):
        ats, key = r["ats"], r["board_key"].strip()
        found[ats] += 1
        if not key or (ats, key.lower()) in known:
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
        live[ats] += 1
        known.add((ats, key.lower()))
        out.append({c: "" for c in COLUMNS} | {"provider": ats, "key": key, "origins": "athena",
                                               "bet_checked": f"jobs={len(data.get('jobs') or [])}"})
    report = dict(found=dict(found), new=dict(new), live_added=dict(live), not_added=dict(failed),
                  next_providers={a: n for a, n in new.most_common() if a not in CHECK})
    return out, report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results", type=Path)
    ap.add_argument("--inputs", type=Path, default=Path("companies"))
    ap.add_argument("--out", type=Path, default=Path("companies/athena.csv"))
    ap.add_argument("--report", type=Path, default=Path("reports/athena.json"))
    ap.add_argument("--interval", type=float, default=0.5)
    args = ap.parse_args(argv)
    rows, report = discover(candidates(args.results), known_boards(args.inputs, args.out),
                            Client(interval=args.interval).get)
    with args.out.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
