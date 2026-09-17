"""What a symbol on a drawing is, and how sure the platform is of it.

The block name is the weakest evidence there is; the geometry inside the
block and the drawing's own legend are the strong ones. These tests hold
the engine to that: a name on its own is never enough to accept a symbol,
and a symbol the platform has been taught is recognised whatever the
draughtsman called it.
"""

import ezdxf

from app.services.device_symbols import (
    ACCEPTED,
    FLAGGED,
    REVIEW,
    UNRESOLVED,
    Geometry,
    SymbolLibrary,
    band,
    classify,
    device_in,
    geometry_of,
    legend_entries,
)
from app.services.floor_devices import Box, Insert, Label


class _Row:
    """A device_symbols row, as the library reads it."""

    def __init__(self, device, fingerprints=(), shapes=(), block_names=()):
        self.device = device
        self.fingerprints = list(fingerprints)
        self.shapes = list(shapes)
        self.block_names = list(block_names)


def _symbol(doc, name: str, *, circles: int = 1, label: str = "S", lines: int = 0, scale: float = 1.0):
    """A block drawn the way a fire alarm symbol is drawn."""
    block = doc.blocks.new(name=name)
    for index in range(circles):
        block.add_circle((0, 0), radius=(50 + index * 30) * scale)
    for index in range(lines):
        block.add_line((-50 * scale, index * 10 * scale), (50 * scale, index * 10 * scale))
    if label:
        block.add_text(label, dxfattribs={"height": 20 * scale}).set_placement((0, 0))
    return block


def test_the_geometry_is_the_symbol_whatever_the_block_is_called():
    doc = ezdxf.new(setup=True)
    detector = geometry_of(_symbol(doc, "SD", circles=1, label="S"))
    sounder_base = geometry_of(_symbol(doc, "SD-SB", circles=2, label="S"))
    heat = geometry_of(_symbol(doc, "HD", circles=1, label="H"))
    # The same symbol, drawn on another drawing under another name and at
    # another scale.
    again = geometry_of(_symbol(doc, "BLOCK_123", circles=1, label="S", scale=2.0))

    # A second circle is a sounder base, not a rounding error.
    assert detector.fingerprint != sounder_base.fingerprint
    assert sounder_base.features["circles"] == 2 and sounder_base.features["radii"] == [0.62, 1.0]
    # The letter inside is part of the symbol.
    assert detector.fingerprint != heat.fingerprint
    assert detector.features["labels"] == ["S"] and heat.features["labels"] == ["H"]
    # The name and the scale are not.
    assert again.fingerprint == detector.fingerprint

    empty = geometry_of(doc.blocks.new(name="NOTHING"))
    assert empty.empty and not detector.empty


def test_a_legend_is_the_drawings_own_dictionary():
    box = Box(left=0, bottom=0, right=1000, top=1000)
    inserts = [
        Insert(block="SD", layer="LEGEND", x=100, y=900, space="Model"),
        Insert(block="AB12", layer="LEGEND", x=100, y=700, space="Model"),
        Insert(block="MCP", layer="LEGEND", x=100, y=500, space="Model"),
        Insert(block="OUTSIDE", layer="FA", x=5000, y=5000, space="Model"),
    ]
    labels = [
        Label(text="SMOKE DETECTOR", x=300, y=900, space="Model"),
        Label(text="SMOKE DETECTOR WITH SOUNDER BASE", x=300, y=700, space="Model"),
        Label(text="1", x=300, y=500, space="Model"),          # a tag, not a description
        Label(text="MANUAL CALL POINT", x=20, y=500, space="Model"),   # to the left: another column
    ]

    entries = legend_entries(inserts, labels, box, {"SD": Geometry("abc", "coarse", {"entities": 2})})

    assert [(entry.block, entry.description) for entry in entries] == [
        ("SD", "SMOKE DETECTOR"),
        ("AB12", "SMOKE DETECTOR WITH SOUNDER BASE"),
    ]
    assert entries[0].fingerprint == "abc"
    # And what the words mean: a sounder base is not a plain detector.
    assert device_in(entries[0].description) == "Smoke detector"
    assert device_in(entries[1].description) == "Smoke detector with sounder base"
    assert legend_entries(inserts, labels, None, {}) == []


