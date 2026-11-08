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

BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}
INTERVAL = {"1 YEAR": "year", "1 MONTH": "month", "1 WEEK": "week", "1 DAY": "day", "1 HOUR": "hour"}
WORKPLACE = {"onsite": "onsite", "on_site": "onsite", "hybrid": "hybrid", "remote": "remote"}
# Every field that changes what the board shows or selects; a change makes a new version.
HASHED = ("title", "company_name", "department", "employment", "workplace", "location_raw", "locations",
          "countries", "remote_regions", "published_at", "salary_raw", "salary_min", "salary_max",
          "salary_currency", "salary_period", "url", "description_sha")


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
        if tag in BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_text(markup: str | None) -> str:
    """Readable plain text: paragraphs on their own lines, list items as bullets."""
    if not markup:
        return ""
    parser = _Text()
    parser.feed(markup)
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    text = "\n".join(line for line in lines if line and line != "•")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


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
    structured = [((a or {}).get("postalAddress") or {}).get("addressCountry") or "" for a in addresses]
    kind = (job.get("workplaceType") or "").lower()
    workplace = WORKPLACE.get(kind) or ("remote" if job.get("isRemote") else "unknown")
    comp = job.get("compensation") or {}
    salary = next((c for c in comp.get("summaryComponents") or [] if c.get("compensationType") == "Salary"), {})
    return dict(title=job.get("title"), department=job.get("department") or job.get("team"),
                employment=job.get("employmentType"), workplace=workplace, locations=[l for l in locs if l],
                structured=structured, published_at=_time(job.get("publishedAt")), url=job.get("jobUrl"),
                salary_raw=comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary"),
                salary_min=salary.get("minValue"), salary_max=salary.get("maxValue"),
                salary_currency=salary.get("currencyCode"), salary_period=INTERVAL.get(salary.get("interval") or ""),
                description_text=(job.get("descriptionPlain") or "").strip() or html_text(job.get("descriptionHtml")),
                company_name=None)


def _greenhouse(job: dict) -> dict:
    loc = (job.get("location") or {}).get("name") or ""
    meta = {m.get("name"): m.get("value") for m in job.get("metadata") or [] if isinstance(m, dict)}
    countries = meta.get("Country") or []
    return dict(title=job.get("title"), department=", ".join(d.get("name", "") for d in job.get("departments") or []) or None,
                employment=None, workplace=None, locations=[loc] if loc else [],
                structured=countries if isinstance(countries, list) else [countries],
                published_at=_time(job.get("first_published") or job.get("updated_at")), url=job.get("absolute_url"),
                salary_raw=None, salary_min=None, salary_max=None, salary_currency=None, salary_period=None,
                description_text=html_text(html.unescape(job.get("content") or "")),
                company_name=job.get("company_name"))


def _workable(job: dict) -> dict:
    loc = job.get("location") or {}
    primary = ", ".join(x for x in (loc.get("city"), loc.get("subregion"), loc.get("countryName")) if x)
    others = [l for l in job.get("locations") or [] if l and l != "TELECOMMUTE"]
    body = "\n\n".join(html_text(job.get(k)) for k in ("description", "requirementsSection", "benefitsSection") if job.get(k))
    return dict(title=job.get("title"), department=job.get("department"), employment=job.get("employmentType") or None,
                workplace=WORKPLACE.get((job.get("workplace") or "").lower(), "unknown"),
                locations=[primary] + [l for l in others if l != primary] if primary else others,
                structured=[loc.get("countryName") or ""], published_at=_time(job.get("created")), url=job.get("url"),
                salary_raw=None, salary_min=None, salary_max=None, salary_currency=None, salary_period=None,
                description_text=body, company_name=(job.get("company") or {}).get("title"))


SOURCES = {"ashby": _ashby, "greenhouse": _greenhouse, "workable": _workable}


def payload_hash(row: dict) -> str:
    def plain(v):
        return v.isoformat() if isinstance(v, datetime) else v
    return hashlib.sha256(json.dumps({k: plain(row.get(k)) for k in HASHED}, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()[:20]


def normalize(source: str, board: str, native_id: str, payload: str, run_id: str, seen_at: datetime,
              company_id: str | None = None) -> dict:
    """A VERSION row with classification. `kept` says whether it passes the board's filter."""
    f = SOURCES[source](json.loads(payload))
    place = places.parse(f["locations"], f.pop("structured"))
    workplace = f["workplace"] or ("remote" if place.remote else "hybrid" if place.hybrid else "unknown")
    if workplace == "unknown" and place.remote:
        workplace = "remote"
    text = f.pop("description_text") or ""
    title = (f["title"] or "").strip()
    row = dict(source=source, board=board, native_id=native_id, run_id=run_id, seen_at=seen_at,
               company_id=company_id or f"{source}:{board}", company_name=f["company_name"], url=f["url"],
               title=title, department=f["department"], employment=f["employment"], workplace=workplace,
               location_raw=" | ".join(f["locations"]) or None, locations=f["locations"],
               countries=list(place.countries), remote_regions=list(place.remote_regions),
               published_at=f["published_at"], salary_raw=f["salary_raw"],
               salary_min=float(f["salary_min"]) if f["salary_min"] is not None else None,
               salary_max=float(f["salary_max"]) if f["salary_max"] is not None else None,
               salary_currency=f["salary_currency"], salary_period=f["salary_period"],
               description_text=text, description_sha=hashlib.sha256(text.encode()).hexdigest()[:20],
               role_family=classify.role_family(title), seniority=classify.seniority(title),
               stack=classify.stack(f"{title}\n{text}"))
    row["payload_hash"] = payload_hash(row)
    row["kept"] = bool(row["role_family"]) and (places.in_europe(place) or (workplace == "remote" and not place.remote_regions
                                                                             and not place.countries))
    return row
