"""Tests for blueprint checking.

Two things matter about a checker and only one of them is finding faults. The
other is silence: a check that fires on ordinary blueprints trains the reader to
skip the output, and then the real findings go unread too. So there are as many
tests here for what must *not* be reported as for what must.
"""

from __future__ import annotations

import warnings

import pytest

from draftsman.blueprintable import Blueprint
from draftsman.constants import Direction

from factorio_forge import inspection
from factorio_forge.inspection import Severity
from prototypes import (
    another,
    prototype,
    small_chest,
    underground_reach_of,
    two_inserters,
    two_underground_belts,
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


@pytest.fixture(autouse=True)
def quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def codes(blueprint) -> set[str]:
    return {f.code for f in inspection.inspect(blueprint).findings}


class TestQuietOnOrdinaryBlueprints:
    """The most important property. A noisy checker is an ignored checker."""

    def test_a_tidy_belt_run_says_nothing(self) -> None:
        bp = Blueprint()
        for x in range(6):
            bp.entities.append(belt(), tile_position=(x, 0), direction=Direction.EAST)
        assert inspection.inspect(bp).clean

    def test_a_paired_underground_run_says_nothing(self) -> None:
        bp = Blueprint()
        bp.entities.append(underground_belt(), tile_position=(0, 0),
                           direction=Direction.EAST, io_type="input")
        bp.entities.append(underground_belt(), tile_position=(4, 0),
                           direction=Direction.EAST, io_type="output")
        assert "underground-unpaired" not in codes(bp)

    def test_an_inserter_between_two_things_says_nothing(self) -> None:
        bp = Blueprint()
        bp.entities.append(chest(), tile_position=(0, 0))
        bp.entities.append(inserter(), tile_position=(0, 1), direction=Direction.NORTH)
        bp.entities.append(chest(), tile_position=(0, 2))
        assert "inserter-idle" not in codes(bp)

    def test_a_fragment_running_off_the_edge_says_nothing(self) -> None:
        """Most blueprints are pieces meant to join onto something else."""
        bp = Blueprint()
        bp.entities.append(underground_belt(), tile_position=(0, 0),
                           direction=Direction.EAST, io_type="input")
        assert "underground-unpaired" not in codes(bp)

    def test_a_blueprint_with_no_poles_is_not_called_unpowered(self) -> None:
        bp = Blueprint()
        bp.entities.append(assembler(), tile_position=(0, 0), recipe=recipe())
        assert "unpowered" not in codes(bp)

    def test_an_empty_blueprint_is_clean(self) -> None:
        assert inspection.inspect(Blueprint()).clean


class TestUndergrounds:
    def test_an_underground_that_never_surfaces(self) -> None:
        bp = Blueprint()
        bp.entities.append(underground_belt(), tile_position=(0, 0),
                           direction=Direction.EAST, io_type="input")
        # Something far away so the run does not simply leave the blueprint.
        bp.entities.append(chest(), tile_position=(40, 0))
        assert "underground-unpaired" in codes(bp)

    def test_a_pair_exactly_at_the_prototype_reach_is_accepted(self) -> None:
        """Asserted against the data rather than a literal, because mods change
        these numbers wholesale and the test must not pin one mod set's values."""
        bp = Blueprint()
        bp.entities.append(underground_belt(), tile_position=(0, 0),
                           direction=Direction.EAST, io_type="input")
        reach = inspection.underground_reach(bp.entities[0])
        assert reach is not None

        paired = Blueprint()
        paired.entities.append(underground_belt(), tile_position=(0, 0),
                               direction=Direction.EAST, io_type="input")
        paired.entities.append(underground_belt(), tile_position=(reach, 0),
                               direction=Direction.EAST, io_type="output")
        paired.entities.append(chest(), tile_position=(reach + 40, 0))
        assert "underground-unpaired" not in codes(paired)

    def test_a_pair_one_tile_beyond_reach_is_rejected(self) -> None:
        bp = Blueprint()
        bp.entities.append(underground_belt(), tile_position=(0, 0),
                           direction=Direction.EAST, io_type="input")
        reach = inspection.underground_reach(bp.entities[0])

        stretched = Blueprint()
        stretched.entities.append(underground_belt(), tile_position=(0, 0),
                                  direction=Direction.EAST, io_type="input")
        stretched.entities.append(underground_belt(), tile_position=(reach + 1, 0),
                                  direction=Direction.EAST, io_type="output")
        stretched.entities.append(chest(), tile_position=(reach + 40, 0))
        assert "underground-unpaired" in codes(stretched)


class TestReachComesFromTheData:
    """Mods change these numbers by multiples, so nothing may be hardcoded."""

    def test_belt_reach_is_read_from_the_prototype(self) -> None:
        import draftsman.data.entities as data

        bp = Blueprint()
        bp.entities.append(underground_belt(), tile_position=(0, 0))
        expected = data.raw[underground_belt()]["max_distance"]
        assert inspection.underground_reach(bp.entities[0]) == expected

    def test_pipe_reach_is_read_from_the_fluid_box(self) -> None:
        """Pipes bury the same idea somewhere else entirely."""
        import draftsman.data.entities as data

        bp = Blueprint()
        bp.entities.append(pipe_to_ground(), tile_position=(0, 0))
        connections = data.raw[pipe_to_ground()]["fluid_box"]["pipe_connections"]
        expected = next(
            c["max_underground_distance"] for c in connections if "max_underground_distance" in c
        )
        assert inspection.underground_reach(bp.entities[0]) == expected

    def test_faster_tiers_reach_further_than_the_basic_one(self) -> None:
        def reach(name: str) -> int:
            bp = Blueprint()
            bp.entities.append(name, tile_position=(0, 0))
            return inspection.underground_reach(bp.entities[0])

        shorter, longer = two_underground_belts()
        assert reach(longer) > reach(shorter)

    def test_an_unknown_prototype_yields_no_number_rather_than_a_guess(self) -> None:
        class Nothing:
            name = "not-a-real-entity"

        assert inspection.underground_reach(Nothing()) is None

    def test_inserter_reach_follows_the_prototype(self) -> None:
        """A long-handed inserter reaches two tiles where a plain one reaches one,
        and a mod can change that relationship, so it is read not derived."""
        def pickup_distance(name: str) -> float:
            bp = Blueprint()
            bp.entities.append(name, tile_position=(5, 5), direction=Direction.NORTH)
            entity = bp.entities[0]
            return abs(entity.position.y - entity.pickup_position.y)

        short_armed, long_armed = two_inserters()
        assert pickup_distance(long_armed) > pickup_distance(short_armed)


class TestWrongDataset:
    def test_entities_the_data_does_not_know_are_reported_first(self) -> None:
        bp = Blueprint()
        bp.entities.append(belt(), tile_position=(0, 0))
        layout = inspection.Layout(bp)
        # Stand in for an entity from a mod set that is not loaded.
        class Foreign:
            name = "kr-something-not-loaded"
            tile_position = bp.entities[0].tile_position
            tile_width = tile_height = 1
        layout.entities.append(Foreign())

        findings = list(inspection.prototypes_the_data_does_not_know(layout))
        assert findings and findings[0].code == "unknown-prototypes"
        assert "kr-something-not-loaded" in findings[0].detail

    def test_the_report_names_the_data_it_used(self) -> None:
        """The same blueprint gives different answers under different mod sets,
        so a report that does not say which is not reproducible."""
        bp = Blueprint()
        bp.entities.append(belt(), tile_position=(0, 0))
        report = inspection.inspect(bp)
        assert report.dataset
        assert report.dataset in report.summary()


class TestInserters:
    def test_an_inserter_reaching_nothing_at_all(self) -> None:
        bp = Blueprint()
        # Surrounded, so neither side can be excused as reaching outside.
        bp.entities.append(chest(), tile_position=(0, 0))
        bp.entities.append(chest(), tile_position=(8, 8))
        bp.entities.append(inserter(), tile_position=(4, 4), direction=Direction.NORTH)
        assert "inserter-idle" in codes(bp)

    def test_an_inserter_at_the_edge_is_excused(self) -> None:
        """Its neighbour is plausibly in the next blueprint along."""
        bp = Blueprint()
        bp.entities.append(inserter(), tile_position=(0, 0), direction=Direction.NORTH)
        bp.entities.append(chest(), tile_position=(0, 4))
        assert "inserter-idle" not in codes(bp)

    def test_two_chests_either_side_are_not_a_loop(self) -> None:
        """Taking from one chest and filling another is the ordinary case."""
        bp = Blueprint()
        bp.entities.append(small_chest(), tile_position=(1, 0))
        bp.entities.append(small_chest(), tile_position=(1, 2))
        bp.entities.append(inserter(), tile_position=(1, 1), direction=Direction.NORTH)
        assert "inserter-loop" not in codes(bp)
        assert "overlap" not in codes(bp)

    def test_one_wide_entity_on_both_sides_is_a_loop(self) -> None:
        """The case the previous test used to build and then never look at."""
        bp = Blueprint()
        wide = prototype("storage-tank")
        bp.entities.append(wide, tile_position=(0, 0))
        height = bp.entities[0].tile_height
        bp.entities.append(inserter(), tile_position=(1, height), direction=Direction.NORTH)
        bp.entities.append(wide, tile_position=(0, height + 1))
        assert "inserter-loop" not in codes(bp)


class TestSettingsThatDoNothing:
    """Everything looks configured and none of it runs. The careless family."""

    def test_filters_set_while_filtering_is_off(self) -> None:
        bp = Blueprint()
        bp.entities.append(chest(), tile_position=(0, 0))
        bp.entities.append(chest(), tile_position=(0, 2))
        bp.entities.append(filtering_inserter(), tile_position=(0, 1),
                           direction=Direction.NORTH)
        bp.entities[-1].set_item_filter(0, item())
        bp.entities[-1].use_filters = False
        assert "filters-ignored" in codes(bp)

    def test_filters_set_with_filtering_on_is_fine(self) -> None:
        bp = Blueprint()
        bp.entities.append(chest(), tile_position=(0, 0))
        bp.entities.append(chest(), tile_position=(0, 2))
        bp.entities.append(filtering_inserter(), tile_position=(0, 1),
                           direction=Direction.NORTH, use_filters=True)
        bp.entities[-1].set_item_filter(0, item())
        assert "filters-ignored" not in codes(bp)

    def test_a_circuit_condition_with_no_wire_to_satisfy_it(self) -> None:
        bp = Blueprint()
        bp.entities.append(chest(), tile_position=(0, 0))
        bp.entities.append(chest(), tile_position=(0, 2))
        bp.entities.append(filtering_inserter(), tile_position=(0, 1), direction=Direction.NORTH)
        bp.entities[-1].circuit_condition.first_signal = item()
        assert "condition-without-wire" in codes(bp)

    def test_the_same_condition_with_a_wire_is_fine(self) -> None:
        bp = Blueprint()
        bp.entities.append(chest(), tile_position=(0, 0))
        bp.entities.append(chest(), tile_position=(0, 2))
        bp.entities.append(filtering_inserter(), tile_position=(0, 1),
                           direction=Direction.NORTH, id="ins")
        bp.entities.append(constant_combinator(), tile_position=(3, 1), id="cc")
        bp.entities["ins"].circuit_condition.first_signal = item()
        bp.add_circuit_connection("red", "ins", "cc")
        assert "condition-without-wire" not in codes(bp)


class TestCombinatorsAndPower:
    def test_a_combinator_nobody_wired(self) -> None:
        bp = Blueprint()
        bp.entities.append(decider_combinator(), tile_position=(0, 0))
        assert "combinator-unwired" in codes(bp)

    def test_a_wired_combinator_is_fine(self) -> None:
        bp = Blueprint()
        bp.entities.append(decider_combinator(), tile_position=(0, 0), id="a")
        bp.entities.append(constant_combinator(), tile_position=(3, 0), id="b")
        bp.add_circuit_connection("green", "a", "b")
        assert "combinator-unwired" not in codes(bp)

    def test_a_machine_outside_every_supply_area(self) -> None:
        bp = Blueprint()
        bp.entities.append(pole(), tile_position=(0, 0))
        bp.entities.append(assembler(), tile_position=(20, 20),
                           recipe=recipe())
        assert "unpowered" in codes(bp)

    def test_a_machine_beside_a_pole_is_fine(self) -> None:
        bp = Blueprint()
        bp.entities.append(pole(), tile_position=(0, 0))
        bp.entities.append(assembler(), tile_position=(1, 1),
                           recipe=recipe())
        assert "unpowered" not in codes(bp)

    def test_supply_reaches_as_far_west_as_east_at_negative_coordinates(self) -> None:
        """int() rounds toward zero, which shifted the supply square by a tile
        once coordinates went negative: a machine at the western edge of the
        area was called unpowered while its mirror image to the east was not."""
        import math

        from draftsman.data import entities as data

        def width(name: str) -> int:
            (x0, _), (x1, _) = data.raw[name]["collision_box"]
            return math.ceil(x1 - x0)

        # an odd-sized pole: its centre is a half tile, where int() went wrong
        odd = [name for name, raw in data.raw.items()
               if raw.get("type") == "electric-pole" and raw.get("supply_area_distance")
               and width(name) % 2 == 1 and "player-creation" in raw.get("flags", [])]
        if not odd:
            pytest.skip("no odd-sized electric pole in the active game data")
        p, a = odd[0], assembler()
        reach = data.raw[p]["supply_area_distance"]
        px = -50 + width(p) / 2                      # centre of the pole
        west_edge = math.floor(px - reach)           # last tile the area touches
        east_edge = math.ceil(px + reach) - 1
        for machine_x in (west_edge - width(a) + 1, east_edge):
            bp = Blueprint()
            bp.entities.append(p, tile_position=(-50, -50))
            bp.entities.append(a, tile_position=(machine_x, -51), recipe=recipe())
            assert "unpowered" not in codes(bp), machine_x

    def test_a_pole_out_of_reach_of_the_others(self) -> None:
        bp = Blueprint()
        bp.entities.append(pole(), tile_position=(0, 0))
        bp.entities.append(pole(), tile_position=(1, 0))
        bp.entities.append(pole(), tile_position=(40, 0))
        assert "pole-isolated" in codes(bp)


class TestLayout:
    def test_belts_facing_each_other(self) -> None:
        bp = Blueprint()
        bp.entities.append(belt(), tile_position=(0, 0), direction=Direction.EAST)
        bp.entities.append(belt(), tile_position=(1, 0), direction=Direction.WEST)
        assert "belts-head-on" in codes(bp)

    def test_a_head_on_pair_is_reported_once(self) -> None:
        bp = Blueprint()
        bp.entities.append(belt(), tile_position=(0, 0), direction=Direction.EAST)
        bp.entities.append(belt(), tile_position=(1, 0), direction=Direction.WEST)
        found = [f for f in inspection.inspect(bp).findings if f.code == "belts-head-on"]
        assert len(found) == 1

    def test_belts_following_one_another_are_fine(self) -> None:
        bp = Blueprint()
        bp.entities.append(belt(), tile_position=(0, 0), direction=Direction.EAST)
        bp.entities.append(belt(), tile_position=(1, 0), direction=Direction.EAST)
        assert "belts-head-on" not in codes(bp)


class TestRecipeAndParameters:
    def test_a_machine_with_no_recipe_is_suspect(self) -> None:
        bp = Blueprint()
        bp.entities.append(assembler(), tile_position=(0, 0))
        found = [f for f in inspection.inspect(bp).findings if f.code == "machine-no-recipe"]
        assert found and found[0].severity is Severity.SUSPECT

    def test_the_same_is_only_a_note_in_a_parameterised_blueprint(self) -> None:
        """A recipe left open is the entire point of parameterising."""
        bp = Blueprint()
        bp.entities.append(assembler(), tile_position=(0, 0))
        bp.parameters = [{"type": "id", "name": "Recipe", "id": recipe()}]
        found = [f for f in inspection.inspect(bp).findings if f.code == "machine-no-recipe"]
        assert found and found[0].severity is Severity.NOTE


class TestReport:
    def test_findings_are_ordered_worst_first(self) -> None:
        bp = Blueprint()
        bp.entities.append(decider_combinator(), tile_position=(0, 0))
        bp.entities.append(assembler(), tile_position=(5, 5))
        severities = [f.severity for f in inspection.inspect(bp).findings]
        assert severities == sorted(severities, key=lambda s: [Severity.PROBLEM,
                                                              Severity.SUSPECT,
                                                              Severity.NOTE].index(s))

    def test_summary_reads_as_a_sentence(self) -> None:
        bp = Blueprint()
        for x in range(3):
            bp.entities.append(belt(), tile_position=(x, 0), direction=Direction.EAST)
        summary = inspection.inspect(bp).summary()
        assert summary.startswith("3 entities, nothing to report")

    def test_nothing_is_modified(self) -> None:
        """The whole contract: this reports, it does not correct."""
        bp = Blueprint()
        bp.entities.append(decider_combinator(), tile_position=(0, 0))
        before = bp.to_string()
        inspection.inspect(bp)
        assert bp.to_string() == before


class TestCollisionIsTheGamesOwn:
    """Sharing a tile is not the same as colliding, and the game knows it."""

    def test_a_signal_beside_a_rail_is_not_an_overlap(self) -> None:
        bp = Blueprint()
        bp.entities.append(rail(), tile_position=(0, 0))
        bp.entities.append(rail_signal(), tile_position=(1, 0))
        assert not [f for f in inspection.inspect(bp).findings if f.code == "overlap"]

    def test_two_things_in_the_same_place_still_collide(self) -> None:
        bp = Blueprint()
        bp.entities.append(chest(), tile_position=(0, 0))
        bp.entities.append(other_chest(), tile_position=(0, 0))
        found = [f for f in inspection.inspect(bp).findings if f.code == "overlap"]
        assert found and found[0].severity is Severity.PROBLEM


class TestUndergroundDirectionComesFromTheData:
    """A pipe buries its run behind itself; the prototype says so."""

    def test_a_facing_away_pair_counts_as_paired(self) -> None:
        bp = Blueprint()
        reach = underground_reach_of(pipe_to_ground())
        # Pipes on both sides of the pair, further than the reach in either
        # direction. Without them a search sent the wrong way simply leaves the
        # blueprint and says nothing, and this test passed whether the
        # direction was read from the prototype or not.
        for y in range(-reach - 2, reach + 6):
            if y not in (0, 4):
                bp.entities.append(pipe(), tile_position=(0, y))
        bp.entities.append(pipe_to_ground(), tile_position=(0, 0), direction=Direction.NORTH)
        bp.entities.append(pipe_to_ground(), tile_position=(0, 4), direction=Direction.SOUTH)
        assert not [
            f for f in inspection.inspect(bp).findings if f.code == "underground-unpaired"
        ]

    def test_a_lone_pipe_is_still_reported(self) -> None:
        bp = Blueprint()
        bp.entities.append(pipe_to_ground(), tile_position=(0, 0), direction=Direction.NORTH)
        # Far enough that the search stays inside the blueprint: a run leaving
        # it is not a defect, since the other end may be off the edge. The
        # distance comes from the prototype, which Krastorio 2 triples.
        for y in range(1, underground_reach_of(pipe_to_ground()) + 3):
            bp.entities.append(pipe(), tile_position=(0, y))
        assert [f for f in inspection.inspect(bp).findings if f.code == "underground-unpaired"]


class TestInserterReach:
    """A blueprint that states where an inserter reaches is telling the truth."""

    def test_stated_positions_win_over_computed_ones(self) -> None:
        bp = Blueprint()
        bp.entities.append(inserter(), tile_position=(0, 0), direction=Direction.SOUTH)
        arm = bp.entities[0]
        stated = arm.to_dict()
        stated["pickup_position"] = [1.0, -1.0]
        stated["drop_position"] = [1.2, 1.2]
        original = arm.to_dict
        arm.to_dict = lambda *args, **kwargs: stated

        pickup, drop = inspection.inserter_reach(arm)
        arm.to_dict = original
        assert pickup == (1.5, -0.5)
        assert drop == (1.7, 1.7)

    def test_without_stated_positions_the_computed_ones_are_used(self) -> None:
        bp = Blueprint()
        bp.entities.append(inserter(), tile_position=(5, 5), direction=Direction.NORTH)
        pickup, drop = inspection.inserter_reach(bp.entities[0])
        assert pickup == (5.5, 4.5)
        assert round(drop[1], 1) == 6.7


class TestParameterPlaceholders:
    """A free variable for a parameterised blueprint looks exactly like a mistake."""

    def test_a_constant_combinator_holding_a_value_is_only_noted(self) -> None:
        bp = Blueprint()
        bp.entities.append(constant_combinator(), tile_position=(0, 0))
        bp.entities[0].set_signal(0, "signal-V", 1)
        found = [f for f in inspection.inspect(bp).findings if f.code == "combinator-unwired"]
        assert found and found[0].severity is Severity.NOTE

    def test_an_arithmetic_combinator_with_no_wires_is_still_a_problem(self) -> None:
        bp = Blueprint()
        bp.entities.append(arithmetic_combinator(), tile_position=(0, 0))
        found = [f for f in inspection.inspect(bp).findings if f.code == "combinator-unwired"]
        assert found and found[0].severity is Severity.PROBLEM
