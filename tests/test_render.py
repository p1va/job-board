import json
from datetime import datetime, timezone

import pytest

from jobboard.join import join
from jobboard.render import render, salary, where_pairs
from test_join import JOB, T0, inputs, shard


def built(tmp_path, jobs):
    shard(tmp_path / "raw", "1-1", {"ashby:acme": ("complete", 200, jobs)}, T0)
    join(tmp_path / "raw", tmp_path / "data", inputs(tmp_path, "acme"), "1-1", T0, verify_inputs=False)
    return render(tmp_path / "data", tmp_path / "site", T0)


def test_site_has_hashed_assets_and_light_job_list(tmp_path):
    report = built(tmp_path, [JOB, JOB | {"id": "j2", "title": "Data Scientist", "location": "Remote - Europe"}])
    site = tmp_path / "site"
    page = (site / "index.html").read_text()
    assert "{{" not in page and report["data_file"] in page and (site / ".nojekyll").exists()
    data = json.loads((site / report["data_file"]).read_text())
    assert len(data["jobs"]) == 2 and all("description" not in j for j in data["jobs"])
    by_title = {j["title"]: j for j in data["jobs"]}
    assert by_title["Senior Backend Engineer"]["regions"] == ["Germany"]
    assert by_title["Data Scientist"]["regions"] == ["Remote: Europe"] and by_title["Data Scientist"]["family"] == "data_science"
    job = data["jobs"][0]
    texts = json.loads((site / data["descriptions"] / f"{job['d']}.json").read_text())
    assert texts[job["id"]]                                   # the description is where the job points


def test_open_job_without_a_version_refuses_to_render(tmp_path):
    built(tmp_path, [JOB])
    for f in (tmp_path / "data" / "versions").rglob("*.parquet"):
        f.unlink()
    with pytest.raises(ValueError, match="no version"):
        render(tmp_path / "data", tmp_path / "site", T0)


@pytest.mark.parametrize("where,expected", [
    (["DE|onsite", "FR|onsite"], [["Germany", "Office"], ["Rest of Europe", "Office"]]),
    (["US|remote", "worldwide|remote"], [["Remote: worldwide", "Remote"]]),
    (["GB|remote"], [["United Kingdom", "Remote"]]),
    (["unstated|remote"], [["Remote: region not stated", "Remote"]]),
    (["US|remote"], []),
    (["europe|remote"], [["Remote: Europe", "Remote"]]),
    # London is an office (style unknown), only the US location is remote: no "United Kingdom + Remote" pair
    (["GB|unknown", "US|remote"], [["United Kingdom", "Not stated"]]),
])
def test_where_pairs(where, expected):
    assert where_pairs(dict(where=where)) == expected


def test_salary_text():
    base = dict(salary_raw=None, salary_min=80000.0, salary_max=100000.0, salary_currency="EUR", salary_period="year")
    assert salary(base) == "€80K – €100K"
    assert salary(base | {"salary_raw": "€80K – €100K + equity"}) == "€80K – €100K + equity"
    assert salary(base | {"salary_min": None, "salary_max": None}) is None


def test_explicit_remote_with_a_named_region_reaches_the_default_view(tmp_path):
    report = built(tmp_path, [JOB | {"id": "j3", "title": "Data Scientist", "location": "Europe", "workplaceType": "Remote"}])
    data = json.loads((tmp_path / "site" / report["data_file"]).read_text())
    assert data["jobs"][0]["where"] == [["Remote: Europe", "Remote"]]
