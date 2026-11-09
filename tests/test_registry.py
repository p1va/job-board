import json

from jobboard.registry import import_bet, parse_board


def test_parse_board_forms():
    assert parse_board("n8n") == ("ashby", "n8n")
    assert parse_board("https://jobs.ashbyhq.com/n8n/abc") == ("ashby", "n8n")
    assert parse_board("jobs.ashbyhq.com/n8n") == ("ashby", "n8n")
    assert parse_board("greenhouse:n26") == ("greenhouse", "n26")
    assert parse_board("https://job-boards.eu.greenhouse.io/n26") == ("greenhouse", "n26")
    assert parse_board("# comment") is None and parse_board("  ") is None
    assert parse_board("lever:foo") is None                   # region is required
    assert parse_board("jobs.eu.lever.co/acme") == ("lever", "eu:acme")
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


LIST_HEADER = "name,website,country,city,why,source_url,careers_url,ats,board_key,checked\n"


def test_import_lists_scans_supported_boards_and_reports_the_rest(tmp_path):
    from jobboard.registry import import_lists, memberships
    (tmp_path / "ai-labs.csv").write_text(LIST_HEADER +
        "Anthropic,https://anthropic.com,US,SF,lab,https://x,https://y,greenhouse,anthropic,2026-11-08\n"
        "Hugging Face,https://hf.co,US,NYC,lab,https://x,https://y,workable,huggingface,2026-11-08\n"
        "DeepMind,https://deepmind.google,GB,London,lab,https://x,https://y,other,,2026-11-08\n")
    (tmp_path / "uk-hottest.csv").write_text(LIST_HEADER +
        "Anthropic,https://anthropic.com,GB,London,lab,https://x,https://y,greenhouse,anthropic,2026-11-08\n")
    boards, unscanned = import_lists(tmp_path)
    assert [(b["provider"], b["key"], b["origins"]) for b in boards] == [("greenhouse", "anthropic", "list:ai-labs|list:uk-hottest")]
    assert {u["name"]: u["reason"].split(";")[0] for u in unscanned} == {"Hugging Face": "Workable account slug", "DeepMind": "no board found"}
    assert memberships(tmp_path)["greenhouse:anthropic"] == ["AI labs", "UK hottest"]


def test_list_with_wrong_columns_is_rejected(tmp_path):
    import pytest
    from jobboard.registry import read_lists
    (tmp_path / "bad.csv").write_text("name,ats\nX,ashby\n")
    with pytest.raises(ValueError, match="columns"):
        read_lists(tmp_path)
