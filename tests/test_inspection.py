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
            bp.entities.append("transport-belt", tile_position=(x, 0), direction=Direction.EAST)
        assert inspection.inspect(bp).clean

    def test_a_paired_underground_run_says_nothing(self) -> None:
        bp = Blueprint()
        bp.entities.append("underground-belt", tile_position=(0, 0),
                           direction=Direction.EAST, io_type="input")
        bp.entities.append("underground-belt", tile_position=(4, 0),
                           direction=Direction.EAST, io_type="output")
        assert "underground-unpaired" not in codes(bp)

    def test_an_inserter_between_two_things_says_nothing(self) -> None:
        bp = Blueprint()
        bp.entities.append("steel-chest", tile_position=(0, 0))
        bp.entities.append("inserter", tile_position=(0, 1), direction=Direction.NORTH)
        bp.entities.append("steel-chest", tile_position=(0, 2))
        assert "inserter-idle" not in codes(bp)

    def test_a_fragment_running_off_the_edge_says_nothing(self) -> None:
        """Most blueprints are pieces meant to join onto something else."""
        bp = Blueprint()
        bp.entities.append("underground-belt", tile_position=(0, 0),
                           direction=Direction.EAST, io_type="input")
        assert "underground-unpaired" not in codes(bp)

    def test_a_blueprint_with_no_poles_is_not_called_unpowered(self) -> None:
        bp = Blueprint()
        bp.entities.append("assembling-machine-2", tile_position=(0, 0), recipe="iron-gear-wheel")
        assert "unpowered" not in codes(bp)

    def test_an_empty_blueprint_is_clean(self) -> None:
        assert inspection.inspect(Blueprint()).clean


class TestUndergrounds:
    def test_an_underground_that_never_surfaces(self) -> None:
        bp = Blueprint()
        bp.entities.append("underground-belt", tile_position=(0, 0),
                           direction=Direction.EAST, io_type="input")
        # Something far away so the run does not simply leave the blueprint.
        bp.entities.append("steel-chest", tile_position=(12, 0))
        assert "underground-unpaired" in codes(bp)

    def test_reach_comes_from_the_prototype_not_a_guess(self) -> None:
        """An express underground reaches nine tiles where a basic one reaches five."""
        def run(name: str, gap: int) -> set[str]:
            bp = Blueprint()
            bp.entities.append(name, tile_position=(0, 0),
                               direction=Direction.EAST, io_type="input")
            bp.entities.append(name, tile_position=(gap, 0),
                               direction=Direction.EAST, io_type="output")
            bp.entities.append("steel-chest", tile_position=(gap + 6, 0))
            return {f.code for f in inspection.inspect(bp).findings}

        assert "underground-unpaired" not in run("express-underground-belt", 8)
        assert "underground-unpaired" in run("underground-belt", 8)


class TestInserters:
    def test_an_inserter_reaching_nothing_at_all(self) -> None:
        bp = Blueprint()
        # Surrounded, so neither side can be excused as reaching outside.
        bp.entities.append("steel-chest", tile_position=(0, 0))
        bp.entities.append("steel-chest", tile_position=(8, 8))
        bp.entities.append("inserter", tile_position=(4, 4), direction=Direction.NORTH)
        assert "inserter-idle" in codes(bp)

    def test_an_inserter_at_the_edge_is_excused(self) -> None:
        """Its neighbour is plausibly in the next blueprint along."""
        bp = Blueprint()
        bp.entities.append("inserter", tile_position=(0, 0), direction=Direction.NORTH)
        bp.entities.append("steel-chest", tile_position=(0, 4))
        assert "inserter-idle" not in codes(bp)

    def test_an_inserter_shuffling_one_chest_into_itself(self) -> None:
        bp = Blueprint()
        bp.entities.append("steel-chest", tile_position=(1, 0))
        bp.entities.append("steel-chest", tile_position=(1, 2))
        bp.entities.append("inserter", tile_position=(1, 1), direction=Direction.NORTH)
        # Give it a wide neighbour that covers both sides.
        bp2 = Blueprint()
        bp2.entities.append("storage-tank", tile_position=(0, 0))  # 3x3
        bp2.entities.append("inserter", tile_position=(1, 3), direction=Direction.NORTH)
        bp2.entities.append("storage-tank", tile_position=(0, 4))
        assert "inserter-loop" not in codes(bp)


class TestSettingsThatDoNothing:
    """Everything looks configured and none of it runs. The careless family."""

    def test_filters_set_while_filtering_is_off(self) -> None:
        bp = Blueprint()
        bp.entities.append("steel-chest", tile_position=(0, 0))
        bp.entities.append("steel-chest", tile_position=(0, 2))
        inserter = bp.entities.append("stack-inserter", tile_position=(0, 1),
                                      direction=Direction.NORTH)
        bp.entities[-1].set_item_filter(0, "iron-plate")
        bp.entities[-1].use_filters = False
        assert "filters-ignored" in codes(bp)

    def test_filters_set_with_filtering_on_is_fine(self) -> None:
        bp = Blueprint()
        bp.entities.append("steel-chest", tile_position=(0, 0))
        bp.entities.append("steel-chest", tile_position=(0, 2))
        bp.entities.append("stack-inserter", tile_position=(0, 1),
                           direction=Direction.NORTH, use_filters=True)
        bp.entities[-1].set_item_filter(0, "iron-plate")
        assert "filters-ignored" not in codes(bp)

    def test_a_circuit_condition_with_no_wire_to_satisfy_it(self) -> None:
        bp = Blueprint()
        bp.entities.append("steel-chest", tile_position=(0, 0))
        bp.entities.append("steel-chest", tile_position=(0, 2))
        bp.entities.append("stack-inserter", tile_position=(0, 1), direction=Direction.NORTH)
        bp.entities[-1].circuit_condition.first_signal = "iron-plate"
        assert "condition-without-wire" in codes(bp)

    def test_the_same_condition_with_a_wire_is_fine(self) -> None:
        bp = Blueprint()
        bp.entities.append("steel-chest", tile_position=(0, 0))
        bp.entities.append("steel-chest", tile_position=(0, 2))
        bp.entities.append("stack-inserter", tile_position=(0, 1),
                           direction=Direction.NORTH, id="ins")
        bp.entities.append("constant-combinator", tile_position=(3, 1), id="cc")
        bp.entities["ins"].circuit_condition.first_signal = "iron-plate"
        bp.add_circuit_connection("red", "ins", "cc")
        assert "condition-without-wire" not in codes(bp)


