import json
from datetime import datetime, timezone
from pathlib import Path

from jobboard.normalize import html_text, normalize, payload_hash

FIXTURES = Path(__file__).parent / "fixtures"
T = datetime(2026, 11, 9, 6, 0, tzinfo=timezone.utc)


def first(source):
    return json.loads((FIXTURES / f"{source}.json").read_text())[0]


def norm(source, job, board="b"):
    return normalize(source, board, str(job["id"]), json.dumps(job), "r-1", T)


def test_every_source_fills_the_shared_fields():
    for source in ("ashby", "greenhouse", "workable"):
        row = norm(source, first(source))
        assert row["title"] and row["url"].startswith("https://") and row["description_text"], source
        assert row["workplace"] in ("onsite", "hybrid", "remote", "unknown"), source
        assert row["published_at"] is None or row["published_at"].tzinfo is not None, source
        assert len(row["payload_hash"]) == 20 and isinstance(row["kept"], bool), source


def test_ashby_salary_and_structured_country():
    job = first("ashby") | {"compensation": {"compensationTierSummary": "€80K – €100K", "summaryComponents": [
        {"compensationType": "Salary", "currencyCode": "EUR", "interval": "1 YEAR", "minValue": 80000, "maxValue": 100000}]},
        "location": "Remote", "secondaryLocations": [], "workplaceType": "Remote",
        "address": {"postalAddress": {"addressCountry": "Germany"}}}
    row = norm("ashby", job)
    assert (row["salary_min"], row["salary_max"], row["salary_currency"], row["salary_period"]) == (80000.0, 100000.0, "EUR", "year")
    assert row["countries"] == ["DE"] and row["workplace"] == "remote"


def test_hash_changes_with_display_fields_only():
    job = first("ashby")
    a, b = norm("ashby", job), norm("ashby", job | {"title": job["title"] + " (m/f/d)"})
    assert a["payload_hash"] != b["payload_hash"]
    assert payload_hash(a) == a["payload_hash"]
    c = normalize("ashby", "b", str(job["id"]), json.dumps(job), "another-run", T.replace(day=10))
    assert c["payload_hash"] == a["payload_hash"]          # run and time are not part of a version


def test_kept_needs_a_family_and_europe():
    job = first("ashby")
    berlin = norm("ashby", job | {"title": "Senior Backend Engineer", "location": "Berlin", "secondaryLocations": [], "address": None})
    us = norm("ashby", job | {"title": "Senior Backend Engineer", "location": "Remote - United States", "secondaryLocations": [], "address": None})
    sales = norm("ashby", job | {"title": "Account Executive", "location": "Berlin", "secondaryLocations": [], "address": None})
    assert (berlin["kept"], us["kept"], sales["kept"]) == (True, False, False)


def test_html_text_keeps_paragraphs_and_bullets():
    assert html_text("<p>Hello <b>you</b></p><ul><li>One</li><li>Two</li></ul>") == "Hello you\n• One\n• Two"
