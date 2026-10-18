"""Power between blocks: the report lists each pole network, and `power.link` joins them.

Pole reach comes from the active data, so every distance below is read from the
prototype rather than written down.
"""

from __future__ import annotations

import math

import pytest
from draftsman.data import entities

import prototypes
from factorio_forge import inspection, plan, power
from test_route import block_spec, chained_recipes


def pole_reach(name: str) -> float:
    return float(entities.raw[name]["maximum_wire_distance"])


def two_blocks(distance: int = 60, **extra) -> dict:
    """Two blocks side by side with nothing between them but empty ground."""
    recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
    spec = block_spec(recipe, machine, [2])
    width = plan.build({"blocks": [spec]}).blocks[0].width
    return {"label": "power test", "blocks": [spec, {**spec, "at": [width + distance, 0]}], **extra}


def added_tiles(result) -> set[tuple[int, int]]:
    return {(math.floor(p["x"]), math.floor(p["y"])) for p in result.power["added"]}


def split(result) -> list:
    return [f for f in result.findings if f.code == "power-split"]


class TestReport:
    def test_each_network_lists_its_poles_and_what_it_powers(self) -> None:
        result = plan.build(two_blocks())
        report = result.power
        assert len(report["networks"]) == result.power_networks == 2
        for index, net in enumerate(report["networks"]):
            assert net["index"] == index
            assert net["poles"] and all({"name", "x", "y", "block", "added"} <= set(p) for p in net["poles"])
            assert len(net["powers"]["blocks"]) == 1
            owner = net["powers"]["blocks"][0]["block"]
            assert all(p["block"] == owner and not p["added"] for p in net["poles"])
        assert {n["powers"]["blocks"][0]["block"] for n in report["networks"]} == {0, 1}
        assert sum(len(n["poles"]) for n in report["networks"]) == sum(
            1 for e in result.blueprint.entities if e.type == "electric-pole")

    def test_separate_networks_give_the_closest_poles_and_the_reach(self) -> None:
        result = plan.build(two_blocks())
        (gap,) = result.power["gaps"]
        assert gap["networks"] == [0, 1]
        nets = result.power["networks"]
        best = min(math.dist((a["x"], a["y"]), (b["x"], b["y"])) for a in nets[0]["poles"] for b in nets[1]["poles"])
        assert gap["distance"] == pytest.approx(best, abs=0.01)
        poles = [p for n in nets for p in n["poles"]]
        assert gap["reach"] == min(pole_reach(p["name"]) for p in poles)
        assert gap["short_by"] == pytest.approx(gap["distance"] - gap["reach"], abs=0.01)
        (finding,) = split(result)
        assert f"{gap['distance']:g} tiles apart" in finding.detail and "link" in finding.detail

    def test_one_network_has_no_gaps(self) -> None:
        result = plan.build({"blocks": two_blocks()["blocks"][:1]})
        assert result.power["gaps"] == [] and len(result.power["networks"]) == 1

    def test_the_report_is_in_the_json(self) -> None:
        report = plan.build(two_blocks()).to_dict()
        assert report["power_networks"] == 2 and len(report["power"]["networks"]) == 2


