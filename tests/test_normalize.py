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
    assert html_text("<p>Hello <b>you</b></p><ul><li>One</li><li>Two</li></ul>") == "Hello you\n\n• One\n• Two"


def ashby(**changes):
    return first("ashby") | {"title": "Senior Backend Engineer", "location": "Berlin", "secondaryLocations": [],
                             "address": None, "workplaceType": None, "isRemote": None} | changes


def test_unlisted_ashby_jobs_are_not_kept_and_change_the_version():
    listed, unlisted = norm("ashby", ashby(isListed=True)), norm("ashby", ashby(isListed=False))
    assert listed["kept"] and not unlisted["kept"] and listed["payload_hash"] != unlisted["payload_hash"]


def test_derived_fields_are_part_of_the_version():
    row = norm("ashby", ashby())
    assert payload_hash(row | {"stack": row["stack"] + ["rust"]}) != row["payload_hash"]
    assert payload_hash(row | {"role_family": "research"}) != row["payload_hash"]


def test_unreadable_place_is_not_kept_as_unstated_remote():
    remote = norm("ashby", ashby(location="Remote", workplaceType="Remote"))
    honduras = norm("workable", first("workable") | {"title": "Senior Data Engineer", "workplace": "remote",
                                                     "location": {"countryName": "Atlantis"}, "locations": []})
    assert remote["kept"] and not honduras["kept"]


def test_style_from_location_when_the_provider_is_silent():
    assert norm("ashby", ashby(location="Berlin (Hybrid)"))["workplace"] == "hybrid"


def test_direct_ashby_address_country_counts():
    row = norm("ashby", ashby(location="Remote", workplaceType="Remote", address={"addressCountry": "DE"}))
    assert row["countries"] == ["DE"]


def test_greenhouse_updated_at_is_not_a_publication_date():
    job = first("greenhouse") | {"first_published": None, "updated_at": "2026-11-08T10:00:00Z"}
    assert norm("greenhouse", job)["published_at"] is None


def test_ashby_plain_text_blank_runs_collapse():
    row = norm("ashby", ashby(descriptionPlain="Intro\n\n\n\n\nMore   text\n\n\n- point"))
    assert row["description_text"] == "Intro\n\nMore text\n\n- point"


def test_ashby_address_country_decides_a_bare_city():
    london_ontario = norm("ashby", ashby(location="London", address={"postalAddress": {"addressCountry": "Canada"}}))
    assert london_ontario["countries"] == ["CA"] and not london_ontario["kept"]


def test_indented_html_lists_stay_compact():
    assert html_text("<ul>\n  <li>One</li>\n  <li>Two</li>\n</ul>") == "• One\n• Two"


def gh(**changes):
    return first("greenhouse") | {"title": "Senior Backend Engineer"} | changes


def test_greenhouse_country_field_belongs_to_its_location():
    london = norm("greenhouse", gh(location={"name": "London"}, metadata=[{"name": "Country", "value": ["Canada"]}]))
    remote_de = norm("greenhouse", gh(location={"name": "Remote"}, metadata=[{"name": "Country", "value": ["Germany"]}]))
    assert london["countries"] == ["CA"] and not london["kept"]
    assert remote_de["where"] == ["DE|remote"] and remote_de["workplace"] == "remote" and remote_de["kept"]


def test_an_address_never_narrows_an_explicit_worldwide_label():
    row = norm("ashby", ashby(location="Remote - Worldwide", workplaceType="Remote",
                              address={"postalAddress": {"addressCountry": "US"}}))
    assert "worldwide|remote" in row["where"] and row["kept"]
    restricted = norm("ashby", ashby(location="Remote - Anywhere in the United States", workplaceType="Remote",
                                     address={"postalAddress": {"addressCountry": "US"}}))
    assert not restricted["kept"]
