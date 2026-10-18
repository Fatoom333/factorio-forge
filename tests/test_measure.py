"""Tests for the measuring request and for reading its answer back.

Nothing here needs the game: the answer is a hand-written ``measure.json`` of
the shape the companion mod writes, and the request is built from stand-ins
for a built plan. Only the one test that writes a spec from a real plan file
asks the active data for the prototypes the plan names.
"""

from __future__ import annotations

import base64
import json
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from draftsman.data import entities, recipes

from factorio_forge import cli, lanes, measure

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------
# stand-ins for a built plan
# --------------------------------------------------------------------------


def out_port(index, x, y, items, direction=4, kind="belt", io="out") -> dict:
    return {"index": index, "kind": kind, "io": io, "x": x, "y": y, "direction": direction, "items": list(items)}


def block(index, ports) -> SimpleNamespace:
    return SimpleNamespace(index=index, ports=ports)


def route(rid, start_port=None, goal_port=None, goal=(0, 0), direction=4, ok=True, kind="belt",
          delivered=None, lanes_=None, start=(0, 0), items=(), rate=None, sideload=False,
          junction=None, pieces=0, surface="transport-belt") -> SimpleNamespace:
    begin = SimpleNamespace(port=start_port, x=start[0], y=start[1], direction=direction, items=tuple(items),
                            rate=rate)
    end = SimpleNamespace(port=goal_port, x=goal[0], y=goal[1], direction=direction, sideload=sideload)
    return SimpleNamespace(id=rid, ok=ok, kind=kind, spec=SimpleNamespace(start=begin, goal=end, surface=surface),
                           delivered=delivered, lanes=lanes_, junction=junction, pieces=[None] * pieces)


# --------------------------------------------------------------------------
# the request
# --------------------------------------------------------------------------


class TestSources:
    def test_a_source_becomes_a_tile_with_its_lanes_and_rate(self) -> None:
        notes: list[str] = []
        feed = lanes.Feed((3, -2), ("iron-plate", "copper-cable"), 2.5)
        (entry,) = measure.sources_from([("source 'a'", feed, 4)], notes)
        assert entry == {"id": "source 'a'", "at": [3, -2], "direction": 4,
                         "items": ["iron-plate", "copper-cable"], "rate": 2.5}
        assert notes == []

    def test_one_item_on_both_lanes_is_written_once(self) -> None:
        (entry,) = measure.sources_from([("s", lanes.Feed((0, 0), ("iron-plate", "iron-plate"), 1.0), 4)], [])
        assert entry["items"] == ["iron-plate"]

    def test_no_rate_is_left_out_and_said(self) -> None:
        notes: list[str] = []
        (entry,) = measure.sources_from([("s", lanes.Feed((0, 0), ("iron-plate", "iron-plate")), 4)], notes)
        assert "rate" not in entry
        assert any("no rate" in n for n in notes)

    def test_a_source_with_no_items_feeds_nothing(self) -> None:
        notes: list[str] = []
        assert measure.sources_from([("s", lanes.Feed((0, 0), None, 1.0), 4)], notes) == []
        assert any("nothing is fed" in n for n in notes)


