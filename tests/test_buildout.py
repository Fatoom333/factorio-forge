"""From a diagnosis to orders and mends: the request each shortfall becomes, the surplus that arrives, the fix list.

Pure-logic tests run on invented recipes through `RecipeBook`; the ones that compute a real bill and draft it
use the active data's own recipes and skip when they cannot.
"""

from __future__ import annotations

import json

import pytest

from factorio_forge import buildout, cli, request
from factorio_forge import survey as survey_module
from factorio_forge.diagnose import diagnose
from test_diagnose import BOOK, drill, field, machine, survey


def starved(items=None, **extra):
    gears = [machine(i, "gear", 0.4, {"plate": 0.6}) for i in range(1, 5)]
    smelters = [machine(100 + i, "smelt", 1.0) for i in range(10)]
    return survey(gears + smelters, items=items, **extra)


def anything(_name: str) -> bool:
    return True


class TestOrders:
    def test_a_shortfall_becomes_a_request_review_and_chain_take(self) -> None:
        snapshot = starved()
        found = diagnose(snapshot, book=BOOK)
        (item,) = [b for b, _ in buildout.build_orders(found, snapshot, bill=False, known=anything)]
        assert item.request["targets"] == [{"item": "plate", "per_second": pytest.approx(9.6)}]
        assert item.request["surface"] == "testland"
        assert item.more_machines == 31 and item.machine == "assembler"
        spec = request.parse(item.request)  # the file review and chain read
        assert spec.targets[0][0] == "plate" and spec.surface == "testland"

    def test_what_the_base_makes_beyond_its_use_becomes_the_boundary(self) -> None:
        items = {"copper-sheet": {"made": 5.0, "used": 3.0},      # surplus 2
                 "iron-tile": {"made": 4.0, "used": 4.0},          # balanced
                 "lead-bit": {"made": 1.0, "used": 2.0},           # short
                 "plate": {"made": 8.0, "used": 1.0}}              # the target itself: never its own boundary
        snapshot = starved(items=items)
        (item,) = [b for b, _ in buildout.build_orders(diagnose(snapshot, book=BOOK), snapshot, bill=False,
                                                       known=anything)]
        assert item.request["boundary"] == ["copper-sheet"]
        assert item.arrives == {"copper-sheet": pytest.approx(2.0)}

    def test_orders_that_build_each_others_goods_take_them_as_arriving(self) -> None:
        snapshot = starved()
        found = diagnose(snapshot, {"gear": 20.0}, book=BOOK)
        orders = {b.item: b for b, _ in buildout.build_orders(found, snapshot, bill=False, known=anything)}
        assert set(orders) == {"gear", "plate"}
        assert orders["gear"].request["boundary"] == ["plate"]
        assert orders["plate"].request["boundary"] == ["gear"]
        assert orders["plate"].alongside == ["gear"]

    def test_a_tiny_surplus_is_noise(self) -> None:
        snapshot = survey(items={"a": {"made": 100.0, "used": 99.9}, "b": {"made": 100.0, "used": 90.0}})
        assert list(buildout.surplus(snapshot)) == ["b"]

    def test_names_the_game_does_not_know_stay_out_of_the_boundary(self) -> None:
        snapshot = starved(items={"not-an-item-anywhere": {"made": 5.0, "used": 1.0}})
        (item,) = [b for b, _ in buildout.build_orders(diagnose(snapshot, book=BOOK), snapshot, bill=False,
                                                       known=lambda name: False)]
        assert item.request["boundary"] == []

    def test_the_bill_is_asked_for_each_order_and_a_thin_surplus_is_flagged(self) -> None:
        calls = []

        def fake(order):
            calls.append(order)
            return {"lines": [{"recipe": "smelt", "machine": "furnace", "machines": 31, "outputs": {}}],
                    "from_outside": {"ore": 9.6}, "power_w": 1e6}, object(), []

        snapshot = starved(items={"ore": {"made": 5.0, "used": 1.0}})
        (item,) = [b for b, _ in buildout.build_orders(diagnose(snapshot, book=BOOK), snapshot,
                                                       known=anything, compute=fake)]
        assert calls[0]["targets"][0]["item"] == "plate"
        assert "31 x furnace on smelt" in item.text
        assert any("surplus is 4" in p for p in item.problems)

    def test_ore_the_drills_cannot_supply_is_not_a_chain_order(self) -> None:
        smelters = [machine(100 + i, "smelt", 0.5, {"ore": 0.5}) for i in range(4)]
        snapshot = survey(smelters, miners=[drill(i) for i in range(1, 5)], fields=[field(amount=9e9)],
                          items={"ore": {"made": 2.0, "used": 2.0}})
        (item,) = [b for b, _ in buildout.build_orders(diagnose(snapshot, book=BOOK), snapshot, known=anything)]
        assert item.request is None and item.bill is None
        assert "more drill" in item.text


