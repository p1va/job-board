from jobboard import gazetteer


def _dump(tmp_path, rows):
    """A minimal cities5000-style file: name (1), asciiname (2), country (8), population (14)."""
    lines = []
    for name, country, pop in rows:
        f = [""] * 19
        f[1], f[2], f[8], f[14] = name, name, country, str(pop)
        lines.append("\t".join(f))
    path = tmp_path / "cities.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_build_keeps_only_clearly_owned_names(tmp_path):
    dump = _dump(tmp_path, [
        ("Münster", "DE", 300_000), ("Münster", "CH", 2_000),        # dominant: Germany
        ("Cambridge", "GB", 145_000), ("Cambridge", "US", 118_000),  # both large: left out
        ("Sankt Wendel", "DE", 26_000),
        ("Smallville", "US", 50_000),                                # outside Europe, under 100k
        ("Latur", "IN", 380_000),
        ("Remote", "DE", 9_000),                                     # an ordinary location word
    ])
    out = tmp_path / "cities.tsv"
    assert gazetteer.build(dump, {"DE", "CH", "GB"}, out) == 5
    table = dict(line.split("\t") for line in out.read_text(encoding="utf-8").splitlines()[1:])
    assert table == {"munster": "DE", "sankt wendel": "DE", "st. wendel": "DE", "st wendel": "DE", "latur": "IN"}


def test_lookup_folds_case_accents_and_hyphens():
    assert gazetteer.key("MÜNSTER") == gazetteer.key("münster") == "munster"
    assert gazetteer.key("Halle-Neustadt") == "halle neustadt"
    assert gazetteer.country("Deggendorf") == "DE"
