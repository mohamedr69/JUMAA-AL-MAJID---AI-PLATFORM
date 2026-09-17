"""The floor-wise device schedule read off a drawing: the legend and the
title block left out, one row per floor, a typical layout counted for
every floor it is issued for."""

from pathlib import Path

import ezdxf
import pytest

from app.services.floor_devices import device_of, extract, floor_of, read_dxf

DEVICES = ["SD", "SD", "SD", "HD", "MCP", "SOUNDER", "SD"]


def _plan(space, left: float, bottom: float, width: float = 4000.0, height: float = 3000.0) -> None:
    """A rectangle of wall on an architectural layer: the plan's extent."""
    space.add_lwpolyline(
        [(left, bottom), (left + width, bottom), (left + width, bottom + height), (left, bottom + height), (left, bottom)],
        dxfattribs={"layer": "A-WALL"},
    )


def _devices(space, left: float, bottom: float, blocks: list[str], layer: str = "FA-DEVICES") -> None:
    for index, block in enumerate(blocks):
        space.add_blockref(block, (left + 200 + index * 300, bottom + 500), dxfattribs={"layer": layer})


def _legend(space, x: float, y: float, blocks: list[str]) -> None:
    """A legend: its title, and one of each symbol in a column under it."""
    space.add_text("LEGEND", dxfattribs={"height": 250}).set_placement((x, y))
    for index, block in enumerate(set(blocks)):
        space.add_blockref(block, (x + 100, y - 300 - index * 300), dxfattribs={"layer": "LEGEND"})


def _blocks(doc, names: list[str]) -> None:
    for name in set(names):
        block = doc.blocks.new(name=name)
        block.add_circle((0, 0), radius=50)


