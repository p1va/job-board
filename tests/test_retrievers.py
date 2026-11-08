import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from jobboard.contract import Kind, Status
from jobboard.retrievers import Selection, retrieve, workable_selections

FIXTURES = Path(__file__).parent / 'fixtures'


def jobs(provider):
    return json.loads((FIXTURES / f'{provider}.json').read_text())


def replay(*events):
    events, calls = iter(events), []

    def get(url):
        calls.append(url)
        event = next(events)
        if isinstance(event, Exception):
            raise event
        return event

    return get, calls


@pytest.mark.parametrize('provider,key,param', [('ashby', 'n8n', 'includeCompensation'),
                                               ('greenhouse', 'n26', 'content')])
def test_full_board_keeps_provider_payload_unchanged(provider, key, param):
    original = jobs(provider)
    get, calls = replay({'jobs': original, 'meta': {'total': 2}})
    result = retrieve(provider, Selection(f'{provider}:{key}', Kind.BOARD, key), get, 'r')
    assert result.scope.status == Status.COMPLETE and result.scope.jobs == 2
    assert parse_qs(urlsplit(calls[0]).query)[param] == ['true']
    assert [json.loads(r['payload']) for r in result.rows] == original
    assert result.rows[0]['native_id'] == str(original[0]['id'])


def test_200_empty_is_authoritative_but_failed_request_is_not():
    s = Selection('ashby:acme', Kind.BOARD, 'acme')
    get, _ = replay({'jobs': []})
    result = retrieve('ashby', s, get, 'r')
    assert result.scope.status == Status.EMPTY and result.scope.pages == 1
    get, _ = replay(HTTPError('u', 404, 'gone', {}, None))
    result = retrieve('ashby', s, get, 'r')
    assert result.scope.status == Status.FAILED and result.scope.http_status == 404 and not result.rows


@pytest.mark.parametrize('response', [{}, {'jobs': None}, {'jobs': [None]}, {'jobs': [{}]}])
def test_malformed_success_is_never_empty(response):
    get, _ = replay(response)
    result = retrieve('ashby', Selection('ashby:acme', Kind.BOARD, 'acme'), get, 'r')
    assert result.scope.status == Status.FAILED and result.scope.error


def test_greenhouse_missing_content_or_count_mismatch_is_unresolved():
    original = jobs('greenhouse')
    for response in [{'jobs': [{k: v for k, v in original[0].items() if k != 'content'}]},
                     {'jobs': original, 'meta': {'total': 3}}]:
        get, _ = replay(response)
        result = retrieve('greenhouse', Selection('greenhouse:n26', Kind.BOARD, 'n26'), get, 'r')
        assert result.scope.status in (Status.FAILED, Status.PARTIAL) and result.scope.error


def test_workable_cursors_and_overlapping_queries_preserve_native_keys():
    original = jobs('workable')
    get, calls = replay({'jobs': original[:1], 'totalSize': 2, 'nextPageToken': 'next'},
                        {'jobs': original[1:], 'totalSize': 2})
    s = workable_selections(['GB'], 'exclude')[0]
    result = retrieve('workable', s, get, 'r')
    assert result.scope.status == Status.COMPLETE and result.scope.kind == Kind.QUERY
    assert result.scope.pages == 2 and result.scope.jobs == 2
    assert parse_qs(urlsplit(calls[0]).query) == {'location': ['United Kingdom']}
    assert parse_qs(urlsplit(calls[1]).query)['pageToken'] == ['next']
    assert result.rows[0]['board'] == original[0]['company']['id']
    assert json.loads(result.rows[0]['payload']) == original[0]


@pytest.mark.parametrize('second', [HTTPError('u', 429, 'slow', {}, None),
    {'jobs': [], 'totalSize': 2},
    {'jobs': [], 'totalSize': 2, 'nextPageToken': 'third'},
    {'jobs': [], 'totalSize': 2, 'nextPageToken': 'next'},
    {'jobs': [{}], 'totalSize': 2}])
def test_workable_failed_or_incomplete_walk_keeps_positives_as_partial(second):
    first = {'jobs': jobs('workable')[:1], 'totalSize': 2, 'nextPageToken': 'next'}
    get, _ = replay(first, second)
    result = retrieve('workable', workable_selections([], 'only')[0], get, 'r')
    assert result.scope.status == Status.PARTIAL and result.scope.jobs == 1
    assert result.scope.error and len(result.rows) == 1


def test_workable_page_limit_never_claims_completeness():
    get, _ = replay({'jobs': jobs('workable')[:1], 'totalSize': 2, 'nextPageToken': 'next'})
    result = retrieve('workable', workable_selections([], 'only')[0], get, 'r', max_pages=1)
    assert result.scope.status == Status.PARTIAL and 'page limit' in result.scope.error


def test_workable_invalid_false_cursor_cannot_end_a_walk():
    get, _ = replay({'jobs': jobs('workable'), 'totalSize': 2, 'nextPageToken': False})
    result = retrieve('workable', workable_selections([], 'only')[0], get, 'r')
    assert result.scope.status == Status.PARTIAL and result.scope.jobs == 2


def test_all_europe_and_remote_have_explicit_query_memberships():
    selected = workable_selections()
    assert len({s.scope_id for s in selected}) == len(selected)
    assert {'workable:search:country=DE', 'workable:search:country=GB', 'workable:search:country=IT',
            'workable:search:country=FR', 'workable:search:remote'} <= {s.scope_id for s in selected}
    assert selected[-1].query == {'workplace': 'remote'}
    with pytest.raises(ValueError):
        workable_selections(['US'])


def test_deadline_preserves_a_partial_walk_and_stops_unstarted_scopes():
    now, calls = [0], []

    def get(url):
        calls.append(url)
        now[0] = 10
        return {'jobs': jobs('workable')[:1], 'totalSize': 2, 'nextPageToken': 'next'}

    s = workable_selections([], 'only')[0]
    result = retrieve('workable', s, get, 'r', deadline=1, clock=lambda: now[0])
    assert result.scope.status == Status.PARTIAL and result.scope.jobs == 1 and result.scope.pages == 1
    assert 'deadline' in result.scope.error.lower() and len(calls) == 1
    result = retrieve('workable', s, get, 'r', deadline=1, clock=lambda: now[0])
    assert result.scope.status == Status.FAILED and not result.rows and result.scope.pages == 0
    assert len(calls) == 1


def test_falling_totals_are_reported_without_claiming_a_complete_snapshot():
    original = jobs('workable')
    get, _ = replay({'jobs': original[:1], 'totalSize': 3, 'nextPageToken': 'next'},
                    {'jobs': original[1:], 'totalSize': 2})
    result = retrieve('workable', workable_selections([], 'only')[0], get, 'r')
    assert result.totals == {'first': 3, 'last': 2, 'maximum': 3}
    assert result.scope.status == Status.PARTIAL
