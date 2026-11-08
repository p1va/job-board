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
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .places import EUROPE

BOARD = Path(__file__).resolve().parents[2] / "board"
STYLE = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "Office", "unknown": "Not stated"}
NAMED = {"DE": "Germany", "GB": "United Kingdom", "IT": "Italy"}
SYMBOL = {"EUR": "€", "GBP": "£", "USD": "$", "CHF": "CHF "}


def regions(v: dict) -> list[str]:
    """Where-filter values: the three default countries, the rest of Europe, and remote scopes."""
    out = []
    countries, remote = set(v["countries"] or []), set(v["remote_regions"] or [])
    out += [NAMED[c] for c in ("DE", "GB", "IT") if c in countries]
    if (countries & EUROPE) - NAMED.keys():
        out.append("Rest of Europe")
    if "worldwide" in remote:
        out.append("Remote: worldwide")
    if remote & {"europe", "eu", "emea"}:
        out.append("Remote: Europe")
    if v["workplace"] == "remote" and not countries and not remote:
        out.append("Remote: region not stated")
    return out


def salary(v: dict) -> str | None:
    if v["salary_raw"]:
        return v["salary_raw"]
    if v["salary_min"] is None and v["salary_max"] is None:
        return None
    sym = SYMBOL.get(v["salary_currency"] or "", (v["salary_currency"] or "") + " ")
    fmt = lambda x: f"{sym}{x / 1000:.0f}K" if x and x >= 1000 else f"{sym}{x:g}"
    span = " – ".join(fmt(x) for x in (v["salary_min"], v["salary_max"]) if x is not None)
    return span + (f" per {v['salary_period']}" if v["salary_period"] and v["salary_period"] != "year" else "")


def company_name(v: dict) -> str:
    if v["company_name"]:
        return v["company_name"]
    board = v["board"]
    return board if any(c.isdigit() for c in board) else " ".join(w.capitalize() for w in board.replace("_", "-").split("-"))


def latest_versions(data: Path, keys: set[tuple]) -> dict[tuple, dict]:
    files = sorted((data / "versions").glob("*/*/*/*.parquet"))
    if not files:
        return {}
    latest: dict[tuple, dict] = {}
    for v in ds.dataset(files, format="parquet").to_table().to_pylist():
        key = (v["source"], v["board"], v["native_id"])
        if key in keys and (key not in latest or v["seen_at"] > latest[key]["seen_at"]):
            latest[key] = v
    return latest


def hashed(name: str, content: bytes) -> str:
    stem, dot, ext = name.partition(".")
    return f"{stem}-{hashlib.sha256(content).hexdigest()[:12]}{dot}{ext}"


def render(data: Path, out: Path, now: datetime, lists: dict[str, list[str]] | None = None) -> dict:
    state = pq.read_table(data / "state.parquet").to_pylist()
    open_keys = {(s["source"], s["board"], s["native_id"]) for s in state if s["status"] == "open"}
    versions = latest_versions(data, open_keys)
    missing = open_keys - versions.keys()
    if missing:
        raise ValueError(f"{len(missing)} open jobs have no version, e.g. {sorted(missing)[:3]}")
    lists = lists or {}
    jobs, descriptions = [], {}
    for key in sorted(versions):
        v = versions[key]
        job_id = ":".join(key)
        bucket = hashlib.sha256(job_id.encode()).hexdigest()[:2]
        descriptions.setdefault(bucket, {})[job_id] = v["description_text"] or ""
        jobs.append(dict(
            id=job_id, d=bucket, source=v["source"], company=v["company_id"], companyName=company_name(v),
            title=v["title"], url=v["url"], location=(v["locations"] or [None])[0] or v["location_raw"] or "",
            locations=v["locations"] or [], style=STYLE.get(v["workplace"], "Not stated"),
            employment=v["employment"], department=v["department"], salary=salary(v),
            published=v["published_at"].isoformat() if v["published_at"] else None,
            family=v["role_family"], level=v["seniority"], stack=v["stack"] or [], regions=regions(v),
            lists=lists.get(v["company_id"], [])))
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    desc_blob = json.dumps(descriptions, sort_keys=True, ensure_ascii=False).encode()
    desc_dir = f"desc-{hashlib.sha256(desc_blob).hexdigest()[:12]}"
    (out / desc_dir).mkdir()
    for bucket, texts in descriptions.items():
        (out / desc_dir / f"{bucket}.json").write_text(json.dumps(texts, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    payload = json.dumps(dict(generatedAt=now.isoformat(), descriptions=desc_dir + "/", jobs=jobs, companies={}),
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
    args = ap.parse_args(argv)
    print(json.dumps(render(args.data, args.out, datetime.now(timezone.utc)), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
