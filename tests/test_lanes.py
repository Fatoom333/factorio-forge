"""Tests for following belt lanes through a finished build, against the active data.

Most of these lay belts by hand -- a belt, its underground and its splitter
of one speed, asked of the data by what they are (`prototypes.belt_set`) --
and declare what enters with a plan's `inputs`, so the item names can be
anything: the tracker only moves names from lane to lane. Where a recipe
matters (what a machine makes, what its inserters take) the recipe is asked
of the data by its shape, as everywhere in the suite.
"""

from __future__ import annotations

import pytest
from draftsman.blueprintable import Blueprint
from draftsman.data import entities, items, recipes

import prototypes
from factorio_forge import fluids, inspection, lanes, plan, rows
from test_route import block_spec, chained_recipes, codes, point

NORTH, EAST, SOUTH, WEST = 0, 4, 8, 12
LEFT, RIGHT = lanes.LEFT, lanes.RIGHT


def belt(x, y, d, name=None) -> dict:
    return {"name": name or prototypes.belt_set()[0], "position": [x, y], "direction": d}


def underground(x, y, d, io) -> dict:
    return {"name": prototypes.belt_set()[1], "position": [x, y], "direction": d, "io_type": io}


def line(x0, x1, y, d=EAST) -> list[dict]:
    return [belt(x, y, d) for x in range(x0, x1 + 1)]


def column(x, y0, y1, d) -> list[dict]:
    return [belt(x, y, d) for y in range(y0, y1 + 1)]


def built(entities_=(), inputs=(), blocks=(), connections=()) -> plan.BuildResult:
    return plan.build({"label": "lanes", "entities": list(entities_), "inputs": list(inputs),
                       "blocks": list(blocks), "connections": list(connections)})


def traced(result: plan.BuildResult, inputs=(), in_ports=()) -> lanes.LaneMap:
    feeds = [lanes.Feed(tuple(i["at"]), (i["items"][0], i["items"][-1])) for i in inputs]
    return lanes.trace(list(result.blueprint.entities), feeds, in_ports)


def items_at(lane_map, tile) -> tuple[set, set]:
    left, right = lane_map.at(tile)
    return set(left.items), set(right.items)


def lane_codes(result) -> list[str]:
    return sorted(c for c in codes(result) if c.startswith("lanes-") or c == "route-lanes-disagree")


def take(at) -> dict:
    return {"at": list(at), "items": ["a", "b"]}


