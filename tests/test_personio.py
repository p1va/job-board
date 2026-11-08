"""Personio board completeness, optional translation and public board integration."""
import copy
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

import pyarrow.parquet as pq
import pytest

from jobboard.collect import collect, plan, write_summary
from jobboard.contract import Kind, Status
from jobboard.discover import discover
from jobboard.join import join
from jobboard.normalize import normalize
from jobboard.personio import board_slug, parse_feed
from jobboard.registry import parse_board
from jobboard.render import render
from jobboard.retrievers import Selection, retrieve
from test_http import Response, client

XML = (Path(__file__).parent / "fixtures/personio.xml").read_bytes()
NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)


def replay(*events):
    events, calls = iter(events), []
    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["parse"] is parse_feed and kwargs["accept"] == "application/xml"
        event = next(events)
        if isinstance(event, Exception):
            raise event
        return copy.deepcopy(event)
    return get, calls


def selection():
    return Selection("personio:example-gmbh", Kind.BOARD, "example-gmbh")


@pytest.mark.parametrize("bad", ["", "-bad", "bad-", "a.b", "a/b", "a@b", "a_b", "a:80", "a" * 64])
def test_only_a_dns_label_can_become_a_personio_host(bad):
    with pytest.raises(ValueError):
        board_slug(bad)
    assert parse_board("personio:" + bad) is None


@pytest.mark.parametrize("value", ["https://ACME.jobs.personio.com/job/101", "acme.jobs.personio.de", "personio:ACME"])
def test_urls_and_prefixed_keys_use_one_canonical_board(value):
    assert parse_board(value) == ("personio", "acme")


@pytest.mark.parametrize("body", [b"<html>Error</html>", b"<workzag-jobs>",
    b"<workzag-jobs><error>Unavailable</error></workzag-jobs>",
    b"<workzag-jobs><position><name>Engineer</name></position></workzag-jobs>",
    b"<not-a-feed/>"])
def test_an_error_or_malformed_feed_is_never_an_empty_board(body):
    with pytest.raises(ValueError):
        parse_feed(body)


def test_xml_fields_and_duplicate_ids():
    jobs = parse_feed(XML)["jobs"]
    assert jobs[0]["additionalOffices"] == ["London", "Remote - Europe"]
    assert jobs[0]["salaryInformation"]["currencyCode"] == "EUR"
    assert jobs[0]["yearsOfExperience"] == "5-7" and jobs[0]["keywords"] == "Python,SQL"
    assert jobs[1]["jobDescriptions"] == []
    with pytest.raises(ValueError, match="duplicate"):
        parse_feed(XML.replace(b"<id>102</id>", b"<id>101</id>"))


def test_xml_uses_the_same_gzip_and_bounded_retry_transport():
    c, calls, clock = client(Response(b"<workzag-jobs>"),
        Response(gzip.compress(XML), **{"Content-Encoding": "gzip"}))
    assert len(c.get("https://example.jobs.personio.de/xml", parse=parse_feed, accept="application/xml")["jobs"]) == 2
    assert len(calls) == 2 and clock.waits == [2]
    assert calls[0].get_header("Accept-encoding") == "gzip" and calls[0].get_header("Accept") == "application/xml"


def test_english_only_fills_missing_sections_and_never_changes_base_membership():
    base = parse_feed(XML)
    english = copy.deepcopy(base)
    for job in english["jobs"]:
        job.update(name="Translated name", office="New York", jobDescriptions=[{"name": "Role", "value": "English body"}])
    english["jobs"].append(english["jobs"][0] | {"id": "999"})
    get, calls = replay(base, english)
    result = retrieve("personio", selection(), get, "run")
    jobs = [json.loads(r["payload"]) for r in result.rows]
    assert calls == ["https://example-gmbh.jobs.personio.de/xml", "https://example-gmbh.jobs.personio.de/xml?language=en"]
    assert result.scope.status == Status.COMPLETE and result.scope.pages == 2 and result.scope.jobs == 2
    assert jobs[0] == base["jobs"][0]
    assert jobs[1] == base["jobs"][1] | {"jobDescriptions": english["jobs"][1]["jobDescriptions"]}
    assert {r["native_id"] for r in result.rows} == {"101", "102"}
    assert result.totals["descriptions_filled"] == 1 and result.totals["descriptions_missing"] == 0


def test_optional_language_failure_keeps_the_authoritative_base_board():
    get, _ = replay(parse_feed(XML), HTTPError("u", 429, "deferred", {"Retry-After": "120"}, None))
    result = retrieve("personio", selection(), get, "run")
    assert result.scope.status == Status.COMPLETE and result.scope.http_status == 200 and result.scope.jobs == 2
    assert result.totals["descriptions_missing"] == 1 and "429" in result.totals["description_error"]


def test_empty_board_needs_no_language_fetch_and_404_is_unresolved():
    get, calls = replay(parse_feed(b"<workzag-jobs/>"))
    result = retrieve("personio", selection(), get, "run")
    assert result.scope.status == Status.EMPTY and not result.rows and len(calls) == 1
    get, _ = replay(HTTPError("u", 404, "gone", {}, None))
    result = retrieve("personio", selection(), get, "run")
    assert result.scope.status == Status.FAILED and result.scope.http_status == 404


