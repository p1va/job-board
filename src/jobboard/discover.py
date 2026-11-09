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
from .registry import COLUMNS, lookup_key
from .personio import board_slug, parse_feed
from .lever import candidate_keys, parse_postings, postings_url, validate_postings

CHECK = {
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{key}",
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{key}/jobs",
    "personio": "https://{key}.jobs.personio.de/xml",
    "lever": "https://api{region}.lever.co/v0/postings/{key}?mode=json",
}


def known_boards(inputs: Path, skip: Path | None = None) -> set[tuple[str, str]]:
    known = set()
    for path in sorted(inputs.glob("*.csv")):
        if skip is not None and path.resolve() == skip.resolve():
            continue
        with path.open(newline="", encoding="utf-8") as stream:
            known |= {(r["provider"], lookup_key(r["provider"], r["key"])) for r in csv.DictReader(stream)}
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
    if ats == "lever":
        try:
            validate_postings(jobs)
        except ValueError:
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


def discover(rows: list[dict], known: set[tuple[str, str]], get, skip: set[tuple[str, str]] = frozenset(),
             providers: set[str] | None = None) -> tuple[list[dict], dict]:
    found, new, live, failed = Counter(), Counter(), Counter(), Counter()
    out, jobs, invalid, checks = [], {}, {}, {}
    attempted = set()
    for r in sorted(rows, key=lambda r: (r["ats"], -int(r.get("captures") or 0), r["board_key"])):
        ats, key = r["ats"], r["board_key"].strip()
        found[ats] += 1
        try:
            keys = candidate_keys(key) if ats == "lever" and (providers is None or ats in providers) else [key]
        except ValueError:
            failed[f"{ats} invalid key"] += 1
            continue
        for key in keys:
            identity = ats, key if ats == "lever" else key.lower()
            if not key or identity in known or identity in attempted:
                continue
            attempted.add(identity)
            if identity in skip:
                failed[f"{ats} excluded by skip list"] += 1
                continue
            new[ats] += 1
            if ats not in CHECK or (providers is not None and ats not in providers):
                continue
            board_id = f"{ats}:{key}"
            try:
                if ats == "personio":
                    key = board_slug(key)
                    data = get(CHECK[ats].format(key=key), parse=parse_feed, accept="application/xml")
                elif ats == "lever":
                    data = get(postings_url(key), parse=parse_postings)
                else:
                    data = get(CHECK[ats].format(key=quote(key, safe="")))
            except HTTPError as exc:
                failed[f"{ats} HTTP {exc.code}"] += 1
                checks[board_id] = dict(http_status=exc.code, status="unresolved")
                continue
            except Exception as exc:                       # network trouble: not proof the board is dead
                failed[f"{ats} {type(exc).__name__}"] += 1
                checks[board_id] = dict(status="unresolved", error=type(exc).__name__)
                continue
            if not valid_board(ats, data):
                failed[f"{ats} invalid response"] += 1
                checks[board_id] = dict(http_status=200, status="invalid")
                continue
            live[ats] += 1
            known.add((ats, lookup_key(ats, key)))
            out.append({c: "" for c in COLUMNS} | {"provider": ats, "key": key,
                "name": r.get("name") or "", "website": r.get("website") or "",
                "origins": r.get("origins") or "athena"})
            jobs[f"{ats}:{key}"] = len(data["jobs"])
            checks[board_id] = dict(http_status=200, status="live", jobs=len(data["jobs"]))
            if ats == "personio" and data.get("invalid_positions"):
                invalid[f"{ats}:{key}"] = len(data["invalid_positions"])
    report = dict(found=dict(found), new=dict(new), live_added=dict(live), not_added=dict(failed),
                  next_providers={a: n for a, n in new.most_common() if a not in CHECK}, jobs_on_added=jobs,
                  checks=checks)
    if invalid:
        report["invalid_positions_on_added"] = invalid
    return out, report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results", type=Path)
    ap.add_argument("--inputs", type=Path, default=Path("companies"))
    ap.add_argument("--out", type=Path, default=Path("companies/athena.csv"))
    ap.add_argument("--report", type=Path, default=Path("reports/athena.json"))
    ap.add_argument("--skip", type=Path, default=Path("discovery/skip.csv"), help="provider,key,reason to leave out")
    ap.add_argument("--interval", type=float, help="request spacing; default 1s with Personio/Lever, 0.5s otherwise")
    ap.add_argument("--providers", nargs="+", choices=sorted(CHECK), help="check only these providers")
    args = ap.parse_args(argv)
    skip, invalid_skips = set(), []
    for row in read_csv(args.skip):
        try:
            keys = candidate_keys(row["key"]) if row["provider"] == "lever" else [row["key"].lower()]
            skip.update((row["provider"], key) for key in keys)
        except ValueError:
            invalid_skips.append(f"{row['provider']}:{row['key']}")
            print(f"Invalid skip entry: {invalid_skips[-1]}", file=sys.stderr)
    previous = read_csv(args.out)                    # earlier discoveries stay, even if a check now fails
    known = known_boards(args.inputs, args.out) | {(r["provider"], lookup_key(r["provider"], r["key"])) for r in previous}
    providers = set(args.providers) if args.providers else None
    interval = args.interval if args.interval is not None else (1 if providers is None or providers & {"personio", "lever"} else 0.5)
    rows, report = discover(candidates(args.results), known, Client(interval=interval).get, skip, providers)
    merged = {(r["provider"], lookup_key(r["provider"], r["key"])): r for r in previous + rows}
    report["kept_from_before"] = len(previous)
    if invalid_skips:
        report["invalid_skip_entries"] = invalid_skips
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
