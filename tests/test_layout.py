"""Tests for the numbers a row layout is decided with.

A small synthetic world, for the same reason `test_bom.py` uses one: the
arithmetic has to be exact and independent of whichever mod set is active.
The first version of this module doubled the row for every two-ingredient
recipe and again for every output; the tests named after those mistakes are
here so they cannot come back.
"""

from __future__ import annotations

import pytest

import prototypes
from factorio_forge import layout

RECIPES = {
    "smelt-plate": {
        "name": "smelt-plate",
        "energy_required": 2,
        "ingredients": [{"type": "item", "name": "ore", "amount": 2}],
        "results": [{"type": "item", "name": "plate", "amount": 1}],
    },
    "make-board": {
        "name": "make-board",
        "energy_required": 1,
        "ingredients": [
            {"type": "item", "name": "wire", "amount": 6},
            {"type": "item", "name": "plate", "amount": 1},
        ],
        "results": [{"type": "item", "name": "board", "amount": 1}],
    },
    "make-chip": {
        "name": "make-chip",
        "energy_required": 1,
        "ingredients": [
            {"type": "item", "name": "wire", "amount": 4},
            {"type": "item", "name": "plate", "amount": 1},
            {"type": "item", "name": "plastic", "amount": 1},
        ],
        "results": [{"type": "item", "name": "chip", "amount": 1}],
    },
    "refine-oil": {
        "name": "refine-oil",
        "energy_required": 1,
        "ingredients": [{"type": "fluid", "name": "crude", "amount": 10}],
        "results": [{"type": "fluid", "name": "lube", "amount": 10}],
    },
    "split-ore": {
        "name": "split-ore",
        "energy_required": 1,
        "ingredients": [{"type": "item", "name": "ore", "amount": 1}],
        "results": [
            {"type": "item", "name": "iron", "amount": 1},
            {"type": "item", "name": "copper", "amount": 1},
        ],
    },
}

ENTITIES = {
    "machine": {"name": "machine", "crafting_speed": 1.0, "collision_box": [[-1.2, -1.2], [1.2, 1.2]]},
    "big-machine": {"name": "big-machine", "crafting_speed": 2.0, "collision_box": [[-2.3, -2.3], [2.3, 2.3]]},
    "long-machine": {"name": "long-machine", "crafting_speed": 1.0, "collision_box": [[-2.4, -1.4], [2.4, 1.4]]},
    # speed 0.125 tiles/tick -> 60 items/s across both lanes, 30 a lane
    "belt": {"name": "belt", "type": "transport-belt", "speed": 0.125},
    "slow-belt": {"name": "slow-belt", "type": "transport-belt", "speed": 0.0625},
    "arm": {"name": "arm", "type": "inserter", "rotation_speed": 0.05, "pickup_position": [0, -1], "insert_position": [0, 1.2]},
    "slow-arm": {"name": "slow-arm", "type": "inserter", "rotation_speed": 0.01, "pickup_position": [0, -1], "insert_position": [0, 1.2]},
    "long-arm": {"name": "long-arm", "type": "inserter", "rotation_speed": 0.05, "pickup_position": [0, -2], "insert_position": [0, 2.2]},
    "side-arm": {"name": "side-arm", "type": "inserter", "rotation_speed": 0.05, "pickup_position": [-1, 0], "insert_position": [0, 1.2]},
}


@pytest.fixture
def synthetic_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(layout.recipe_data, "raw", RECIPES)
    monkeypatch.setattr(layout.entity_data, "raw", ENTITIES)


class TestPrototypeNumbers:
    def test_belt_and_lane(self, synthetic_data: None) -> None:
        assert layout.belt_throughput("belt") == 60.0
        assert layout.lane_throughput("belt") == 30.0

    def test_a_real_belt_carries_something(self) -> None:
        assert layout.belt_throughput(prototypes.belt()) > 0

    def test_refuses_something_that_is_not_a_belt(self, synthetic_data: None) -> None:
        with pytest.raises(layout.LayoutError):
            layout.belt_throughput("machine")

    def test_size_rounds_the_collision_box_up(self, synthetic_data: None) -> None:
        assert layout.machine_size("machine") == (3, 3)
        assert layout.machine_size("big-machine") == (5, 5)

    def test_size_turns_with_direction(self, synthetic_data: None) -> None:
        assert layout.machine_size("long-machine", 0) == (5, 3)
        assert layout.machine_size("long-machine", 4) == (3, 5)
        assert layout.machine_size("long-machine", 8) == (5, 3)

    def test_inserter_reach_is_read_whichever_way_it_points(self, synthetic_data: None) -> None:
        assert layout.inserter_reach("arm") == 1
        assert layout.inserter_reach("long-arm") == 2
        assert layout.inserter_reach("side-arm") == 1

    def test_inserter_rate_is_one_swing_per_stack(self, synthetic_data: None) -> None:
        assert layout.inserter_rate("arm") == pytest.approx(3.0)
        assert layout.inserter_rate("arm", stack_size=3) == pytest.approx(9.0)

    def test_a_real_inserter_moves_something(self) -> None:
        assert layout.inserter_rate(prototypes.inserter()) > 0