def inserter_between(pick, drop) -> tuple[str, int, tuple[int, int]]:
    """A one-tile inserter, its direction and its tile, taking from `pick` and putting at `drop`."""
    name = prototypes.inserter_reaching(1)
    tile = ((pick[0] + drop[0]) // 2, (pick[1] + drop[1]) // 2)
    for direction in (0, 4, 8, 12):
        bp = Blueprint()
        entity = bp.entities.append(name, tile_position=tile, direction=direction)
        got = inspection.inserter_reach(entity)
        if got and lanes._floor(got[0]) == tuple(pick) and lanes._floor(got[1]) == tuple(drop):
            return name, direction, tile
    pytest.skip(f"{name} cannot be turned to take from {pick} and put at {drop}")


# --------------------------------------------------------------------------
# the rules
# --------------------------------------------------------------------------


class TestRules:
    def test_a_straight_line_keeps_its_lanes(self) -> None:
        inputs = [{"at": [0, 0], "items": ["a", "b"]}]
        result = built(line(0, 5, 0), inputs)
        assert items_at(traced(result, inputs), (5, 0)) == ({"a"}, {"b"})
        assert lane_codes(result) == []

    @pytest.mark.parametrize("turn", [NORTH, SOUTH])
    def test_curves_keep_lanes(self, turn: int) -> None:
        step = -1 if turn == NORTH else 1
        inputs = [{"at": [0, 0], "items": ["a", "b"]}]
        result = built(line(0, 1, 0) + [belt(2, 0, turn), belt(2, step, turn), belt(2, 2 * step, turn)], inputs)
        lane_map = traced(result, inputs)
        assert lane_map.nodes[(2, 0)].shape == "curve"
        assert items_at(lane_map, (2, 2 * step)) == ({"a"}, {"b"})

    @pytest.mark.parametrize("side", ["left", "right"])
    def test_a_side_load_fills_the_near_lane(self, side: str) -> None:
        # Travelling east, the left side is north.
        y, d = (-2, SOUTH) if side == "left" else (2, NORTH)
        step = 1 if side == "left" else -1
        inputs = [{"at": [0, 0], "items": ["m"]}, {"at": [2, y], "items": ["a", "b"]}]
        result = built(line(0, 4, 0) + [belt(2, y, d), belt(2, y + step, d)], inputs)
        left, right = items_at(traced(result, inputs), (4, 0))
        near, far = (left, right) if side == "left" else (right, left)
        assert near == {"m", "a", "b"} and far == {"m"}

    def test_a_t_junction_puts_each_side_on_its_own_lane(self) -> None:
        inputs = [{"at": [2, -2], "items": ["a"]}, {"at": [2, 2], "items": ["b"]}]
        sides = [belt(2, -2, SOUTH), belt(2, -1, SOUTH), belt(2, 2, NORTH), belt(2, 1, NORTH)]
        result = built(line(2, 4, 0) + sides, inputs)
        assert items_at(traced(result, inputs), (4, 0)) == ({"a"}, {"b"})

    def test_an_underground_pair_keeps_lanes(self) -> None:
        reach = prototypes.underground_reach_of(prototypes.belt_set()[1])
        exit_x = 1 + min(reach, 3)
        inputs = [{"at": [0, 0], "items": ["a", "b"]}]
        placed = [belt(0, 0, EAST), underground(1, 0, EAST, "input"), underground(exit_x, 0, EAST, "output"),
                  belt(exit_x + 1, 0, EAST)]
        result = built(placed, inputs)
        assert items_at(traced(result, inputs), (exit_x + 1, 0)) == ({"a"}, {"b"})

    @pytest.mark.parametrize("side", ["left", "right"])
    def test_the_hood_passes_one_lane(self, side: str) -> None:
        reach = prototypes.underground_reach_of(prototypes.belt_set()[1])
        exit_x = 2 + min(reach, 3)
        y, d, step = (-2, SOUTH, 1) if side == "left" else (2, NORTH, -1)
        inputs = [{"at": [2, y], "items": ["a", "b"]}]
        placed = [underground(2, 0, EAST, "input"), underground(exit_x, 0, EAST, "output"),
                  belt(exit_x + 1, 0, EAST), belt(2, y, d), belt(2, y + step, d)]
        result = built(placed, inputs)
        passes = lanes.hood_passes(side)
        through = {"left": "a", "right": "b"}[passes]
        blocked = {"a", "b"} - {through}
        left, right = items_at(traced(result, inputs), (exit_x + 1, 0))
        assert (left, right) == (({through}, set()) if side == "left" else (set(), {through}))
        (stuck,) = [f for f in result.findings if f.code == "lanes-not-taken"]
        assert stuck.severity is inspection.Severity.SUSPECT and blocked.pop() in stuck.summary

    def test_a_splitter_sends_each_lane_to_both_outputs(self) -> None:
        _, _, splitter = prototypes.belt_set()
        inputs = [{"at": [0, 0], "items": ["a", "b"]}]
        placed = line(0, 1, 0) + [{"name": splitter, "position": [2, 0], "direction": EAST}] + line(3, 4, 0) + line(3, 4, 1)
        result = built(placed, inputs)
        lane_map = traced(result, inputs)
        assert items_at(lane_map, (4, 0)) == ({"a"}, {"b"})
        assert items_at(lane_map, (4, 1)) == ({"a"}, {"b"})
        assert lane_codes(result) == []

    def test_lane_of_point_agrees_with_the_arm_fitting(self) -> None:
        name = prototypes.inserter_reaching(1)
        arm = rows.fit_arm(name, "output", 3)
        drop = fluids.rotate(tuple(entities.raw[name]["insert_position"]), arm.direction)
        point_ = (0.5 + drop[0], 0.5 + drop[1])
        tile = lanes._floor(point_)
        # Travelling east, with the machine above (north): the far lane is the right one.
        assert lanes.lane_of_point(tile, EAST, point_) == (RIGHT if arm.far_lane else LEFT)

    @pytest.mark.parametrize("stack", ["repeat", "mirror"])
    @pytest.mark.parametrize("shape", ["one", "chain"])
    def test_out_ports_carry_what_the_block_says(self, stack: str, shape: str) -> None:
        if shape == "one":
            recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
        else:
            (recipe, machine), _ = chained_recipes()
        result = plan.build({"blocks": [{**block_spec(recipe, machine, [2, 2]), "stack": stack}]})
        entities_ = list(result.blueprint.entities)
        known = {i: recipe for i, e in enumerate(entities_) if e.name == machine}
        lane_map = lanes.trace(entities_, (), (), known)
        outs = [p for p in result.blocks[0].ports if p["kind"] == "belt" and p["io"] == "out"]
        assert outs
        for port in outs:
            left, right = lane_map.at((port["x"], port["y"]))
            assert (set(left.items), set(right.items)) == tuple(set(lanes._parts(i)) for i in port["items"])


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------


def block_and_port(shape=(2, 0, 1, 0), at=(0, 0)):
    recipe, machine = prototypes.crafting_setup(*shape)
    block = {**block_spec(recipe, machine, [2]), "at": list(at)}
    first = plan.build({"blocks": [block]})
    port = next(p for p in first.blocks[0].ports if p["io"] == "in" and p["kind"] == "belt")
    return block, port


def feed_port(port, items) -> dict:
    dx, dy = inspection.STEP[port["direction"]]
    start = point(port["x"] - 6 * dx, port["y"] - 6 * dy, port["direction"], items=items)
    return {"from": start, "to": {"block": 0, "port": port["index"]}}


class TestFindings:
    def test_block_to_block_is_clean(self) -> None:
        (first_recipe, first_machine), (second_recipe, second_machine) = chained_recipes()
        maker = block_spec(first_recipe, first_machine, [2])
        alone = plan.build({"blocks": [maker]})
        out = next(p for p in alone.blocks[0].ports if p["io"] == "out")
        user = {**block_spec(second_recipe, second_machine, [2]), "at": [alone.blocks[0].width + 10, 0]}
        in_index = next(p["index"] for p in plan.build({"blocks": [user]}).blocks[0].ports
                        if p["io"] == "in" and p["kind"] == "belt")
        result = built(blocks=[maker, user],
                       connections=[{"from": {"block": 0, "port": out["index"]}, "to": {"block": 1, "port": in_index}}])
        assert result.routes[0].ok
        assert lane_codes(result) == []
        port = result.blocks[1].ports[in_index]
        assert port["arrives"] is not None and any(port["arrives"])

    def test_a_wrong_item_never_arrives(self) -> None:
        block, port = block_and_port((1, 0, 1, 0))
        result = built(blocks=[block], connections=[feed_port(port, ["not-an-ingredient"])])
        assert "lanes-missing" in codes(result)

    def test_swapped_ingredients_are_only_a_note(self) -> None:
        block, port = block_and_port()
        w0, w1 = port["items"]
        result = built(blocks=[block], connections=[feed_port(port, [w1, w0])])
        (swapped,) = [f for f in result.findings if f.code.startswith("lanes-")]
        assert swapped.code == "lanes-swapped" and swapped.severity is inspection.Severity.NOTE

    def test_swapped_point_lanes_are_wrong(self) -> None:
        belt_, _, _ = prototypes.belt_set()
        result = built(connections=[{"kind": "belt", "belt": belt_, "from": point(0, 0, items=["a", "b"]),
                                     "to": point(8, 0, items=["b", "a"])}])
        (wrong,) = [f for f in result.findings if f.code == "lanes-wrong"]
        assert "swapped" in wrong.detail

    def test_an_unused_item_alone_is_suspect_and_shared_is_a_problem(self) -> None:
        block, port = block_and_port((1, 0, 1, 0))
        wanted = port["items"][0]
        alone = built(blocks=[block], connections=[feed_port(port, ["junk", wanted])])
        (stuck,) = [f for f in alone.findings if f.code == "lanes-not-taken"]
        assert stuck.severity is inspection.Severity.SUSPECT
        shared = built(blocks=[block], connections=[feed_port(port, [f"junk+{wanted}", ""])])
        (stuck,) = [f for f in shared.findings if f.code == "lanes-not-taken"]
        assert stuck.severity is inspection.Severity.PROBLEM and wanted in stuck.summary

    def test_two_ingredients_on_one_lane_are_mixed(self) -> None:
        block, port = block_and_port()
        w0, w1 = port["items"]
        result = built(blocks=[block], connections=[feed_port(port, [f"{w0}+{w1}", ""])])
        assert "lanes-mixed" in codes(result)

    def test_a_split_where_one_branch_takes_overflows(self) -> None:
        _, _, splitter = prototypes.belt_set()
        name, direction, tile = inserter_between((4, 0), (4, -2))
        placed = (line(0, 1, 0) + [{"name": splitter, "position": [2, 0], "direction": EAST}] + line(3, 4, 0)
                  + line(3, 4, 1) + [belt(5, 1, WEST)]
                  + [{"name": name, "position": list(tile), "direction": direction},
                     {"name": prototypes.small_chest(), "position": [4, -2]}])
        result = built(placed, [{"at": [0, 0], "items": ["a"]}])
        assert "lanes-overflow" in codes(result)
        assert not [f for f in result.findings if f.code == "lanes-not-taken"]

    def test_an_undeclared_head_is_unknown_until_declared(self) -> None:
        block, port = block_and_port(at=(10, 0))
        x, y = port["x"], port["y"]
        hand = line(x - 4, x - 1, y) + column(x - 2, y - 2, y - 1, SOUTH)
        w0, w1 = port["items"]
        side = [{"at": [x - 2, y - 2], "items": [w0]}]
        result = built(hand, side, blocks=[block])
        assert "lanes-unknown" in codes(result)
        declared = built(hand, side + [{"at": [x - 4, y], "items": ["", w1]}], blocks=[block])
        assert lane_codes(declared) == []
        with pytest.raises(plan.PlanError, match="no belt"):
            built(hand, [{"at": [x - 4, y - 5], "items": ["a"]}], blocks=[block])
        with pytest.raises(plan.PlanError, match="fed from behind"):
            built(hand, [{"at": [x - 3, y], "items": ["a"]}], blocks=[block])

    def test_a_side_fed_tile_takes_no_input(self) -> None:
        placed = line(0, 2, 0) + column(3, -2, 0, NORTH)
        with pytest.raises(plan.PlanError, match="fed from its side"):
            built(placed, [{"at": [0, 0], "items": ["a", "b"]}, {"at": [3, 0], "items": ["c"]}])

    def test_an_undeclared_hand_head_inside_the_build_is_unknown(self) -> None:
        block, port = block_and_port(at=(10, 0))
        x, y = port["x"], port["y"]
        # A chest further out puts the head of the hand line inside the build.
        hand = line(x - 4, x - 1, y) + [{"name": prototypes.small_chest(), "position": [x - 8, y]}]
        result = built(hand, blocks=[block])
        assert "lanes-unknown" in codes(result)
        declared = built(hand, [{"at": [x - 4, y], "items": list(port["items"])}], blocks=[block])
        assert lane_codes(declared) == []

    def test_a_belt_into_a_pole_is_a_dead_end(self) -> None:
        placed = line(0, 3, 0) + [{"name": prototypes.small_pole(), "position": [4, 0]}]
        result = built(placed, [{"at": [0, 0], "items": ["a"]}])
        stuck = [f for f in result.findings if f.code == "lanes-not-taken"]
        assert {f.severity for f in stuck} == {inspection.Severity.SUSPECT} and len(stuck) == 2

    def test_an_open_end_is_quiet_and_a_block_line_tail_is_closed(self) -> None:
        result = built(line(0, 3, 0), [{"at": [0, 0], "items": ["a"]}])
        assert lane_codes(result) == []
        block, port = block_and_port((1, 0, 1, 0))
        fed = built(blocks=[block], connections=[feed_port(port, [port["items"][0]])])
        lane_map = lanes.trace(list(fed.blueprint.entities), (), [(port["x"], port["y"])])
        assert any(key[0][1] == port["y"] for key in lane_map.closed)

    def test_an_exit_feeding_only_a_side_is_unchecked(self) -> None:
        placed = [underground(0, 0, EAST, "input"), underground(2, 0, EAST, "output"),
                  belt(3, 0, NORTH), belt(3, -1, NORTH)]
        inputs = [{"at": [0, 0], "items": ["a"]}]
        result = built(placed, inputs)
        assert "lanes-unchecked" in codes(result)
        left, right = traced(result, inputs).at((3, -1))
        assert left.unknown and right.unknown

    def test_a_filter_is_respected(self) -> None:
        name = prototypes.inserter_reaching(1)
        if not entities.raw[name].get("filter_count"):
            pytest.skip(f"{name} takes no filters")
        _, direction, tile = inserter_between((2, 0), (2, 2))
        bp = Blueprint()
        for x in range(5):
            bp.entities.append(prototypes.belt_set()[0], tile_position=(x, 0), direction=EAST)
            bp.entities.append(prototypes.belt_set()[0], tile_position=(x, 2), direction=EAST)
        a, b = sorted(n for n, d in items.raw.items() if isinstance(d, dict))[:2]
        bp.entities.append(name, tile_position=tile, direction=direction, use_filters=True,
                           filters=[{"index": 1, "name": a}])
        lane_map = lanes.trace(list(bp.entities), [lanes.Feed((0, 0), (a, b)), lanes.Feed((0, 2), ("", ""))])
        left, right = lane_map.at((4, 2))
        assert left.items | right.items == {a}

    def test_a_loop_ends(self) -> None:
        loop = [belt(0, 0, EAST), belt(1, 0, SOUTH), belt(1, 1, WEST), belt(0, 1, NORTH), belt(-1, 0, EAST)]
        result = built(loop, [{"at": [-1, 0], "items": ["a"]}])
        lane_map = traced(result, [{"at": [-1, 0], "items": ["a"]}])
        assert "a" in lane_map.at((1, 1))[0].items | lane_map.at((1, 1))[1].items

    def test_a_machine_short_of_an_ingredient_starves(self) -> None:
        recipe, machine = prototypes.crafting_setup(2, 0, 1, 0)
        if prototypes._footprint(entities.raw[machine]) != (3, 3):
            pytest.skip(f"{machine} is not 3x3")
        name, direction, tile = inserter_between((1, -2), (1, 0))
        ingredient = lanes._solid(recipes.raw[recipe]["ingredients"])
        first = sorted(ingredient)[0]
        placed = line(-1, 3, -2) + [{"name": name, "position": list(tile), "direction": direction},
                                    {"name": machine, "position": [0, 0], "recipe": recipe}]
        result = built(placed, [{"at": [-1, -2], "items": [first]}])
        (starved,) = [f for f in result.findings if f.code == "lanes-starved"]
        assert sorted(ingredient - {first})[0] in starved.summary

        block, port = block_and_port()
        short = built(blocks=[block], connections=[feed_port(port, [port["items"][0], ""])])
        assert "lanes-missing" in codes(short) and "lanes-starved" not in codes(short)


# --------------------------------------------------------------------------
# moved from the router's lane checks
# --------------------------------------------------------------------------


class TestPointLanes:
    def build(self, start, goal) -> plan.BuildResult:
        belt_, _, _ = prototypes.belt_set()
        return built(connections=[{"kind": "belt", "belt": belt_, "from": point(0, 0, items=start),
                                   "to": point(8, 0, items=goal)}])

    def test_unknown_lanes_are_noted(self) -> None:
        result = self.build(None, ["a"])
        assert "lanes-unknown" in codes(result)
        assert "lanes-wrong" not in codes(result)

    @pytest.mark.parametrize("source", [["", "a"], ["a", ""]])
    def test_one_item_for_both_lanes_takes_it_from_either(self, source) -> None:
        result = self.build(source, ["a"])
        assert result.routes[0].ok
        assert "lanes-wrong" not in codes(result)

    def test_one_item_for_both_lanes_still_wants_that_item(self) -> None:
        assert "lanes-wrong" in codes(self.build(["b", ""], ["a"]))

    def test_a_mixed_lane_carries_both(self) -> None:
        assert "lanes-wrong" not in codes(self.build(["", "a+b"], ["", "b"]))