class TestOutputs:
    def test_an_out_port_nobody_routes_from_is_an_output_end(self) -> None:
        blocks = [block(0, [out_port(0, 5, 6, ["circuit"]), out_port(1, 0, 0, ["x"], io="in")])]
        assert measure.outputs_from(blocks, []) == [
            {"id": "block 0 port 0", "at": [5, 6], "direction": 4, "item": "circuit"}]

    def test_a_routed_out_port_is_not(self) -> None:
        blocks = [block(0, [out_port(0, 5, 6, ["circuit"])])]
        routes = [route("a", start_port=(0, 0), goal_port=(1, 0))]
        assert measure.outputs_from(blocks, routes) == []

    def test_a_route_ending_at_a_point_is_one_entry_per_item(self) -> None:
        blocks = [block(0, [out_port(0, 5, 6, ["circuit"])])]
        routes = [route("out", start_port=(0, 0), goal=(40, 3), direction=8, delivered=("circuit", "plate"))]
        assert measure.outputs_from(blocks, routes) == [
            {"id": "route out", "at": [40, 3], "direction": 8, "item": "circuit"},
            {"id": "route out", "at": [40, 3], "direction": 8, "item": "plate"}]

    def test_a_route_merging_onto_a_belt_is_not_an_end(self) -> None:
        routes = [route("m", goal=(28, -12), direction=4, sideload=True, delivered=("iron-plate", "-"))]
        assert measure.outputs_from([], routes) == []

    def test_a_lane_carrying_nothing_is_never_an_item(self) -> None:
        routes = [route("out", goal=(40, 3), delivered=("-", "circuit"))]
        assert measure.outputs_from([], routes) == [{"id": "route out", "at": [40, 3], "direction": 4, "item": "circuit"}]

    def test_a_failed_route_and_a_pipe_are_not_ends(self) -> None:
        routes = [route("a", goal=(1, 1), ok=False), route("b", goal=(2, 2), kind="pipe")]
        assert measure.outputs_from([], routes) == []

    def test_a_fluid_port_is_not_measured(self) -> None:
        blocks = [block(0, [out_port(0, 5, 6, ["water"], kind="pipe")])]
        assert measure.outputs_from(blocks, []) == []

    def test_the_same_item_at_the_same_tile_is_listed_once(self) -> None:
        blocks = [block(0, [out_port(0, 5, 6, ["a", "a"])])]
        assert len(measure.outputs_from(blocks, [])) == 1


class TestRouteSources:
    def test_a_connection_starting_at_a_point_is_a_source(self) -> None:
        notes: list[str] = []
        r = route("iron", start=(24, -24), direction=8, items=("iron-plate", "iron-plate"), rate=2, sideload=True)
        assert measure.route_sources_from([r], notes) == [
            {"id": "connection iron start", "at": [24, -24], "direction": 8, "items": ["iron-plate"], "rate": 2.0}]
        assert notes == []

    def test_two_different_lanes_are_kept_and_a_missing_rate_is_said(self) -> None:
        notes: list[str] = []
        r = route("mix", start=(1, 2), items=("a", "b"))
        (entry,) = measure.route_sources_from([r], notes)
        assert entry["items"] == ["a", "b"] and "rate" not in entry
        assert any("no rate" in n for n in notes)

    def test_a_route_from_a_port_a_splitter_branch_a_pipe_and_a_failure_are_not(self) -> None:
        branch = route("b", start=(3, 3), items=("a", "a"), rate=1, junction=SimpleNamespace(kind="splitter"))
        routes = [route("p", start_port=(0, 0), items=("a", "a")), branch,
                  route("w", start=(1, 1), items=("water",), kind="pipe"),
                  route("x", start=(2, 2), items=("a", "a"), ok=False)]
        assert measure.route_sources_from(routes, []) == []


    def test_a_lane_that_is_empty_nothing_or_two_items_is_not_a_source(self) -> None:
        for items in (("iron-plate", ""), ("-", "iron-plate"), ("a+b", "a+b")):
            notes: list[str] = []
            assert measure.route_sources_from([route("x", start=(1, 1), items=items, rate=1)], notes) == []
            assert notes and "not one item each" in notes[0]


class TestBeltTravel:
    def test_the_longest_chain_of_routes_counts_at_belt_speed(self) -> None:
        from draftsman.data import entities as entity_data
        speed = entity_data.raw["transport-belt"]["speed"] * 60  # tiles per second
        routes = [route("a", start_port=(0, 0), goal_port=(1, 0), pieces=30),
                  route("b", start_port=(1, 1), goal_port=(2, 0), pieces=20),
                  route("c", start_port=(0, 1), goal_port=(2, 1), pieces=10)]
        assert measure.belt_travel_s(routes) == pytest.approx(50 / speed)

    def test_a_route_from_a_point_starts_a_chain_and_a_route_to_a_free_end_ends_one(self) -> None:
        from draftsman.data import entities as entity_data
        speed = entity_data.raw["transport-belt"]["speed"] * 60
        routes = [route("in", start=(9, 9), goal_port=(0, 0), pieces=40),
                  route("a", start_port=(0, 1), goal_port=(1, 0), pieces=30),
                  route("out", start_port=(1, 1), goal=(5, 5), pieces=20)]
        assert measure.belt_travel_s(routes) == pytest.approx(90 / speed)

    def test_no_routes_is_zero(self) -> None:
        assert measure.belt_travel_s([]) == 0.0


