"""Tests for the measuring request and for reading its answer back.

Nothing here needs the game: the answer is a hand-written ``measure.json`` of
the shape the companion mod writes, and the request is built from stand-ins
for a built plan. Only the one test that writes a spec from a real plan file
asks the active data for the prototypes the plan names.
"""

from __future__ import annotations

import json
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
          delivered=None, lanes_=None) -> SimpleNamespace:
    start = SimpleNamespace(port=start_port, x=0, y=0, direction=direction)
    end = SimpleNamespace(port=goal_port, x=goal[0], y=goal[1], direction=direction)
    return SimpleNamespace(id=rid, ok=ok, kind=kind, spec=SimpleNamespace(start=start, goal=end),
                           delivered=delivered, lanes=lanes_)


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

    def test_a_failed_route_and_a_pipe_are_not_ends(self) -> None:
        routes = [route("a", goal=(1, 1), ok=False), route("b", goal=(2, 2), kind="pipe")]
        assert measure.outputs_from([], routes) == []

    def test_a_fluid_port_is_not_measured(self) -> None:
        blocks = [block(0, [out_port(0, 5, 6, ["water"], kind="pipe")])]
        assert measure.outputs_from(blocks, []) == []

    def test_the_same_item_at_the_same_tile_is_listed_once(self) -> None:
        blocks = [block(0, [out_port(0, 5, 6, ["a", "a"])])]
        assert len(measure.outputs_from(blocks, [])) == 1


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