class TestFixes:
    def snapshot(self):
        machines = [machine(1, "gear", states={"working": 0.2, "no_fuel": 0.8}, at=[3.5, 4.5]),
                    machine(2, "panel", states={"working": 0.2, "output_full": 0.8}, at=[7.5, 1.5]),
                    machine(3, None, states={"no_recipe": 1.0}, at=[9.5, 9.5]),
                    machine(4, "widget", states={"working": 0.2, "low_power": 0.8})]
        power = [{"network": 1, "made_w": 30e6, "used_w": 40e6, "satisfaction": 0.75, "machines": 4}]
        return survey(machines, power=power)

    def test_what_needs_no_building_is_listed_with_units_and_positions(self) -> None:
        snapshot = self.snapshot()
        listed = buildout.fixes(diagnose(snapshot, book=BOOK), snapshot)
        by_kind = {f.kind: f for f in listed}
        assert {"no_fuel", "output_full_end", "no_recipe", "power"} <= set(by_kind)
        assert by_kind["no_fuel"].units == [1] and by_kind["no_fuel"].positions == [[3.5, 4.5]]
        assert by_kind["no_recipe"].units == [3] and by_kind["no_recipe"].positions == [[9.5, 9.5]]
        assert by_kind["power"].numbers["short_w"] == pytest.approx(10e6)
        assert len([f for f in listed if f.kind == "power"]) == 1

    def test_ore_blocked_at_the_drills_is_a_fix_not_a_build(self) -> None:
        smelters = [machine(100 + i, "smelt", 0.5, {"ore": 0.5}) for i in range(4)]
        drills = [drill(i, states={"working": 0.3, "output_full": 0.7}) for i in range(1, 3)]
        snapshot = survey(smelters, miners=drills, fields=[field()])
        found = diagnose(snapshot, book=BOOK)
        assert [f.kind for f in buildout.fixes(found, snapshot)] == ["drills_blocked"]
        assert found.shortfalls == []


class TestReport:
    def report(self, **kw):
        snapshot = starved(items={"iron-tile": {"made": 5.0, "used": 1.0}})
        found = diagnose(snapshot, {"gear": 20.0}, book=BOOK)
        return buildout.report(found, snapshot, bill=False, known=anything, **kw)

    def test_the_text_has_every_section_in_order(self) -> None:
        text = self.report().text()
        order = [text.index(s) for s in ("what holds the base back", "build:", "limits of this reading")]
        assert order == sorted(order)
        assert "-> " in text and "plate" in text

    def test_the_json_is_the_same_structure(self) -> None:
        data = json.loads(json.dumps(self.report().to_dict()))
        assert data["build"][0]["item"] == "plate" and "fix" in data and data["causes"][0]["kind"] == "target_gap"

    def test_good_groups_are_listed(self) -> None:
        snapshot = survey([machine(1, "gear", 0.97)])
        text = buildout.report(diagnose(snapshot, book=BOOK), snapshot, bill=False).text()
        assert "works well" in text and "gear" in text

    def test_drafts_are_only_written_for_orders_with_a_bill(self, tmp_path) -> None:
        wrote = []
        monkey = buildout.write_draft
        buildout.write_draft = lambda reviewed, folder: wrote.append(folder) or {"plan": str(folder / "plan.json")}
        try:
            snapshot = starved()
            found = diagnose(snapshot, book=BOOK)
            bill = ({"lines": [{"recipe": "smelt", "machine": "m", "machines": 2, "outputs": {}}],
                     "from_outside": {}, "power_w": 0}, object(), [])
            done = buildout.report(found, snapshot, out_dir=tmp_path, drafts=True, known=anything,
                                   compute=lambda order: bill)
            assert [p.name for p in wrote] == ["plate"]
            assert done.build[0].draft["plan"].endswith("plan.json")
            wrote.clear()
            buildout.report(found, snapshot, out_dir=tmp_path, drafts=False, known=anything,
                            compute=lambda order: bill)
            assert wrote == []
        finally:
            buildout.write_draft = monkey

    def test_a_folder_name_made_from_an_item_never_leaves_the_folder(self) -> None:
        taken: set[str] = set()
        assert buildout._folder_name("../../evil name", taken) == "evil-name"
        assert buildout._folder_name("evil/name", taken) == "evil-name-2"
        assert buildout._folder_name("..", taken) == "item"


