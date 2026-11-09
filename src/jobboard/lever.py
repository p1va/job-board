"""Lever board identity and its public postings-array response."""
from __future__ import annotations

import json
import re
from urllib.parse import quote, urlparse

HOSTS = {"jobs.lever.co": "global", "jobs.eu.lever.co": "eu"}
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")


def board_key(value: str) -> str:
    """An explicit regional key or hosted URL -> global:<slug> / eu:<slug>.

    Slug case matters. The same slug can have independent live boards in both regions. A bare
    slug is deliberately ambiguous; only discovery probes both regions.
    """
    value = value.strip()
    if "/" in value:
        url = urlparse(value if "://" in value else "https://" + value)
        region = HOSTS.get(url.hostname or "")
        slug = url.path.strip("/").split("/")[0]
    else:
        region, _, slug = value.partition(":")
        region = region.lower()
    if region not in ("global", "eu") or not SLUG.fullmatch(slug):
        raise ValueError("Lever board needs global:<slug>, eu:<slug>, or a hosted Lever URL")
    return f"{region}:{slug}"


def candidate_keys(value: str) -> list[str]:
    """An unqualified discovery slug is checked in both independent regions."""
    if ":" in value or "/" in value:
        return [board_key(value)]
    return [board_key(f"{region}:{value.strip()}") for region in ("global", "eu")]


def postings_url(key: str) -> str:
    region, slug = board_key(key).split(":", 1)
    host = "api.eu.lever.co" if region == "eu" else "api.lever.co"
    return f"https://{host}/v0/postings/{quote(slug, safe='')}?mode=json"


def validate_postings(jobs) -> None:
    """Reject malformed arrays before a response can establish board absence."""
    if not isinstance(jobs, list):
        raise ValueError("Lever response must be a postings array")
    seen = set()
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get("id"), str) or not job["id"].strip():
            raise ValueError("Lever posting has no native ID")
        if not isinstance(job.get("text"), str) or not job["text"].strip():
            raise ValueError("Lever posting has no title")
        if job["id"] in seen:
            raise ValueError("duplicate Lever posting ID")
        seen.add(job["id"])


def parse_postings(body: bytes) -> dict:
    """Wrap the validated array for the shared paced/retrying HTTP client."""
    jobs = json.loads(body)
    validate_postings(jobs)
    return {"jobs": jobs}
