import pytest

from jobboard.places import in_europe, parse

CASES = [
    # location strings, countries, remote regions, remote?, in Europe?
    (["London, UK"], ("GB",), (), False, True),
    (["Berlin, DE"], ("DE",), (), False, True),
    (["Wilmington, DE"], ("US",), (), False, False),           # Delaware, not Germany
    (["Gräfelfing/Munich"], ("DE",), (), False, True),
    (["Remote - UK"], ("GB",), ("GB",), True, True),             # country-restricted remote
    (["Remote, Germany"], ("DE",), ("DE",), True, True),
    (["Remote - Europe"], (), ("europe",), True, True),
    (["Remote (EMEA)"], (), ("emea",), True, True),
    (["Worldwide"], (), ("worldwide",), True, True),
    (["Remote - United States"], ("US",), ("US",), True, False),  # US-only remote
    (["Remote, Canada; Remote, United States"], ("CA", "US"), ("CA", "US"), True, False),
    (["London, UK; Remote - United States"], ("GB", "US"), ("US",), True, True),   # London is not remote
    (["San Francisco, CA • New York, NY • United States"], ("US",), (), False, False),
    (["Remote"], (), (), True, False),                           # region unsaid
    (["Berlin / Remote"], ("DE",), (), True, True),
    (["Latin America"], (), (), False, False),
    (["Remoto - Italia"], ("IT",), ("IT",), True, True),
    (["Join us in Milan"], ("IT",), (), False, True),            # lowercase "us" isn't the US
    # review round 1 (phase 3a): worldwide only as an explicit, unrestricted scope label
    (["Remote - Anywhere in the United States"], ("US",), ("US",), True, False),
    (["Remote, Global (APAC)"], (), ("apac",), True, False),
    (["Bonifacio Global City, Philippines"], ("PH",), (), False, False),
    (["Remote - Global"], (), ("worldwide",), True, True),
    (["Work from anywhere in Europe"], (), ("europe",), True, True),
    # explicit country/state/province evidence beats city aliases
    (["London, Ontario, Canada"], ("CA",), (), False, False),
    (["Berlin, New Hampshire, United States"], ("US",), (), False, False),
    (["Mahwah, New Jersey"], ("US",), (), False, False),
    (["Berlin, NH"], ("US",), (), False, False),
    (["Valletta, MT"], ("MT",), (), False, True),
    (["Venezuela, Remote"], ("VE",), ("VE",), True, False),
    (["Istanbul"], ("TR",), (), False, True),                    # the agreed broad Europe
]


@pytest.mark.parametrize("locations,countries,regions,remote,europe", CASES)
def test_parse(locations, countries, regions, remote, europe):
    p = parse(locations)
    assert (p.countries, p.remote_regions, p.remote, in_europe(p)) == (countries, regions, remote, europe)


def test_structured_country_fields_count():
    p = parse(["Remote"], ["Germany"])
    assert p.countries == ("DE",) and in_europe(p)


def test_named_regions_without_remote_are_areas():
    p = parse(["EMEA"])
    assert p.areas == ("emea",) and not p.remote and in_europe(p)


def test_dach_expands_to_countries():
    assert parse(["Remote (DACH)"]).remote_regions == ("AT", "CH", "DE")


def test_unreadable_places_are_not_unstated():
    assert parse(["Multiple Locations"]).unparsed and not parse(["Remote"]).unparsed
    assert parse(["Remote"], ["Narnia"]).unparsed
    assert not parse(["Berlin (Hybrid)"]).unparsed and parse(["Berlin (Hybrid)"]).hybrid


def test_attached_country_beats_a_bare_city():
    assert parse([("London", "Canada")]).countries == ("CA",)
    assert parse([("Berlin", "US")]).countries == ("US",)
    assert parse([("Berlin", "Germany"), ("New York", "United States")]).countries == ("DE", "US")   # separate offices


def test_declared_remote_inherits_named_regions():
    p = parse([("Europe", None)], workplace="remote")
    assert p.remote_regions == ("europe",) and p.where == ("europe|remote",) and in_europe(p)


def test_where_keeps_place_and_mode_together():
    assert parse(["London, UK; Remote - United States"]).where == ("GB|unknown", "US|remote")


def test_unicode_case_forms_never_crash_and_still_match():
    assert parse(["İstanbul"]).countries == ("TR",)            # Turkish dotted capital I
    assert parse(["MÜNCHEN"]).countries == ("DE",) and parse(["Zürich"]).countries == ("CH",)
