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


# Town names via the GeoNames table, European states and regions, and work styles given as their own piece
@pytest.mark.parametrize("location,where", [
    ("Münster", ("DE|unknown",)),
    ("Münster, DE", ("DE|unknown",)),                     # a town names Germany; no Delaware
    ("Darmstadt, DE", ("DE|unknown",)),
    ("St. Wendel", ("DE|unknown",)),                      # Sankt Wendel
    ("Halle (Saale)", ("DE|unknown",)),
    ("Bielefeld - Cherusker", ("DE|unknown",)),
    ("BK, Bayern", ("DE|unknown",)),
    ("Remote (Sachsen)", ("DE|remote",)),
    ("Remote: Hessen", ("DE|remote",)),
    ("Barcelona, Catalonia, Spain", ("ES|unknown",)),
    ("Latur", ("IN|unknown",)),
    ("Darmstadt // Hybrid", ("DE|hybrid",)),
    ("Germany (Remote/Hybrid/On-site)", ("DE|hybrid", "DE|onsite", "DE|remote")),
    ("Germany (On-site/Remote/Hybrid)", ("DE|hybrid", "DE|onsite", "DE|remote")),   # order doesn't matter
    ("Tokyo (Remote/Hybrid)", ("JP|hybrid", "JP|remote")),
    ("Remote (Canada / US)", ("CA|remote", "US|remote")),        # no split inside parentheses
    ("Hybrid / Berlin", ("DE|hybrid",)),
    ("London, UK; Hybrid; Remote - United States", ("GB|hybrid", "US|remote")),   # only the nearest place
    ("London, UK; Remote - United States / Hybrid", ("GB|unknown", "US|remote")),  # never across clauses
    ("Germany (Hybrid/On-site/Remote)", ("DE|hybrid", "DE|onsite", "DE|remote")),
    ("Munich Onsite (NavVis GmbH)", ("DE|onsite",)),
    ("Berlin / Remote", ("DE|unknown", "unstated|remote")),   # a bare Remote piece stays region-unstated
    ("Wilmington, DE", ("US|unknown",)),
])
def test_towns_regions_and_styles(location, where):
    p = parse([location])
    assert p.where == where and not p.unparsed


def test_ambiguous_or_unknown_towns_stay_unparsed():
    assert parse(["Cambridge"]).unparsed            # UK and Massachusetts are both large
    assert parse(["Club Hüfingen"]).unparsed
    assert not parse(["In-Office"]).unparsed and parse(["In-Office"]).where == ()
    assert parse(["PERM - N/A"]).where == ()        # short capitals are codes: not Perm, Russia
    assert parse(["Remote (US/CAN)"]).where == ("US|remote",)   # nor Çan, Turkey


@pytest.mark.parametrize("location,countries", [
    # European regions are weak evidence, like cities: an explicit country or US state wins
    ("Normandy, MO", ("US",)), ("Hessen, Kansas", ("US",)), ("Schwyz, United States", ("US",)),
    ("Bayern, DE", ("DE",)), ("Munich, Bavaria, Germany", ("DE",)), ("Toscana", ("IT",)),
])
def test_european_regions_yield_to_explicit_countries(location, countries):
    assert parse([location]).countries == countries
