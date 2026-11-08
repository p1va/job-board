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
