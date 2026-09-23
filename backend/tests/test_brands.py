"""One spelling per manufacturer, wherever a brand is recorded."""

import pytest

from app.services import brands


@pytest.mark.parametrize(
    "written, recorded",
    [
        # Case alone: the same supplier arrived both ways on real DRFs.
        ("EDWARDS", "EDWARDS"),
        ("Edwards", "EDWARDS"),
        ("  edwards ", "EDWARDS"),
        ("MENVIER", "MENVIER"),
        ("Menvier", "MENVIER"),
        # Misspellings seen on a DRF and in the library's own folder names,
        # both transposing the I. These matched no library at all.
        ("MENIVER", "MENVIER"),
        ("MENIVIER", "MENVIER"),
        # The range named alongside the maker. The maker is what has a
        # library folder and a submittal shelf.
        ("Edwards EST4", "EDWARDS"),
        ("Cooper Menvier", "MENVIER"),
        # The longer spelling wins, so this is not read as ELAND.
        ("ELAND CABLES", "ELAND CABLES"),
    ],
)
def test_a_brand_is_recorded_one_way(written, recorded):
    assert brands.normalise(written) == recorded


def test_an_unknown_supplier_is_kept():
    """A brand the table has not met is upper-cased, never dropped: the
    next supplier the company takes on must not be silently blanked."""
    assert brands.normalise("Some New Supplier") == "SOME NEW SUPPLIER"


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_nothing_given_is_nothing_recorded(empty):
    assert brands.normalise(empty) is None


def test_it_does_not_fold_menvier_into_eaton():
    """`knowledge.policy.canonical_manufacturer` answers a different
    question -- whether a record's maker is the project's maker -- and
    folds MENVIER into EATON, which owns it. Doing that here would key the
    project to a library that does not exist."""
    from app.knowledge.policy import canonical_manufacturer

    assert canonical_manufacturer("Menvier") == "EATON"
    assert brands.normalise("Menvier") == "MENVIER"


def test_the_normalised_brand_finds_its_library_and_its_shelf():
    """What the spelling is for: a brand is the key to the datasheet
    library and to the submittal builder's shelf, and the misspelling
    matched neither."""
    from app.services import company_library
    from app.services.datasheet_library import get_libraries, libraries_for

    libraries = get_libraries()
    if "MENVIER" not in libraries:
        pytest.skip("the Menvier library is not held on this machine")

    # The misspelling matches no library, and `libraries_for` then falls
    # back to every one of them -- searching Edwards fire alarm sheets for
    # an emergency light.
    assert [l.name for l in libraries_for("MENIVER", libraries)] == sorted(libraries)
    assert [l.name for l in libraries_for(brands.normalise("MENIVER"), libraries)] == ["MENVIER"]

    root = company_library.library_root() / "submittal"
    if not (root / "MENVIER").is_dir():
        pytest.skip("the Menvier submittal shelf is not held on this machine")
    assert company_library.submittal_path(root, "MENIVER", "Test certificates") is None
    assert company_library.submittal_path(root, brands.normalise("MENIVER"), "Test certificates") is not None