def test_the_confidence_is_the_share_of_the_evidence_that_agreed():
    doc = ezdxf.new(setup=True)
    geometry = geometry_of(_symbol(doc, "SOUNDER_BASE", circles=2, label="S"))
    library = SymbolLibrary([_Row("Smoke detector with sounder base", fingerprints=[geometry.fingerprint])])

    # The name alone: it reads as a smoke detector, and that is worth 5 of
    # the 50 an accepted symbol needs. Nothing is accepted on a name.
    named = classify(block="SMOKE_DETECTOR")
    assert named.device == "Smoke detector" and named.confidence == 10.0 and named.state == UNRESOLVED

    # The geometry: the platform has seen this symbol before.
    seen = classify(block="BLOCK_123", geometry=geometry, library=library)
    assert seen.device == "Smoke detector with sounder base" and seen.confidence == 80.0
    assert seen.state == FLAGGED and "geometry" in seen.method

    # The legend agreeing with the geometry: nothing is left to doubt.
    sure = classify(block="BLOCK_123", geometry=geometry, library=library,
                    legend={"BLOCK_123": "SMOKE DETECTOR WITH SOUNDER BASE"})
    assert sure.confidence == 100.0 and sure.state == ACCEPTED
    assert [evidence.source for evidence in sure.evidence] == ["legend", "geometry"]

    # An anonymous block that says what it is: the exit signs of a real
    # drawing are called *U29 and carry TYP=EXIT. One weak source is
    # enough to count it as an exit light, and not nearly enough to say
    # so with confidence -- it is counted, and put up for review.
    anonymous = classify(block="*U29", attributes={"TYP": "EXIT"})
    assert anonymous.device == "Exit light" and anonymous.confidence == 10.0
    assert anonymous.state == UNRESOLVED and anonymous.evidence

    # Nothing at all can speak for it: it is reported as itself, not guessed.
    unknown = classify(block="BLOCK_123")
    assert unknown.device == "BLOCK_123" and unknown.confidence == 0.0 and unknown.state == UNRESOLVED


def test_the_bands_an_engineer_works_by():
    assert band(100) == band(95) == ACCEPTED
    assert band(94.9) == band(80) == FLAGGED
    assert band(79.9) == band(60) == REVIEW
    assert band(59.9) == band(0) == UNRESOLVED


def test_the_block_name_is_reported_when_the_evidence_disagrees_with_it():
    doc = ezdxf.new(setup=True)
    geometry = geometry_of(_symbol(doc, "X", circles=2, label="S"))
    library = SymbolLibrary([_Row("Smoke detector with sounder base", fingerprints=[geometry.fingerprint])])

    found = classify(block="SMOKE_DETECTOR", geometry=geometry, library=library)

    assert found.device == "Smoke detector with sounder base"
    assert found.conflict and "SMOKE_DETECTOR" in found.conflict and "Smoke detector with sounder base" in found.conflict
    # And it is still counted as what the evidence says it is.
    assert found.state == FLAGGED


def test_the_library_recognises_what_it_was_taught_and_says_nothing_when_unsure():
    doc = ezdxf.new(setup=True)
    detector = geometry_of(_symbol(doc, "SD", circles=1, label="S"))
    bell = geometry_of(_symbol(doc, "BELL", circles=1, label="", lines=5))
    library = SymbolLibrary()

    library.learn("Smoke detector", geometry=detector, block="SD")
    assert library.match(detector) == ("Smoke detector", "the same symbol, drawn the same way")
    # A name the library was taught speaks for a symbol with no geometry
    # at all -- and is worth no more than any other name.
    by_name = classify(block="SD", library=library)
    assert by_name.device == "Smoke detector" and by_name.confidence == 10.0
    assert library.match(bell) is None
    assert library.match(None) is None

    # Two devices sharing the coarse shape: the library keeps quiet rather
    # than guessing between them.
    other = Geometry(fingerprint="different", shape=detector.shape, features={"entities": 2})
    library.learn("Heat detector", geometry=other, block="HD")
    assert library.match(Geometry(fingerprint="unseen", shape=detector.shape, features={"entities": 2})) is None
