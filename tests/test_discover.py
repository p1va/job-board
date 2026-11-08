from urllib.error import HTTPError

from jobboard.discover import discover


def test_only_new_live_supported_boards_are_added():
    rows = [dict(ats="ashby", board_key="known-co", captures="9"),
            dict(ats="ashby", board_key="new-co", captures="5"),
            dict(ats="greenhouse", board_key="dead-co", captures="3"),
            dict(ats="lever", board_key="leverco", captures="7"),
            dict(ats="ashby", board_key="New-Co", captures="1")]          # same board, other case

    def get(url):
        if "dead-co" in url:
            raise HTTPError(url, 404, "not found", {}, None)
        return {"jobs": [{"id": 1}, {"id": 2}]}

    out, report = discover(rows, {("ashby", "known-co")}, get)
    assert [(r["provider"], r["key"], r["bet_checked"]) for r in out] == [("ashby", "new-co", "jobs=2")]
    assert report["not_added"] == {"greenhouse HTTP 404": 1}
    assert report["next_providers"] == {"lever": 1}
    assert report["found"] == {"ashby": 3, "greenhouse": 1, "lever": 1}