def test_a_nameless_position_keeps_healthy_positives_but_cannot_resolve_absence():
    data = parse_feed(XML.replace(b"<name>Data Scientist</name>", b"<name/>"))
    assert len(data["jobs"]) == 1 and data["invalid_positions"][0]["id"] == "102"
    get, _ = replay(data)
    result = retrieve("personio", selection(), get, "run")
    assert result.scope.status == Status.PARTIAL and result.scope.jobs == 1 and result.scope.error
    assert result.totals["invalid_positions"] == 1 and result.rows[0]["native_id"] == "101"
    get, _ = replay(data)
    boards, report = discover([dict(ats="personio", board_key="example-gmbh", captures="1")], set(), get)
    assert len(boards) == 1 and report["invalid_positions_on_added"] == {"personio:example-gmbh": 1}
    empty_invalid = parse_feed(b"<workzag-jobs><position><id>1</id><name/></position></workzag-jobs>")
    get, _ = replay(empty_invalid)
    assert retrieve("personio", selection(), get, "run").scope.status == Status.FAILED


def test_a_partial_feed_cannot_close_a_previously_named_job(tmp_path):
    inputs = tmp_path / "companies"
    inputs.mkdir()
    (inputs / "seed.csv").write_text("provider,key\npersonio,example-gmbh\n")
    for run_id, data in [("100-1", parse_feed(XML)),
                         ("101-1", parse_feed(XML.replace(b"<name>Data Scientist</name>", b"<name/>")))]:
        get, _ = replay(data, data)
        raw = tmp_path / run_id
        collect("personio", plan("personio", inputs), get, run_id, "0-of-1", raw / "personio/0-of-1")
        joined = join(raw, tmp_path / "data", inputs, run_id, NOW)
        assert joined["statuses"] == {"open": 2}
    assert joined["scopes"] == {"partial": 1}


@pytest.mark.parametrize("period,expected", [("yearly", "year"), ("monthly", "month"), ("hourly", "hour")])
def test_normalisation_keeps_all_offices_and_salary_units(period, expected):
    job = parse_feed(XML)["jobs"][0]
    job["salaryInformation"]["type"] = period
    row = normalize("personio", "example-gmbh", "101", json.dumps(job), "run", NOW)
    assert row["kept"] and row["company_name"] == "Example GmbH"
    assert row["locations"] == ["Berlin", "London", "Remote - Europe"]
    assert row["salary_min"] == 70000 and row["salary_max"] == 90000
    assert row["salary_currency"] == "EUR" and row["salary_period"] == expected
    assert row["published_at"].isoformat() == "2026-11-01T09:00:00+00:00"
    assert row["employment"] == "FullTime" and "• Build Python services.\n• Work with SQL." in row["description_text"]
    assert row["url"] == "https://example-gmbh.jobs.personio.de/job/101"


def test_occupation_fallback_is_readable_and_other_is_unknown():
    job = parse_feed(XML)["jobs"][1]
    for category, expected in [("other", None), ("it_software", "IT / software"), ("engineering", "Engineering")]:
        row = normalize("personio", "example-gmbh", "102", json.dumps(job | {"occupationCategory": category}), "r", NOW)
        assert row["department"] == expected


def test_personio_manifest_reaches_join_and_the_board_without_a_title_field(tmp_path):
    inputs = tmp_path / "companies"
    inputs.mkdir()
    (inputs / "personio.csv").write_text("provider,key\npersonio,EXAMPLE-GMBH\n")
    get, _ = replay(parse_feed(XML), parse_feed(XML))
    raw = tmp_path / "raw"
    manifest, report = collect("personio", plan("personio", inputs), get, "100-1", "0-of-1", raw / "personio/0-of-1")
    assert manifest.problems(pq.read_table(raw / "personio/0-of-1/jobs.parquet")) == []
    assert "1 jobs still have no description" in write_summary(manifest, report)
    joined = join(raw, tmp_path / "data", inputs, "100-1", NOW)
    assert not joined["shards_invalid"] and joined["statuses"] == {"open": 2}
    board = render(tmp_path / "data", tmp_path / "site", NOW)
    jobs = json.loads((tmp_path / "site" / board["data_file"]).read_text())["jobs"]
    assert len(jobs) == 2 and all(j["source"] == "personio" for j in jobs)
    assert {j["companyName"] for j in jobs} == {"Example GmbH"}
    assert pq.read_table(tmp_path / "data/boards.parquet")["status"].to_pylist() == ["live"]


def test_discovery_checks_xml_but_never_interpolates_an_invalid_host():
    rows = [dict(ats="personio", board_key="example-gmbh", captures="3"),
            dict(ats="personio", board_key="evil@host", captures="1")]
    get, calls = replay(parse_feed(XML))
    boards, report = discover(rows, set(), get)
    assert len(boards) == 1 and boards[0]["provider"] == "personio" and len(calls) == 1
    assert report["not_added"] == {"personio ValueError": 1}


def test_mixed_subcompanies_have_one_stable_board_label(tmp_path):
    inputs = tmp_path / "companies"
    inputs.mkdir()
    (inputs / "seed.csv").write_text("provider,key\npersonio,example-gmbh\n")
    data = parse_feed(XML)
    data["jobs"][0]["subcompany"] = "Alpha GmbH"
    data["jobs"][1]["subcompany"] = "Beta GmbH"
    get, _ = replay(data, data)
    collect("personio", plan("personio", inputs), get, "100-1", "0-of-1", tmp_path / "raw/personio/0-of-1")
    join(tmp_path / "raw", tmp_path / "data", inputs, "100-1", NOW)
    for profiles, expected in [({}, "Alpha GmbH"), ({"personio:example-gmbh": {"name": "Brand"}}, "Brand")]:
        result = render(tmp_path / "data", tmp_path / "site", NOW, companies=profiles)
        jobs = json.loads((tmp_path / "site" / result["data_file"]).read_text())["jobs"]
        assert {j["companyName"] for j in jobs} == {expected}