class TestLanes:
    def test_a_single_ingredient_takes_both_lanes(self) -> None:
        assert layout.assign_input_lanes({"ore": 1.0}, 1) == [["ore", "ore"]]

    def test_two_ingredients_get_a_lane_each(self) -> None:
        lanes = layout.assign_input_lanes({"wire": 6.0, "plate": 1.0}, 1)
        assert sorted(lanes[0]) == ["plate", "wire"]

    def test_the_spare_lane_goes_to_the_hungriest(self) -> None:
        lanes = layout.assign_input_lanes({"wire": 4.0, "plate": 1.0, "plastic": 1.0}, 2)
        flat = [item for belt in lanes for item in belt]
        assert flat.count("wire") == 2
        assert lanes[0] == ["wire", "wire"]  # a doubled item gets a belt of its own

    def test_too_many_ingredients_for_the_lanes(self) -> None:
        with pytest.raises(layout.LayoutError):
            layout.assign_input_lanes({"a": 1, "b": 1, "c": 1}, 1)


class TestRowCapacity:
    def test_a_single_ingredient_is_fed_by_the_whole_belt(self, synthetic_data: None) -> None:
        # ore 1/s per machine against 60/s; plate 0.5/s against one 30/s lane.
        capacity = layout.row_capacity("smelt-plate", "machine", "belt")
        assert capacity.per_row == 60

    def test_two_ingredients_on_one_belt_get_a_lane_each(self, synthetic_data: None) -> None:
        # The first version checked wire (6/s) against the whole 60/s belt and
        # said 10. Wire rides one 30/s lane: 5.
        capacity = layout.row_capacity("make-board", "machine", "belt")
        assert capacity.per_row == 5
        assert "wire" in capacity.limit

    def test_output_fills_one_lane_only(self, synthetic_data: None, monkeypatch: pytest.MonkeyPatch) -> None:
        # With a fast enough input, the output is what binds, and it binds at
        # one lane: 30 boards/s at 1 per machine. A whole belt would say 60.
        entities = dict(ENTITIES)
        recipes = dict(RECIPES)
        recipes["cheap-board"] = {
            "name": "cheap-board", "energy_required": 1,
            "ingredients": [{"type": "item", "name": "dust", "amount": 1}],
            "results": [{"type": "item", "name": "board", "amount": 1}],
        }
        monkeypatch.setattr(layout.recipe_data, "raw", recipes)
        monkeypatch.setattr(layout.entity_data, "raw", entities)
        capacity = layout.row_capacity("cheap-board", "machine", "belt")
        assert capacity.per_row == 30
        assert "one lane" in capacity.limit

    def test_several_products_share_the_one_lane(self, synthetic_data: None) -> None:
        capacity = layout.row_capacity("split-ore", "machine", "belt")
        assert capacity.per_row == 15  # 2 items/s per machine onto 30/s
        assert any("share one output lane" in note for note in capacity.notes)

    def test_sharing_input_belts_halves_the_row(self, synthetic_data: None) -> None:
        alone = layout.row_capacity("make-board", "machine", "belt")
        shared = layout.row_capacity("make-board", "machine", "belt", rows_per_input_belt=2)
        assert shared.per_row == alone.per_row // 2

    def test_a_slower_belt_shortens_the_row(self, synthetic_data: None) -> None:
        fast = layout.row_capacity("smelt-plate", "machine", "belt")
        slow = layout.row_capacity("smelt-plate", "machine", "slow-belt")
        assert slow.per_row < fast.per_row

    def test_three_ingredients_need_a_second_belt(self, synthetic_data: None) -> None:
        with pytest.raises(layout.LayoutError, match="long_inserter"):
            layout.row_capacity("make-chip", "machine", "belt", inserter="arm")
        capacity = layout.row_capacity("make-chip", "machine", "belt", inserter="arm", long_inserter="long-arm")
        assert len(capacity.input_lanes) == 2

    def test_counts_inserters_a_machine_needs(self, synthetic_data: None) -> None:
        # wire 6/s + plate 1/s from one belt; slow-arm moves 0.6/s.
        capacity = layout.row_capacity("make-board", "machine", "belt", inserter="slow-arm")
        assert capacity.inserters["input-1"] == 12
        assert capacity.inserters["output"] == 2

    def test_a_bigger_hand_needs_fewer_inserters(self, synthetic_data: None) -> None:
        small = layout.row_capacity("make-board", "machine", "belt", inserter="slow-arm")
        big = layout.row_capacity("make-board", "machine", "belt", inserter="slow-arm", stack_size=6)
        assert big.inserters["input-1"] < small.inserters["input-1"]

    def test_a_fluid_row_is_not_belt_limited(self, synthetic_data: None) -> None:
        capacity = layout.row_capacity("refine-oil", "machine", "belt")
        assert capacity.per_row == 0
        assert capacity.fluids_per_machine

    def test_a_machine_that_outruns_its_belt_is_refused(
        self, synthetic_data: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entities = dict(ENTITIES)
        entities["rocket"] = {"name": "rocket", "crafting_speed": 100.0, "collision_box": [[-1.2, -1.2], [1.2, 1.2]]}
        monkeypatch.setattr(layout.entity_data, "raw", entities)
        with pytest.raises(layout.LayoutError, match="outruns"):
            layout.row_capacity("smelt-plate", "rocket", "slow-belt")
