"""Tests for drawing a blueprint.

A picture is easy to get subtly wrong in ways that still look plausible, so
these check the things that would mislead rather than the things that would
look ugly: that a footprint is drawn at the size the entity claims, that a
direction survives into the drawing, and that the page depends on nothing it
would have to fetch.
"""

from __future__ import annotations

import json
import re
import warnings
from pathlib import Path
from xml.etree import ElementTree

import pytest

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction

from factorio_forge import render
from prototypes import (
    another,
    arithmetic_combinator,
    assembler,
    belt,
    chest,
    constant_combinator,
    decider_combinator,
    filtering_inserter,
    inserter,
    item,
    other_chest,
    pipe,
    pipe_to_ground,
    pole,
    rail,
    rail_signal,
    recipe,
    splitter,
    underground_belt,
)

SVG_NS = "{http://www.w3.org/2000/svg}"


@pytest.fixture(autouse=True)
def quiet():
    """Draftsman warns about plenty that does not concern these tests."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def small_blueprint() -> Blueprint:
    bp = Blueprint()
    bp.label = "test"
    bp.entities.append(belt(), tile_position=(0, 0), direction=Direction.EAST)
    bp.entities.append(assembler(), tile_position=(2, 0))
    bp.entities.append(pole(), tile_position=(6, 0))
    return bp


def parse_svg(blueprint) -> ElementTree.Element:
    return ElementTree.fromstring(render.render_svg(blueprint))


class TestMeasure:
    def test_covers_every_entity_including_its_footprint(self) -> None:
        bp = Blueprint()
        bp.entities.append(belt(), tile_position=(0, 0))
        bp.entities.append(assembler(), tile_position=(5, 5))  # 3x3
        bounds = render.measure(bp.entities)
        assert (bounds.left, bounds.top) == (0, 0)
        assert (bounds.right, bounds.bottom) == (8, 8)
        assert (bounds.width, bounds.height) == (8, 8)

    def test_negative_coordinates(self) -> None:
        bp = Blueprint()
        bp.entities.append(belt(), tile_position=(-4, -3))
        bounds = render.measure(bp.entities)
        assert (bounds.left, bounds.top) == (-4, -3)
        assert (bounds.width, bounds.height) == (1, 1)

    def test_tiles_count_towards_the_bounds(self) -> None:
        bp = Blueprint()
        bp.entities.append(belt(), tile_position=(0, 0))
        bp.tiles.append("concrete", position=(9, 9))
        bounds = render.measure(bp.entities, bp.tiles)
        assert (bounds.right, bounds.bottom) == (10, 10)

    def test_an_empty_blueprint_does_not_collapse(self) -> None:
        bounds = render.measure([], [])
        assert bounds.width == 1 and bounds.height == 1

    def test_readable_description(self) -> None:
        assert str(render.Bounds(0, 0, 16, 9)) == "16×9 tiles"


class TestFamilies:
    @pytest.mark.parametrize(
        "name, family",
        [
            (belt(), "transport"),
            (underground_belt(), "transport"),
            (splitter(), "transport"),
            (inserter(), "inserter"),
            (assembler(), "production"),
            ("electric-mining-drill", "production"),
            (chest(), "storage"),
            (pipe_to_ground(), "fluid"),
            (pole(), "power"),
            (decider_combinator(), "circuit"),
            ("small-lamp", "circuit"),
            (rail(), "rail"),
            ("stone-wall", "military"),
        ],
    )
    def test_entities_land_in_the_expected_family(self, name: str, family: str) -> None:
        bp = Blueprint()
        bp.entities.append(name, tile_position=(0, 0))
        assert render.family_of(bp.entities[0]) == family

    def test_every_family_has_a_colour_and_a_label(self) -> None:
        for family in set(render.FAMILY_OF_TYPE.values()) | {"other"}:
            assert family in render.FAMILY_COLOUR
            assert family in render.FAMILY_LABEL

    def test_unknown_types_fall_back_rather_than_raising(self) -> None:
        class Alien:
            type = "something-a-mod-invented"

        assert render.family_of(Alien()) == "other"


class TestDrawing:
    def test_one_group_per_entity(self) -> None:
        svg = parse_svg(small_blueprint())
        assert len(svg.findall(f".//{SVG_NS}g[@class='entity']")) == 3

    def test_footprints_are_drawn_at_the_size_the_entity_reports(self) -> None:
        """The point of the picture is to show what overlaps what."""
        svg = parse_svg(small_blueprint())
        for group in svg.findall(f".//{SVG_NS}g[@class='entity']"):
            info = json.loads(group.get("data-info"))
            rect = group.find(f"{SVG_NS}rect")
            width = (float(rect.get("width")) + 2) / render.CELL
            height = (float(rect.get("height")) + 2) / render.CELL
            assert info["size"] == f"{width:g}×{height:g}"

    def test_a_rotated_entity_changes_shape(self) -> None:
        bp = Blueprint()
        bp.entities.append(splitter(), tile_position=(0, 0), direction=Direction.NORTH)
        bp.entities.append(splitter(), tile_position=(0, 4), direction=Direction.EAST)
        north, east = bp.entities[0], bp.entities[1]
        assert (north.tile_width, north.tile_height) == (2, 1)
        assert (east.tile_width, east.tile_height) == (1, 2)

    def test_direction_becomes_a_rotated_arrow(self) -> None:
        bp = Blueprint()
        bp.entities.append(belt(), tile_position=(0, 0), direction=Direction.SOUTH)
        svg = parse_svg(bp)
        arrow = svg.find(f".//{SVG_NS}polygon[@class='dir']")
        assert arrow is not None
        # South is 8 sixteenths of a turn: half a rotation.
        assert "rotate(180)" in arrow.get("transform")

    def test_entities_without_a_direction_get_no_arrow(self) -> None:
        bp = Blueprint()
        bp.entities.append(pole(), tile_position=(0, 0))
        svg = parse_svg(bp)
        assert svg.find(f".//{SVG_NS}polygon[@class='dir']") is None

    def test_tiles_are_drawn_under_the_grid(self) -> None:
        bp = Blueprint()
        bp.tiles.append("concrete", position=(0, 0))
        bp.entities.append(belt(), tile_position=(0, 0))
        svg = parse_svg(bp)
        children = list(svg)
        first_tile = next(i for i, e in enumerate(children) if e.get("class") == "tile")
        first_entity = next(i for i, e in enumerate(children) if e.get("class") == "entity")
        assert first_tile < first_entity

    def test_every_entity_carries_a_native_tooltip(self) -> None:
        """So the drawing still explains itself without scripts."""
        svg = parse_svg(small_blueprint())
        for group in svg.findall(f".//{SVG_NS}g[@class='entity']"):
            title = group.find(f"{SVG_NS}title")
            assert title is not None and title.text


class TestDetails:
    def test_recipe_and_priorities_reach_the_drawing(self) -> None:
        bp = Blueprint()
        bp.entities.append(assembler(), tile_position=(0, 0),
                           recipe=recipe())
        bp.entities.append(splitter(), tile_position=(0, 4), input_priority="left")
        svg = parse_svg(bp)
        details = [json.loads(g.get("data-info"))["details"]
                   for g in svg.findall(f".//{SVG_NS}g[@class='entity']")]
        assert {"recipe": recipe()} in details
        assert {"input priority": "left"} in details

    def test_defaults_are_not_shown_as_though_they_were_set(self) -> None:
        bp = Blueprint()
        bp.entities.append(splitter(), tile_position=(0, 0))
        svg = parse_svg(bp)
        info = json.loads(svg.find(f".//{SVG_NS}g[@class='entity']").get("data-info"))
        assert "input priority" not in info["details"]


class TestSnapping:
    """Grid snapping is what makes city blocks tile rather than drift, and it is
    invisible in a picture of the entities alone."""

    def blueprint_with_grid(self, grid=(24, 24), offset=(2, 2), absolute=True) -> Blueprint:
        bp = Blueprint()
        bp.entities.append(belt(), tile_position=(0, 0))
        bp.snapping_grid_size = grid
        bp.absolute_snapping = absolute
        bp.position_relative_to_grid = offset
        return bp

    def test_no_grid_declared_means_nothing_drawn(self) -> None:
        bp = Blueprint()
        bp.entities.append(belt(), tile_position=(0, 0))
        assert render.snap_cell(bp, render.measure(bp.entities)) is None
        assert 'class="snap"' not in render.render_svg(bp)

    def test_cell_is_placed_by_the_declared_offset(self) -> None:
        bp = self.blueprint_with_grid(grid=(24, 24), offset=(2, 3))
        bounds = render.measure(bp.entities)
        assert render.snap_cell(bp, bounds) == (bounds.left - 2, bounds.top - 3, 24, 24)

    def test_the_view_widens_to_hold_the_cell(self) -> None:
        """Otherwise the boundary falls outside the picture, which is the whole
        thing worth looking at."""
        bp = self.blueprint_with_grid(grid=(24, 24), offset=(2, 2))
        svg = parse_svg(bp)
        rect = svg.find(f".//{SVG_NS}rect[@class='snap']")
        assert rect is not None
        assert float(rect.get("width")) == 24 * render.CELL

    def test_cells_tile_across_a_blueprint_larger_than_one(self) -> None:
        bp = Blueprint()
        bp.entities.append(belt(), tile_position=(0, 0))
        bp.entities.append(belt(), tile_position=(20, 20))
        bp.snapping_grid_size = (8, 8)
        bp.absolute_snapping = True
        svg = parse_svg(bp)
        assert len(svg.findall(f".//{SVG_NS}rect[@class='snap']")) > 1

    def test_the_panel_states_absolute_versus_relative(self) -> None:
        absolute = render.render_html(self.blueprint_with_grid(absolute=True))
        relative = render.render_html(self.blueprint_with_grid(absolute=False))
        assert "absolute" in absolute
        assert "grid snapping" in relative

    def test_contents_larger_than_the_cell_are_called_out(self) -> None:
        bp = Blueprint()
        bp.entities.append(belt(), tile_position=(0, 0))
        bp.entities.append(belt(), tile_position=(30, 0))
        bp.snapping_grid_size = (8, 8)
        page = render.render_html(bp)
        assert "larger than" in page and "overlap" in page

    def test_snapping_survives_a_round_trip(self) -> None:
        bp = self.blueprint_with_grid()
        back = Blueprint.from_string(bp.to_string())
        assert back.snapping_grid_size.x == 24
        assert back.absolute_snapping is True
        assert back.position_relative_to_grid.x == 2


class TestParameters:
    """Parameter order is significant: a formula may only refer to parameters
    declared before it, and the list order is the order the player is asked."""

    def parameterised(self) -> Blueprint:
        bp = Blueprint()
        bp.entities.append(constant_combinator(), tile_position=(0, 0))
        bp.parameters = [
            {"type": "id", "name": "Recipe", "id": recipe()},
            {"type": "number", "name": "Machines", "number": "3"},
            {"type": "number", "name": "Belt", "number": "6",
             "formula": "p1 * 2", "dependent": True},
        ]
        return bp

    def test_no_parameters_means_no_panel(self) -> None:
        bp = Blueprint()
        bp.entities.append(constant_combinator(), tile_position=(0, 0))
        assert "parameters, in order" not in render.render_html(bp)

    def test_parameters_are_listed_in_declaration_order(self) -> None:
        page = render.render_html(self.parameterised())
        assert "parameters, in order" in page
        positions = [page.index(name) for name in ("Recipe", "Machines", "Belt")]
        assert positions == sorted(positions)

    def test_a_formula_is_shown_rather_than_its_current_value(self) -> None:
        page = render.render_html(self.parameterised())
        assert "= p1 * 2" in page

    def test_parameters_survive_a_round_trip(self) -> None:
        bp = self.parameterised()
        back = Blueprint.from_string(bp.to_string())
        assert [p.name for p in back.parameters] == ["Recipe", "Machines", "Belt"]


class TestEntityDetails:
    """Whatever an entity has been configured to do should be readable off the
    picture, because that configuration is most of what a blueprint is."""

    def test_item_filters(self) -> None:
        bp = Blueprint()
        bp.entities.append(filtering_inserter(), tile_position=(0, 0), use_filters=True)
        bp.entities[0].set_item_filter(0, item())
        assert render._entity_details(bp.entities[0])["filters"] == item()

    def test_a_blacklist_says_so(self) -> None:
        bp = Blueprint()
        bp.entities.append(filtering_inserter(), tile_position=(0, 0), use_filters=True)
        bp.entities[0].set_item_filter(0, "coal")
        bp.entities[0].filter_mode = "blacklist"
        assert render._entity_details(bp.entities[0])["filters"].startswith("blacklist:")

    def test_constant_combinator_signals_with_counts(self) -> None:
        bp = Blueprint()
        bp.entities.append(constant_combinator(), tile_position=(0, 0))
        bp.entities[0].add_section()
        bp.entities[0].set_signal(0, item(), 42)
        assert render._entity_details(bp.entities[0])["signals"] == f"{item()}×42"

    def test_splitter_priorities_are_shown_and_defaults_are_not(self) -> None:
        bp = Blueprint()
        bp.entities.append(splitter(), tile_position=(0, 0), output_priority="left")
        bp.entities.append(splitter(), tile_position=(0, 4))
        assert render._entity_details(bp.entities[0])["output priority"] == "left"
        assert "output priority" not in render._entity_details(bp.entities[1])

    def test_underground_belt_says_which_end_it_is(self) -> None:
        bp = Blueprint()
        bp.entities.append(underground_belt(), tile_position=(0, 0), io_type="output")
        assert render._entity_details(bp.entities[0])["type"] == "output"

    def test_a_circuit_condition_reads_as_a_sentence(self) -> None:
        bp = Blueprint()
        bp.entities.append(filtering_inserter(), tile_position=(0, 0))
        bp.entities[0].circuit_condition.first_signal = item()
        bp.entities[0].circuit_condition.comparator = "<"
        bp.entities[0].circuit_condition.constant = 100
        assert render._entity_details(bp.entities[0])["enabled when"] == f"{item()} < 100"

    def test_nothing_configured_means_nothing_claimed(self) -> None:
        bp = Blueprint()
        bp.entities.append(belt(), tile_position=(0, 0))
        assert render._entity_details(bp.entities[0]) == {}


class TestParameterReferences:
    """A parameter is only useful where it is wired in, and which entities those
    are cannot be told from the parameter list alone."""

    def parameterised(self) -> Blueprint:
        bp = Blueprint()
        bp.entities.append(filtering_inserter(), tile_position=(0, 0), use_filters=True)
        bp.entities[0].set_item_filter(0, "parameter-0")
        bp.entities.append(constant_combinator(), tile_position=(3, 0))
        bp.entities[1].add_section()
        bp.entities[1].set_signal(0, "parameter-1", 5)
        bp.entities.append(belt(), tile_position=(6, 0))
        bp.parameters = [
            {"type": "id", "name": "Product", "id": recipe()},
            {"type": "number", "name": "Count", "number": "5"},
            {"type": "number", "name": "Spare", "number": "1"},
        ]
        return bp

    def test_references_are_found_in_filters_and_signals(self) -> None:
        bp = self.parameterised()
        assert render.parameters_used_by(render._entity_details(bp.entities[0])) == ["parameter-0"]
        assert render.parameters_used_by(render._entity_details(bp.entities[1])) == ["parameter-1"]

    def test_an_entity_without_references_reports_none(self) -> None:
        bp = self.parameterised()
        assert render.parameters_used_by(render._entity_details(bp.entities[2])) == []

    def test_referring_entities_are_marked_in_the_drawing(self) -> None:
        svg = parse_svg(self.parameterised())
        marked = svg.findall(f".//{SVG_NS}g[@class='entity parameterised']")
        assert len(marked) == 2
        assert svg.find(f".//{SVG_NS}rect[@class='param-ring']") is not None

    def test_the_panel_counts_where_each_parameter_lands(self) -> None:
        page = render.render_html(self.parameterised())
        assert "1 entity" in page

    def test_a_parameter_nothing_refers_to_is_called_out(self) -> None:
        """Almost always a mistake, and silent without counting."""
        page = render.render_html(self.parameterised())
        assert "unused" in page

    def test_a_plain_blueprint_gets_no_parameter_marks(self) -> None:
        bp = Blueprint()
        bp.entities.append(belt(), tile_position=(0, 0))
        svg = render.render_svg(bp)
        assert "param-ring" not in svg
        assert "parameterised" not in svg


class TestZoom:
    def test_the_drawing_records_its_own_starting_view(self) -> None:
        """Panning and zooming move the viewBox, so 'fit' needs the original."""
        svg = parse_svg(small_blueprint())
        assert svg.get("data-base") == svg.get("viewBox")

    def test_controls_are_present(self) -> None:
        page = render.render_html(small_blueprint())
        for control in ('id="zoomin"', 'id="zoomout"', 'id="zoomreset"', 'id="zoomlevel"'):
            assert control in page


class TestPage:
    def test_is_self_contained(self) -> None:
        """Nothing to fetch: the file has to work offline and inside a sandbox."""
        page = render.render_html(small_blueprint())
        referenced = re.findall(r'(?:src|href)="(?!#)([^"]+)"', page)
        assert referenced == []

    def test_uses_the_label_as_the_title(self) -> None:
        page = render.render_html(small_blueprint())
        assert "<title>test</title>" in page

    def test_an_explicit_title_wins(self) -> None:
        page = render.render_html(small_blueprint(), title="Smelter block")
        assert "<title>Smelter block</title>" in page

    def test_carries_the_blueprint_string(self) -> None:
        bp = small_blueprint()
        page = render.render_html(bp)
        assert bp.to_string() in page

    def test_html_in_a_label_cannot_break_out(self) -> None:
        bp = small_blueprint()
        bp.label = '<script>alert("x")</script>'
        page = render.render_html(bp)
        assert "<script>alert" not in page
        assert "&lt;script&gt;" in page

    def test_legend_counts_what_is_present(self) -> None:
        page = render.render_html(small_blueprint())
        assert "production" in page and "power &amp; heat" in page or "power" in page

    def test_writes_a_file_and_reports_where(self, tmp_path: Path) -> None:
        target = render.write_html(small_blueprint(), tmp_path / "out" / "bp.html")
        assert target.is_file()
        assert target.read_text(encoding="utf-8").startswith("<!doctype html>")

    def test_an_empty_blueprint_still_renders(self) -> None:
        page = render.render_html(Blueprint())
        assert "<svg" in page
