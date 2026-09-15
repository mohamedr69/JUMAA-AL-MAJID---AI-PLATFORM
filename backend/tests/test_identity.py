"""Building and part-number identity from noisy OCR."""

from app.extraction.identity import building_key, canonical_buildings, clean_catalog, match_catalog


def test_ep30208_banner_variants_become_one_building_each():
    raws = ["DHAID - B1 BUILDING", "DH AID-BIBUILDING", "DHAID - B2 BUILDING", "DHAID - B2 BUILDING",
            "DH AID - BS BUILDING", "DHAID - B5 BUILDING", "DHAID - B4 BUILDING"]
    buildings = canonical_buildings(raws)
    assert buildings["DH AID-BIBUILDING"].key == buildings["DHAID - B1 BUILDING"].key
    assert buildings["DH AID-BIBUILDING"].display == "DHAID - B1 BUILDING"
    assert buildings["DH AID - BS BUILDING"].display == "DHAID - B5 BUILDING"
    assert sorted(buildings["DHAID - B5 BUILDING"].aliases) == ["DH AID - BS BUILDING", "DHAID - B5 BUILDING"]
    assert "BS" in buildings["DH AID - BS BUILDING"].rule or "5" in buildings["DH AID - BS BUILDING"].rule
    assert len({b.key for b in buildings.values()}) == 4


def test_different_buildings_stay_apart():
    assert building_key("TOWER A")[0] != building_key("TOWER B")[0]
    assert building_key("DHAID - B1 BUILDING")[0] != building_key("DHAID - B7 BUILDING")[0]


def test_catalog_cleaning_removes_only_edge_junk():
    assert clean_catalog("£232 301H") == ("E232 301H", "'£' read as 'E'")
    assert clean_catalog("~ §C-630M EB-Q") == ("SC-630M EB-Q", "'§' read as 'S'; removed '~' around the code")
    assert clean_catalog("‘MD-200CTU-EB")[0] == "MD-200CTU-EB"
    assert clean_catalog("SIGA - PS")[0] == "SIGA-PS"
    assert clean_catalog("4-CPU") == ("4-CPU", None)
    assert clean_catalog("|~") == (None, "removed '|~': no letters or digits")


def test_catalog_matching_says_how_it_matched():
    library = {"SIGAPS": "SIGA-PS", "SC630M": "SC-630M", "4CPU": "4-CPU"}
    exact = match_catalog("‘SIGA-PS", library)
    assert exact.canonical == "SIGA-PS" and "exactly" in exact.reason and exact.source == "‘SIGA-PS"
    confused = match_catalog("SC-63OM", library)
    assert confused.canonical == "SC-630M" and "one OCR confusion" in confused.reason
    unknown = match_catalog("XYZ-1", library)
    assert unknown.canonical is None and "not in the part library" in unknown.reason