class TestLink:
    def test_two_blocks_far_apart_are_joined(self) -> None:
        pole = prototypes.small_pole()
        result = plan.build(two_blocks(power={"link": True, "pole": pole}))
        assert result.power_networks == 1 and not split(result)
        added = result.power["added"]
        assert added and all(p["name"] == pole for p in added)
        # Fewest poles: a straight chain across the gap, one pole per reach.
        before = plan.build(two_blocks()).power["gaps"][0]
        assert len(added) <= math.ceil(before["distance"] / pole_reach(pole))
        assert len(added) >= math.ceil(before["distance"] / pole_reach(pole)) - 1
        net = result.power["networks"][0]
        assert sum(1 for p in net["poles"] if p["added"]) == len(added)
        assert {b["block"] for b in net["powers"]["blocks"]} == {0, 1}
        assert result.power["link"]["failures"] == []

    def test_the_pole_defaults_to_the_one_the_blocks_use(self) -> None:
        result = plan.build(two_blocks(power={"link": True}))
        assert result.power_networks == 1
        assert {p["name"] for p in result.power["added"]} == {prototypes.small_pole()}

    def test_nothing_is_added_when_the_poles_are_one_network(self) -> None:
        result = plan.build({**two_blocks(distance=1), "power": {"link": True}})
        assert result.power["added"] == [] and result.power_networks == 1

    def test_a_route_on_the_straight_chain_is_gone_round(self) -> None:
        first, second = chained_recipes()
        maker = block_spec(*first, [2])
        taker = block_spec(*second, [2])
        alone = plan.build({"blocks": [maker]}).blocks[0]
        out = next(p for p in alone.ports if p["io"] == "out")
        at = [alone.width + 30, 0]
        into = next(p for p in plan.build({"blocks": [{**taker, "at": at}]}).blocks[0].ports
                    if p["io"] == "in" and p["kind"] == "belt")
        blocks = [maker, {**taker, "at": at}]
        link = {"link": True, "pole": prototypes.small_pole()}
        # Without the route, the chain's middle pole takes some tile on the line between the blocks.
        free = plan.build({"blocks": blocks, "power": link})
        straight = sorted(added_tiles(free))
        assert len(straight) >= 2
        middle = straight[len(straight) // 2]
        conn = {"id": "r", "from": {"block": 0, "port": out["index"]}, "to": {"block": 1, "port": into["index"]},
                "via": [list(middle)]}
        result = plan.build({"blocks": blocks, "power": link, "connections": [conn]})
        (route_result,) = result.routes
        assert route_result.ok, route_result.reason
        occupied = {t for e in result.blueprint.entities if e.type != "electric-pole"
                    for t in inspection.Layout.tiles_of(e)}
        assert middle in occupied  # the route really sits where the straight chain would put a pole
        assert result.power_networks == 1
        assert result.power["added"] and not added_tiles(result) & occupied
        assert [f.code for f in result.findings if f.code in ("power-split", "pole-isolated")] == []

    def test_a_wall_across_the_gap_is_gone_round(self) -> None:
        base = two_blocks(distance=60)
        first = plan.build({"blocks": base["blocks"][:1]}).blocks[0]
        # A reserved band thicker than any wire reach, open only below the blocks.
        x0 = first.width + 8
        bottom = first.height
        wall = [x0, -200, x0 + 30, bottom]
        result = plan.build({**base, "routing": {"reserve": [wall]}, "power": {"link": True}})
        assert result.power_networks == 1 and not split(result)
        for x, y in added_tiles(result):
            assert not (wall[0] <= x <= wall[2] and wall[1] <= y <= wall[3])
        assert any(p["y"] > bottom for p in result.power["added"])

    def test_poles_keep_off_the_line_of_an_outside_point(self, monkeypatch) -> None:
        seen = {}
        original = plan.route.grid_of

        def spy(entities, area, keep):
            seen["keep"] = set(keep)
            return original(entities, area, keep)

        monkeypatch.setattr(plan.route, "grid_of", spy)
        spec = two_blocks(power={"link": True})
        built = plan.build(spec).blocks
        port = next(p for p in built[0].ports if p["io"] == "out")
        outside = {"at": [200, 200], "direction": 4}
        connection = {"id": "c", "from": {"block": 0, "port": port["index"]}, "to": outside}
        try:
            plan.build({**spec, "connections": [connection]})
        except plan.PlanError:
            pass  # the route itself may be refused; what the pole search was told to keep is what matters
        assert {(200, 200), (201, 200), (199, 200)} <= seen["keep"]

    def test_poles_keep_off_the_tiles_in_front_of_ports(self) -> None:
        result = plan.build(two_blocks(power={"link": True}))
        fronts = set()
        for block in result.blocks:
            for port in block.ports:
                dx, dy = inspection.STEP[port["direction"]]
                sign = 1 if port["io"] == "out" else -1
                fronts.add((port["x"] + sign * dx, port["y"] + sign * dy))
        assert not added_tiles(result) & fronts


class TestImpossible:
    def walled(self) -> dict:
        base = two_blocks(distance=60)
        first = plan.build({"blocks": base["blocks"][:1]}).blocks[0]
        return {**base, "routing": {"reserve": [[first.width + 5, -200, first.width + 50, 200]]},
                "power": {"link": True}}

    def test_a_gap_no_chain_can_cross_is_reported_not_hidden(self) -> None:
        result = plan.build(self.walled())
        assert result.power_networks == 2
        (finding,) = split(result)
        assert finding.severity is inspection.Severity.PROBLEM
        assert result.power["added"] == []
        (reason,) = result.power["link"]["failures"]
        assert "stays apart" in reason and "free tiles" in reason
        assert reason in finding.detail
        assert result.power["gaps"][0]["short_by"] > 0

    def test_the_json_names_the_networks_left_apart(self) -> None:
        report = plan.build(self.walled()).to_dict()
        assert [n["index"] for n in report["power"]["networks"]] == [0, 1]
        assert report["power"]["link"]["failures"]


class TestOptions:
    @pytest.mark.parametrize("value", [True, "yes", ["link"], {"links": True}, {"link": "yes"}, {"pole": 3, "link": True}])
    def test_malformed_power_options_are_refused(self, value) -> None:
        with pytest.raises(plan.PlanError, match="power"):
            plan.build({**two_blocks(), "power": value})

    def test_a_pole_that_is_not_a_pole_is_refused(self) -> None:
        _, machine = prototypes.crafting_setup(1, 0, 1, 0)
        with pytest.raises(plan.PlanError, match="not an electric pole"):
            plan.build({**two_blocks(), "power": {"link": True, "pole": machine}})

    def test_linking_with_no_pole_to_add_is_refused(self) -> None:
        with pytest.raises(plan.PlanError, match="needs a pole"):
            plan.build({"blocks": [], "power": {"link": True}})

    def test_without_link_nothing_is_added(self) -> None:
        result = plan.build({**two_blocks(), "power": {"pole": prototypes.small_pole()}})
        assert result.power_networks == 2 and "added" not in result.power


class TestWiringLeftApart:
    def test_poles_in_reach_but_unwired_are_reported(self) -> None:
        link = power.Link("small-electric-pole")
        plan._link_failures(link, {"networks": [{}, {}]})
        (reason,) = link.failures
        assert "within wire reach" in reason
        quiet = power.Link("small-electric-pole")
        plan._link_failures(quiet, {"networks": [{}]})
        assert quiet.failures == []
