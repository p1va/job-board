"""One provider job (raw payload) -> one normalised version row (tables.VERSION fields).

Per source, only fields the provider actually sends; anything missing stays None/unknown.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from . import classify, places
from .personio import board_slug

BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}
INTERVAL = {"1 YEAR": "year", "1 MONTH": "month", "1 WEEK": "week", "1 DAY": "day", "1 HOUR": "hour"}
WORKPLACE = {"onsite": "onsite", "on_site": "onsite", "hybrid": "hybrid", "remote": "remote"}
# Every field that changes what the board shows or selects; a change makes a new version.
HASHED = ("title", "company_name", "department", "employment", "workplace", "location_raw", "locations",
          "countries", "remote_regions", "published_at", "salary_raw", "salary_min", "salary_max",
          "salary_currency", "salary_period", "url", "description_sha", "listed", "where",
          "role_family", "seniority", "stack")     # derived too: a rule change must produce a new version


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("• ")

    def handle_endtag(self, tag):
        if tag in BLOCK and tag != "li":
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_text(markup: str | None) -> str:
    """Readable plain text: paragraphs on their own lines, list items as bullets."""
    if not markup:
        return ""
    parser = _Text()
    parser.feed(markup)
    return tidy("".join(parser.parts))


def tidy(text: str) -> str:
    """Single spaces within lines, at most one blank line between paragraphs, no empty bullets."""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line != "•"]
    # no blank line between consecutive bullets, so indented HTML lists stay compact
    lines = [line for i, line in enumerate(lines)
             if line or not (0 < i < len(lines) - 1 and lines[i - 1].startswith("• ") and lines[i + 1].startswith("• "))]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _time(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.astimezone(timezone.utc) if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _ashby(job: dict) -> dict:
    locs = [job.get("location") or ""] + [s.get("location") or "" for s in job.get("secondaryLocations") or []]
    addresses = [job.get("address")] + [s.get("address") for s in job.get("secondaryLocations") or []]
    countries = [((a or {}).get("postalAddress") or a or {}).get("addressCountry") or None for a in addresses]
    pairs = [(l, c) for l, c in zip(locs, countries) if l or c]     # each location with its own address
    kind = (job.get("workplaceType") or "").lower()
    workplace = WORKPLACE.get(kind) or ("remote" if job.get("isRemote") else None)
    comp = job.get("compensation") or {}
    salary = next((c for c in comp.get("summaryComponents") or [] if c.get("compensationType") == "Salary"), {})
    return dict(title=job.get("title"), department=job.get("department") or job.get("team"),
                employment=job.get("employmentType"), workplace=workplace, locations=[l for l in locs if l],
                pairs=pairs, structured=[], published_at=_time(job.get("publishedAt")), url=job.get("jobUrl"),
                salary_raw=comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary"),
                salary_min=salary.get("minValue"), salary_max=salary.get("maxValue"),
                salary_currency=salary.get("currencyCode"), salary_period=INTERVAL.get(salary.get("interval") or ""),
                description_text=tidy(job.get("descriptionPlain") or "") or html_text(job.get("descriptionHtml")),
                company_name=None, listed=job.get("isListed") is not False)


def _greenhouse(job: dict) -> dict:
    loc = (job.get("location") or {}).get("name") or ""
    meta = {m.get("name"): m.get("value") for m in job.get("metadata") or [] if isinstance(m, dict)}
    countries = meta.get("Country") or []
    countries = [c for c in (countries if isinstance(countries, list) else [countries]) if isinstance(c, str) and c]
    # one location and one Country value belong together (like Ashby's per-location address)
    pairs = [(loc, countries[0])] if loc and len(countries) == 1 else [loc] if loc else []
    return dict(title=job.get("title"), department=", ".join(d.get("name", "") for d in job.get("departments") or []) or None,
                employment=None, workplace=None, locations=[loc] if loc else [], pairs=pairs,
                structured=[] if loc and len(countries) == 1 else countries,
                published_at=_time(job.get("first_published")), url=job.get("absolute_url"),
                salary_raw=None, salary_min=None, salary_max=None, salary_currency=None, salary_period=None,
                description_text=html_text(html.unescape(job.get("content") or "")),
                company_name=job.get("company_name"), listed=True)


def _workable(job: dict) -> dict:
    loc = job.get("location") or {}
    primary = ", ".join(x for x in (loc.get("city"), loc.get("subregion"), loc.get("countryName")) if x)
    others = [l for l in job.get("locations") or [] if l and l != "TELECOMMUTE"]
    body = "\n\n".join(html_text(job.get(k)) for k in ("description", "requirementsSection", "benefitsSection") if job.get(k))
    return dict(title=job.get("title"), department=job.get("department"), employment=job.get("employmentType") or None,
                workplace=WORKPLACE.get((job.get("workplace") or "").lower(), "unknown"),
                locations=[primary] + [l for l in others if l != primary] if primary else others,
                pairs=[(primary, loc.get("countryName") or None)] + [l for l in others if l != primary],
                structured=[], published_at=_time(job.get("created")), url=job.get("url"),
                salary_raw=None, salary_min=None, salary_max=None, salary_currency=None, salary_period=None,
                description_text=body, company_name=(job.get("company") or {}).get("title"),
                listed=(job.get("state") or "published") == "published")


def _personio(job: dict, board: str) -> dict:
    locations = list(dict.fromkeys(l for l in [job.get("office")] + (job.get("additionalOffices") or []) if l))
    salary = job.get("salaryInformation") or {}
    sections = []
    for section in job.get("jobDescriptions") or []:
        body = html_text(section.get("value"))
        if body:
            sections.append("\n".join(filter(None, (html_text(section.get("name")), body))))
    employment = {"intern": "Intern", "trainee": "Trainee", "working_student": "Working student",
                  "freelance": "Contract", "temporary": "Temporary", "fixed_term": "Fixed term"}.get(job.get("employmentType"))
    employment = employment or {"full-time": "FullTime", "part-time": "PartTime",
                                 "full-or-part-time": "Full or part time"}.get(job.get("schedule")) or job.get("employmentType")
    category = job.get("occupationCategory")
    department = job.get("department") or (
        {"it_software": "IT / software", "r_and_d_and_science": "Research and science"}.get(category)
        or category.replace("_", " ").capitalize() if category and category != "other" else None)
    return dict(title=job.get("name"), department=department,
                employment=employment, workplace=None, locations=locations, pairs=locations, structured=[],
                published_at=_time(job.get("createdAt")), url=f"https://{board_slug(board)}.jobs.personio.de/job/{job['id']}",
                salary_raw=None, salary_min=salary.get("min") or None, salary_max=salary.get("max") or None,
                salary_currency=salary.get("currencyCode") or None,
                salary_period={"yearly": "year", "monthly": "month", "hourly": "hour"}.get(salary.get("type")),
                description_text="\n\n".join(sections), company_name=job.get("subcompany") or None, listed=True)


def _lever(job: dict) -> dict:
    cat = job.get("categories") or {}
    primary = cat.get("location") or ""
    locations = list(dict.fromkeys(l for l in [primary] + (cat.get("allLocations") or []) if l))
    # country qualifies the primary location, not every office or the API's storage region.
    pairs = [(l, job.get("country") if l == primary else None) for l in locations]
    kind = (job.get("workplaceType") or "").lower()
    workplace = {"onsite": "onsite", "on-site": "onsite", "hybrid": "hybrid", "remote": "remote"}.get(kind)
    salary = job.get("salaryRange") or {}
    description = html_text(job.get("description")) or tidy(job.get("descriptionPlain") or "")
    if not description:
        description = "\n\n".join(filter(None, (
            html_text(job.get("opening")) or tidy(job.get("openingPlain") or ""),
            html_text(job.get("descriptionBody")) or tidy(job.get("descriptionBodyPlain") or ""))))
    text = [description]
    for section in job.get("lists") or []:
        body = html_text("<ul>" + (section.get("content") or "") + "</ul>")
        if body:
            text.append("\n".join(filter(None, (html_text(section.get("text")), body))))
    salary_text = html_text(job.get("salaryDescription")) or tidy(job.get("salaryDescriptionPlain") or "")
    text += [html_text(job.get("additional")) or tidy(job.get("additionalPlain") or ""), salary_text]
    created = job.get("createdAt")
    published = None
    if type(created) in (int, float):
        try:
            published = datetime.fromtimestamp(created / 1000, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            pass
    return dict(title=job.get("text"), department=cat.get("department") or cat.get("team"),
                employment=cat.get("commitment"), workplace=workplace, locations=locations,
                pairs=pairs, structured=[] if primary else [job.get("country")],
                published_at=published, url=job.get("hostedUrl"),
                salary_raw=(salary_text or None) if salary.get("min") is None and salary.get("max") is None else None,
                salary_min=salary.get("min"), salary_max=salary.get("max"), salary_currency=salary.get("currency"),
                salary_period={"per-year-salary": "year", "per-month-salary": "month", "per-hour-wage": "hour",
                               "per-week-salary": "week", "per-day-wage": "day"}.get(salary.get("interval")),
                description_text="\n\n".join(filter(None, text)), company_name=None, listed=True)


SOURCES = {"ashby": _ashby, "greenhouse": _greenhouse, "workable": _workable, "lever": _lever}


def payload_hash(row: dict) -> str:
    def plain(v):
        return v.isoformat() if isinstance(v, datetime) else v
    return hashlib.sha256(json.dumps({k: plain(row.get(k)) for k in HASHED}, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()[:20]


def normalize(source: str, board: str, native_id: str, payload: str, run_id: str, seen_at: datetime,
              company_id: str | None = None) -> dict:
    """A VERSION row with classification. `kept` says whether it passes the board's filter."""
    job = json.loads(payload)
    f = _personio(job, board) if source == "personio" else SOURCES[source](job)
    declared = f["workplace"] if f["workplace"] not in (None, "unknown") else None
    place = places.parse(f.pop("pairs"), f.pop("structured"), declared)
    modes = {w.split("|")[1] for w in place.where}
    workplace = declared or ("remote" if modes == {"remote"} else "hybrid" if "hybrid" in modes else
                             "onsite" if "onsite" in modes else "unknown")
    text = tidy(f.pop("description_text") or "")
    title = (f["title"] or "").strip()
    row = dict(source=source, board=board, native_id=native_id, run_id=run_id, seen_at=seen_at,
               company_id=company_id or f"{source}:{board}", company_name=f["company_name"], url=f["url"],
               listed=f["listed"],
               title=title, department=f["department"], employment=f["employment"], workplace=workplace,
               location_raw=" | ".join(f["locations"]) or None, locations=f["locations"],
               countries=list(place.countries), remote_regions=list(place.remote_regions), where=list(place.where),
               published_at=f["published_at"], salary_raw=f["salary_raw"],
               salary_min=float(f["salary_min"]) if f["salary_min"] is not None else None,
               salary_max=float(f["salary_max"]) if f["salary_max"] is not None else None,
               salary_currency=f["salary_currency"], salary_period=f["salary_period"],
               description_text=text, description_sha=hashlib.sha256(text.encode()).hexdigest()[:20],
               role_family=classify.role_family(title), seniority=classify.seniority(title))
    row["stack"] = []
    # Kept: listed publicly, a role family, and in or open to Europe; or remote with nothing stated at all
    # (a place we couldn't read is not "nothing stated").
    unstated = "unstated|remote" in place.where and not place.countries and not place.unparsed
    row["kept"] = bool(row["role_family"]) and row["listed"] and (places.in_europe(place) or unstated)
    # Stack tags are the costliest step (dozens of patterns over the description), so only kept jobs
    # get them; deterministic, so the hash of a job is stable from run to run.
    if row["kept"]:
        row["stack"] = classify.stack(f"{title}\n{text}")
    row["payload_hash"] = payload_hash(row)
    return row
