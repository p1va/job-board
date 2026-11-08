"""Company and board inputs: import Bet's lists into companies/bet.csv.

    uv run python -m jobboard.registry import-bet COMPANIES_TXT BOARDS_JSON WORKABLE_JSON --out companies/bet.csv

One row per board (provider + key), with every origin it came from. Names and websites are only
known for Workable companies; the others get them when their boards are fetched.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

PROVIDERS = ("ashby", "greenhouse", "workable")
GREENHOUSE_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io",
                    "boards.eu.greenhouse.io"}
COLUMNS = ["provider", "key", "name", "website", "origins", "bet_de", "bet_checked"]


def parse_board(line: str) -> tuple[str, str] | None:
    """A companies.txt entry -> (provider, key); bare names are Ashby slugs."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "://" in line or line.startswith(("jobs.ashbyhq.com/", "boards.greenhouse.io/", "job-boards.")):
        url = urlparse(line if "://" in line else "https://" + line)
        slug = url.path.strip("/").split("/")[0]
        if url.hostname == "jobs.ashbyhq.com" and slug:
            return "ashby", slug
        if url.hostname in GREENHOUSE_HOSTS and slug:
            return "greenhouse", slug
        return None
    provider, sep, key = line.partition(":")
    if sep and provider in PROVIDERS and key:
        return provider, key
    return ("ashby", line) if not sep else None


def split_key(board: str) -> tuple[str, str]:
    provider, sep, key = board.partition(":")
    return (provider, key) if sep and provider in PROVIDERS else ("ashby", board)


def import_bet(companies_txt: str, boards_json: str, workable_json: str) -> list[dict]:
    rows: dict[tuple[str, str], dict] = {}

    def add(provider: str, key: str, origin: str, **fields) -> None:
        row = rows.setdefault((provider, key), {c: "" for c in COLUMNS} | {"provider": provider, "key": key})
        row["origins"] = "|".join(sorted(set(filter(None, row["origins"].split("|"))) | {origin}))
        row.update({k: v for k, v in fields.items() if v not in (None, "")})

    skipped = []
    for line in companies_txt.splitlines():
        parsed = parse_board(line)
        if parsed:
            add(*parsed, "bet-companies")
        elif line.strip() and not line.strip().startswith("#"):
            skipped.append(line.strip())
    for board, info in json.loads(boards_json).items():
        if board == "completed_crawls" or not isinstance(info, dict):
            continue
        de = info.get("de")
        add(*split_key(board), "bet-registry", bet_de="" if de is None else str(bool(de)).lower(),
            bet_checked=info.get("checked") or "")
    for board, info in json.loads(workable_json).get("companies", {}).items():
        add(*split_key(board), "bet-workable-search", name=info.get("name") or "", website=info.get("website") or "")
    if skipped:
        print(f"companies.txt entries not understood: {skipped}", file=sys.stderr)
    return [rows[k] for k in sorted(rows)]


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    bet = sub.add_parser("import-bet")
    bet.add_argument("companies_txt")
    bet.add_argument("boards_json")
    bet.add_argument("workable_json")
    bet.add_argument("--out", required=True)
    args = ap.parse_args()
    rows = import_bet(*(Path(p).read_text(encoding="utf-8") for p in
                        (args.companies_txt, args.boards_json, args.workable_json)))
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    by = {}
    for r in rows:
        by[r["provider"]] = by.get(r["provider"], 0) + 1
    print(f"{args.out}: {len(rows):,} boards {by}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
