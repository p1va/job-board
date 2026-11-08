import json

from jobboard.registry import import_bet, parse_board


def test_parse_board_forms():
    assert parse_board("n8n") == ("ashby", "n8n")
    assert parse_board("https://jobs.ashbyhq.com/n8n/abc") == ("ashby", "n8n")
    assert parse_board("jobs.ashbyhq.com/n8n") == ("ashby", "n8n")
    assert parse_board("greenhouse:n26") == ("greenhouse", "n26")
    assert parse_board("https://job-boards.eu.greenhouse.io/n26") == ("greenhouse", "n26")
    assert parse_board("# comment") is None and parse_board("  ") is None
    assert parse_board("lever:foo") is None                   # not a provider we read yet
    assert parse_board("jobs.eu.lever.co/acme") is None       # unsupported host, not an Ashby slug
    assert parse_board("https://boards.greenhouse.io/embed/job_board?for=acme") == ("greenhouse", "acme")
    assert parse_board("https://boards.greenhouse.io/embed/job_board") is None


def test_import_merges_origins_and_keeps_workable_names():
    companies = "# Bet's list\nn8n\ngreenhouse:n26\n"
    boards = json.dumps({"n8n": {"de": True, "checked": "2026-11-01"}, "greenhouse:n26": {"de": False},
                         "workable:u1": {"de": True},
                         "_meta": {"completed_crawls": ["CC-MAIN-2026-39", "CC-MAIN-2026-43"]}})
    workable = json.dumps({"companies": {"workable:u1": {"name": "Acme GmbH", "website": "https://acme.de"}}})
    rows = {(r["provider"], r["key"]): r for r in import_bet(companies, boards, workable)}
    assert set(rows) == {("ashby", "n8n"), ("greenhouse", "n26"), ("workable", "u1")}
    assert rows["ashby", "n8n"]["origins"] == "bet-companies|bet-registry"
    assert (rows["ashby", "n8n"]["bet_de"], rows["ashby", "n8n"]["bet_checked"]) == ("true", "2026-11-01")
    assert rows["greenhouse", "n26"]["bet_de"] == "false"
    assert (rows["workable", "u1"]["name"], rows["workable", "u1"]["origins"]) == ("Acme GmbH", "bet-registry|bet-workable-search")