class TestCombinatorsAndPower:
    def test_a_combinator_nobody_wired(self) -> None:
        bp = Blueprint()
        bp.entities.append("decider-combinator", tile_position=(0, 0))
        assert "combinator-unwired" in codes(bp)

    def test_a_wired_combinator_is_fine(self) -> None:
        bp = Blueprint()
        bp.entities.append("decider-combinator", tile_position=(0, 0), id="a")
        bp.entities.append("constant-combinator", tile_position=(3, 0), id="b")
        bp.add_circuit_connection("green", "a", "b")
        assert "combinator-unwired" not in codes(bp)

    def test_a_machine_outside_every_supply_area(self) -> None:
        bp = Blueprint()
        bp.entities.append("medium-electric-pole", tile_position=(0, 0))
        bp.entities.append("assembling-machine-2", tile_position=(20, 20),
                           recipe="iron-gear-wheel")
        assert "unpowered" in codes(bp)

    def test_a_machine_beside_a_pole_is_fine(self) -> None:
        bp = Blueprint()
        bp.entities.append("medium-electric-pole", tile_position=(0, 0))
        bp.entities.append("assembling-machine-2", tile_position=(1, 1),
                           recipe="iron-gear-wheel")
        assert "unpowered" not in codes(bp)

    def test_a_pole_out_of_reach_of_the_others(self) -> None:
        bp = Blueprint()
        bp.entities.append("small-electric-pole", tile_position=(0, 0))
        bp.entities.append("small-electric-pole", tile_position=(1, 0))
        bp.entities.append("small-electric-pole", tile_position=(40, 0))
        assert "pole-isolated" in codes(bp)


class TestLayout:
    def test_belts_facing_each_other(self) -> None:
        bp = Blueprint()
        bp.entities.append("transport-belt", tile_position=(0, 0), direction=Direction.EAST)
        bp.entities.append("transport-belt", tile_position=(1, 0), direction=Direction.WEST)
        assert "belts-head-on" in codes(bp)

    def test_a_head_on_pair_is_reported_once(self) -> None:
        bp = Blueprint()
        bp.entities.append("transport-belt", tile_position=(0, 0), direction=Direction.EAST)
        bp.entities.append("transport-belt", tile_position=(1, 0), direction=Direction.WEST)
        found = [f for f in inspection.inspect(bp).findings if f.code == "belts-head-on"]
        assert len(found) == 1

    def test_belts_following_one_another_are_fine(self) -> None:
        bp = Blueprint()
        bp.entities.append("transport-belt", tile_position=(0, 0), direction=Direction.EAST)
        bp.entities.append("transport-belt", tile_position=(1, 0), direction=Direction.EAST)
        assert "belts-head-on" not in codes(bp)


class TestRecipeAndParameters:
    def test_a_machine_with_no_recipe_is_suspect(self) -> None:
        bp = Blueprint()
        bp.entities.append("assembling-machine-2", tile_position=(0, 0))
        found = [f for f in inspection.inspect(bp).findings if f.code == "machine-no-recipe"]
        assert found and found[0].severity is Severity.SUSPECT

    def test_the_same_is_only_a_note_in_a_parameterised_blueprint(self) -> None:
        """A recipe left open is the entire point of parameterising."""
        bp = Blueprint()
        bp.entities.append("assembling-machine-2", tile_position=(0, 0))
        bp.parameters = [{"type": "id", "name": "Recipe", "id": "electronic-circuit"}]
        found = [f for f in inspection.inspect(bp).findings if f.code == "machine-no-recipe"]
        assert found and found[0].severity is Severity.NOTE


class TestReport:
    def test_findings_are_ordered_worst_first(self) -> None:
        bp = Blueprint()
        bp.entities.append("decider-combinator", tile_position=(0, 0))
        bp.entities.append("assembling-machine-2", tile_position=(5, 5))
        severities = [f.severity for f in inspection.inspect(bp).findings]
        assert severities == sorted(severities, key=lambda s: [Severity.PROBLEM,
                                                              Severity.SUSPECT,
                                                              Severity.NOTE].index(s))

    def test_summary_reads_as_a_sentence(self) -> None:
        bp = Blueprint()
        for x in range(3):
            bp.entities.append("transport-belt", tile_position=(x, 0), direction=Direction.EAST)
        assert inspection.inspect(bp).summary() == "3 entities, nothing to report"

    def test_nothing_is_modified(self) -> None:
        """The whole contract: this reports, it does not correct."""
        bp = Blueprint()
        bp.entities.append("decider-combinator", tile_position=(0, 0))
        before = bp.to_string()
        inspection.inspect(bp)
        assert bp.to_string() == before
