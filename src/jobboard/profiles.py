"""Company names, websites and logos for boards with open jobs: companies/profiles.json.

    uv run python -m jobboard.profiles --data data --profiles companies/profiles.json [--limit 300]

Ashby job postings carry no company name, so the board would show slugs. This fetches the public
board profile for companies that have open jobs and no profile yet (or one older than 30 days):
Ashby's job-board GraphQL (name, website, square logo) and Greenhouse's board API (name). Paced and
capped per run; a failure leaves the company as it was.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

import pyarrow.parquet as pq

GRAPHQL = "https://jobs.ashbyhq.com/api/non-user-graphql"
QUERY = """query CompanyProfile($slug: String!) {
  organizationFromHostedJobsPageName(organizationHostedJobsPageName: $slug, searchContext: JobBoard) {
    name publicWebsite theme { logoSquareImageUrl logoWordmarkImageUrl }
  }
}"""
GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{key}"
AGENT = "p1va-job-board/0.1 (+https://github.com/p1va/job-board)"
MAX_AGE = timedelta(days=30)


def public_url(value) -> str | None:
    if not isinstance(value, str):
        return None
    parts = urlsplit(value.strip())
    return value.strip() if parts.scheme in ("http", "https") and parts.hostname else None


def _json(request: urllib.request.Request) -> dict:
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def ashby_profile(slug: str, fetch=_json) -> dict:
    body = json.dumps({"operationName": "CompanyProfile", "query": QUERY, "variables": {"slug": slug}}).encode()
    data = fetch(urllib.request.Request(GRAPHQL, data=body, headers={
        "Content-Type": "application/json", "User-Agent": AGENT}))
    if data.get("errors"):
        raise ValueError(f"GraphQL errors: {data['errors']}")
    org = (data.get("data") or {}).get("organizationFromHostedJobsPageName")
    if not isinstance(org, dict) or not (org.get("name") or "").strip():
        raise ValueError("no organization")
    theme = org.get("theme") or {}
    return {k: v for k, v in dict(name=org["name"].strip(), website=public_url(org.get("publicWebsite")),
            logoUrl=public_url(theme.get("logoSquareImageUrl")) or public_url(theme.get("logoWordmarkImageUrl"))).items() if v}


def greenhouse_profile(key: str, fetch=_json) -> dict:
    board = fetch(urllib.request.Request(GREENHOUSE.format(key=quote(key, safe="")), headers={"User-Agent": AGENT}))
    if not (board.get("name") or "").strip():
        raise ValueError("no board name")
    return {"name": board["name"].strip()}


FETCH = {"ashby": ashby_profile, "greenhouse": greenhouse_profile}


def wanted(data: Path, profiles: dict, now: datetime) -> list[str]:
    """Board ids with open jobs whose profile is missing or stale, most jobs first."""
    counts: dict[str, int] = {}
    for s in pq.read_table(data / "state.parquet").to_pylist():
        if s["status"] == "open" and s["source"] in FETCH:
            counts[s["company_id"].lower()] = counts.get(s["company_id"].lower(), 0) + 1

    def stale(board_id: str) -> bool:
        fetched = (profiles.get(board_id) or {}).get("fetchedAt")
        if not profiles.get(board_id) or not fetched:
            return True                       # missing, or of unknown age
        return now - datetime.fromisoformat(fetched) > MAX_AGE

    return sorted((b for b in counts if stale(b)), key=lambda b: (-counts[b], b))


def refresh(data: Path, path: Path, now: datetime, *, limit: int = 300, interval: float = 0.5,
            budget: float = 300, fetchers=FETCH, sleep=time.sleep, clock=time.monotonic) -> dict:
    """Optional enrichment: stops after `budget` seconds so it can't hold up publishing."""
    profiles = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    todo = wanted(data, profiles, now)[:limit]
    done, failed, started = 0, {}, clock()
    for board_id in todo:
        if clock() - started > budget:
            break
        provider, key = board_id.split(":", 1)
        try:
            profile = fetchers[provider](key)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            failed[board_id] = f"{type(exc).__name__}: {exc}"[:120]
        else:
            # keep fields this provider doesn't send (a Greenhouse refresh has no logo or website)
            profiles[board_id] = (profiles.get(board_id) or {}) | profile | {"fetchedAt": now.isoformat()}
            done += 1
        sleep(interval)
    path.write_text(json.dumps(profiles, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return dict(wanted=len(todo), fetched=done, failed=len(failed), unfinished=len(todo) - done - len(failed),
                failures=dict(list(failed.items())[:20]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--profiles", type=Path, default=Path("companies/profiles.json"))
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--budget", type=float, default=300, help="seconds to spend at most")
    args = ap.parse_args(argv)
    print(json.dumps(refresh(args.data, args.profiles, datetime.now(timezone.utc), limit=args.limit,
                             budget=args.budget), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
