"""Tests for the text tile map.

The map is read instead of a drawing while a layout changes, so what matters
is that each tile shows the right thing pointing the right way, at the size
the prototype claims, and that nothing placed goes missing from it.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from draftsman.blueprintable import Blueprint, BlueprintBook
from draftsman.constants import Direction

from factorio_forge import cli, textmap
from factorio_forge.inspection import inserter_reach
from prototypes import assembler, belt, chest, inserter, pole, splitter, underground_belt


@pytest.fixture(autouse=True)
def quiet():
    """Draftsman warns about plenty that does not concern these tests."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def rows(text: str) -> dict[int, str]:
    """The map's rows by their y label, without the label."""
    found = {}
    grid = text.split("\n\n")[0].splitlines()[3:]  # past the size line and two axis rows
    for line in grid:
        head, _, rest = line.lstrip().partition(" ")
        try:
            found[int(head)] = rest
        except ValueError:
            continue
    return found


def test_belts_point_the_way_they_move() -> None:
    bp = Blueprint()
    for x, direction in enumerate((Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)):
        bp.entities.append(belt(), tile_position=(x, 0), direction=direction)
    assert rows(textmap.text_map(bp))[0] == "^>v<"


def test_a_machine_fills_its_footprint_with_a_marked_centre() -> None:
    bp = Blueprint()
    machine = bp.entities.append(assembler(), tile_position=(0, 0))
    width, height = machine.tile_width, machine.tile_height
    bp.entities.append(assembler(), tile_position=(width, 0))
    text = textmap.text_map(bp)
    grid = rows(text)
    assert len(grid) == height
    assert all(len(row) == 2 * width for row in grid.values())
    assert grid[(height - 1) // 2].count("A") == 2  # side by side, still two
    assert sum(row.count("a") for row in grid.values()) == 2 * (width * height - 1)
    assert assembler() in text


def test_inserter_arrow_follows_its_drop() -> None:
    bp = Blueprint()
    arm = bp.entities.append(inserter(), tile_position=(0, 0), direction=Direction.NORTH)
    reach = inserter_reach(arm)
    if reach is None:
        pytest.skip("the active data gives this inserter no reach")
    _, (_, drop_y) = reach
    down = drop_y > arm.position.y
    assert textmap.mark_of(arm).symbol in (("↓", "⇓") if down else ("↑", "⇑"))


def test_belt_parts_poles_and_chests_have_their_own_marks() -> None:
    bp = Blueprint()
    bp.entities.append(underground_belt(), tile_position=(0, 0), direction=Direction.EAST, io_type="input")
    bp.entities.append(underground_belt(), tile_position=(2, 0), direction=Direction.EAST, io_type="output")
    bp.entities.append(splitter(), tile_position=(4, 0), direction=Direction.EAST)
    bp.entities.append(pole(), tile_position=(6, 0))
    bp.entities.append(chest(), tile_position=(8, 0))
    text = textmap.text_map(bp)
    first = rows(text)[0]
    assert first[0] == "U" and first[2] == "u"
    assert first[4] == "S"
    assert first[6].lower() == "p" and first[8].lower() == "c"
    for name in (underground_belt(), splitter(), pole(), chest()):
        assert name in text


def test_unknown_entities_are_still_shown() -> None:
    bp = Blueprint()
    bp.entities.append(belt(), tile_position=(0, 0))
    bp.entities.append("no-such-entity-anywhere", tile_position=(2, 0))
    text = textmap.text_map(bp)
    assert rows(text)[0][2] == "?"
    assert "no-such-entity-anywhere" in text and "not in the loaded game data" in text


def test_overlap_is_marked() -> None:
    bp = Blueprint()
    bp.entities.append(belt(), tile_position=(0, 0))
    bp.entities.append(chest(), tile_position=(0, 0))
    assert rows(textmap.text_map(bp))[0] == "!"


def test_axes_carry_real_coordinates() -> None:
    bp = Blueprint()
    bp.entities.append(belt(), tile_position=(-12, -3))
    bp.entities.append(belt(), tile_position=(1, 2))
    text = textmap.text_map(bp)
    assert "x -12..1, y -3..2" in text
    assert set(rows(text)) == set(range(-3, 3))
    tens, units = text.splitlines()[1:3]
    assert tens.split() == ["-10", "0"]
    assert units.strip() == "21098765432101"


def test_cli_map_reads_a_book_entry(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    bp = Blueprint()
    bp.entities.append(belt(), tile_position=(0, 0), direction=Direction.EAST)
    book = BlueprintBook()
    book.blueprints.append(bp)
    path = tmp_path / "book.txt"
    path.write_text(book.to_string(), encoding="utf-8")

    assert cli.main(["map", str(path)]) == 1  # a book needs --index
    assert cli.main(["map", str(path), "--index", "0"]) == 0
    out = capsys.readouterr().out
    assert ">" in out and belt() in out