def _save(doc, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path


def _one_floor(tmp_path: Path, title: str, name: str = "floor.dxf") -> Path:
    doc = ezdxf.new(setup=True)
    _blocks(doc, DEVICES)
    space = doc.modelspace()
    _plan(space, 0, 0)
    space.add_text(title, dxfattribs={"height": 400}).set_placement((1500, -400))
    _devices(space, 0, 0, DEVICES)
    _legend(space, 6000, 2500, DEVICES)                       # outside the plan, in its own column
    space.add_blockref("SD", (9000, -3000), dxfattribs={"layer": "TITLE-BLOCK"})   # a symbol in the title block
    return _save(doc, tmp_path / name)


def test_a_block_is_the_device_it_stands_for():
    assert device_of("SD-01") == "Smoke detector"
    assert device_of("FA-HD") == "Heat detector"
    assert device_of("MCP") == "Manual call point"
    assert device_of("SIGA-SD") == "Duct smoke detector"
    assert device_of("757-7A-SS70") == "Speaker/strobe"
    assert device_of("SIGA-CT2") == "Monitor/control module"
    assert device_of("WIDGET-9", layer="FA-SOUNDER") == "Sounder"
    # Nothing is dropped for being unknown: the block speaks for itself.
    assert device_of("XYZ-1") == "XYZ-1"


def test_a_floor_is_placed_in_the_building_and_a_typical_layout_covers_its_range():
    assert floor_of("GROUND FLOOR PLAN").order == 0
    assert floor_of("BASEMENT 2 FLOOR PLAN").order == -2
    assert floor_of("FIRST FLOOR").order == 1
    assert floor_of("LEVEL 12").order == 12
    assert floor_of("ROOF PLAN").order == 900
    assert floor_of("MEZZANINE FLOOR").order == 0.5
    order = [f.name for f in sorted([floor_of(n) for n in ["ROOF", "LEVEL 3", "GROUND FLOOR", "BASEMENT 1", "MEZZANINE"]], key=lambda f: f.order)]
    assert order == ["BASEMENT 1", "GROUND FLOOR", "MEZZANINE", "LEVEL 3", "ROOF"]

    typical = floor_of("TYPICAL 2ND TO 14TH FLOOR PLAN")
    assert typical.is_typical and typical.covers[0] == "Level 2" and typical.covers[-1] == "Level 14" and len(typical.covers) == 13
    assert floor_of("FLOOR 1-14").covers[0] == "Level 1"
    assert floor_of("LEVEL 3").covers == ()


def test_the_legend_and_the_title_block_are_not_counted(tmp_path):
    drawing = _one_floor(tmp_path, "GROUND FLOOR PLAN")
    read = read_dxf(drawing)
    assert len(read.inserts) == len(DEVICES) + len(set(DEVICES)) + 1      # the plan, the legend, the title block

    result = extract([drawing])

    assert [f.floor.name for f in result.floors] == ["GROUND FLOOR PLAN"]
    counted = result.floors[0].devices
    assert counted == {"Smoke detector": 4, "Heat detector": 1, "Manual call point": 1, "Sounder": 1}
    assert result.floors[0].total == len(DEVICES)
    assert result.legend_excluded == len(set(DEVICES))
    assert result.outside_plan_excluded == 1                              # the title block's symbol


def test_every_floor_of_one_drawing_is_its_own_row(tmp_path):
    """Three plans side by side in one model space, as a "GF-FF & RF"
    drawing has them."""
    doc = ezdxf.new(setup=True)
    _blocks(doc, DEVICES)
    space = doc.modelspace()
    for index, (title, blocks) in enumerate([
        ("GROUND FLOOR PLAN", ["SD", "SD", "MCP"]),
        ("FIRST FLOOR PLAN", ["SD", "HD"]),
        ("ROOF PLAN", ["SD"]),
    ]):
        left = index * 6000
        _plan(space, left, 0)
        space.add_text(title, dxfattribs={"height": 400}).set_placement((left + 1500, -400))
        _devices(space, left, 0, blocks)
    drawing = _save(doc, tmp_path / "GF-FF & RF.dxf")

    result = extract([drawing])

    assert [f.floor.name for f in result.floors] == ["GROUND FLOOR PLAN", "FIRST FLOOR PLAN", "ROOF PLAN"]
    assert [f.total for f in result.floors] == [3, 2, 1]
    assert result.floors[0].devices == {"Smoke detector": 2, "Manual call point": 1}
    assert result.devices == ["Heat detector", "Manual call point", "Smoke detector"]


def test_a_typical_layout_is_counted_for_every_floor_it_is_issued_for(tmp_path):
    drawing = _one_floor(tmp_path, "TYPICAL 2ND TO 5TH FLOOR PLAN", name="typical.dxf")

    result = extract([drawing])

    assert [f.floor.name for f in result.floors] == ["Level 2", "Level 3", "Level 4", "Level 5"]
    assert all(f.floor.is_typical for f in result.floors)
    assert all(f.devices == {"Smoke detector": 4, "Heat detector": 1, "Manual call point": 1, "Sounder": 1} for f in result.floors)


def test_a_drawing_per_floor_is_one_schedule(tmp_path):
    ground = _one_floor(tmp_path, "GROUND FLOOR PLAN", name="GF.dxf")
    first = _one_floor(tmp_path, "FIRST FLOOR PLAN", name="FF.dxf")

    result = extract([ground, first])

    assert [f.floor.name for f in result.floors] == ["GROUND FLOOR PLAN", "FIRST FLOOR PLAN"]
    assert [f.source for f in result.floors] == ["GF.dxf", "FF.dxf"]
    assert [entry["file"] for entry in result.files] == ["GF.dxf", "FF.dxf"]


def test_a_drawing_that_names_no_floor_is_filed_under_its_file_name(tmp_path):
    doc = ezdxf.new(setup=True)
    _blocks(doc, ["SD"])
    space = doc.modelspace()
    _plan(space, 0, 0)
    _devices(space, 0, 0, ["SD", "SD"])
    drawing = _save(doc, tmp_path / "Level 07 FA layout.dxf")

    result = extract([drawing])

    assert [f.floor.name for f in result.floors] == ["Level 07 FA layout"]
    assert result.floors[0].devices == {"Smoke detector": 2}


def test_a_file_that_is_not_a_drawing_is_reported_not_raised(tmp_path):
    broken = tmp_path / "notes.dxf"
    broken.write_text("this is not a DXF", encoding="utf-8")

    result = extract([broken])

    assert result.floors == [] and result.warnings and "could not be read" in result.warnings[0]
