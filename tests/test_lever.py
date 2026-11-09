"""Regional Lever identity, full-array completeness, locations and board integration."""
import copy
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

import pyarrow.parquet as pq
import pytest

from jobboard.collect import collect, plan
from jobboard.contract import Kind, Status
from jobboard.discover import discover
from jobboard.join import join
from jobboard.lever import board_key, candidate_keys, parse_postings, postings_url
from jobboard.normalize import normalize
from jobboard.registry import import_lists, memberships, parse_board
from jobboard.render import company_name, load_companies, render, salary
from jobboard.retrievers import Selection, retrieve
from test_http import Response, client

BODY = (Path(__file__).parent / "fixtures/lever.json").read_bytes()
JOB = json.loads(BODY)[0]
NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)


def response(jobs):
    return parse_postings(json.dumps(jobs).encode())


def get_result(event):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        assert kwargs == {"parse": parse_postings}
        if isinstance(event, Exception):
            raise event
        return copy.deepcopy(event)
    return get, calls


@pytest.mark.parametrize("value,key", [
    ("global:Example", "global:Example"), ("eu:EXAMPLE", "eu:EXAMPLE"),
    ("https://jobs.lever.co/Example/job-id/apply", "global:Example"),
    ("jobs.eu.lever.co/example", "eu:example"),
])
def test_regional_keys_and_hosted_urls_are_canonical(value, key):
    assert board_key(value) == key
    assert parse_board(value if "/" in value else "lever:" + value) == ("lever", key)


@pytest.mark.parametrize("bad", ["example", "us:example", "eu:", "eu:a/b", "eu:a@b", "eu:a?b", "eu:" + "x" * 101])
def test_ambiguous_or_invalid_board_inputs_are_rejected(bad):
    with pytest.raises(ValueError):
        board_key(bad)
    assert parse_board("lever:" + bad) is None


def test_unknown_region_means_two_discovery_candidates_never_a_fallback():
    assert candidate_keys("Example") == ["global:Example", "eu:Example"]
    assert candidate_keys("eu:example") == ["eu:example"]
    assert postings_url("global:example") == "https://api.lever.co/v0/postings/example?mode=json"
    assert postings_url("eu:example") == "https://api.eu.lever.co/v0/postings/example?mode=json"


@pytest.mark.parametrize("bad", [b"{}", b'{"error":"missing"}', b"[", b"[null]", b"[{}]",
    b'[{"id":"j","text":false}]', b'[{"id":true,"text":"Engineer"}]'])
def test_malformed_arrays_never_become_empty_boards(bad):
    with pytest.raises(ValueError):
        parse_postings(bad)
    with pytest.raises(ValueError, match="duplicate"):
        response([JOB, JOB])


def test_array_transport_uses_existing_gzip_and_bounded_retries():
    c, calls, clock = client(Response(b"["), Response(gzip.compress(BODY), **{"Content-Encoding": "gzip"}))
    get = c.get
    result = retrieve("lever", Selection("lever:eu:example", Kind.BOARD, "eu:example"), get, "run")
    assert result.scope.status == Status.COMPLETE and result.scope.pages == 1
    assert json.loads(result.rows[0]["payload"]) == JOB
    assert len(calls) == 2 and clock.waits == [2]


def test_empty_and_failed_board_are_distinct_and_never_switch_region():
    for event, expected in [(response([]), Status.EMPTY),
                            (HTTPError("u", 404, "gone", {}, None), Status.FAILED)]:
        get, calls = get_result(event)
        result = retrieve("lever", Selection("lever:eu:example", Kind.BOARD, "eu:example"), get, "run")
        assert result.scope.status == expected and len(calls) == 1
        assert calls == [postings_url("eu:example")]


def test_duplicate_or_nameless_postings_cannot_resolve_absence():
    # Even an injected test getter that bypasses the transport parser stays conservative.
    for jobs in ([JOB, JOB], [JOB, JOB | {"id": "other", "text": ""}]):
        get, _ = get_result({"jobs": jobs})
        result = retrieve("lever", Selection("lever:global:example", Kind.BOARD, "global:example"), get, "run")
        assert result.scope.status == Status.FAILED and not result.rows and result.scope.error


def test_normalization_preserves_description_sections_and_primary_country_only():
    row = normalize("lever", "eu:example", JOB["id"], json.dumps(JOB), "run", NOW)
    assert row["kept"] and row["title"] == JOB["text"]
    assert set(row["where"]) == {"DE|hybrid", "GB|hybrid"}
    assert row["published_at"].isoformat() == "2026-11-01T09:00:00+00:00"
    assert row["department"] == "Engineering" and row["employment"] == "Full-time"
    assert row["salary_min"] == 70000 and row["salary_max"] == 90000
    assert row["salary_currency"] == "EUR" and row["salary_period"] == "year"
    assert salary(row) == "€70K – €90K"  # "Plus equity" must not hide the structured range
    text = row["description_text"]
    assert text.count("Opening.") == text.count("Body.") == 1
    assert "• Build Python services.\n• Work with SQL." in text
    assert "Benefits." in text and "Plus equity." in text and "<li>" not in text


