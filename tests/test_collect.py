import json
from pathlib import Path

import pyarrow.parquet as pq

from jobboard.collect import collect, selections
from jobboard.contract import Kind, Manifest, Status
from jobboard.retrievers import Selection


def test_mixed_shard_writes_a_valid_artifact_and_unchanged_payload(tmp_path):
    from urllib.error import HTTPError

    def get(url):
        if '/dead?' in url:
            raise HTTPError(url, 404, 'gone', {}, None)
        if '/empty?' in url:
            return {'jobs': []}
        return {'jobs': [{'id': 'j1', 'title': 'Software Engineer', 'salary': {'min': 100}, 'extra': 'kept'}]}

    selected = [Selection('ashby:' + k, Kind.BOARD, k) for k in ('acme', 'empty', 'dead')]
    manifest, report = collect('ashby', selected, get, 'r', '0-of-1', tmp_path)
    loaded = Manifest.from_json((tmp_path / 'manifest.json').read_text())
    table = pq.read_table(tmp_path / 'jobs.parquet')
    assert loaded == manifest and loaded.problems(table) == []
    assert [s.status for s in loaded.scopes] == [Status.COMPLETE, Status.EMPTY, Status.FAILED]
    assert report['jobs'] == 1 and json.loads(table['payload'][0].as_py())['extra'] == 'kept'


def test_retry_replaces_staging_without_leaving_previous_rows(tmp_path):
    selected = [Selection('ashby:acme', Kind.BOARD, 'acme')]
    collect('ashby', selected, lambda u: {'jobs': [{'id': 'j', 'title': 'Engineer'}]}, 'r', '0', tmp_path)
    collect('ashby', selected, lambda u: {'jobs': []}, 'r', '0', tmp_path)
    table = pq.read_table(tmp_path / 'jobs.parquet')
    manifest = Manifest.from_json((tmp_path / 'manifest.json').read_text())
    assert table.num_rows == 0 and manifest.problems(table) == []
    assert not list(tmp_path.glob('*.tmp'))


def test_company_inputs_are_deterministic_and_workable_is_query_based(tmp_path):
    (tmp_path / 'b.csv').write_text('provider,key\ngreenhouse,n26\nashby,n8n\nworkable,uuid\n')
    (tmp_path / 'a.csv').write_text('provider,key\nashby,n8n\nashby,acme\n')
    selected = selections('ashby', tmp_path)
    assert [s.board for s in selected] == ['acme', 'n8n']
    assert selected[::2] + selected[1::2] == selected
    assert [s.scope_id for s in selections('workable', tmp_path, ['DE'], 'exclude')] == ['workable:search:country=DE']


def test_invalid_retry_cannot_leave_an_old_completion_marker(tmp_path, monkeypatch):
    import importlib
    import pytest
    from jobboard.retrievers import Result
    from jobboard.contract import Scope

    selected = [Selection('ashby:acme', Kind.BOARD, 'acme')]
    collect('ashby', selected, lambda u: {'jobs': []}, 'r', '0', tmp_path)
    assert (tmp_path / 'manifest.json').exists()
    module = importlib.import_module('jobboard.collect')
    monkeypatch.setattr(module, 'retrieve', lambda *a, **k:
                        Result(Scope('ashby:acme', Kind.BOARD, Status.EMPTY, 503, 0, 0)))
    with pytest.raises(ValueError):
        collect('ashby', selected, lambda u: None, 'r', '0', tmp_path)
    assert not (tmp_path / 'manifest.json').exists()
