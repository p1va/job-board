"""Render the static board from data/: open jobs' latest versions -> site/.

    uv run python -m jobboard.render --data data --out site

The job list is one light JSON file (no descriptions). Descriptions are split into 256 files by a
hash of the job id and fetched when a job is opened, so the page stays small at tens of thousands
of jobs. Asset names carry content hashes, so a new generation is never mixed with a cached one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .places import EUROPE
from .registry import memberships, read_lists

BOARD = Path(__file__).resolve().parents[2] / "board"
STYLE = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "Office", "unknown": "Not stated"}
NAMED = {"DE": "Germany", "GB": "United Kingdom", "IT": "Italy"}
SYMBOL = {"EUR": "€", "GBP": "£", "USD": "$", "CHF": "CHF "}


MODE = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "Office", "unknown": "Not stated"}


def place_label(place: str, mode: str) -> str | None:
    """A where-filter label for one place: the three default countries, the rest of Europe, remote scopes."""
    if place in NAMED:
        return NAMED[place]
    if place in EUROPE:
        return "Rest of Europe"
    if place == "worldwide" and mode == "remote":
        return "Remote: worldwide"
    if place in ("europe", "eu", "emea"):
        return "Remote: Europe" if mode == "remote" else "Rest of Europe"
    if place == "unstated":
        return "Remote: region not stated"
    return None                                         # outside Europe and not open to it


def where_pairs(v: dict) -> list[list[str]]:
    """[label, style] for each place the job names, so filters can combine place and work style."""
    pairs = []
    for item in v["where"] or []:
        place, mode = item.split("|", 1)
        label = place_label(place, mode)
        if label and [label, MODE.get(mode, "Not stated")] not in pairs:
            pairs.append([label, MODE.get(mode, "Not stated")])
    return pairs


def regions(v: dict) -> list[str]:
    return list(dict.fromkeys(label for label, _ in where_pairs(v)))


def salary(v: dict) -> str | None:
    if v["salary_raw"]:
        return v["salary_raw"]
    if v["salary_min"] is None and v["salary_max"] is None:
        return None
    sym = SYMBOL.get(v["salary_currency"] or "", (v["salary_currency"] or "") + " ")
    fmt = lambda x: f"{sym}{x / 1000:.0f}K" if x and x >= 1000 else f"{sym}{x:g}"
    span = " – ".join(fmt(x) for x in (v["salary_min"], v["salary_max"]) if x is not None)
    return span + (f" per {v['salary_period']}" if v["salary_period"] and v["salary_period"] != "year" else "")


def company_name(v: dict, profile: dict | None = None) -> str:
    """Best name we have: cached profile, the provider's own, a list's, then the board slug."""
    if profile and profile.get("name"):
        return profile["name"]
    if v["company_name"]:
        return v["company_name"]
    board = v["board"]
    return board if any(c.isdigit() for c in board) else " ".join(w.capitalize() for w in board.replace("_", "-").split("-"))


def current_versions(data: Path, wanted: dict[tuple, str]) -> dict[tuple, dict]:
    """The version each open job's state points to (by current_hash), never simply the newest file:
    an uncommitted or interrupted attempt's version must not replace what state references."""
    files = sorted((data / "versions").glob("*/*/*/*.parquet"))
    if not files:
        return {}
    found: dict[tuple, dict] = {}
    for v in ds.dataset(files, format="parquet").to_table().to_pylist():
        key = (v["source"], v["board"], v["native_id"])
        if wanted.get(key) == v["payload_hash"]:
            found[key] = v
    return found


def hashed(name: str, content: bytes) -> str:
    stem, dot, ext = name.partition(".")
    return f"{stem}-{hashlib.sha256(content).hexdigest()[:12]}{dot}{ext}"


def load_companies(profiles: Path, lists_dir: Path) -> dict[str, dict]:
    """company_id -> {name, website, logoUrl}: cached board profiles first, then list entries."""
    companies = json.loads(profiles.read_text(encoding="utf-8")) if profiles.exists() else {}
    if lists_dir.exists():
        for r in read_lists(lists_dir):
            if r["board_key"]:
                entry = companies.setdefault(f"{r['ats']}:{r['board_key']}".lower(), {})
                entry.setdefault("name", r["name"])
                if r["website"]:
                    entry.setdefault("website", r["website"])
    return companies