class TestTargetsAndSpec:
    def test_targets_per_minute_become_per_second(self) -> None:
        assert measure.targets_from([{"item": "x", "per_second": 2}, {"item": "y", "per_minute": 30}]) == [
            {"item": "x", "rate": 2.0}, {"item": "y", "rate": 0.5}]

    @pytest.mark.parametrize("bad", [[{"item": "x"}], [{"per_second": 1}], [{"item": "x", "per_second": -1}],
                                     [{"item": "x", "per_second": True}], ["x"]])
    def test_a_malformed_target_is_refused(self, bad) -> None:
        with pytest.raises(measure.MeasureError):
            measure.targets_from(bad)

    def test_the_spec_has_the_agreed_shape(self) -> None:
        spec = measure.build_spec(
            "0eNq", sources=[{"at": [0, 0], "direction": 4, "items": ["a"], "rate": 1.0}],
            outputs=[{"at": [4, 6], "item": "b"}], targets=[{"item": "b", "rate": 0.5}],
            surface="vulcanus", warmup_s=60, measure_s=120, speed=None)
        assert spec == {"blueprint": "0eNq", "surface": "vulcanus",
                        "sources": [{"at": [0, 0], "direction": 4, "items": ["a"], "rate": 1.0}],
                        "outputs": [{"at": [4, 6], "item": "b"}], "targets": [{"item": "b", "rate": 0.5}],
                        "warmup_s": 60, "measure_s": 120}
        json.dumps(spec)

    def test_a_spec_without_a_blueprint_or_anything_to_measure_is_refused(self) -> None:
        with pytest.raises(measure.MeasureError):
            measure.build_spec("", sources=[], outputs=[{"at": [0, 0]}], targets=[])
        with pytest.raises(measure.MeasureError, match="nothing to measure"):
            measure.build_spec("0eNq", sources=[], outputs=[], targets=[])
        with pytest.raises(measure.MeasureError):
            measure.build_spec("0eNq", sources=[], outputs=[{"at": [0, 0]}], targets=[], measure_s=0)

    def test_command_line_pieces(self) -> None:
        assert measure.parse_fluid("1,2,water,50") == {"at": [1, 2], "fluid": "water", "rate": 50.0}
        assert measure.parse_fluid("1,2,water") == {"at": [1, 2], "fluid": "water"}
        assert measure.parse_output("8,0") == {"at": [8, 0]}
        assert measure.parse_output("8,0,gear,4") == {"at": [8, 0], "item": "gear", "direction": 4}
        assert measure.parse_target("gear=0.5") == {"item": "gear", "rate": 0.5}
        for bad, parse in (("1,2", measure.parse_fluid), ("x,0", measure.parse_output),
                           ("gear", measure.parse_target), ("gear=fast", measure.parse_target)):
            with pytest.raises(measure.MeasureError):
                parse(bad)


# --------------------------------------------------------------------------
# the answer
# --------------------------------------------------------------------------


def machine(name="assembling-machine-2", recipe="gear", working=0.0, waiting=0.0, full=0.0, power=0.0,
            missing=None, other=None) -> dict:
    return {"name": name, "recipe": recipe, "position": {"x": 1.5, "y": 2.5}, "samples": 100,
            "shares": {"working": working, "waiting_input": waiting, "output_full": full, "no_power": power,
                       "no_fuel": 0, "other": 0},
            "missing": missing if missing is not None else {}, "other": other}


def answer(**changes) -> dict:
    base = {
        "ok": True, "placed": 14, "expected": 14, "refused": [],
        "window": {"warmup_s": 60, "measure_s": 120, "speed": 10},
        "surface": {"name": "vulcanus", "properties": {"pressure": 4000}},
        "sources": [{"at": [0, 0], "kind": "belt", "declared_rate": 2.0, "lanes": [
            {"lane": "left", "item": "iron-plate", "declared_rate": 1.0, "fed_rate": 1.0},
            {"lane": "right", "item": "iron-plate", "declared_rate": 1.0, "fed_rate": 1.0}]}],
        "outputs": [{"at": [4, 6], "declared_items": ["gear"], "delivered": {"gear": 0.49}}],
        "produced": {"gear": 0.5}, "consumed": {"iron-plate": 1.0},
        "machines": [machine(working=0.5, waiting=0.5, missing={"iron-plate": 0.5})],
        "problems": [],
    }
    base.update(changes)
    return base


