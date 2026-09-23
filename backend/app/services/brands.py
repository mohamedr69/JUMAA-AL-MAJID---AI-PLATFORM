"""One spelling for a manufacturer, wherever the platform records one.

A brand reaches the platform three ways -- read off a DRF, typed by an
engineer, or filed as a folder name -- and the same maker arrived as
"EDWARDS", "Edwards", "MENVIER", "Menvier" and "MENIVER". They are one
supplier, and a project that recorded the misspelling matched nothing.

**The canonical spelling is the library folder's, upper-cased.** Not a
matter of taste: the datasheet library is found by name
(`datasheet_library.libraries_for`), a part's link is found by
`part_datasheet_links.manufacturer`, and the submittal builder's shelves
are `submittal/<BRAND>/` (`company_library.submittal_path`). A brand
spelled any other way is a different key to all three.

Unlike `knowledge.policy.canonical_manufacturer`, which answers "is this
record's maker the project's maker?" and returns None for a maker it does
not know, this never discards a name: a brand it has not met is passed
through trimmed and upper-cased. The two are deliberately separate --
that one folds MENVIER into EATON, which is right for compliance and
would break every datasheet lookup if used here.
"""

from __future__ import annotations

# The spelling each brand is recorded under: the library folder's name.
# A new brand needs a line here only when it is spelled more than one way;
# anything unlisted is simply upper-cased.
BRAND_SPELLINGS: dict[str, tuple[str, ...]] = {
    "EDWARDS": ("EDWARDS", "EST", "EST3", "EST4", "EDWARDS EST"),
    # Misspellings seen on real DRFs and in the library's own folder names
    # ("submittal/MENIVIER/"), both transposing the I.
    "MENVIER": ("MENVIER", "MENIVER", "MENIVIER", "MENVIER BRAND"),
    "JSB": ("JSB",),
    "FIREGUARD": ("FIREGUARD", "FIRE GUARD"),
    "EATON": ("EATON",),
    # ROPAG is RP-Technik under its older name; `knowledge.policy` already
    # says so, and the register still carries both.
    "RP-TECHNIK": ("RP-TECHNIK", "RP TECHNIK", "RPTECHNIK", "ROPAG"),
    "ELAND CABLES": ("ELAND CABLES", "ELAND"),
    "PRYSMIAN": ("PRYSMIAN",),
}
_LOOKUP = {spelling: brand for brand, spellings in BRAND_SPELLINGS.items() for spelling in spellings}


def normalise(name: str | None) -> str | None:
    """The brand as it is recorded, or None only when nothing was given.

    A name this does not know is kept, trimmed and upper-cased: the next
    supplier the company takes on must not be quietly blanked because no
    one has added it here yet.
    """
    if name is None:
        return None
    text = " ".join(name.split()).upper()
    if not text:
        return None
    if text in _LOOKUP:
        return _LOOKUP[text]
    # A DRF names the range as well as the maker -- "Edwards EST4",
    # "Cooper Menvier". The maker is the part that has a library folder,
    # and the submittal builder matches its shelf by the whole name
    # (`company_library.submittal_path`), so a brand that keeps the range
    # finds its datasheets and silently loses its certificates. Longest
    # spelling first, so ELAND CABLES is not read as ELAND.
    for spelling, brand in sorted(_LOOKUP.items(), key=lambda kv: -len(kv[0])):
        if spelling in text:
            return brand
    return text


def spellings_of(brand: str | None) -> tuple[str, ...]:
    """Every spelling that means this brand, for finding what was stored
    before it was normalised."""
    canonical = normalise(brand)
    return BRAND_SPELLINGS.get(canonical, (canonical,)) if canonical else ()
