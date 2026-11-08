import json
from datetime import timedelta

from jobboard.join import join
from jobboard.profiles import ashby_profile, refresh
from test_join import JOB, T0, inputs, shard


def test_refresh_fetches_missing_profiles_and_keeps_failures_out(tmp_path):
    shard(tmp_path / "raw", "1-1", {"ashby:acme": ("complete", 200, [JOB]), "ashby:broken": ("complete", 200, [JOB | {"id": "j9"}])}, T0)
    join(tmp_path / "raw", tmp_path / "data", inputs(tmp_path, "acme", "broken"), "1-1", T0, verify_inputs=False)
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"ashby:fresh": {"name": "Fresh"}}))

    def fake(key):
        if key == "broken":
            raise ValueError("no organization")
        return {"name": "Acme Inc"}

    report = refresh(tmp_path / "data", path, T0, fetchers={"ashby": fake}, sleep=lambda s: None)
    profiles = json.loads(path.read_text())
    assert report == {"wanted": 2, "fetched": 1, "failed": 1, "failures": {"ashby:broken": "ValueError: no organization"}}
    assert profiles["ashby:acme"]["name"] == "Acme Inc" and "ashby:broken" not in profiles and profiles["ashby:fresh"]
    again = refresh(tmp_path / "data", path, T0 + timedelta(days=1), fetchers={"ashby": fake}, sleep=lambda s: None)
    assert again["wanted"] == 1                         # acme is fresh now; broken is retried
    later = refresh(tmp_path / "data", path, T0 + timedelta(days=31), fetchers={"ashby": fake}, sleep=lambda s: None)
    assert later["wanted"] == 2                         # acme's profile is stale after 30 days


def test_ashby_profile_parsing():
    data = {"data": {"organizationFromHostedJobsPageName": {"name": " Acme ", "publicWebsite": "https://acme.io",
            "theme": {"logoSquareImageUrl": "javascript:alert(1)", "logoWordmarkImageUrl": "https://cdn/logo.png"}}}}
    # an unsafe square logo is skipped and the wordmark used instead
    assert ashby_profile("acme", fetch=lambda request: data) == {
        "name": "Acme", "website": "https://acme.io", "logoUrl": "https://cdn/logo.png"}