def write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "measure.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestCompare:
    TARGET = [{"item": "gear", "rate": 0.5}]

    def test_ordered_against_delivered(self) -> None:
        c = measure.compare(answer(), self.TARGET)
        (line,) = c.targets
        assert (line.item, line.ordered, line.delivered, line.produced) == ("gear", 0.5, 0.49, 0.5)
        assert line.met and c.all_met
        assert line.share == pytest.approx(0.98)

    def test_a_shortfall_is_short(self) -> None:
        c = measure.compare(answer(outputs=[{"at": [4, 6], "delivered": {"gear": 0.3}}]), self.TARGET)
        assert not c.targets[0].met and not c.all_met

    def test_without_an_output_end_what_the_machines_made_is_measured(self) -> None:
        c = measure.compare(answer(outputs=[]), self.TARGET)
        assert c.targets[0].delivered is None and c.targets[0].measured == 0.5

    def test_an_output_that_got_nothing_is_zero_not_missing(self) -> None:
        c = measure.compare(answer(outputs=[{"at": [4, 6], "delivered": []}]), self.TARGET)
        assert c.targets[0].delivered == 0.0 and not c.targets[0].met

    def test_two_output_ends_add_up(self) -> None:
        outputs = [{"at": [1, 1], "delivered": {"gear": 0.2}}, {"at": [2, 2], "delivered": {"gear": 0.3}}]
        assert measure.compare(answer(outputs=outputs), self.TARGET).targets[0].delivered == pytest.approx(0.5)

    def test_machines_of_a_kind_are_averaged(self) -> None:
        machines = [machine(working=1.0), machine(working=0.5, waiting=0.5, missing={"a": 0.5}),
                    machine(recipe="cable", waiting=1.0, missing={"copper-plate": 1.0})]
        c = measure.compare(answer(machines=machines), self.TARGET)
        cable, gear = c.groups
        assert (gear.recipe, gear.count) == ("gear", 2)
        assert gear.shares["working"] == pytest.approx(0.75) and gear.shares["waiting_input"] == pytest.approx(0.25)
        assert gear.starved and not cable.blocked and cable.starved
        assert cable.missing == {"copper-plate": 1.0}

    def test_lua_writes_empty_tables_as_lists(self) -> None:
        c = measure.compare(answer(produced=[], consumed=[], refused=[], machines=[], problems=[],
                                   outputs=[{"at": [1, 1], "delivered": []}]), self.TARGET)
        assert c.produced == {} and c.groups == [] and c.delivered == {}


class TestFormat:
    TARGET = [{"item": "gear", "rate": 0.5}]

    def text(self, **changes) -> str:
        return measure.format_comparison(measure.compare(answer(**changes), self.TARGET))

    def test_a_good_run_says_every_target_met(self) -> None:
        text = self.text()
        assert "gear: ordered 0.5/s, measured 0.49/s at the output belts (98%)" in text
        assert "14 of 14 entities stood" in text
        assert text.endswith("every target met")

    def test_a_starved_machine_names_what_it_waited_for(self) -> None:
        text = self.text(machines=[machine(waiting=0.8, working=0.2, missing={"iron-plate": 0.8, "gear": 0.1})])
        assert "starved: 1 x assembling-machine-2 (gear) waited for input 80%" in text
        assert "iron-plate missing 80% of the time" in text

    def test_a_full_output_and_a_missing_power_are_said(self) -> None:
        text = self.text(machines=[machine(full=0.6, working=0.4), machine(name="inserter-x", recipe=None, power=0.5)])
        assert "blocked: 1 x assembling-machine-2" in text
        assert "no power: 1 x inserter-x" in text

    def test_a_supply_the_belt_could_not_take_is_said_once_per_item(self) -> None:
        lanes_ = [{"lane": "left", "item": "iron-plate", "declared_rate": 1.0, "fed_rate": 0.2},
                  {"lane": "right", "item": "iron-plate", "declared_rate": 1.0, "fed_rate": 0.2}]
        text = self.text(sources=[{"at": [0, 0], "kind": "belt", "lanes": lanes_}])
        assert text.count("supply held back: iron-plate") == 1
        assert "fed 0.4/s of 2/s declared" in text

    def test_a_machine_with_no_recipe_is_said(self) -> None:
        text = self.text(machines=[machine(recipe=None, other={"no_recipe": 100})])
        assert "no recipe: 1 x assembling-machine-2 stood without one" in text

    def test_refused_prototypes_are_listed(self) -> None:
        assert "refused: foo x2" in self.text(refused={"foo": 2}, placed=12)

    def test_a_failed_run_says_why(self) -> None:
        c = measure.compare({"ok": False, "error": "nothing from the blueprint stands"}, self.TARGET)
        assert measure.format_comparison(c) == "measuring failed: nothing from the blueprint stands"
        assert not c.all_met

    def test_a_short_target_is_counted(self) -> None:
        text = self.text(outputs=[{"at": [4, 6], "delivered": {"gear": 0.1}}])
        assert "[SHORT]" in text and text.endswith("1 of 1 target(s) short")