def test_plain_only_descriptions_and_unstructured_salary_are_kept():
    job = JOB | {"description": None, "descriptionPlain": "Build Python software.", "salaryRange": None}
    row = normalize("lever", "eu:example", JOB["id"], json.dumps(job), "run", NOW)
    assert "Build Python software." in row["description_text"]
    assert row["salary_raw"] == "Plus equity."


@pytest.mark.parametrize("mode,expected", [("onsite", "onsite"), ("on-site", "onsite"),
                                          ("remote", "remote"), ("unspecified", "unknown")])
def test_workplace_and_salary_units_remain_provider_facts(mode, expected):
    job = JOB | {"workplaceType": mode, "salaryRange": {"min": 0, "max": 5000, "currency": "EUR", "interval": "per-month-salary"}}
    row = normalize("lever", "eu:example", JOB["id"], json.dumps(job), "run", NOW)
    assert row["workplace"] == expected
    assert row["salary_period"] == "month" and row["salary_min"] == 0 and row["salary_max"] == 5000


def test_remote_us_only_does_not_become_european_from_the_api_region():
    job = JOB | {"country": "US", "workplaceType": "remote", "categories": {"location": "Remote", "allLocations": ["Remote"]}}
    row = normalize("lever", "eu:example", JOB["id"], json.dumps(job), "run", NOW)
    assert not row["kept"] and row["where"] == ["US|remote"]
    job["categories"] = {"location": "London", "allLocations": ["London", "Berlin"]}
    job["country"] = "CA"
    row = normalize("lever", "global:example", JOB["id"], json.dumps(job), "run", NOW)
    assert row["kept"] and set(row["where"]) == {"CA|remote", "DE|remote"}


def test_discovery_checks_both_regions_even_when_global_is_live_and_remembers_failures():
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return response([JOB])
    rows = [dict(ats="lever", board_key="example", captures="4", api_region="eu", name="Example Co"),
            dict(ats="lever", board_key="example", captures="1")]
    boards, report = discover(rows, set(), get)
    assert {r["key"] for r in boards} == {"global:example", "eu:example"}
    assert len(calls) == 2 and report["live_added"] == {"lever": 2}
    assert all(r["name"] == "Example Co" for r in boards)
    get, calls = get_result(HTTPError("u", 404, "missing", {}, None))
    boards, report = discover(rows, set(), get)
    assert not boards and len(calls) == 2 and len(report["checks"]) == 2


def test_known_and_skipped_regional_boards_do_not_hide_the_other_region():
    rows = [dict(ats="lever", board_key="example", captures="1")]
    for known, skip in [({("lever", "global:example")}, set()), (set(), {("lever", "global:example")})]:
        get, calls = get_result(response([]))
        boards, _ = discover(rows, known, get, skip)
        assert [r["key"] for r in boards] == ["eu:example"]
        assert calls == [postings_url("eu:example")]


def test_case_sensitive_tokens_survive_discovery_planning_and_rendering(tmp_path):
    # /Legend is live and /legend is 404 in the independent live probe.
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        if "/legend?" in url:
            raise HTTPError(url, 404, "wrong case", {}, None)
        return response([JOB])
    rows = [dict(ats="lever", board_key="global:Legend", captures="2", name="Legend"),
            dict(ats="lever", board_key="global:legend", captures="1", name="Wrong Case")]
    boards, report = discover(rows, set(), get)
    assert [(r["key"], r["name"]) for r in boards] == [("global:Legend", "Legend")]
    assert len(calls) == 2 and report["checks"]["lever:global:legend"]["http_status"] == 404
    inputs = tmp_path / "companies"
    inputs.mkdir()
    # Keep both cases in the input to also verify their identities never collapse.
    (inputs / "seed.csv").write_text("provider,key,name\nlever,global:Legend,Legend\nlever,global:legend,Wrong Case\n")
    selected = plan("lever", inputs)
    assert {s.board for s in selected} == {"global:Legend", "global:legend"}
    raw = tmp_path / "raw"
    collect("lever", selected, get, "100-1", "0-of-1", raw / "lever/0-of-1")
    joined = join(raw, tmp_path / "data", inputs, "100-1", NOW)
    assert joined["statuses"] == {"open": 1}
    profiles = load_companies(inputs / "profiles.json", tmp_path / "lists", inputs)
    assert profiles["lever:global:Legend"]["name"] == "Legend"
    assert profiles["lever:global:legend"]["name"] == "Wrong Case"
    result = render(tmp_path / "data", tmp_path / "site", NOW, companies=profiles)
    job = json.loads((tmp_path / "site" / result["data_file"]).read_text())["jobs"][0]
    assert job["companyName"] == "Legend" and job["company"] == "lever:global:Legend"


