"""Retrieve provider JSON without normalising or applying the board's filters."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from .contract import SCHEMA_VERSION, Kind, Scope, Status
from .http import DeadlineExceeded
from .lever import parse_postings, postings_url
from .personio import feed_url, has_description, parse_feed

# Geographic Europe, including transcontinental countries and dependencies.
# These are discovery selections, not evidence of a job's actual work eligibility.
EUROPE = dict(item.split(":", 1) for item in (
    "AD:Andorra", "AL:Albania", "AM:Armenia", "AT:Austria", "AZ:Azerbaijan", "BA:Bosnia and Herzegovina",
    "BE:Belgium", "BG:Bulgaria", "BY:Belarus", "CH:Switzerland", "CY:Cyprus", "CZ:Czech Republic",
    "DE:Germany", "DK:Denmark", "EE:Estonia", "ES:Spain", "FI:Finland", "FO:Faroe Islands", "FR:France",
    "GB:United Kingdom", "GE:Georgia", "GG:Guernsey", "GI:Gibraltar", "GR:Greece", "HR:Croatia",
    "HU:Hungary", "IE:Ireland", "IM:Isle of Man", "IS:Iceland", "IT:Italy", "JE:Jersey", "KZ:Kazakhstan",
    "LI:Liechtenstein", "LT:Lithuania", "LU:Luxembourg", "LV:Latvia", "MC:Monaco", "MD:Moldova",
    "ME:Montenegro", "MK:North Macedonia", "MT:Malta", "NL:Netherlands", "NO:Norway", "PL:Poland",
    "PT:Portugal", "RO:Romania", "RS:Serbia", "RU:Russia", "SE:Sweden", "SI:Slovenia", "SJ:Svalbard",
    "SK:Slovakia", "SM:San Marino", "TR:Turkey", "UA:Ukraine", "VA:Vatican City", "XK:Kosovo",
))


@dataclass(frozen=True)
class Selection:
    scope_id: str
    kind: Kind
    board: str = ""
    query: dict[str, str] = field(default_factory=dict)


@dataclass
class Result:
    scope: Scope
    rows: list[dict] = field(default_factory=list)
    totals: dict = field(default_factory=dict)


def workable_selections(countries=None, remote: str = "include") -> list[Selection]:
    countries = sorted(EUROPE) if countries is None else sorted(set(countries))
    if set(countries) - EUROPE.keys():
        raise ValueError(f"unknown European country codes: {sorted(set(countries) - EUROPE.keys())}")
    selections = [] if remote == "only" else [
        Selection(f"workable:search:country={code}", Kind.QUERY, query={"location": EUROPE[code]})
        for code in countries]
    if remote != "exclude":
        selections.append(Selection("workable:search:remote", Kind.QUERY, query={"workplace": "remote"}))
    return selections


def job_title(source: str, job: dict) -> str:
    return job.get({"personio": "name", "lever": "text"}.get(source, "title")) or ""


def _identity(job: dict, source: str, board: str) -> tuple[str, str]:
    if not isinstance(job, dict):
        raise ValueError("job must be an object")
    native_id = job.get("id")
    if type(native_id) not in (str, int) or not str(native_id).strip():
        raise ValueError("job has no native ID")
    title = job_title(source, job)
    if not isinstance(title, str) or not title.strip():
        raise ValueError("job has no title")
    if source == "workable":
        company = job.get("company")
        if not isinstance(company, dict) or not isinstance(company.get("id"), str) or not company["id"].strip():
            raise ValueError("Workable job has no company ID")
        board = company["id"]
    if source == "greenhouse" and not isinstance(job.get("content"), str):
        raise ValueError("Greenhouse job missing content from full board request")
    return board, str(native_id)


def _personio_descriptions(jobs: list[dict], get, url: str, scope: Scope, deadline, clock) -> tuple[list[dict], dict]:
    """One optional English fetch fills only missing descriptions, never changes base membership."""
    missing = sum(not has_description(job) for job in jobs)
    totals = dict(descriptions_missing_initial=missing, descriptions_filled=0, descriptions_missing=missing)
    if not missing:
        return jobs, totals
    try:
        if deadline is not None and clock() >= deadline:
            raise DeadlineExceeded("no time left for optional English descriptions")
        other = get(url + "?language=en", parse=parse_feed, accept="application/xml")
        scope.pages += 1
        translated = {str(job["id"]): job for job in other["jobs"]}
        merged = []
        for job in jobs:
            english = translated.get(str(job["id"]))
            if not has_description(job) and english and has_description(english):
                job = job | {"jobDescriptions": english["jobDescriptions"]}
                totals["descriptions_filled"] += 1
            merged.append(job)
        jobs = merged
        totals["descriptions_missing"] = missing - totals["descriptions_filled"]
    except Exception as exc:
        totals["description_error"] = f"{type(exc).__name__}: {exc}"[:200]
    return jobs, totals


def retrieve(source: str, selection: Selection, get, run_id: str, *, max_pages: int = 5000,
             progress=None, deadline=None, clock=time.monotonic) -> Result:
    """A failed page keeps earlier positives, but can never establish absence."""
    scope = Scope(selection.scope_id, selection.kind, Status.FAILED)
    rows: dict[tuple[str, str], dict] = {}
    expected, cursor, cursors = 0, None, set()
    totals = {}
    try:
        for _ in range(max_pages):
            if deadline is not None and clock() >= deadline:
                raise DeadlineExceeded("shard deadline reached")
            if source == "ashby":
                url = f"https://api.ashbyhq.com/posting-api/job-board/{quote(selection.board, safe='')}?includeCompensation=true"
            elif source == "greenhouse":
                url = f"https://boards-api.greenhouse.io/v1/boards/{quote(selection.board, safe='')}/jobs?content=true"
            elif source == "workable":
                params = selection.query | ({"pageToken": cursor} if cursor else {})
                url = "https://jobs.workable.com/api/v1/jobs?" + urlencode(params)
            elif source == "personio":
                url = feed_url(selection.board)
            elif source == "lever":
                url = postings_url(selection.board)
            else:
                raise ValueError(f"unsupported source: {source}")
            if source == "personio":
                data = get(url, parse=parse_feed, accept="application/xml")
            elif source == "lever":
                data = get(url, parse=parse_postings)
            else:
                data = get(url)
            scope.http_status = 200
            scope.pages += 1
            jobs = data.get("jobs")
            if not isinstance(jobs, list):
                raise ValueError("response has no jobs list")
            if source == "personio":
                for job in jobs:
                    _identity(job, source, selection.board)
                jobs, totals = _personio_descriptions(jobs, get, url, scope, deadline, clock)
                invalid = data.get("invalid_positions") or []
                if invalid:
                    totals["invalid_positions"] = len(invalid)
                    totals["invalid_position_ids"] = [j["id"] for j in invalid][:10]
            total = data.get("totalSize") if source == "workable" else (data.get("meta") or {}).get("total")
            if source == "workable" and type(total) is not int:
                raise ValueError("Workable response missing integer totalSize")
            if total is not None:
                if type(total) is not int or total < 0:
                    raise ValueError("response has invalid total")
                expected = max(expected, total)
                totals.setdefault("first", total)
                totals.update(last=total, maximum=expected)
            fetched_at = datetime.now(timezone.utc)
            page = {}
            for job in jobs:
                board, native_id = _identity(job, source, selection.board)
                if (board, native_id) in page:
                    raise ValueError("duplicate job within page")
                page[board, native_id] = dict(schema_version=SCHEMA_VERSION, run_id=run_id, source=source,
                    scope_id=selection.scope_id, board=board, native_id=native_id, fetched_at=fetched_at,
                    payload=json.dumps(job, ensure_ascii=False, sort_keys=True))
            rows.update(page)
            if progress and source == "workable" and scope.pages % 20 == 0:
                progress(f"{scope.scope_id}: {scope.pages} pages, {len(rows)} jobs (advertised {expected})")
            token = data.get("nextPageToken") if source == "workable" else None
            if token is None or token == "":
                if len(rows) < expected:
                    raise ValueError(f"pagination ended with {len(rows)} jobs, advertised {expected}")
                if source == "personio" and totals.get("invalid_positions"):
                    scope.status = Status.PARTIAL if rows else Status.FAILED
                    scope.error = f"{totals['invalid_positions']} Personio positions have no name"
                else:
                    scope.status = Status.COMPLETE if rows else Status.EMPTY
                break
            if not isinstance(token, str) or token in cursors or not jobs:
                raise ValueError("invalid/repeated cursor or empty intermediate page")
            cursors.add(token)
            cursor = token
        else:
            raise ValueError(f"page limit ({max_pages}) reached before end of pagination")
    except Exception as exc:
        scope.status = Status.PARTIAL if rows else Status.FAILED
        scope.http_status = getattr(exc, "code", scope.http_status)
        scope.error = f"{type(exc).__name__}: {exc}"
        retry = (getattr(exc, "headers", None) or {}).get("Retry-After")
        if retry:
            scope.error += f" (Retry-After: {retry})"
    scope.jobs = len(rows)
    return Result(scope, list(rows.values()), totals)