class TestWarmupHint:
    TARGET = [{"item": "iron-gear-wheel", "rate": 0.5}]

    def short(self, window, groups, belt_travel_s=0.0, produced=None, sources=None, target=None,
              delivered=0.0) -> measure.Comparison:
        target = target or self.TARGET
        item = target[0]["item"]
        changes = {"window": window, "machines": groups, "produced": produced if produced is not None else {},
                   "outputs": [{"at": [4, 6], "delivered": {item: delivered}}]}
        if sources is not None:
            changes["sources"] = sources
        return measure.compare(answer(**changes), target, belt_travel_s)

    def test_a_target_that_is_short_while_supplied_gets_the_rerun_advice(self) -> None:
        c = self.short({"warmup_s": 600, "measure_s": 60},
                       [machine(recipe="iron-gear-wheel", working=0.9, waiting=0.1)], belt_travel_s=100,
                       produced={"iron-gear-wheel": 0.4}, delivered=0.3)
        text = measure.format_comparison(c)
        assert "may have been too short" in text and "at least ~" in text
        assert "belt travel 100 s" in text and "at least twice as long (it was 600 s)" in text
        assert "not a fault of the plan" in text and "two windows agree" in text
        assert text.index("may have been too short") < text.index("result:")

    def test_the_hint_does_not_wait_for_the_estimate_to_exceed_the_warmup(self) -> None:
        # A real case: 240 s of warm-up, 87% of the target, the last stage waited 9%.
        if not {"chemical-science-pack", "engine-unit", "advanced-circuit"} <= set(recipes.raw):
            pytest.skip("the active data lacks the blue science recipes")
        groups = [machine(recipe="chemical-science-pack", working=0.91, waiting=0.09,
                          missing={"engine-unit": 0.09, "advanced-circuit": 0.02}),
                  machine(recipe="engine-unit", working=0.95, waiting=0.05),
                  machine(recipe="advanced-circuit", working=1.0)]
        target = [{"item": "chemical-science-pack", "rate": 1.0}]
        c = self.short({"warmup_s": 240, "measure_s": 180}, groups, target=target, delivered=0.867,
                       produced={"chemical-science-pack": 0.878, "engine-unit": 0.99, "advanced-circuit": 1.5})
        hint = measure.warmup_hint(c)
        assert hint and "may have been too short" in hint
        assert "0.867/s of 1/s ordered" in hint

    def test_an_ingredient_nothing_makes_or_feeds_is_a_missing_source_not_a_short_warmup(self) -> None:
        # red/run1: 0/s, plastic-bar nowhere, electronic-circuit never made.
        if not {"advanced-circuit", "electronic-circuit"} <= set(recipes.raw):
            pytest.skip("the active data lacks the advanced circuit recipes")
        groups = [machine(recipe="advanced-circuit", waiting=1.0, missing={"plastic-bar": 1.0, "electronic-circuit": 1.0}),
                  machine(recipe="electronic-circuit", waiting=1.0, missing={"iron-plate": 1.0})]
        copper = [{"at": [0, 0], "kind": "belt", "declared_rate": 5, "lanes": [
            {"lane": "left", "item": "copper-plate", "declared_rate": 2.5, "fed_rate": 0}]}]
        c = self.short({"warmup_s": 120, "measure_s": 180}, groups, sources=copper,
                       target=[{"item": "advanced-circuit", "rate": 1.0}])
        text = measure.format_comparison(c)
        assert "too short" not in text
        assert "plastic-bar is never supplied" in text and "a missing source" in text
        assert "electronic-circuit was not made in the window" in text
        assert "A longer warm-up will not help" in text

    def test_an_ingredient_a_source_feeds_counts_as_arriving(self) -> None:
        groups = [machine(recipe="iron-gear-wheel", waiting=0.8, missing={"iron-plate": 0.8})]
        c = self.short({"warmup_s": 600, "measure_s": 60}, groups)  # the default answer feeds iron-plate
        assert measure.unsupplied(c, "iron-gear-wheel") == []
        assert "waited for input 80%" in measure.warmup_hint(c)
        fed_nothing = [{"at": [0, 0], "lanes": [{"lane": "left", "item": "iron-plate", "fed_rate": 0}]}]
        c = self.short({"warmup_s": 600, "measure_s": 60}, groups, sources=fed_nothing)
        assert measure.unsupplied(c, "iron-gear-wheel") == [("iron-plate", False)]

    def test_nothing_made_and_a_working_last_stage_say_nothing(self) -> None:
        c = self.short({"warmup_s": 600, "measure_s": 60}, [machine(recipe="iron-gear-wheel", working=0.9, waiting=0.1)])
        assert measure.warmup_hint(c) is None

    def test_a_met_target_gets_no_hint(self) -> None:
        c = measure.compare(answer(window={"warmup_s": 1, "measure_s": 60}), self.TARGET, 500)
        assert measure.warmup_hint(c) is None

    def test_the_chain_adds_the_stages_craft_times(self) -> None:
        from draftsman.data import entities as entity_data
        from draftsman.data import recipes as recipe_data
        gear, speed = recipe_data.raw["iron-gear-wheel"], entity_data.raw["assembling-machine-2"]["crafting_speed"]
        groups = [measure.MachineGroup("assembling-machine-2", "iron-gear-wheel", 1, {}, {})]
        assert measure.craft_chain_s(groups, "iron-gear-wheel") == pytest.approx(gear.get("energy_required", 0.5) / speed)
        assert measure.craft_chain_s(groups, "nothing-makes-this") is None

    def test_the_command_reads_the_belt_travel_from_the_spec(self, tmp_path, capsys) -> None:
        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps({"targets": self.TARGET, "belt_travel_s": 100}), encoding="utf-8")
        data = answer(window={"warmup_s": 5, "measure_s": 60}, machines=[machine(recipe="iron-gear-wheel")],
                      outputs=[{"at": [4, 6], "delivered": {"iron-gear-wheel": 0.2}}])
        assert cli.main(["measure", "result", str(write(tmp_path, data)), "--spec", str(spec)]) == 1
        assert "may have been too short" in capsys.readouterr().out


