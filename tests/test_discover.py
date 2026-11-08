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
        return {"jobs": [{"id": 1}, {"id": 2}], "apiVersion": "1"}

    out, report = discover(rows, {("ashby", "known-co")}, get)
    assert [(r["provider"], r["key"]) for r in out] == [("ashby", "new-co")] and report["jobs_on_added"] == {"ashby:new-co": 2}
    assert report["not_added"] == {"greenhouse HTTP 404": 1}
    assert report["next_providers"] == {"lever": 1}
    assert report["found"] == {"ashby": 3, "greenhouse": 1, "lever": 1}


def test_error_bodies_and_skipped_boards_are_not_added():
    from jobboard.discover import valid_board
    assert not valid_board("ashby", {"error": "board not found"})
    assert not valid_board("greenhouse", {"jobs": [{"id": 1}], "meta": {"total": 5}})      # truncated
    assert valid_board("greenhouse", {"jobs": [], "meta": {"total": 0}})                  # real empty board
    rows = [dict(ats="greenhouse", board_key="repost-co", captures="2"), dict(ats="ashby", board_key="err-co", captures="1")]
    out, report = discover(rows, set(), lambda url: {"error": "nope"}, {("greenhouse", "repost-co")})
    assert out == [] and report["not_added"] == {"greenhouse skipped (reposts another board)": 1, "ashby invalid response": 1}


def test_reimport_keeps_earlier_discoveries(tmp_path, monkeypatch):
    import csv as _csv
    import jobboard.discover as d
    companies = tmp_path / "companies"
    companies.mkdir()
    (companies / "seed.csv").write_text("provider,key\nashby,known-co\n")
    out = companies / "athena.csv"
    results = tmp_path / "r.csv"
    monkeypatch.setattr(d, "Client", lambda interval: type("C", (), {"get": staticmethod(lambda url: {"jobs": [], "apiVersion": "1"})})())
    results.write_text("ats,board_key,captures\nashby,first-co,3\n")
    d.main([str(results), "--inputs", str(companies), "--out", str(out), "--report", str(tmp_path / "rep.json"), "--skip", str(tmp_path / "none.csv")])
    results.write_text("ats,board_key,captures\nashby,second-co,3\n")
    d.main([str(results), "--inputs", str(companies), "--out", str(out), "--report", str(tmp_path / "rep.json"), "--skip", str(tmp_path / "none.csv")])
    keys = [r["key"] for r in _csv.DictReader(out.open())]
    assert keys == ["first-co", "second-co"]