class TestCommand:
    def write(self, tmp_path, snapshot_dict):
        path = tmp_path / "survey.json"
        path.write_text(json.dumps(snapshot_dict), encoding="utf-8")
        return path

    def dict_of(self):
        return {"kind": "survey", "surface": "testland", "tick": 1, "window_s": 60,
                "machines": [machine(1, "no-such-recipe-here", 0.1)], "flows": {"items": {}, "fluids": {}}}

    def test_drafts_without_a_folder_are_refused_in_one_line(self, tmp_path, capsys) -> None:
        path = self.write(tmp_path, self.dict_of())
        assert cli.main(["base", str(path), "--drafts"]) == 2
        assert capsys.readouterr().err.strip().count("\n") == 0

    def test_a_missing_survey_is_one_line(self, tmp_path, capsys) -> None:
        assert cli.main(["base", str(tmp_path / "nope.json")]) == 2
        assert "does not exist" in capsys.readouterr().err

    def test_a_bad_target_is_refused(self, tmp_path, capsys) -> None:
        path = self.write(tmp_path, self.dict_of())
        assert cli.main(["base", str(path), "--target", "iron-plate"]) == 2

    def test_the_report_and_the_json(self, tmp_path, capsys) -> None:
        path = self.write(tmp_path, self.dict_of())
        assert cli.main(["base", str(path), "-o", str(tmp_path / "out")]) == 0
        assert "limits of this reading" in capsys.readouterr().out
        assert (tmp_path / "out" / "base.json").is_file()
        assert cli.main(["base", str(path), "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["machines"] == 1


class TestActiveData:
    def surveyed(self):
        from draftsman.data import recipes

        from factorio_forge import surface
        from test_route import chained_recipes

        if not surface.names():
            pytest.skip("the active data has no surface")
        (first, first_machine), (second, second_machine) = chained_recipes()
        item = recipes.raw[second]["ingredients"][0]["name"]
        makers = [machine(100 + i, first, 1.0, name=first_machine) for i in range(2)]
        users = [machine(i, second, 0.2, {item: 0.8}, name=second_machine) for i in range(1, 4)]
        data = {"kind": "survey", "surface": surface.names()[0], "tick": 1, "window_s": 60,
                "machines": users + makers, "flows": {"items": {}, "fluids": {}}}
        return survey_module.from_dict(data), item

    def test_the_bill_for_the_missing_item(self) -> None:
        snapshot, item = self.surveyed()
        found = diagnose(snapshot)
        (order,) = [b for b, _ in buildout.build_orders(found, snapshot)]
        if order.bill is None:
            pytest.skip(f"the order is not ready on this data: {order.problems}")
        assert order.request["targets"][0]["item"] == item
        assert any(line["machines"] >= 1 for line in order.bill["lines"])

    def test_a_draft_is_written_into_a_folder_of_the_item(self, tmp_path) -> None:
        snapshot, item = self.surveyed()
        found = diagnose(snapshot)
        done = buildout.report(found, snapshot, out_dir=tmp_path, drafts=True)
        (order,) = done.build
        if order.bill is None:
            pytest.skip(f"the order is not ready on this data: {order.problems}")
        assert order.draft is not None
        if "error" in order.draft:
            pytest.skip(order.draft["error"])
        plan_file = tmp_path / buildout._folder_name(item, set()) / "plan.json"
        assert plan_file.is_file()
        assert json.loads(plan_file.read_text(encoding="utf-8"))["blocks"]


# --------------------------------------------------------------------------
# orders sized together, the base's own recipe and machine
# --------------------------------------------------------------------------


class TestSizedTogether:
    def draw(self, plate_for_gear: float):
        def compute(order):
            target = order["targets"][0]
            if target["item"] == "gear":
                outside = {"plate": plate_for_gear}
            else:
                outside = {}
            line = {"recipe": "r", "machine": "m", "machines": 1, "outputs": {}}
            return {"lines": [line], "from_outside": outside, "power_w": 0}, object(), []

        return compute

    def orders(self, compute):
        snapshot = starved()
        found = diagnose(snapshot, {"gear": 20.0}, book=BOOK)
        return {b.item: b for b, _ in buildout.build_orders(found, snapshot, known=anything, compute=compute)}, found

    def test_what_one_order_draws_from_another_is_added_to_the_other(self) -> None:
        orders, found = self.orders(self.draw(3.0))
        plate_short = next(s.short_per_s for s in found.shortfalls if s.item == "plate")
        assert orders["plate"].extra_per_s == pytest.approx(3.0)
        assert orders["plate"].request["targets"][0]["per_second"] == pytest.approx(plate_short + 3.0)
        assert orders["gear"].extra_per_s == 0
        assert "3/s more for the other orders" in orders["plate"].text
        assert "made by the other order" in orders["gear"].text

    def test_orders_that_draw_on_each_other_without_end_are_flagged(self) -> None:
        def compute(order):
            target = order["targets"][0]
            other = "plate" if target["item"] == "gear" else "gear"
            line = {"recipe": "r", "machine": "m", "machines": 1, "outputs": {}}
            return {"lines": [line], "from_outside": {other: 2.0 * target["per_second"]}, "power_w": 0}, object(), []

        orders, _ = self.orders(compute)
        assert all(any("had not settled" in p for p in o.problems) for o in orders.values())

    def test_nothing_drawn_means_one_round(self) -> None:
        calls = []

        def compute(order):
            calls.append(order)
            return {"lines": [], "from_outside": {}, "power_w": 0}, object(), []

        self.orders(compute)
        assert len(calls) == 2


class TestOwnRecipeAndMachine:
    def test_the_order_pins_the_recipe_and_the_machine_of_the_base(self) -> None:
        snapshot = starved()
        found = diagnose(snapshot, book=BOOK)
        (item,) = [b for b, _ in buildout.build_orders(found, snapshot, bill=False, known=anything,
                                                       category_of=lambda recipe: "smelting-here")]
        assert item.request["recipe_choices"] == {"plate": "smelt"}
        assert item.request["machine_choices"] == {"smelting-here": "assembler"}
        spec = request.parse(item.request)
        assert spec.recipe_choices == {"plate": "smelt"} and spec.machine_choices == {"smelting-here": "assembler"}

    def test_without_a_category_only_the_recipe_is_pinned(self) -> None:
        snapshot = starved()
        found = diagnose(snapshot, book=BOOK)
        (item,) = [b for b, _ in buildout.build_orders(found, snapshot, bill=False, known=anything,
                                                       category_of=lambda recipe: None)]
        assert item.request["recipe_choices"] == {"plate": "smelt"}
        assert "machine_choices" not in item.request


    def test_the_bills_machine_is_the_one_the_base_uses(self) -> None:
        from test_route import chained_recipes

        snapshot, _item = TestActiveData().surveyed()
        (first, first_machine), _second = chained_recipes()
        found = diagnose(snapshot)
        (order,) = [b for b, _ in buildout.build_orders(found, snapshot)]
        if order.bill is None:
            pytest.skip(f"the order is not ready on this data: {order.problems}")
        lines = [ln for ln in order.bill["lines"] if ln["recipe"] == first]
        assert lines, "the bill does not use the recipe the base uses"
        assert {ln["machine"] for ln in lines} == {first_machine}