class TestCommand:
    def test_result_prints_and_exits_by_the_verdict(self, tmp_path, capsys) -> None:
        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps({"targets": [{"item": "gear", "rate": 0.5}]}), encoding="utf-8")
        assert cli.main(["measure", "result", str(write(tmp_path, answer())), "--spec", str(spec)]) == 0
        assert "every target met" in capsys.readouterr().out
        short = write(tmp_path, answer(outputs=[{"at": [4, 6], "delivered": {"gear": 0.1}}]))
        assert cli.main(["measure", "result", str(short), "--spec", str(spec)]) == 1

    def test_result_takes_a_target_from_the_command_line_and_a_bom(self, tmp_path, capsys) -> None:
        path = tmp_path / "measure.json"
        path.write_text(json.dumps(answer()), encoding="utf-8-sig")
        assert cli.main(["measure", "result", str(path), "--target", "gear=0.5"]) == 0

    def test_a_missing_or_foreign_file_is_a_message_not_a_traceback(self, tmp_path, capsys) -> None:
        assert cli.main(["measure", "result", str(tmp_path / "nope.json")]) == 1
        other = tmp_path / "other.json"
        other.write_text("{}", encoding="utf-8")
        assert cli.main(["measure", "result", str(other)]) == 1
        assert "not a measure.json" in capsys.readouterr().err

    def test_spec_from_a_plan_file(self, tmp_path, capsys) -> None:
        the_plan = json.loads((FIXTURES / "gear-line.json").read_text(encoding="utf-8"))
        names = {e["name"] for e in the_plan["entities"]}
        if not names <= set(entities.raw) or "iron-gear-wheel" not in recipes.raw:
            pytest.skip("the active data lacks the prototypes of the fixture plan")
        out = tmp_path / "spec.json"
        code = cli.main(["measure", "spec", str(FIXTURES / "gear-line.json"), "-o", str(out),
                         "--output-end", "4,6,iron-gear-wheel,4", "--surface", "vulcanus", "--speed", "5"])
        assert code == 0, capsys.readouterr()
        spec = json.loads(out.read_text(encoding="utf-8"))
        assert spec["surface"] == "vulcanus" and spec["speed"] == 5
        assert spec["blueprint"].startswith("0")
        assert [s["at"] for s in spec["sources"]] == [[0, 0]]
        assert spec["sources"][0]["items"] == ["iron-plate"] and spec["sources"][0]["rate"] == 1.0
        assert spec["outputs"] == [{"at": [4, 6], "item": "iron-gear-wheel", "direction": 4}]
        assert spec["targets"] == [{"item": "iron-gear-wheel", "rate": 0.5}]
        assert (spec["warmup_s"], spec["measure_s"]) == (60, 120)

        # The coordinate contract: a spec tile is the blueprint tile, so a 1x1 belt
        # on tile (x, y) stands at (x + 0.5, y + 0.5) in the blueprint itself.
        document = json.loads(zlib.decompress(base64.b64decode(spec["blueprint"][1:])))
        belts = {(e["position"]["x"], e["position"]["y"]) for e in document["blueprint"]["entities"]
                 if e["name"].endswith("transport-belt")}
        for source in spec["sources"]:
            assert (source["at"][0] + 0.5, source["at"][1] + 0.5) in belts
        assert (4.5, 6.5) in belts

    def test_spec_from_a_plan_with_point_start_connections_and_merges(self, tmp_path, capsys) -> None:
        the_plan = json.loads((FIXTURES / "red-circuits-routed.json").read_text(encoding="utf-8"))
        names = {b["machine"] for b in the_plan["blocks"]} | {e["name"] for e in the_plan["entities"]}
        names |= {b[k] for b in the_plan["blocks"] for k in ("belt", "inserter", "long_inserter", "pole")}
        if not names <= set(entities.raw) or not {b["recipe"] for b in the_plan["blocks"]} <= set(recipes.raw):
            pytest.skip("the active data lacks the fixture's prototypes")
        for c in the_plan["connections"]:
            if "at" in c["from"]:
                c["from"]["rate"] = 2
        the_plan["targets"] = [{"item": "advanced-circuit", "per_second": 1}]
        path, out = tmp_path / "plan.json", tmp_path / "spec.json"
        path.write_text(json.dumps(the_plan), encoding="utf-8")
        code = cli.main(["measure", "spec", str(path), "-o", str(out)])
        assert code == 0, capsys.readouterr()
        spec = json.loads(out.read_text(encoding="utf-8"))
        starts = {s["id"]: s for s in spec["sources"]}
        assert {"connection iron start", "connection plastic start"} <= set(starts)
        iron = starts["connection iron start"]
        assert (iron["at"], iron["direction"], iron["items"], iron["rate"]) == ([19, -3], 8, ["iron-plate"], 2.0)
        assert starts["connection plastic start"]["items"] == ["plastic-bar"]
        # The merges end on a belt that carries on, so neither is an output end, and no output names "-".
        assert not {"route iron", "route plastic"} & {o["id"] for o in spec["outputs"]}
        assert all(o.get("item") != "-" for o in spec["outputs"]) and spec["outputs"]

    def test_spec_refuses_a_plan_with_nothing_to_measure(self, tmp_path, capsys) -> None:
        the_plan = json.loads((FIXTURES / "gear-line.json").read_text(encoding="utf-8"))
        names = {e["name"] for e in the_plan["entities"]}
        if not names <= set(entities.raw):
            pytest.skip("the active data lacks the prototypes of the fixture plan")
        the_plan.pop("targets")
        path = tmp_path / "plan.json"
        path.write_text(json.dumps(the_plan), encoding="utf-8")
        assert cli.main(["measure", "spec", str(path)]) == 1
        assert "nothing to measure" in capsys.readouterr().err