def render(data: Path, out: Path, now: datetime, lists: dict[str, list[str]] | None = None,
           companies: dict[str, dict] | None = None) -> dict:
    state = pq.read_table(data / "state.parquet").to_pylist()
    wanted = {(s["source"], s["board"], s["native_id"]): s["current_hash"] for s in state if s["status"] == "open"}
    versions = current_versions(data, wanted)
    missing = wanted.keys() - versions.keys()
    if missing:
        raise ValueError(f"{len(missing)} open jobs have no version matching their state, e.g. {sorted(missing)[:3]}")
    lists, companies = lists or {}, companies or {}
    companies = {k: dict(v) for k, v in companies.items()}
    names = defaultdict(Counter)
    for v in versions.values():
        if v["company_name"]:
            names[v["company_id"].lower()][v["company_name"].strip()] += 1
    # A board can contain several legal entities: choose one fallback consistently for its
    # heading, dropdown and every role, including roles without a company name.
    for company_id, counts in names.items():
        profile = companies.setdefault(company_id, {})
        if not profile.get("name"):
            profile["name"] = min(counts, key=lambda name: (-counts[name], name.casefold(), name))
    jobs, descriptions = [], {}
    for key in sorted(versions):
        v = versions[key]
        job_id = ":".join(key)
        bucket = hashlib.sha256(job_id.encode()).hexdigest()[:2]
        descriptions.setdefault(bucket, {})[job_id] = v["description_text"] or ""
        jobs.append(dict(
            id=job_id, d=bucket, source=v["source"], company=v["company_id"],
            companyName=company_name(v, companies.get(v["company_id"].lower())),
            title=v["title"], url=v["url"], location=(v["locations"] or [None])[0] or v["location_raw"] or "",
            locations=v["locations"] or [], style=STYLE.get(v["workplace"], "Not stated"),
            employment=v["employment"], department=v["department"], salary=salary(v),
            published=v["published_at"].isoformat() if v["published_at"] else None,
            family=v["role_family"], level=v["seniority"], stack=v["stack"] or [], regions=regions(v),
            where=where_pairs(v),
            lists=lists.get(v["company_id"].lower(), [])))
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    desc_blob = json.dumps(descriptions, sort_keys=True, ensure_ascii=False).encode()
    desc_dir = f"desc-{hashlib.sha256(desc_blob).hexdigest()[:12]}"
    (out / desc_dir).mkdir()
    for bucket, texts in descriptions.items():
        (out / desc_dir / f"{bucket}.json").write_text(json.dumps(texts, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    used = {j["company"] for j in jobs}
    shown = {c: companies[c.lower()] for c in used if c.lower() in companies}
    payload = json.dumps(dict(generatedAt=now.isoformat(), descriptions=desc_dir + "/", jobs=jobs, companies=shown),
                         ensure_ascii=False, separators=(",", ":")).encode()
    data_file = hashed("jobs.json", payload)
    (out / data_file).write_bytes(payload)
    names = {"DATA_FILE": data_file, "ROLE_COUNT": f"{len(jobs):,}",
             "COMPANY_COUNT": f"{len({j['company'] for j in jobs}):,}"}
    for asset in ("app.js", "styles.css"):
        content = (BOARD / asset).read_bytes()
        names[asset.upper().replace(".", "_")] = hashed(asset, content)
        (out / names[asset.upper().replace(".", "_")]).write_bytes(content)
    page = (BOARD / "index.html").read_text(encoding="utf-8")
    for key, value in names.items():
        page = page.replace("{{" + key + "}}", value)
    if "{{" in page:
        raise ValueError("unfilled placeholder in index.html")
    (out / "index.html").write_text(page, encoding="utf-8")
    shutil.copy(BOARD / "favicon.svg", out / "favicon.svg")
    (out / ".nojekyll").touch()
    return dict(jobs=len(jobs), companies=len({j["company"] for j in jobs}), data_file=data_file,
                data_bytes=len(payload), description_files=len(descriptions),
                description_bytes=sum(f.stat().st_size for f in (out / desc_dir).iterdir()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--out", type=Path, default=Path("site"))
    ap.add_argument("--lists", type=Path, default=Path("lists"))
    ap.add_argument("--profiles", type=Path, default=Path("companies/profiles.json"))
    args = ap.parse_args(argv)
    print(json.dumps(render(args.data, args.out, datetime.now(timezone.utc), memberships(args.lists),
                            load_companies(args.profiles, args.lists)), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
