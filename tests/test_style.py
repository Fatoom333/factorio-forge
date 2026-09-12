"""Tests for measuring a base's style from its own construction.

Layout is checked with hand-placed entities of known geometry. Identity checks
lean on one non-obvious fact about draftsman: an untouched optional field
already reads back a real default value (a train stop is red before anyone
paints it), so "was this a choice" has to be answered from what actually
serialises, not from the live attribute -- see the docstring on
``measure_identity``.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction

from factorio_forge import style
from prototypes import assembler, belt, constant_combinator, lamp, pole, train_stop


@pytest.fixture(autouse=True)
def quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


class TestSpacing:
    def test_typical_distance_between_same_type_neighbours(self) -> None:
        bp = Blueprint()
        name = assembler()
        bp.entities.append(name, tile_position=(0, 0))
        bp.entities.append(name, tile_position=(3, 0))
        bp.entities.append(name, tile_position=(6, 0))

        result = style.measure_layout(list(bp.entities))["spacing"][name]
        assert result["tiles"] == 3.0
        assert result["sample_size"] == 3
        assert result["agreement"] == 1.0

    def test_a_lone_entity_has_no_spacing(self) -> None:
        bp = Blueprint()
        bp.entities.append(assembler(), tile_position=(0, 0))
        assert style.measure_layout(list(bp.entities))["spacing"] == {}


class TestAlignment:
    def test_everything_on_a_two_tile_grid(self) -> None:
        bp = Blueprint()
        name = pole()
        bp.entities.append(name, tile_position=(0, 0))
        bp.entities.append(name, tile_position=(2, 4))
        bp.entities.append(name, tile_position=(6, 2))

        assert style.measure_layout(list(bp.entities))["alignment"] == {
            "step_x": 2,
            "step_y": 2,
        }

    def test_no_pattern_is_a_one_tile_step(self) -> None:
        bp = Blueprint()
        name = pole()
        bp.entities.append(name, tile_position=(0, 0))
        bp.entities.append(name, tile_position=(3, 0))
        bp.entities.append(name, tile_position=(5, 1))

        assert style.measure_layout(list(bp.entities))["alignment"] == {
            "step_x": 1,
            "step_y": 1,
        }


class TestSymmetry:
    def test_a_mirrored_pair_is_fully_symmetric(self) -> None:
        bp = Blueprint()
        name = pole()
        bp.entities.append(name, tile_position=(0, 0))
        bp.entities.append(name, tile_position=(4, 0))

        assert style.measure_layout(list(bp.entities))["symmetry"]["horizontal"] == 1.0

    def test_an_unmatched_layout_is_not_symmetric(self) -> None:
        bp = Blueprint()
        bp.entities.append(pole(), tile_position=(0, 0))
        bp.entities.append(assembler(), tile_position=(4, 0))

        assert style.measure_layout(list(bp.entities))["symmetry"]["horizontal"] == 0.0


class TestOrientation:
    def test_a_shared_facing_dominates(self) -> None:
        bp = Blueprint()
        name = belt()
        bp.entities.append(name, tile_position=(0, 0), direction=Direction.EAST)
        bp.entities.append(name, tile_position=(1, 0), direction=Direction.EAST)
        bp.entities.append(name, tile_position=(2, 0), direction=Direction.NORTH)

        orientation = style.measure_layout(list(bp.entities))["orientation"]
        assert orientation[str(int(Direction.EAST))] == pytest.approx(2 / 3, abs=0.01)
        assert orientation[str(int(Direction.NORTH))] == pytest.approx(1 / 3, abs=0.01)


class TestIdentity:
    def test_defaults_are_not_reported_as_a_choice(self) -> None:
        bp = Blueprint()
        bp.entities.append(train_stop(), tile_position=(0, 0))

        result = style.measure_identity(list(bp.entities))
        assert result["station_names"] == []
        assert result["colors"] == {}

    def test_a_customised_station_name_is_reported(self) -> None:
        bp = Blueprint()
        bp.entities.append(train_stop(), tile_position=(0, 0))
        bp.entities[0].station = "Iron Smelting 3"

        result = style.measure_identity(list(bp.entities))
        assert result["station_names"] == ["Iron Smelting 3"]

    def test_a_customised_colour_is_reported(self) -> None:
        bp = Blueprint()
        bp.entities.append(lamp(), tile_position=(0, 0))
        bp.entities[0].color = (0, 1, 0, 1)

        result = style.measure_identity(list(bp.entities))
        assert sum(result["colors"].values()) == 1

    def test_an_annotation_on_a_combinator_is_reported(self) -> None:
        bp = Blueprint()
        bp.entities.append(constant_combinator(), tile_position=(0, 0))
        bp.entities[0].player_description = "buffer for requester chests"

        result = style.measure_identity(list(bp.entities))
        assert result["annotations"] == ["buffer for requester chests"]

    def test_tags_are_counted_by_key(self) -> None:
        bp = Blueprint()
        bp.entities.append(constant_combinator(), tile_position=(0, 0))
        bp.entities[0].tags = {"purpose": "buffer"}

        result = style.measure_identity(list(bp.entities))
        assert result["tag_keys"] == {"purpose": 1}


class TestCache:
    def test_round_trip(self, tmp_path: Path) -> None:
        measured = style.Style(
            measured_at="2026-09-12T00:00:00+00:00",
            source_files=("region-1.txt",),
            layout={"alignment": {"step_x": 2, "step_y": 2}},
            identity={"station_names": ["Iron Smelting 3"]},
        )
        measured.write(tmp_path)

        assert style.Style.load(tmp_path) == measured

    def test_loading_something_absent(self, tmp_path: Path) -> None:
        with pytest.raises(style.StyleError, match="no measured style"):
            style.Style.load(tmp_path)


class TestMeasure:
    def test_no_reference_blueprints_is_a_named_error(self, tmp_path: Path) -> None:
        with pytest.raises(style.StyleError, match="no reference blueprints"):
            style.measure(tmp_path)

    def test_measuring_from_files_on_disk(self, tmp_path: Path) -> None:
        bp = Blueprint()
        name = pole()
        bp.entities.append(name, tile_position=(0, 0))
        bp.entities.append(name, tile_position=(3, 0))
        (tmp_path / "region-1.txt").write_text(bp.to_string(), encoding="utf-8")

        measured = style.measure(tmp_path)
        assert measured.source_files == ("region-1.txt",)
        assert measured.layout["spacing"][name]["tiles"] == 3.0

    def test_new_reference_blueprints_are_reported(self, tmp_path: Path) -> None:
        bp = Blueprint()
        bp.entities.append(pole(), tile_position=(0, 0))
        (tmp_path / "region-1.txt").write_text(bp.to_string(), encoding="utf-8")
        measured = style.measure(tmp_path)

        (tmp_path / "region-2.txt").write_text(bp.to_string(), encoding="utf-8")
        assert style.new_reference_blueprints(measured, tmp_path) == ["region-2.txt"]

    def test_nothing_new_once_measured(self, tmp_path: Path) -> None:
        bp = Blueprint()
        bp.entities.append(pole(), tile_position=(0, 0))
        (tmp_path / "region-1.txt").write_text(bp.to_string(), encoding="utf-8")

        measured = style.measure(tmp_path)
        assert style.new_reference_blueprints(measured, tmp_path) == []
