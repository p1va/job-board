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
from urllib.parse import parse_qs, urlparse

from .personio import board_slug
from .lever import HOSTS as LEVER_HOSTS, board_key as lever_key

PROVIDERS = ("ashby", "greenhouse", "workable", "personio", "lever")
GREENHOUSE_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io",
                    "boards.eu.greenhouse.io"}
COLUMNS = ["provider", "key", "name", "website", "origins", "bet_de", "bet_checked"]


def lookup_key(provider: str, key: str) -> str:
    """Lever tokens are case-sensitive; older providers retain their lookup convention."""
    return lever_key(key) if provider == "lever" else key.lower()


def company_key(board_id: str) -> str:
    provider, _, key = board_id.partition(":")
    return f"{provider}:{lookup_key(provider, key)}"


def parse_board(line: str) -> tuple[str, str] | None:
    """A companies.txt entry -> (provider, key); bare names are Ashby slugs."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "/" in line or line.endswith((".jobs.personio.de", ".jobs.personio.com")):
        url = urlparse(line if "://" in line else "https://" + line)
        slug = url.path.strip("/").split("/")[0]
        if url.hostname in LEVER_HOSTS:
            try:
                return "lever", lever_key(line)
            except ValueError:
                return None
        if url.hostname and url.hostname.endswith((".jobs.personio.de", ".jobs.personio.com")):
            try:
                return "personio", board_slug(url.hostname.split(".jobs.personio.", 1)[0])
            except ValueError:
                return None
        if url.hostname == "jobs.ashbyhq.com" and slug:
            return "ashby", slug
        if url.hostname in GREENHOUSE_HOSTS:
            if slug == "embed":   # boards.greenhouse.io/embed/job_board?for=<slug>
                slug = (parse_qs(url.query).get("for") or [""])[0]
            return ("greenhouse", slug) if slug else None
        return None
    provider, sep, key = line.partition(":")
    if sep and provider in PROVIDERS and key:
        if provider == "personio":
            try:
                key = board_slug(key)
            except ValueError:
                return None
        elif provider == "lever":
            try:
                key = lever_key(key)
            except ValueError:
                return None
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
        if board.startswith("_") or board == "completed_crawls" or not isinstance(info, dict):
            continue              # metadata such as "_meta": {"completed_crawls": [...]}, not a board
        de = info.get("de")
        add(*split_key(board), "bet-registry", bet_de="" if de is None else str(bool(de)).lower(),
            bet_checked=info.get("checked") or "")
    for board, info in json.loads(workable_json).get("companies", {}).items():
        add(*split_key(board), "bet-workable-search", name=info.get("name") or "", website=info.get("website") or "")
    if skipped:
        print(f"companies.txt entries not understood: {skipped}", file=sys.stderr)
    return [rows[k] for k in sorted(rows)]


LIST_COLUMNS = ["name", "website", "country", "city", "why", "source_url", "careers_url", "ats", "board_key", "checked"]
SCANNED = {"ashby", "greenhouse", "personio", "lever"}  # Workable list keys are account slugs,
                                      # not the search's company IDs, so they wait for an account retriever


def list_label(path: Path) -> str:
    """lists/ai-labs.csv -> 'AI labs'; uk-hottest -> 'UK hottest'."""
    words = path.stem.replace("_", "-").split("-")
    return " ".join(w.upper() if w in ("ai", "uk", "eu", "us") else w for w in words).capitalize().replace("Ai ", "AI ").replace("Uk ", "UK ")


def read_lists(lists: Path) -> list[dict]:
    rows = []
    for path in sorted(lists.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != LIST_COLUMNS:
                raise ValueError(f"{path}: columns {reader.fieldnames} != {LIST_COLUMNS}")
            rows += [r | {"list": list_label(path), "file": path.name} for r in reader]
    return rows


def list_key(row: dict) -> str | None:
    """Canonical list identity; a bare Lever slug needs its hosted careers URL."""
    key = row["board_key"]
    if not key:
        return None
    if row["ats"] == "personio":
        return board_slug(key)
    if row["ats"] == "lever":
        try:
            return lever_key(key)
        except ValueError:
            parsed = parse_board(row.get("careers_url") or "")
            if parsed and parsed[0] == "lever" and parsed[1].split(":", 1)[1].casefold() == key.strip().casefold():
                return parsed[1]
            return None
    return key


def import_lists(lists: Path) -> tuple[list[dict], list[dict]]:
    """Boards to scan (companies/lists.csv rows) and the entries we can't scan yet (coverage report)."""
    boards: dict[tuple[str, str], dict] = {}
    unscanned = []
    for r in read_lists(lists):
        key = list_key(r)
        if r["ats"] in SCANNED and key:
            row = boards.setdefault((r["ats"], key), {c: "" for c in COLUMNS} | {
                "provider": r["ats"], "key": key, "name": r["name"], "website": r["website"]})
            row["origins"] = "|".join(sorted(set(filter(None, row["origins"].split("|"))) | {"list:" + r["file"][:-4]}))
        else:
            unscanned.append(dict(list=r["list"], name=r["name"], ats=r["ats"], board_key=r["board_key"],
                                  reason="no board found" if not r["board_key"] else
                                  "Lever region unknown; use a regional key or hosted careers URL" if r["ats"] == "lever" and not key else
                                  "Workable account slug; only Workable search is read" if r["ats"] == "workable"
                                  else f"{r['ats']} not read yet"))
    return [boards[k] for k in sorted(boards)], unscanned


def memberships(lists: Path) -> dict[str, list[str]]:
    """Company lookup identity -> list labels, preserving Lever token case."""
    out: dict[str, set[str]] = {}
    if lists.exists():
        for r in read_lists(lists):
            if key := list_key(r):
                out.setdefault(company_key(f"{r['ats']}:{key}"), set()).add(r["list"])
    return {k: sorted(v) for k, v in out.items()}


def write_csv(path: str, rows: list[dict], columns: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    bet = sub.add_parser("import-bet")
    bet.add_argument("companies_txt")
    bet.add_argument("boards_json")
    bet.add_argument("workable_json")
    bet.add_argument("--out", required=True)
    lst = sub.add_parser("import-lists")
    lst.add_argument("lists", type=Path)
    lst.add_argument("--out", required=True)
    lst.add_argument("--coverage", required=True)
    args = ap.parse_args()
    if args.cmd == "import-lists":
        boards, unscanned = import_lists(args.lists)
        write_csv(args.out, boards, COLUMNS)
        write_csv(args.coverage, unscanned, ["list", "name", "ats", "board_key", "reason"])
        print(f"{args.out}: {len(boards)} boards to scan; {args.coverage}: {len(unscanned)} entries not scanned",
              file=sys.stderr)
        return 0
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
