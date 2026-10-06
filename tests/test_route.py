"""Tests for joining ports with belts and pipes, against the active data.

Like the rest of the suite these ask the data for what they need -- a belt
with an underground that goes with it, a pipe and its pipe-to-ground, a
one-tile chest to build walls from -- and read reach from the prototype, so
they hold under any mod set. Walls are hand-placed chests; most routes run
between points, so the block code is only involved where blocks are the
subject. Every route is then held to the general checker and the fluid
networks, which know nothing about how the route was found.
"""

from __future__ import annotations

import pytest
from draftsman.data import entities, recipes

import prototypes
from factorio_forge import cli, fluids, inspection, lanes, layout, plan, route, rows
from factorio_forge.categories import crafts, recipe_categories

NORTH, EAST, SOUTH, WEST = 0, 4, 8, 12


# --------------------------------------------------------------------------
# prototypes and helpers
# --------------------------------------------------------------------------


def belt_with_underground() -> tuple[str, str]:
    """A buildable belt and the underground belt that goes with it."""
    placeable = prototypes._buildable()
    for name in sorted(entities.raw):
        data = entities.raw[name]
        if data.get("type") != "transport-belt" or name not in placeable:
            continue
        try:
            underground = route.related_underground(name)
        except route.RouteError:
            continue
        if underground:
            return name, underground
    pytest.skip("the active game data has no belt with an underground belt named for it or of its speed")


def pipe_pair() -> tuple[str, str]:
    return rows._default_pipe("pipe"), rows._default_pipe("pipe-to-ground")


def build(connections, entities_=(), blocks=(), routing=None) -> plan.BuildResult:
    the_plan = {"label": "route test", "blocks": list(blocks), "entities": list(entities_),
                "connections": list(connections)}
    if routing is not None:
        the_plan["routing"] = routing
    return plan.build(the_plan)


def point(x, y, direction=EAST, items=None, rate=None) -> dict:
    found = {"at": [x, y], "direction": direction}
    if items is not None:
        found["items"] = list(items)
    if rate is not None:
        found["rate"] = rate
    return found


def chests(tiles) -> list[dict]:
    chest = prototypes.small_chest()
    return [{"name": chest, "position": [x, y]} for x, y in tiles]


def wall(x0, x1, y0, y1) -> list[dict]:
    return chests((x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))


def codes(result) -> list[str]:
    return [f.code for f in result.findings]


def assert_clean(result, expected=()) -> None:
    serious = [
        str(f) for f in result.findings
        if f.severity is not inspection.Severity.NOTE and f.code not in expected
    ]
    assert serious == []


def tiles_of(route_result) -> set[tuple[int, int]]:
    return {(p.x, p.y) for p in route_result.pieces}


def block_spec(recipe, machine, counts, **extra) -> dict:
    fields = dict(recipe=recipe, machine=machine, rows=counts, belt=prototypes.fastest_belt(),
                  inserter=prototypes.inserter_reaching(1), pole=prototypes.small_pole(), stack_size=12)
    fields.update(extra)
    return fields


def chained_recipes() -> tuple[tuple[str, str], tuple[str, str]]:
    """Two one-item-in, one-item-out recipes, the second eating the first's product, each with a 3x3 machine."""
    placeable = prototypes._buildable()

    def fluids_fit(data) -> bool:  # as in prototypes.crafting_setup
        return all(
            c.get("direction") in (0, 8)
            for box in data.get("fluid_boxes") or []
            for c in box.get("pipe_connections") or []
            if c.get("connection_type") != "underground"
        )

    machines = sorted(
        name for name, data in entities.raw.items()
        if name in placeable and data.get("crafting_speed") and data.get("crafting_categories")
        and prototypes._footprint(data) == (3, 3) and fluids_fit(data)
    )

    def solid_only(recipe) -> str | None:
        """The single solid product of a one-solid-in, one-solid-out recipe, else None."""
        if not recipe or "recycling" in recipe_categories(recipe):
            return None
        ins, outs = recipe.get("ingredients") or [], recipe.get("results") or []
        if len(ins) != 1 or len(outs) != 1 or any(p.get("type") == "fluid" for p in ins + outs):
            return None
        if "amount" not in outs[0]:
            return None
        return outs[0].get("name")

    def machine_for(recipe) -> str | None:
        return next((m for m in machines if crafts(entities.raw[m], recipe)), None)

    for first in sorted(recipes.raw):
        product = solid_only(recipes.raw[first])
        if product is None or not machine_for(recipes.raw[first]):
            continue
        for second in sorted(recipes.raw):
            data = recipes.raw[second]
            if second == first or solid_only(data) is None or data["ingredients"][0].get("name") != product:
                continue
            machine = machine_for(data)
            if machine:
                return (first, machine_for(recipes.raw[first])), (second, machine)
    pytest.skip("the active game data has no two one-item recipes where the second eats the first's product")


def ports_of(result, block=0) -> list[dict]:
    return result.blocks[block].ports


def belt_route(start, goal, belt, underground=None, **extra) -> dict:
    found = {"kind": "belt", "from": start, "to": goal, "belt": belt}
    if underground is not None:
        found["underground"] = underground
    found.update(extra)
    return found


# --------------------------------------------------------------------------
# plain belts
# --------------------------------------------------------------------------