def test_regional_lists_and_seed_names_use_the_same_identity(tmp_path):
    inputs, lists = tmp_path / "companies", tmp_path / "lists"
    inputs.mkdir()
    lists.mkdir()
    (inputs / "lever.csv").write_text("provider,key,name,website\nlever,eu:example,Seed Name,https://example.test\n")
    (lists / "ai-labs.csv").write_text("name,website,country,city,why,source_url,careers_url,ats,board_key,checked\n"
        "List Name,,DE,Berlin,x,https://s,https://jobs.eu.lever.co/example,lever,example,2026-11-09\n"
        "Unknown,,DE,Berlin,x,https://s,https://company.test/careers,lever,unknown,2026-11-09\n")
    boards, unscanned = import_lists(lists)
    assert [r["key"] for r in boards] == ["eu:example"] and len(unscanned) == 1
    assert "region unknown" in unscanned[0]["reason"]
    assert memberships(lists) == {"lever:eu:example": ["AI labs"]}
    profiles = inputs / "profiles.json"
    assert load_companies(profiles, lists, inputs)["lever:eu:example"] == {"name": "List Name", "website": "https://example.test"}
    profiles.write_text(json.dumps({"lever:eu:example": {"name": "Cached Name"}}))
    assert load_companies(profiles, lists, inputs)["lever:eu:example"]["name"] == "Cached Name"


def test_fallback_name_preserves_the_token_without_the_region():
    for key, expected in [("global:Legend", "Legend"), ("eu:wypoon", "wypoon")]:
        assert company_name(dict(source="lever", board=key, company_name=None), None) == expected


def test_cli_bare_lever_skip_expands_both_regions_and_invalid_skip_is_reported(tmp_path, monkeypatch):
    import jobboard.discover as module
    inputs = tmp_path / "companies"
    inputs.mkdir()
    results = tmp_path / "candidates.csv"
    results.write_text("ats,board_key,captures\nlever,example,1\n")
    skip = tmp_path / "skip.csv"
    skip.write_text("provider,key,reason\nlever,example,exclude both\nlever,evil@host,invalid\n")
    class NoRequests:
        def __init__(self, **kwargs):
            pass
        def get(self, *args, **kwargs):
            raise AssertionError("neither regional board should be fetched")
    monkeypatch.setattr(module, "Client", NoRequests)
    out, report = inputs / "lever.csv", tmp_path / "report.json"
    assert module.main([str(results), "--inputs", str(inputs), "--out", str(out),
                        "--report", str(report), "--skip", str(skip)]) == 0
    saved = json.loads(report.read_text())
    assert saved["not_added"] == {"lever excluded by skip list": 2}
    assert saved["invalid_skip_entries"] == ["lever:evil@host"]


def test_regional_lifecycle_and_board_work_end_to_end(tmp_path):
    inputs = tmp_path / "companies"
    inputs.mkdir()
    (inputs / "lever.csv").write_text("provider,key,name\nlever,global:example,Example Co\nlever,eu:example,Example Co\n")
    selected = plan("lever", inputs)
    for run_id in ("100-1", "101-1", "102-1"):
        def get(url, **kwargs):
            if run_id == "100-1":
                return response([JOB])  # same native ID remains distinct across regions
            if run_id == "101-1" and "api.eu." in url:
                raise HTTPError(url, 404, "gone", {}, None)
            return response([])
        raw = tmp_path / run_id
        manifest, _ = collect("lever", selected, get, run_id, "0-of-1", raw / "lever/0-of-1")
        joined = join(raw, tmp_path / "data", inputs, run_id, NOW)
        assert not joined["shards_invalid"]
        if run_id == "100-1":
            assert joined["statuses"] == {"open": 2}
            board = render(tmp_path / "data", tmp_path / "site", NOW,
                           companies=load_companies(inputs / "profiles.json", tmp_path / "lists", inputs))
            jobs = json.loads((tmp_path / "site" / board["data_file"]).read_text())["jobs"]
            assert len({j["id"] for j in jobs}) == 2 and {j["source"] for j in jobs} == {"lever"}
            assert {j["companyName"] for j in jobs} == {"Example Co"}
            boards = pq.read_table(tmp_path / "data/boards.parquet").to_pylist()
            assert {b["api_region"] for b in boards} == {"global", "eu"}
        elif run_id == "101-1":
            assert joined["statuses"] == {"closed": 1, "open": 1}
        else:
            assert joined["statuses"] == {"closed": 2}
