"""Where a job is: countries, remote regions and workplace, from providers' location text.

Rules, not guesses: a place counts only when a known country, alias or city names it. Remote
without a region stays an empty `remote_regions` list: "Remote" alone doesn't say where from.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ISO-2 -> names and aliases (lowercase), English plus common native spellings.
COUNTRIES: dict[str, tuple[str, ...]] = {
    "AD": ("andorra",), "AL": ("albania", "shqipëri"), "AT": ("austria", "österreich", "oesterreich"),
    "BA": ("bosnia and herzegovina", "bosnia"), "BE": ("belgium", "belgië", "belgique", "belgien"),
    "BG": ("bulgaria", "българия"), "BY": ("belarus",), "CH": ("switzerland", "schweiz", "suisse", "svizzera"),
    "CY": ("cyprus",), "CZ": ("czech republic", "czechia", "česko", "česká republika"),
    "DE": ("germany", "deutschland"), "DK": ("denmark", "danmark"), "EE": ("estonia", "eesti"),
    "ES": ("spain", "españa", "espana"), "FI": ("finland", "suomi"), "FO": ("faroe islands",),
    "FR": ("france",), "GB": ("united kingdom", "uk", "u.k.", "great britain", "britain", "england",
                               "scotland", "wales", "northern ireland"),
    "GG": ("guernsey",), "GI": ("gibraltar",), "GR": ("greece", "ελλάδα", "hellas"), "HR": ("croatia", "hrvatska"),
    "HU": ("hungary", "magyarország"), "IE": ("ireland", "éire", "republic of ireland"), "IM": ("isle of man",),
    "IS": ("iceland", "ísland"), "IT": ("italy", "italia"), "JE": ("jersey",), "LI": ("liechtenstein",),
    "LT": ("lithuania", "lietuva"), "LU": ("luxembourg", "luxemburg"), "LV": ("latvia", "latvija"),
    "MC": ("monaco",), "MD": ("moldova",), "ME": ("montenegro",), "MK": ("north macedonia", "macedonia"),
    "MT": ("malta",), "NL": ("netherlands", "the netherlands", "nederland", "holland"),
    "NO": ("norway", "norge"), "PL": ("poland", "polska"), "PT": ("portugal",), "RO": ("romania", "românia"),
    "RS": ("serbia", "srbija"), "SE": ("sweden", "sverige"), "SI": ("slovenia", "slovenija"),
    "SK": ("slovakia", "slovensko"), "SM": ("san marino",), "UA": ("ukraine",), "VA": ("vatican city",),
    "XK": ("kosovo",), "RU": ("russia", "russian federation"), "TR": ("turkey", "türkiye", "turkiye"),
    "GE": ("georgia (country)",), "AM": ("armenia",), "AZ": ("azerbaijan",), "KZ": ("kazakhstan",),
    # outside Europe, so jobs there are recognised as elsewhere
    "US": ("united states", "united states of america", "usa", "u.s.", "u.s.a.", "us"),
    "CA": ("canada",), "MX": ("mexico", "méxico"), "BR": ("brazil", "brasil"), "AR": ("argentina",),
    "CO": ("colombia",), "CL": ("chile",), "PE": ("peru", "perú"), "UY": ("uruguay",),
    "IN": ("india",), "SG": ("singapore",), "JP": ("japan",), "KR": ("south korea", "korea"),
    "CN": ("china",), "HK": ("hong kong",), "TW": ("taiwan",), "AU": ("australia",), "NZ": ("new zealand",),
    "IL": ("israel",), "AE": ("united arab emirates", "uae"), "SA": ("saudi arabia",), "EG": ("egypt",),
    "ZA": ("south africa",), "NG": ("nigeria",), "KE": ("kenya",), "PH": ("philippines",),
    "TH": ("thailand",), "VN": ("vietnam", "viet nam"), "ID": ("indonesia",), "MY": ("malaysia",),
    "PK": ("pakistan",), "BD": ("bangladesh",), "CR": ("costa rica",), "SJ": ("svalbard",),
    "VE": ("venezuela",), "HN": ("honduras",), "EC": ("ecuador",), "GT": ("guatemala",), "SV": ("el salvador",),
    "NI": ("nicaragua",), "PA": ("panama", "panamá"), "DO": ("dominican republic",), "PR": ("puerto rico",),
    "BO": ("bolivia",), "PY": ("paraguay",), "JM": ("jamaica",), "MA": ("morocco", "maroc"), "TN": ("tunisia",),
    "DZ": ("algeria",), "GH": ("ghana",), "ET": ("ethiopia",), "UG": ("uganda",), "RW": ("rwanda",),
    "TZ": ("tanzania",), "SN": ("senegal",), "CI": ("ivory coast", "côte d'ivoire"), "CM": ("cameroon",),
    "JO": ("jordan",), "LB": ("lebanon",), "QA": ("qatar",), "KW": ("kuwait",), "BH": ("bahrain",), "OM": ("oman",),
    "LK": ("sri lanka",), "NP": ("nepal",), "KH": ("cambodia",), "MM": ("myanmar",), "MN": ("mongolia",),
    "UZ": ("uzbekistan",), "KG": ("kyrgyzstan",), "TJ": ("tajikistan",), "TM": ("turkmenistan",),
}

EU = {"AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU", "IE", "IT", "LT",
      "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK"}
# Europe as searched by the Workable retriever (retrievers.EUROPE): the agreed broad cohort, including
# transcontinental countries. The board's default view (Germany, UK, Italy, remote) narrows what is shown.
EUROPE = EU | {"AD", "AL", "AM", "AZ", "BA", "BY", "CH", "FO", "GB", "GE", "GG", "GI", "IM", "IS", "JE", "KZ",
               "LI", "MC", "MD", "ME", "MK", "NO", "RS", "RU", "SJ", "SM", "TR", "UA", "VA", "XK"}

# Cities that name a country on their own (lowercase). Ambiguous names (e.g. Cambridge, Birmingham,
# Valencia) are only listed for the country where a job board most often means them.
CITIES: dict[str, str] = {
    **{c: "DE" for c in ("berlin", "munich", "münchen", "muenchen", "hamburg", "frankfurt", "frankfurt am main",
                         "cologne", "köln", "koeln", "düsseldorf", "duesseldorf", "stuttgart", "leipzig", "dresden",
                         "hanover", "hannover", "nuremberg", "nürnberg", "karlsruhe", "heidelberg", "bonn", "essen",
                         "dortmund", "bremen", "mannheim", "potsdam", "aachen", "freiburg", "gräfelfing", "garching")},
    **{c: "GB" for c in ("london", "manchester", "edinburgh", "glasgow", "bristol", "leeds", "oxford",
                         "cambridge, uk", "belfast", "cardiff", "liverpool", "newcastle", "sheffield",
                         "nottingham", "brighton", "reading", "milton keynes", "guildford", "bath")},
    **{c: "IT" for c in ("milan", "milano", "rome", "roma", "turin", "torino", "bologna", "florence", "firenze",
                         "naples", "napoli", "genoa", "genova", "padua", "padova", "verona", "trento", "bari",
                         "pisa", "venice", "venezia", "trieste")},
    **{c: "FR" for c in ("paris", "lyon", "marseille", "toulouse", "nantes", "bordeaux", "lille", "nice",
                         "montpellier", "rennes", "grenoble", "sophia antipolis")},
    **{c: "ES" for c in ("madrid", "barcelona", "valencia", "seville", "sevilla", "malaga", "málaga", "bilbao")},
    **{c: "NL" for c in ("amsterdam", "rotterdam", "the hague", "den haag", "utrecht", "eindhoven", "delft",
                         "leiden", "groningen")},
    **{c: "PT" for c in ("lisbon", "lisboa", "porto", "braga", "coimbra")},
    **{c: "IE" for c in ("dublin", "cork", "galway", "limerick")},
    **{c: "CH" for c in ("zurich", "zürich", "geneva", "genève", "lausanne", "basel", "bern", "zug", "lugano")},
    **{c: "AT" for c in ("vienna", "wien", "graz", "linz", "salzburg", "innsbruck")},
    **{c: "BE" for c in ("brussels", "bruxelles", "brussel", "antwerp", "antwerpen", "ghent", "gent", "leuven")},
    **{c: "DK" for c in ("copenhagen", "københavn", "aarhus", "odense")},
    **{c: "SE" for c in ("stockholm", "gothenburg", "göteborg", "malmö", "malmo", "uppsala", "lund")},
    **{c: "NO" for c in ("oslo", "bergen", "trondheim")},
    **{c: "FI" for c in ("helsinki", "espoo", "tampere", "oulu")},
    **{c: "PL" for c in ("warsaw", "warszawa", "krakow", "kraków", "wroclaw", "wrocław", "gdansk", "gdańsk",
                         "poznan", "poznań", "lodz", "łódź")},
    **{c: "CZ" for c in ("prague", "praha", "brno")},
    **{c: "HU" for c in ("budapest",)}, **{c: "RO" for c in ("bucharest", "bucurești", "cluj-napoca", "cluj", "iasi")},
    **{c: "BG" for c in ("sofia",)}, **{c: "GR" for c in ("athens", "thessaloniki")},
    **{c: "HR" for c in ("zagreb", "split")}, **{c: "RS" for c in ("belgrade", "novi sad")},
    **{c: "SI" for c in ("ljubljana",)}, **{c: "SK" for c in ("bratislava",)}, **{c: "EE" for c in ("tallinn", "tartu")},
    **{c: "LV" for c in ("riga",)}, **{c: "LT" for c in ("vilnius", "kaunas")}, **{c: "LU" for c in ("luxembourg city",)},
    **{c: "UA" for c in ("kyiv", "kiev", "lviv", "kharkiv")}, **{c: "MT" for c in ("valletta",)},
    **{c: "CY" for c in ("limassol", "nicosia")}, **{c: "IS" for c in ("reykjavik", "reykjavík")},
    **{c: "TR" for c in ("istanbul", "ankara", "izmir")},
    **{c: "RU" for c in ("moscow", "saint petersburg", "st. petersburg")}, **{c: "BY" for c in ("minsk",)},
    **{c: "GE" for c in ("tbilisi",)}, **{c: "AM" for c in ("yerevan",)}, **{c: "AZ" for c in ("baku",)},
    **{c: "KZ" for c in ("almaty", "astana")},
    # common non-European job cities
    **{c: "US" for c in ("san francisco", "south san francisco", "new york", "new york city", "nyc", "seattle",
                         "austin", "boston", "chicago", "los angeles", "denver", "atlanta", "miami", "san jose",
                         "san diego", "palo alto", "mountain view", "menlo park", "sunnyvale", "redwood city",
                         "washington, dc", "washington dc", "philadelphia", "pittsburgh", "portland", "dallas",
                         "houston", "salt lake city", "raleigh", "cambridge, ma", "bay area", "silicon valley",
                         "brooklyn", "minneapolis", "detroit", "phoenix", "nashville", "boulder")},
    **{c: "CA" for c in ("toronto", "vancouver", "montreal", "montréal", "ottawa", "calgary", "waterloo")},
    **{c: "IN" for c in ("bangalore", "bengaluru", "hyderabad", "pune", "mumbai", "delhi", "new delhi", "gurgaon",
                         "gurugram", "noida", "chennai")},
    **{c: "BR" for c in ("são paulo", "sao paulo", "rio de janeiro", "campinas")},
    **{c: "MX" for c in ("mexico city", "ciudad de méxico", "guadalajara")},
    **{c: "AU" for c in ("sydney", "melbourne", "brisbane")}, **{c: "JP" for c in ("tokyo",)},
    **{c: "IL" for c in ("tel aviv", "tel aviv-yafo", "jerusalem", "haifa")}, **{c: "AE" for c in ("dubai", "abu dhabi")},
    **{c: "SG" for c in ("singapore",)}, **{c: "CO" for c in ("bogota", "bogotá", "medellin", "medellín")},
    **{c: "AR" for c in ("buenos aires",)}, **{c: "KR" for c in ("seoul",)}, **{c: "CN" for c in ("shanghai", "beijing", "shenzhen")},
}

US_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
             "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
# Subdivisions are explicit country evidence and are matched (and masked) before country and city names,
# so "New Jersey" isn't Jersey and "London, Ontario" isn't London.
SUBDIVISIONS = {
    **{s: "US" for s in ("alabama alaska arizona arkansas california colorado connecticut delaware florida "
                         "hawaii idaho illinois indiana iowa kansas kentucky louisiana maine maryland "
                         "massachusetts michigan minnesota mississippi missouri montana nebraska nevada ohio "
                         "oklahoma oregon pennsylvania tennessee texas utah vermont virginia wisconsin wyoming").split()},
    **{s: "US" for s in ("new jersey", "new hampshire", "new mexico", "north carolina", "south carolina",
                         "north dakota", "south dakota", "west virginia", "rhode island", "district of columbia")},
    **{s: "CA" for s in ("ontario", "quebec", "québec", "british columbia", "alberta", "nova scotia", "manitoba",
                         "saskatchewan", "new brunswick", "newfoundland")},
    **{s: "AU" for s in ("new south wales", "queensland")},
}

REGIONS = {  # region words -> codes
    "worldwide": "worldwide", "anywhere": "worldwide", "global": "worldwide", "globally": "worldwide",
    "any location": "worldwide", "all locations": "worldwide",
    "europe": "europe", "european": "europe", "european economic area": "europe", "eea": "europe",
    "cee": "europe", "eu": "eu", "european union": "eu", "emea": "emea",
    "dach": "DACH", "nordics": "NORDICS", "benelux": "BENELUX", "uk&i": "UKI",
    "americas": "americas", "north america": "americas", "latin america": "latam", "latam": "latam",
    "apac": "apac", "asia pacific": "apac", "asia": "apac", "middle east": "mena", "mena": "mena",
}
GROUPS = {"DACH": ("DE", "AT", "CH"), "NORDICS": ("DK", "SE", "NO", "FI", "IS"), "BENELUX": ("BE", "NL", "LU"),
          "UKI": ("GB", "IE")}
OPEN_TO_EUROPE = EUROPE | {"worldwide", "europe", "eu", "emea"}
REMOTE = re.compile(r"\b(remote|remotely|anywhere|work from home|wfh|telecommute|distributed|home[- ]based|"
                    r"remoto|remota|teletrabajo|t[ée]l[ée]travail|home ?office|fernarbeit|mobiles arbeiten)\b", re.I)
HYBRID = re.compile(r"\bhybrid\b", re.I)
# Separators between different places in one location string. Within a part, "Remote - UK" stays together.
SEGMENT = re.compile(r"\s*(?:;|\||•|·|/|\n|\bor\b|\band\b|&(?!i\b))\s*", re.I)
# What a bare scope label may contain besides remote and region words ("Remote - Global", "100% remote")
FILLER = re.compile(r"[\s\-–—,.:()\[\]%!]+|\b(fully|only|100|based|position|role|work|from|in|the|world|"
                    r"possible|option|first|friendly|ok)\b", re.I)


def _alternation(names) -> re.Pattern:
    return re.compile(r"(?<![\w.])(" + "|".join(re.escape(a) for a in sorted(names, key=len, reverse=True))
                      + r")(?![\w])", re.I)


_COUNTRY_NAMES = {a: c for c, names in COUNTRIES.items() for a in names if a not in ("us", "uk")}
_COUNTRY_ANY = _alternation(_COUNTRY_NAMES)
_CITY_ANY = _alternation(CITIES)
_SUBDIV_ANY = _alternation(SUBDIVISIONS)
_REGION_ANY = _alternation(REGIONS)
# Uppercase tokens only, so "us" in "join us" or "it" never count
_UPPER = [(re.compile(r"(?<![\w.])US(?![\w])"), "US"), (re.compile(r"(?<![\w.])UK(?![\w])"), "GB")]
# A trailing European ISO code ("Remote - GB"); codes that are also US states (DE, AL, ME, MD, MT, VA, GA, IN)
# need a city or country name instead, so "Wilmington, DE" stays Delaware.
STATE_CLASH = {"DE", "AL", "ME", "MD", "MT", "VA", "GA", "IN"}
_ISO_EUROPE = re.compile(r"(?:,|\s-|^)\s*(" + "|".join(sorted(EUROPE - STATE_CLASH)) + r")\s*$")
_STATE = re.compile(r",\s*(" + "|".join(US_STATES) + r")\b(?!\w)")


@dataclass(frozen=True)
class Place:
    countries: tuple[str, ...]          # sorted ISO-2 codes named by location text or structured fields
    remote_regions: tuple[str, ...]     # where remote work is offered from: worldwide | europe | eu | emea |
                                        # americas | latam | apac | mena | ISO-2; empty = remote, region unsaid
    areas: tuple[str, ...]              # regions named without saying remote (e.g. a location "EMEA")
    remote: bool                        # some location says remote
    hybrid: bool
    unparsed: bool = False              # some location or country field named a place we couldn't read


def countries_in(text: str) -> set[str]:
    """Country codes named in one place description. Explicit names (countries, states, provinces, ISO
    codes) win over city names, which only count when nothing explicit is said."""
    explicit = {SUBDIVISIONS[m.group(1).lower()] for m in _SUBDIV_ANY.finditer(text)}
    masked = _SUBDIV_ANY.sub(" ", text)
    explicit |= {_COUNTRY_NAMES[m.group(1).lower()] for m in _COUNTRY_ANY.finditer(masked)}
    explicit |= {code for pattern, code in _UPPER if pattern.search(masked)}
    if m := _ISO_EUROPE.search(masked.strip()):
        explicit.add(m.group(1))
    state = _STATE.search(masked)
    if state and state.group(1) not in STATE_CLASH:
        explicit.add("US")                    # ", NH", ", CA": only US states
    if explicit:
        return explicit
    cities = {CITIES[m.group(1).lower()] for m in _CITY_ANY.finditer(masked)}
    if state and not cities & EUROPE:
        return cities | {"US"}                # ", DE" without a European city: Delaware
    return cities                             # "Berlin, DE", "Valletta, MT": the city decides


def regions_in(text: str) -> set[str]:
    found: set[str] = set()
    for m in _REGION_ANY.finditer(text):
        region = REGIONS[m.group(1).lower()]
        found.update(GROUPS.get(region, (region,)))
    return found


def _bare(part: str) -> bool:
    """Only remote/region words and filler: a scope label rather than a place name."""
    return not FILLER.sub("", _REGION_ANY.sub("", REMOTE.sub("", part))).strip()


def parse(locations: list[str], structured_countries: tuple[str, ...] | list[str] = ()) -> Place:
    """Combine providers' location strings and country fields into one Place."""
    countries: set[str] = set()
    remote_regions: set[str] = set()
    areas: set[str] = set()
    remote = hybrid = unparsed = False
    for raw in locations:
        if not raw or not raw.strip():
            continue
        hybrid |= bool(HYBRID.search(raw))
        for part in (p for p in SEGMENT.split(raw) if p and p.strip()):
            named, regions = countries_in(part), regions_in(part)
            # Worldwide only as an explicit scope label, and never beside a narrower scope:
            # "Remote - Anywhere in the United States" is US, "Bonifacio Global City" is a place.
            if "worldwide" in regions and (named or regions - {"worldwide"} or not _bare(part)):
                regions.discard("worldwide")
            countries |= named
            if REMOTE.search(part) or "worldwide" in regions:
                remote = True
                remote_regions |= named | regions
            else:
                areas |= regions
            if not named and not regions and not _bare(part) and not HYBRID.fullmatch(part.strip()):
                unparsed = True
    for name in structured_countries:
        if not name:
            continue
        code = country_code(name)
        if code:
            countries.add(code)
        else:
            unparsed = True
    return Place(tuple(sorted(countries)), tuple(sorted(remote_regions)), tuple(sorted(areas)), remote, hybrid,
                 unparsed)


def country_code(name: str) -> str | None:
    name = name.strip()
    if len(name) == 2 and name.upper() in COUNTRIES:
        return name.upper()
    found = countries_in(name)
    return next(iter(found)) if len(found) == 1 else None


def in_europe(place: Place) -> bool:
    """Located in Europe, or open to work from somewhere in Europe."""
    return bool(set(place.countries) & EUROPE or set(place.remote_regions) & OPEN_TO_EUROPE
                or set(place.areas) & OPEN_TO_EUROPE)