class TestBelts:
    def test_a_straight_run(self) -> None:
        belt, ug = belt_with_underground()
        result = build([belt_route(point(0, 0, items=["a"]), point(9, 0), belt, ug)])
        (done,) = result.routes
        assert done.ok
        assert [(p.x, p.y) for p in done.pieces] == [(x, 0) for x in range(10)]
        assert {p.direction for p in done.pieces} == {EAST}
        assert {p.name for p in done.pieces} == {belt}
        assert result.findings == []

    def test_one_turn_not_a_staircase(self) -> None:
        belt, ug = belt_with_underground()
        result = build([belt_route(point(0, 0), point(8, 5, SOUTH), belt, ug)])
        (done,) = result.routes
        assert done.ok and done.turns == 1 and done.hops == 0

    def test_a_hop_under_a_wall(self) -> None:
        belt, ug = belt_with_underground()
        reach = prototypes.underground_reach_of(ug)
        result = build(
            [belt_route(point(0, 0, items=["a"]), point(reach + 4, 0), belt, ug)],
            wall(3, 3, -1, 1),
            routing={"margin": 0},
        )
        (done,) = result.routes
        assert done.ok and done.hops == 1
        assert sum(1 for p in done.pieces if p.name == ug) == 2
        assert "underground-unpaired" not in codes(result)
        assert_clean(result)

    def test_the_longest_hop_is_the_stated_reach(self) -> None:
        belt, ug = belt_with_underground()
        reach = prototypes.underground_reach_of(ug)
        result = build(
            [belt_route(point(0, 0), point(reach + 4, 0), belt, ug)],
            wall(2, reach, -1, 1),  # reach - 1 thick
            routing={"margin": 0},
        )
        (done,) = result.routes
        assert done.ok, done.reason
        entry, exit_ = [p for p in done.pieces if p.name == ug]
        assert exit_.x - entry.x == reach
        assert "underground-unpaired" not in codes(result)

    def test_one_tile_more_fails_and_places_nothing(self) -> None:
        belt, ug = belt_with_underground()
        reach = prototypes.underground_reach_of(ug)
        blocking = wall(2, reach + 1, -1, 1)  # reach thick, the whole area high
        result = build([belt_route(point(0, 0), point(reach + 5, 0), belt, ug)], blocking, routing={"margin": 0})
        (done,) = result.routes
        assert not done.ok and done.pieces == []
        assert "route-failed" in codes(result)
        assert len(result.blueprint.entities) == len(blocking)

    def test_without_undergrounds_it_goes_round(self) -> None:
        belt, ug = belt_with_underground()
        result = build([belt_route(point(0, 0), point(10, 0), belt, False)], wall(5, 5, -2, 2))
        (done,) = result.routes
        assert done.ok and done.hops == 0
        assert {p.name for p in done.pieces} == {belt}
        assert not tiles_of(done) & {(5, y) for y in range(-2, 3)}
        assert_clean(result)

    @pytest.mark.parametrize("hops", [True, False])
    def test_never_side_loads_into_a_foreign_belt_or_takes_its_feed(self, hops: bool) -> None:
        belt, ug = belt_with_underground()
        ug = ug if hops else False
        foreign = [
            {"name": belt, "position": [5, -1], "direction": SOUTH},  # points into the straight line
            {"name": belt, "position": [7, 1], "direction": 0},
        ]
        result = build([belt_route(point(0, 0), point(10, 0), belt, ug)], foreign)
        (done,) = result.routes
        assert done.ok
        assert (5, 0) not in tiles_of(done) and (7, 0) not in tiles_of(done)
        # and no route piece points into either foreign belt
        for p in done.pieces:
            if p.io_type == "input":
                continue
            dx, dy = inspection.STEP[p.direction]
            assert (p.x + dx, p.y + dy) not in {(5, -1), (7, 1)}
        assert "belts-head-on" not in codes(result)

    def test_a_point_continues_whatever_feeds_it_from_behind(self) -> None:
        belt, ug = belt_with_underground()
        pipe, _ = pipe_pair()
        result = build(
            [belt_route(point(0, 0), point(6, 0), belt, ug),
             {"kind": "pipe", "from": point(0, 3), "to": point(6, 3), "pipe": pipe}],
            [{"name": belt, "position": [-1, 0], "direction": EAST}, {"name": pipe, "position": [-1, 3]},
             {"name": pipe, "position": [7, 3]}],
        )
        assert all(r.ok for r in result.routes), [r.reason for r in result.routes]
        assert_clean(result)

    def test_a_point_never_side_loads_into_a_crossing_route(self) -> None:
        belt, ug = belt_with_underground()
        result = build([
            belt_route(point(0, 0), point(10, 0), belt, ug, id="across"),
            belt_route(point(5, -4, SOUTH), point(5, -1, SOUTH), belt, ug, id="down"),
        ])
        across, down = result.routes
        assert across.ok and not down.ok and down.pieces == []
        assert "(5, 0)" in down.reason
        assert "route-failed" in codes(result)

    def test_a_point_may_continue_a_belt_carrying_the_same_way(self) -> None:
        belt, ug = belt_with_underground()
        result = build([belt_route(point(0, 0), point(10, 0), belt, ug)],
                       [{"name": belt, "position": [11, 0], "direction": EAST}])
        (done,) = result.routes
        assert done.ok, done.reason
        assert_clean(result)

    def test_a_point_into_a_curve_is_refused(self) -> None:
        belt, ug = belt_with_underground()
        curve = [{"name": belt, "position": [11, 0], "direction": EAST},
                 {"name": belt, "position": [11, -1], "direction": SOUTH}]
        result = build([belt_route(point(0, 0), point(10, 0), belt, ug)], curve)
        (done,) = result.routes
        assert not done.ok and "curve" in done.reason

    def test_a_pipe_point_into_something_without_a_connection_is_refused(self) -> None:
        pipe, ptg = pipe_pair()
        result = build([{"kind": "pipe", "from": point(0, 0, items=["f"]), "to": point(6, 0), "pipe": pipe,
                         "pipe_to_ground": ptg}], chests([(7, 0)]))
        (done,) = result.routes
        assert not done.ok and "no fluid connection" in done.reason

    @pytest.mark.parametrize("hops", [True, False])
    def test_keeps_out_of_an_inserters_reach(self, hops: bool) -> None:
        belt, ug = belt_with_underground()
        ug = ug if hops else False
        arm = prototypes.inserter_reaching(1)
        placed = [{"name": arm, "position": [5, -1], "direction": 0}] + chests([(5, -2)])
        result = build([belt_route(point(0, 0), point(10, 0), belt, ug)], placed)
        (done,) = result.routes
        assert done.ok
        inserter = next(e for e in result.blueprint.entities if e.name == arm)
        reached = {(int(x // 1), int(y // 1)) for x, y in inspection.inserter_reach(inserter)}
        assert not tiles_of(done) & reached


# --------------------------------------------------------------------------
# undergrounds already there
# --------------------------------------------------------------------------


def belt_pairs(blueprint) -> dict[tuple[int, int], tuple[int, int]]:
    """Each underground belt's partner, found the way the checker finds it."""
    view = inspection.Layout(blueprint)
    found = {}
    for e in blueprint.entities:
        if e.type != "underground-belt":
            continue
        step = inspection.STEP[int(e.direction)]
        if e.io_type == "output":
            step = (-step[0], -step[1])
        x, y = view.tile_of(e)
        for k in range(1, inspection.underground_reach(e) + 1):
            other = [o for o in view.at(x + step[0] * k, y + step[1] * k) if o is not e and o.name == e.name]
            if other:
                found[(x, y)] = view.tile_of(other[0])
                break
    return found


class TestTunnels:
    def corridor(self, ours, theirs, chest_at=5):
        """A one-tile-high corridor with a foreign pair across it and a chest between its ends."""
        belt, _ = belt_with_underground()
        foreign = [
            {"name": theirs, "position": [4, 0], "direction": EAST, "io_type": "input"},
            {"name": theirs, "position": [6, 0], "direction": EAST, "io_type": "output"},
        ] + chests([(chest_at, 0)])
        return build([belt_route(point(0, 0), point(14, 0), belt, ours)], foreign, routing={"margin": 0})

    def test_a_pair_of_the_same_name_is_not_intercepted(self) -> None:
        _, ug = belt_with_underground()
        if prototypes.underground_reach_of(ug) < 5:
            pytest.skip(f"{ug} does not reach far enough to tempt an interception")
        result = self.corridor(ug, ug)
        (done,) = result.routes
        assert not done.ok
        assert belt_pairs(result.blueprint) == {(4, 0): (6, 0), (6, 0): (4, 0)}

    def test_a_pair_of_another_name_is_passed_beneath(self) -> None:
        short, long = prototypes.two_underground_belts()
        if prototypes.underground_reach_of(long) < 5:
            pytest.skip(f"{long} does not reach far enough to pass the other pair")
        result = self.corridor(long, short)
        (done,) = result.routes
        assert done.ok, done.reason
        pairs = belt_pairs(result.blueprint)
        assert pairs[(4, 0)] == (6, 0) and pairs[(6, 0)] == (4, 0)
        entry, exit_ = [p for p in done.pieces if p.name == long]
        assert pairs[(entry.x, entry.y)] == (exit_.x, exit_.y)
        assert "underground-unpaired" not in codes(result)

    def test_a_pipe_pair_of_the_same_name_is_not_intercepted(self) -> None:
        pipe, ptg = pipe_pair()
        dive, surface = route.ptg_directions(ptg, EAST)
        foreign = [
            {"name": ptg, "position": [4, 0], "direction": dive},
            {"name": ptg, "position": [6, 0], "direction": surface},
        ] + chests([(5, 0)])
        result = build(
            [{"kind": "pipe", "from": point(0, 0, items=["f"]), "to": point(14, 0), "pipe": pipe, "pipe_to_ground": ptg}],
            foreign, routing={"margin": 0},
        )
        assert not result.routes[0].ok
        nets, _ = fluids.networks(list(result.blueprint.entities))
        joined = [n for n in nets if len(n.nodes) > 1]
        assert len(joined) == 1 and {node[0] for node in joined[0].nodes} == {0, 1}


# --------------------------------------------------------------------------
# blocks
# --------------------------------------------------------------------------


class TestBlocks:
    def test_a_belt_into_a_blocks_input_port(self) -> None:
        recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
        block = block_spec(recipe, machine, [2])
        first = plan.build({"blocks": [block]})
        index, port = next((p["index"], p) for p in ports_of(first) if p["io"] == "in" and p["kind"] == "belt")
        ingredient = port["items"][0]
        dx, dy = inspection.STEP[port["direction"]]
        start = point(port["x"] - 6 * dx, port["y"] - 6 * dy, port["direction"], items=[ingredient])
        result = build([{"from": start, "to": {"block": 0, "port": index, "items": [ingredient]}}], blocks=[block])
        (done,) = result.routes
        assert done.ok, done.reason
        last = done.pieces[-1]
        assert (last.x, last.y, last.direction) == (port["x"] - dx, port["y"] - dy, port["direction"])
        assert done.lanes == (ingredient, ingredient)
        assert_clean(result)

    @pytest.mark.parametrize("rotate", [0, 90])
    def test_a_blocks_output_port_to_a_point(self, rotate: int) -> None:
        recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
        block = {**block_spec(recipe, machine, [2]), "rotate": rotate}
        first = plan.build({"blocks": [block]})
        index, port = next((p["index"], p) for p in ports_of(first) if p["io"] == "out")
        assert port["direction"] == (EAST + rotate // 90 * 4) % 16
        dx, dy = inspection.STEP[port["direction"]]
        goal = point(port["x"] + 6 * dx, port["y"] + 6 * dy, port["direction"])
        result = build([{"from": {"block": 0, "port": index}, "to": goal}], blocks=[block])
        (done,) = result.routes
        assert done.ok, done.reason
        assert (done.pieces[0].x, done.pieces[0].y) == (port["x"] + dx, port["y"] + dy)
        assert_clean(result)

    def test_a_blocks_output_feeds_another_blocks_input(self) -> None:
        (first_recipe, first_machine), (second_recipe, second_machine) = chained_recipes()
        maker = block_spec(first_recipe, first_machine, [2])
        alone = plan.build({"blocks": [maker]})
        out_index, out_port = next((p["index"], p) for p in ports_of(alone) if p["io"] == "out")
        (product,) = {part for lane in out_port["items"] for part in lane.split("+") if part}
        user = {**block_spec(second_recipe, second_machine, [2]), "at": [alone.blocks[0].width + 10, 0]}
        in_index = next(p["index"] for p in ports_of(plan.build({"blocks": [user]}))
                        if p["io"] == "in" and p["kind"] == "belt")
        result = build(
            [{"from": {"block": 0, "port": out_index, "items": [product]},
              "to": {"block": 1, "port": in_index, "items": [product]}}],
            blocks=[maker, user],
        )
        (done,) = result.routes
        assert done.ok, done.reason
        assert not [c for c in codes(result) if c.startswith("lanes-")]
        # The blocks stand apart and nothing wires them together; power is not the route's job.
        # A block powered by a single pole (big machines) also shows as an isolated pole.
        assert_clean(result, expected=("power-split", "pole-isolated"))

    def test_the_report_numbers_ports_and_names_prototypes(self) -> None:
        recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
        result = plan.build({"blocks": [block_spec(recipe, machine, [2])]})
        report = result.to_dict()
        assert [p["index"] for p in report["blocks"][0]["ports"]] == list(range(len(ports_of(result))))
        assert report["blocks"][0]["prototypes"]["belt"] == prototypes.fastest_belt()
        assert report["routes"] == []


# --------------------------------------------------------------------------
# pipes
# --------------------------------------------------------------------------


class TestPipes:
    def test_a_pipe_keeps_away_from_another_fluid(self) -> None:
        recipe, machine = prototypes.crafting_setup(0, 1, 1, 0)
        block = block_spec(recipe, machine, [2])
        first = plan.build({"blocks": [block]})
        index, port = next((p["index"], p) for p in ports_of(first) if p["kind"] == "pipe" and p["io"] == "in")
        top = min(e.tile_position.y for e in first.blueprint.entities)
        bottom = max(e.tile_position.y + e.tile_height - 1 for e in first.blueprint.entities)
        if port["y"] == top:
            beside = port["y"] - 1
        elif port["y"] == bottom:
            beside = port["y"] + 1
        else:
            pytest.skip("the block's pipe does not run along its edge")
        fluid = port["items"][0]
        width = max(e.tile_position.x + e.tile_width for e in first.blueprint.entities)
        result = build(
            [
                {"from": point(port["x"] - 6, port["y"], items=[fluid]), "to": {"block": 0, "port": index}},
                {"kind": "pipe", "from": point(port["x"] - 6, beside, items=["another-fluid"]),
                 "to": point(width + 4, beside)},
            ],
            blocks=[block],
        )
        feed, other = result.routes
        assert feed.ok and other.ok, (feed.reason, other.reason)
        entities_ = list(result.blueprint.entities)
        others = {i for i, e in enumerate(entities_) if (e.tile_position.x, e.tile_position.y) in tiles_of(other)
                  and e.name in {p.name for p in other.pieces}}
        nets, _ = fluids.networks(entities_)
        for net in nets:
            members = {node[0] for node in net.nodes}
            if members & others:
                assert members <= others, "the second route joined someone else's pipes"
        assert "fluids-mixed" not in codes(result)
        assert "fluid-box-unconnected" not in codes(result)

    def test_pipe_to_ground_directions_come_from_the_prototype(self) -> None:
        pipe, ptg = pipe_pair()
        offset = inspection.underground_direction(ptg)
        for travel in (0, 4, 8, 12):
            dive, surface = route.ptg_directions(ptg, travel)
            assert (dive + offset) % 16 == travel
            assert (surface + offset) % 16 == fluids.OPPOSITE[travel]
        self.assert_hop_joins(pipe, ptg)

    def test_a_turned_pipe_to_ground_is_read_not_assumed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pipe, ptg = pipe_pair()
        data = dict(entities.raw[ptg])
        box = dict(data["fluid_box"])
        box["pipe_connections"] = [
            {**c, "direction": (int(c["direction"]) + 4) % 16} for c in box["pipe_connections"]
        ]
        data["fluid_box"] = box
        monkeypatch.setitem(entities.raw, ptg, data)
        offset = inspection.underground_direction(ptg)
        dive, surface = route.ptg_directions(ptg, EAST)
        assert (dive + offset) % 16 == EAST and (surface + offset) % 16 == fluids.OPPOSITE[EAST]
        self.assert_hop_joins(pipe, ptg)

    @staticmethod
    def assert_hop_joins(pipe: str, ptg: str) -> None:
        result = build(
            [{"kind": "pipe", "from": point(0, 0, items=["f"]), "to": point(8, 0), "pipe": pipe, "pipe_to_ground": ptg}],
            wall(3, 3, -1, 1), routing={"margin": 0},
        )
        (done,) = result.routes
        assert done.ok and done.hops == 1, done.reason
        nets, _ = fluids.networks(list(result.blueprint.entities))
        routed = [n for n in nets if len(n.nodes) > 1]
        assert len(routed) == 1 and len(routed[0].nodes) == len(done.pieces)


# --------------------------------------------------------------------------
# steering, order, budget
# --------------------------------------------------------------------------


class TestSteering:
    def test_via_tiles_are_covered_in_order(self) -> None:
        belt, ug = belt_with_underground()
        via = [[3, 3], [7, -3]]
        result = build([belt_route(point(0, 0), point(10, 0), belt, ug, via=via)])
        (done,) = result.routes
        assert done.ok, done.reason
        path = [(p.x, p.y) for p in done.pieces]
        assert path.index((3, 3)) < path.index((7, -3))
        assert all(p.name == belt for p in done.pieces if (p.x, p.y) in {(3, 3), (7, -3)})

    @pytest.mark.parametrize("hops", [True, False])
    def test_vias_that_double_back_do_not_cross_the_path(self, hops: bool) -> None:
        belt, ug = belt_with_underground()
        via = [(6, 0), (3, -3)]
        result = build([belt_route(point(0, 0), point(3, 3, SOUTH), belt, ug if hops else False,
                                   via=[list(v) for v in via])])
        (done,) = result.routes
        assert done.ok, done.reason
        path = [(p.x, p.y) for p in done.pieces]
        assert len(path) == len(set(path))
        assert path.index(via[0]) < path.index(via[1])
        assert_clean(result)

    def test_an_occupied_via_tile_is_named(self) -> None:
        belt, ug = belt_with_underground()
        result = build([belt_route(point(0, 0), point(10, 0), belt, ug, via=[[5, 2]])], chests([(5, 2)]))
        (failed,) = [f for f in result.findings if f.code == "route-failed"]
        assert "(5, 2)" in failed.summary

    def corridor_plan(self, via=None) -> plan.BuildResult:
        belt, _ = belt_with_underground()
        first = belt_route(point(0, 0), point(10, 0), belt, False, id="first")
        if via:
            first["via"] = via
        second = belt_route(point(3, 0), point(7, 0), belt, False, id="second")
        walls = wall(2, 8, -1, -1) + wall(2, 8, 1, 1)
        return build([first, second], walls)

    def test_an_earlier_route_is_an_obstacle(self) -> None:
        result = self.corridor_plan()
        first, second = result.routes
        belt, _ = belt_with_underground()
        assert first.ok and not second.ok
        assert belt in second.reason

    def test_a_via_lets_both_through(self) -> None:
        result = self.corridor_plan(via=[[5, -3]])
        first, second = result.routes
        assert first.ok and second.ok, (first.reason, second.reason)
        assert_clean(result)

    def test_the_search_budget_is_reported(self) -> None:
        belt, ug = belt_with_underground()
        result = build([belt_route(point(0, 0), point(20, 5), belt, ug)], routing={"max_nodes": 10})
        (done,) = result.routes
        assert not done.ok and "search stopped" in done.reason

    def test_reserved_tiles_are_kept_free(self) -> None:
        belt, ug = belt_with_underground()
        result = build([belt_route(point(0, 0), point(10, 0), belt, False)], routing={"reserve": [[4, -1, 6, 1]]})
        (done,) = result.routes
        assert done.ok and not tiles_of(done) & {(x, y) for x in range(4, 7) for y in range(-1, 2)}

    def test_the_same_plan_builds_the_same_blueprint(self) -> None:
        belt, ug = belt_with_underground()
        the_plan = {
            "label": "twice",
            "entities": wall(4, 4, -2, 2) + chests([(8, 3)]),
            "connections": [
                belt_route(point(0, 0, items=["a"]), point(12, 4, SOUTH), belt, ug),
                belt_route(point(0, -4, items=["b"]), point(12, -4), belt, ug, via=[[6, -1]]),
            ],
        }
        assert plan.build(the_plan).blueprint.to_string() == plan.build(the_plan).blueprint.to_string()


# --------------------------------------------------------------------------
# splits, merges, lane joins
# --------------------------------------------------------------------------


def splitters_of(result) -> list:
    return [e for e in result.blueprint.entities if e.type == "splitter"]


def lane_findings(result) -> list[str]:
    return [c for c in codes(result) if c.startswith("lanes-") or c == "route-lanes-disagree"]


def chain_blocks(users: int):
    """A maker block and `users` blocks eating its product, stacked to its right; the ports to join."""
    (first_recipe, first_machine), (second_recipe, second_machine) = chained_recipes()
    maker = block_spec(first_recipe, first_machine, [2])
    alone = plan.build({"blocks": [maker]})
    out_index = next(p["index"] for p in ports_of(alone) if p["io"] == "out")
    user = block_spec(second_recipe, second_machine, [2])
    user_alone = plan.build({"blocks": [user]})
    in_index = next(p["index"] for p in ports_of(user_alone) if p["io"] == "in" and p["kind"] == "belt")
    pitch = user_alone.blocks[0].height + 6
    blocks = [maker] + [{**user, "at": [alone.blocks[0].width + 12, k * pitch]} for k in range(users)]
    return blocks, out_index, in_index


class TestSplits:
    def test_one_out_port_to_two_in_ports(self) -> None:
        blocks, out_index, in_index = chain_blocks(2)
        belt = blocks[0]["belt"]
        split = {"id": "share", "from": {"block": 0, "port": out_index},
                 "to": [{"block": 1, "port": in_index}, {"block": 2, "port": in_index}]}
        result = build([split], blocks=blocks)
        assert [r.id for r in result.routes] == ["share/0", "share/1"]
        assert all(r.ok for r in result.routes), [r.reason for r in result.routes]
        (splitter,) = splitters_of(result)
        assert splitter.name == route.related_splitter(belt)
        a, b = sorted(inspection.Layout.tiles_of(splitter))
        dx, dy = inspection.STEP[int(splitter.direction)]
        assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1 and (b[0] - a[0]) * dx + (b[1] - a[1]) * dy == 0
        assert_clean(result, expected=("power-split", "pole-isolated"))
        for block in result.blocks[1:]:
            assert block.ports[in_index]["arrives"] is not None
        assert not lane_findings(result)

    def test_three_destinations_cascade(self) -> None:
        blocks, out_index, in_index = chain_blocks(3)
        split = {"from": {"block": 0, "port": out_index}, "to": [{"block": k, "port": in_index} for k in (1, 2, 3)]}
        result = build([split], blocks=blocks)
        assert all(r.ok for r in result.routes), [r.reason for r in result.routes]
        assert len(splitters_of(result)) == 2
        assert "route-split-cascade" in codes(result)

    @pytest.mark.parametrize("side", ["left", "right"])
    def test_a_chosen_split_tile_is_honoured(self, side: str) -> None:
        belt, ug, _ = prototypes.belt_set()
        other = 6 if side == "right" else -6
        split = {"kind": "belt", "belt": belt, "from": point(0, 0, items=["a"]),
                 "to": [point(12, 0), point(12, other)], "split": [{"at": [4, 0], "side": side}]}
        result = build([split])
        assert all(r.ok for r in result.routes), [r.reason for r in result.routes]
        (splitter,) = splitters_of(result)
        assert set(inspection.Layout.tiles_of(splitter)) == {(4, 0), (4, 1 if side == "right" else -1)}
        assert result.routes[1].junction.tile == (4, 0) and result.routes[1].junction.side == side
        # The route still reads as a path through its splitter.
        straight = route.straight_pieces(result.routes[0])
        assert 4 not in straight and 5 not in straight and 6 in straight
        assert_clean(result)

    def test_a_split_tile_off_the_straight_is_refused(self) -> None:
        belt, _, _ = prototypes.belt_set()
        split = {"kind": "belt", "belt": belt, "from": point(0, 0, items=["a"]),
                 "to": [point(12, 0), point(12, 6)], "split": [{"at": [4, 3], "side": "right"}]}
        result = build([split])
        assert not any(r.ok for r in result.routes)
        (failed, _) = [f for f in result.findings if f.code == "route-failed"]
        assert "not a straight belt" in failed.summary

    def test_no_room_for_the_second_half_places_nothing(self) -> None:
        belt, _, _ = prototypes.belt_set()
        walls = wall(-1, 13, -1, -1) + wall(-1, 13, 1, 1)
        split = {"kind": "belt", "belt": belt, "underground": False, "from": point(0, 0, items=["a"]),
                 "to": [point(12, 0), point(12, 4)]}
        result = build([split], walls, routing={"margin": 1})
        assert [r.ok for r in result.routes] == [False, False]
        assert codes(result).count("route-failed") == 2
        assert len(result.blueprint.entities) == len(build([], walls).blueprint.entities)

    def test_no_splitter_of_the_belts_speed_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        belt, _, _ = prototypes.belt_set()
        speed = entities.raw[belt]["speed"]
        for name, data in list(entities.raw.items()):
            if data.get("type") == "splitter" and data.get("speed") == speed:
                monkeypatch.delitem(entities.raw, name)
        with pytest.raises(plan.PlanError, match="'splitter'"):
            build([{"kind": "belt", "belt": belt, "from": point(0, 0), "to": [point(12, 0), point(12, 6)]}])

    def test_pipes_do_not_split(self) -> None:
        pipe, _ = pipe_pair()
        with pytest.raises(plan.PlanError, match="pipes join whatever they touch"):
            build([{"kind": "pipe", "pipe": pipe, "from": point(0, 0), "to": [point(12, 0), point(12, 6)]}])


def two_ingredient_block():
    recipe, machine = prototypes.crafting_setup(2, 0, 1, 0)
    block = block_spec(recipe, machine, [2])
    port = next(p for p in ports_of(plan.build({"blocks": [block]})) if p["io"] == "in" and p["kind"] == "belt")
    return block, port


class TestMerges:
    def test_onto_a_route_with_the_lane_chosen(self) -> None:
        block, port = two_ingredient_block()
        w0, w1 = port["items"]
        dx, dy = inspection.STEP[port["direction"]]
        start = point(port["x"] - 10 * dx, port["y"] - 10 * dy, port["direction"], items=[w0, ""])
        side = point(port["x"] - 6 * dx - 4 * dy, port["y"] - 6 * dy - 4 * dx, port["direction"], items=[w1])
        result = build([{"id": "main", "from": start, "to": {"block": 0, "port": port["index"]}},
                        {"id": "side", "from": side, "onto": {"route": "main"}}], blocks=[block])
        main, merged = result.routes
        assert main.ok and merged.ok, (main.reason, merged.reason)
        assert merged.junction.kind == "sideload"
        assert not lane_findings(result)
        tiles = [(p.x, p.y) for p in main.pieces]
        at = tiles.index(merged.junction.tile)
        assert at > 0 and main.pieces[at - 1].direction == main.pieces[at].direction
        assert result.blocks[0].ports[port["index"]]["arrives"] == [w0, w1]
        assert_clean(result)

    def test_onto_a_port_means_the_route_ending_there(self) -> None:
        block, port = two_ingredient_block()
        w0, w1 = port["items"]
        dx, dy = inspection.STEP[port["direction"]]
        start = point(port["x"] - 10 * dx, port["y"] - 10 * dy, port["direction"], items=["", w1])
        side = point(port["x"] - 6 * dx - 4 * dy, port["y"] - 6 * dy - 4 * dx, port["direction"], items=[w0])
        target = {"block": 0, "port": port["index"]}
        result = build([{"id": "main", "from": start, "to": target},
                        {"id": "side", "from": side, "onto": target}], blocks=[block])
        assert result.routes[1].ok and result.routes[0].merges
        assert not lane_findings(result)
        with pytest.raises(plan.PlanError, match="routed later"):
            build([{"id": "side", "from": side, "onto": {"route": "main"}},
                   {"id": "main", "from": start, "to": target}], blocks=[block])

    def test_a_merge_lands_past_the_routes_splitter(self) -> None:
        belt, _, _ = prototypes.belt_set()
        split = {"id": "s", "kind": "belt", "belt": belt, "from": point(0, 0, items=["a", ""]),
                 "to": [point(16, 0), point(16, 6)], "split": [{"at": [3, 0], "side": "right"}]}
        merge = {"kind": "belt", "belt": belt, "from": point(1, -6, SOUTH, items=["b"]),
                 "onto": {"route": "s/0", "lane": "left"}}
        result = build([split, merge])
        assert all(r.ok for r in result.routes), [r.reason for r in result.routes]
        base, branch, merged = result.routes
        at = [(p.x, p.y) for p in base.pieces].index(merged.junction.tile)
        assert at > [(p.x, p.y) for p in base.pieces].index((3, 0))
        assert branch.delivered == ("a", "-")

    def hand_line(self) -> tuple[list[dict], list[dict]]:
        belt, _, _ = prototypes.belt_set()
        return [{"name": belt, "position": [x, 0], "direction": EAST} for x in range(9)], \
            [{"at": [0, 0], "items": ["a", ""]}]

    def merge(self, onto, lane, items=("b",), extra=(), inputs=None) -> plan.BuildResult:
        belt, _, _ = prototypes.belt_set()
        line, declared = self.hand_line()
        return plan.build({"entities": line + list(extra), "inputs": declared if inputs is None else inputs,
                           "connections": [{"kind": "belt", "belt": belt, "from": point(4, -5, SOUTH, items=list(items)),
                                            "onto": {"at": list(onto), "lane": lane}}]})

    def test_onto_a_hand_belt_at_a_tile(self) -> None:
        result = self.merge((4, 0), "left")
        (done,) = result.routes
        assert done.ok, done.reason
        assert result.routes[0].delivered is not None
        assert not lane_findings(result)

    def test_onto_a_curve_or_a_head_is_refused(self) -> None:
        belt, _, _ = prototypes.belt_set()
        curve = [{"name": belt, "position": [9, 0], "direction": SOUTH}, {"name": belt, "position": [9, 1], "direction": SOUTH}]
        result = self.merge((9, 0), "left", extra=curve)
        assert not result.routes[0].ok and "nothing feeding it from behind" in result.routes[0].reason
        result = self.merge((0, 0), "left")
        assert not result.routes[0].ok and "nothing feeding it from behind" in result.routes[0].reason

    def test_onto_an_entrance_through_the_hood(self) -> None:
        belt, ug, _ = prototypes.belt_set()
        reach = prototypes.underground_reach_of(ug)
        exit_x = 4 + min(reach, 3)
        line = [{"name": belt, "position": [x, 0], "direction": EAST} for x in range(4)]
        line += [{"name": ug, "position": [4, 0], "direction": EAST, "io_type": "input"},
                 {"name": ug, "position": [exit_x, 0], "direction": EAST, "io_type": "output"}]
        blocked = "left" if lanes.hood_passes("left") == "right" else "right"
        items = ["b", ""] if blocked == "left" else ["", "b"]
        result = plan.build({"entities": line, "inputs": [{"at": [0, 0], "items": ["a", ""]}],
                             "connections": [{"kind": "belt", "belt": belt, "from": point(4, -5, SOUTH, items=items),
                                              "onto": {"at": [4, 0], "lane": "left"}}]})
        assert not result.routes[0].ok and "hood" in result.routes[0].reason


    def test_a_merge_counts_only_its_lanes_share_of_the_route(self) -> None:
        belt, _, _ = prototypes.belt_set()
        lane = layout.lane_throughput(belt)

        def merged(extra: float) -> plan.BuildResult:
            main = {"id": "main", "kind": "belt", "belt": belt, "from": point(0, 0, items=["a"], rate=lane),
                    "to": point(16, 0)}
            side = {"kind": "belt", "belt": belt, "from": point(6, -5, SOUTH, items=["b"], rate=extra),
                    "onto": {"route": "main", "lane": "left"}}
            result = build([main, side])
            assert all(r.ok for r in result.routes), [r.reason for r in result.routes]
            return result

        # 'a' on both lanes at one lane's rate is half a lane each: room for more.
        assert "route-belt-slow" not in codes(merged(lane * 0.3))
        assert "route-belt-slow" in codes(merged(lane * 0.6))


class TestLaneJoins:
    def test_two_sources_onto_the_lanes_asked_for(self) -> None:
        belt, _, _ = prototypes.belt_set()
        join = {"kind": "belt", "belt": belt, "from": [point(0, 0, items=["a"]), point(12, 0, WEST, items=["b"])],
                "to": point(6, 8, SOUTH, items=["b", "a"])}
        result = build([join])
        assert all(r.ok for r in result.routes), [r.reason for r in result.routes]
        assert result.routes[0].delivered == ("b", "a")
        assert result.routes[0].junction.kind == "lane-join"
        assert not lane_findings(result)
        assert_clean(result)

    def test_a_path_without_a_turn_is_refused(self) -> None:
        belt, _, _ = prototypes.belt_set()
        join = {"kind": "belt", "belt": belt, "from": [point(0, 0, items=["a"]), point(4, -4, SOUTH, items=["b"])],
                "to": point(10, 0, items=["a", "b"])}
        result = build([join])
        assert not any(r.ok for r in result.routes)
        assert "via" in result.routes[0].reason


class TestJunctionsAgree:
    def plan_(self) -> dict:
        belt, ug, _ = prototypes.belt_set()
        blocks, out_index, in_index = chain_blocks(2)
        return {"label": "twice", "blocks": blocks, "entities": [], "connections": [
            {"from": {"block": 0, "port": out_index}, "to": [{"block": 1, "port": in_index}, {"block": 2, "port": in_index}]},
        ]}

    def test_the_same_plan_builds_the_same_blueprint(self) -> None:
        the_plan = self.plan_()
        assert plan.build(the_plan).blueprint.to_string() == plan.build(the_plan).blueprint.to_string()

    def test_a_wrong_hood_rule_is_caught(self, monkeypatch: pytest.MonkeyPatch) -> None:
        belt, ug, _ = prototypes.belt_set()
        reach = prototypes.underground_reach_of(ug)
        walls = wall(5, 5, -1, 1)
        main = {"id": "main", "kind": "belt", "belt": belt, "underground": ug, "from": point(0, 0, items=["a", ""]),
                "to": point(reach + 4, 0, items=["a", ""])}
        base = build([main], walls, routing={"margin": 0})
        entrance = next(p for p in base.routes[0].pieces if p.io_type == "input")
        merge = {"kind": "belt", "belt": belt, "from": point(entrance.x, -5, SOUTH, items=["b", "c"]),
                 "onto": {"at": [entrance.x, entrance.y], "lane": "left"}}
        honest = build([main, merge], walls, routing={"margin": 0})
        assert honest.routes[1].ok, honest.routes[1].reason
        assert "route-lanes-disagree" not in codes(honest)
        wrong = {"left": "left", "right": "right"}
        monkeypatch.setattr(lanes, "hood_passes", lambda side: wrong[side])
        seeded = build([main, merge], walls, routing={"margin": 0})
        assert "route-lanes-disagree" in codes(seeded)


def test_cli_build_prints_routes(tmp_path, capsys: pytest.CaptureFixture) -> None:
    import json

    belt, ug = belt_with_underground()
    the_plan = {
        "label": "cli routes",
        "entities": wall(4, 4, -1, 1),
        "connections": [
            belt_route(point(0, 0, items=["a"]), point(9, 0), belt, ug, id="first"),
            belt_route(point(0, 4), point(9, 4), belt, False, id="second"),
        ],
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(the_plan), encoding="utf-8")
    assert cli.main(["build", str(path)]) == 0
    out = capsys.readouterr().out
    assert "routes:" in out
    assert "route first: belt" in out and "route second: belt" in out
    assert "FAILED" not in out


# --------------------------------------------------------------------------
# default prototypes
# --------------------------------------------------------------------------


class TestDefaults:
    def block_route(self) -> dict:
        recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
        block = block_spec(recipe, machine, [2])
        first = plan.build({"blocks": [block]})
        index, port = next((p["index"], p) for p in ports_of(first) if p["io"] == "out")
        dx, dy = inspection.STEP[port["direction"]]
        return {"blocks": [block], "connections": [
            {"from": {"block": 0, "port": index}, "to": point(port["x"] + 8 * dx, port["y"] + 8 * dy, port["direction"])}
        ]}

    def test_the_blocks_belt_and_its_underground(self) -> None:
        the_plan = self.block_route()
        belt = the_plan["blocks"][0]["belt"]
        result = plan.build(the_plan)
        report = result.to_dict()["routes"][0]
        assert report["surface"] == belt
        assert report["underground"] == route.related_underground(belt)

    def test_no_underground_is_noted_and_the_route_stays_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        the_plan = self.block_route()
        belt = the_plan["blocks"][0]["belt"]
        speed = entities.raw[belt].get("speed")
        for name, data in list(entities.raw.items()):
            if data.get("type") == "underground-belt" and (data.get("speed") == speed
                                                            or name == entities.raw[belt].get("related_underground_belt")):
                monkeypatch.delitem(entities.raw, name)
        result = plan.build(the_plan)
        (done,) = result.routes
        assert done.ok and done.hops == 0
        assert "route-no-underground" in codes(result)

    def test_two_candidates_and_no_named_one_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        belt, ug = belt_with_underground()
        unnamed = {k: v for k, v in entities.raw[belt].items() if k != "related_underground_belt"}
        monkeypatch.setitem(entities.raw, belt, unnamed)
        twin = "forge-test-twin-underground"
        monkeypatch.setitem(entities.raw, twin, dict(entities.raw[ug]))
        same = sorted(n for n, d in entities.raw.items()
                      if d.get("type") == "underground-belt" and d.get("speed") == unnamed.get("speed"))
        with pytest.raises(plan.PlanError) as refused:
            build([belt_route(point(0, 0), point(5, 0), belt)])
        message = str(refused.value)
        assert "connection 0" in message
        assert all(name in message for name in same)

    def test_the_slowest_belt_is_too_slow_for_a_big_rate(self) -> None:
        placeable = prototypes._buildable()
        slow = min(
            (n for n, d in entities.raw.items() if d.get("type") == "transport-belt" and d.get("speed") and n in placeable),
            key=lambda n: (entities.raw[n]["speed"], n),
        )
        rate = layout.belt_throughput(slow) * 1.5
        result = build([belt_route(point(0, 0, items=["a"], rate=rate), point(6, 0), slow, False)])
        assert "route-belt-slow" in codes(result)


# --------------------------------------------------------------------------
# malformed plans
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mixed() -> tuple[dict, list[dict]]:
    """A block with a fluid input port and a belt output port."""
    recipe, machine = prototypes.crafting_setup(0, 1, 1, 0)
    block = block_spec(recipe, machine, [2])
    return block, ports_of(plan.build({"blocks": [block]}))


class TestRefusals:
    def refused(self, block, connections, match) -> str:
        with pytest.raises(plan.PlanError, match=match) as raised:
            plan.build({"blocks": [block], "connections": connections})
        return str(raised.value)

    def index(self, ports, io, kind) -> int:
        return next(p["index"] for p in ports if p["io"] == io and p["kind"] == kind)

    def test_an_id_used_twice(self) -> None:
        belt, _, _ = prototypes.belt_set()
        first = {"id": "x", "kind": "belt", "belt": belt, "from": point(0, 0), "to": point(8, 0)}
        second = {**first, "from": point(0, 4), "to": point(8, 4)}
        with pytest.raises(plan.PlanError, match="already used by connection 0"):
            build([first, second])
        with pytest.raises(plan.PlanError, match="has a '/'"):
            build([{**first, "id": "x/0"}])

    def test_a_port_out_of_range(self, mixed) -> None:
        block, ports = mixed
        self.refused(block, [{"from": {"block": 0, "port": 99}, "to": point(30, 0)}], "connection 0 .*port 99")

    def test_from_an_input_port(self, mixed) -> None:
        block, ports = mixed
        source = {"block": 0, "port": self.index(ports, "in", "pipe")}
        self.refused(block, [{"from": source, "to": point(30, 0)}], "connection 0 .*'in' port")

    def test_a_belt_to_a_pipe(self, mixed) -> None:
        block, ports = mixed
        connection = {"from": {"block": 0, "port": self.index(ports, "out", "belt")},
                      "to": {"block": 0, "port": self.index(ports, "in", "pipe")}}
        self.refused(block, [connection], "connection 0 .*belt port and the other a pipe")

    def test_two_fluids(self, mixed) -> None:
        block, ports = mixed
        connection = {"from": point(-10, 0, items=["not-the-same-fluid"]),
                      "to": {"block": 0, "port": self.index(ports, "in", "pipe")}}
        self.refused(block, [connection], "connection 0 .*would join")

    def test_the_items_guard(self, mixed) -> None:
        block, ports = mixed
        connection = {"from": point(-10, 0),
                      "to": {"block": 0, "port": self.index(ports, "in", "pipe"), "items": ["something-else"]}}
        self.refused(block, [connection], "connection 0 .*ports renumber")

    def test_one_port_twice(self, mixed) -> None:
        block, ports = mixed
        target = {"block": 0, "port": self.index(ports, "in", "pipe")}
        connections = [{"from": point(-10, 0), "to": target}, {"from": point(-10, 5), "to": target}]
        self.refused(block, connections, "connection 1 .*already used by connection 0")

    def test_an_unknown_field(self, mixed) -> None:
        block, ports = mixed
        self.refused(block, [{"from": point(0, 0), "to": point(5, 0), "kind": "belt", "colour": "red"}],
                     "connection 0 .*unknown field")

    def test_a_point_without_a_kind(self, mixed) -> None:
        block, _ = mixed
        self.refused(block, [{"from": point(0, 0), "to": point(5, 0)}], "connection 0 .*'kind'")

    def test_a_bad_direction(self, mixed) -> None:
        block, _ = mixed
        self.refused(block, [{"kind": "belt", "from": point(0, 0, direction=2), "to": point(5, 0)}],
                     "connection 0 .*direction")

    def test_unknown_routing_options(self, mixed) -> None:
        block, _ = mixed
        with pytest.raises(plan.PlanError, match="routing: unknown"):
            plan.build({"blocks": [block], "routing": {"speed": 3}})
