"""The diagnosis of a surveyed base: chains down to the first cause, with numbers.

Most tests run on invented recipes handed in through `RecipeBook`, so they need no game data and the
numbers can be worked out by hand. The last class reads the active data's own recipes.
"""

from __future__ import annotations

import json
import math

import pytest

from factorio_forge import survey as survey_module
from factorio_forge.diagnose import Cause, RecipeBook, diagnose

RECIPES = {
    "smelt": {"ingredients": [{"name": "ore", "amount": 1}], "results": [{"name": "plate", "amount": 1}],
              "energy_required": 3.2},
    "gear": {"ingredients": [{"name": "plate", "amount": 2}], "results": [{"name": "gear", "amount": 1}],
             "energy_required": 0.5},
    "panel": {"ingredients": [{"name": "gear", "amount": 5}, {"name": "plate", "amount": 5}],
              "results": [{"name": "panel", "amount": 1}], "energy_required": 10},
    "widget": {"ingredients": [{"name": "gizmo", "amount": 1}], "results": [{"name": "widget", "amount": 1}],
               "energy_required": 1},
    # two recipes that feed each other, as a loop in the recipe graph does
    "ping": {"ingredients": [{"name": "pong", "amount": 1}], "results": [{"name": "ping", "amount": 1}],
             "energy_required": 1},
    "pong": {"ingredients": [{"name": "ping", "amount": 1}], "results": [{"name": "pong", "amount": 1}],
             "energy_required": 1},
}
BOOK = RecipeBook(RECIPES, is_mined=lambda item: item == "ore")


def machine(unit, recipe, working=1.0, missing=None, states=None, speed=1.0, name="assembler", at=None):
    shares = states if states is not None else {"working": working}
    if states is None and working < 1.0:
        shares["no_ingredients"] = round(1.0 - working, 6)
    return {"unit": unit, "name": name, "position": at or [unit, 0.5], "recipe": recipe, "crafting_speed": speed,
            "states": shares, "missing": missing or {}, "crafts_per_s": 0.0}


def drill(unit, field=1, states=None, resource="ore"):
    return {"unit": unit, "name": "drill", "position": [50 + unit, 50], "resource": resource, "field": field,
            "states": states or {"working": 1.0}}


def survey(machines=(), miners=(), fields=(), items=None, power=(), **extra):
    data = {"kind": "survey", "surface": "testland", "tick": 1, "window_s": 60, "machines": list(machines),
            "miners": list(miners), "fields": list(fields),
            "flows": {"items": items or {}, "fluids": {}}, "power": list(power)}
    data.update(extra)
    return survey_module.from_dict(data)


def field(fid=1, amount=1e6, drills=4, resource="ore"):
    return {"id": fid, "resource": resource, "amount": amount, "tiles": 50, "drills": drills, "center": [50, 50]}


def kinds(cause: Cause) -> list[str]:
    return [c.kind for c in cause.walk()]


def find(cause: Cause, kind: str) -> Cause:
    return next(c for c in cause.walk() if c.kind == kind)


# --------------------------------------------------------------------------


class TestChains:
    def starved_gears(self, smelter_working=1.0):
        gears = [machine(i, "gear", 0.4, {"plate": 0.6}) for i in range(1, 5)]
        smelters = [machine(100 + i, "smelt", smelter_working) for i in range(10)]
        return survey(gears + smelters, items={"plate": {"made": 3.0, "used": 3.0}})

    def test_smelters_that_never_stop_are_too_few(self) -> None:
        found = diagnose(self.starved_gears(), book=BOOK)
        assert [c.recipe for c in found.causes] == ["gear"]
        root = found.causes[0]
        assert kinds(root) == ["low_output", "waiting", "too_few"]
        waiting, too_few = root.children[0], root.children[0].children[0]
        # 4 gear assemblers, 2 crafts/s each, 2 plates a craft, 60% of the time starved
        assert waiting.short_per_s == pytest.approx(4 * 0.6 * 2 * 2)
        assert waiting.share == pytest.approx(0.6)
        assert too_few.numbers["per_machine_per_s"] == pytest.approx(1 / 3.2)
        assert too_few.numbers["machines_needed"] == math.ceil(4 * 0.6 * 4 * 3.2 - 1e-9)
        assert sorted(too_few.units) == list(range(100, 110))
        assert root.lost_per_s == pytest.approx(4 * 0.6 * 2)

    def test_the_gap_is_a_shortfall_with_a_machine_count(self) -> None:
        found = diagnose(self.starved_gears(), book=BOOK)
        (short,) = found.shortfalls
        assert (short.item, short.kind, short.maker_recipe) == ("plate", "too_few", "smelt")
        assert short.short_per_s == pytest.approx(9.6)
        assert short.machines_needed == 31

    def test_smelters_that_are_idle_themselves_are_the_cause_not_too_few(self) -> None:
        smelters = [machine(100 + i, "smelt", 0.5, {"ore": 0.5}) for i in range(10)]
        gears = [machine(i, "gear", 0.4, {"plate": 0.6}) for i in range(1, 5)]
        found = diagnose(survey(gears + smelters, miners=[drill(1)], fields=[field()],
                                items={"ore": {"made": 1.0, "used": 1.0}}), book=BOOK)
        assert [c.recipe for c in found.causes] == ["gear"]  # smelt is explained by gear's chain
        names = kinds(found.causes[0])
        assert names[:4] == ["low_output", "waiting", "low_output", "waiting"]
        assert "too_few" in names  # they would add 10 * 0.5 / 3.2 = 1.56/s of 9.6/s wanted

    def test_the_makers_headroom_is_taken_off_what_is_still_short(self) -> None:
        smelters = [machine(100 + i, "smelt", 0.5, {"ore": 0.5}) for i in range(10)]
        gears = [machine(i, "gear", 0.4, {"plate": 0.6}) for i in range(1, 5)]
        found = diagnose(survey(gears + smelters, miners=[drill(1)], fields=[field()]), book=BOOK)
        too_few = find(found.causes[0], "too_few")
        assert too_few.numbers["headroom_per_s"] == pytest.approx(10 * 0.5 / 3.2)
        assert too_few.short_per_s == pytest.approx(9.6 - 10 * 0.5 / 3.2)

    def test_a_group_that_works_nearly_all_the_time_is_good_not_a_cause(self) -> None:
        found = diagnose(survey([machine(i, "gear", 0.95) for i in range(3)]), book=BOOK)
        assert found.causes == []
        assert [g["recipe"] for g in found.good] == ["gear"]
        assert found.good[0]["machines"] == 3

    def test_full_rate_counts_the_built_in_productivity(self) -> None:
        book = RecipeBook(RECIPES, built_in=lambda name: 0.5)
        recipes = {**RECIPES, "gear": {**RECIPES["gear"], "allow_productivity": True}}
        book.recipes = recipes
        found = diagnose(survey([machine(1, "gear", 0.5, {"plate": 0.5})]), book=book)
        assert found.causes[0].numbers["possible_per_s"] == pytest.approx(2 * 1.5)

    def test_a_loop_in_the_recipes_does_not_loop_the_walk(self) -> None:
        found = diagnose(survey([machine(1, "ping", 0.3, {"pong": 0.7}), machine(2, "pong", 0.3, {"ping": 0.7})]),
                         book=BOOK)
        assert len(found.causes) == 1  # one half of the loop is enough
        assert "cycle" in kinds(found.causes[0])

    def test_causes_come_biggest_loss_first(self) -> None:
        machines = [machine(1, "panel", 0.5, {"gear": 0.5}), machine(2, "widget", 0.5, {"gizmo": 0.5}, speed=4.0)]
        found = diagnose(survey(machines), book=BOOK)
        lost = [c.lost_per_s for c in found.causes]
        assert lost == sorted(lost, reverse=True) and len(lost) == 2
        assert found.causes[0].recipe == "widget"  # 2 widgets/s lost against 0.05 panels/s

    def test_a_recipe_the_data_lacks_is_skipped_and_said(self) -> None:
        found = diagnose(survey([machine(1, "unheard-of", 0.1)]), book=BOOK)
        assert found.causes == []
        assert any("unheard-of" in limit for limit in found.limits)

    def test_the_limits_always_name_what_the_snapshot_cannot_see(self) -> None:
        found = diagnose(survey([machine(1, "gear", 1.0)]), book=BOOK)
        text = " ".join(found.limits)
        assert "trains" in text and "robots" in text and "by hand" in text and "beacons" in text


class TestSources:
    def starved_smelters(self, drills, fields, items=None):
        smelters = [machine(100 + i, "smelt", 0.5, {"ore": 0.5}) for i in range(4)]
        return diagnose(survey(smelters, miners=drills, fields=fields, items=items), book=BOOK)

    def test_drills_with_a_full_output_mean_the_ore_is_not_carried_off(self) -> None:
        drills = [drill(i, states={"working": 0.3, "output_full": 0.7}) for i in range(1, 5)]
        found = self.starved_smelters(drills, [field()], {"ore": {"made": 1.0, "used": 1.0}})
        blocked = find(found.causes[0], "drills_blocked")
        assert blocked.share == pytest.approx(0.7)
        assert blocked.units == [1, 2, 3, 4]
        assert "field_depleting" not in kinds(found.causes[0])
        assert "too_few_drills" not in kinds(found.causes[0])  # they do not work all the time

    def test_a_nearly_empty_field_says_how_many_hours_are_left(self) -> None:
        # 4 drills work all the time and 2 ore/s are mined: 1000 ore last 500 s
        found = self.starved_smelters([drill(i) for i in range(1, 5)], [field(amount=1000)],
                                      {"ore": {"made": 2.0, "used": 2.0}})
        node = find(found.causes[0], "field_depleting")
        assert node.numbers["hours_left"] == pytest.approx(1000 / 2.0 / 3600)
        assert node.numbers["rate_per_s"] == pytest.approx(2.0)
        assert node.units == [1, 2, 3, 4]
        assert "no_field" not in kinds(found.causes[0])

    def test_drills_that_always_work_on_a_big_field_are_too_few(self) -> None:
        found = self.starved_smelters([drill(i) for i in range(1, 5)], [field(amount=9e9)],
                                      {"ore": {"made": 2.0, "used": 2.0}})
        node = find(found.causes[0], "too_few_drills")
        assert node.numbers["per_drill_per_s"] == pytest.approx(0.5)
        assert node.numbers["machines_needed"] == math.ceil(node.short_per_s / 0.5 - 1e-9)
        assert "field_depleting" not in kinds(found.causes[0])
        (short,) = found.shortfalls
        assert short.kind == "too_few_drills" and short.machines_needed == node.numbers["machines_needed"]

    def test_a_field_without_drills_and_no_field_at_all(self) -> None:
        free = self.starved_smelters([], [field(drills=0)])
        assert "no_drills" in kinds(free.causes[0])
        none = self.starved_smelters([], [])
        assert "no_field" in kinds(none.causes[0])

    def test_nobody_makes_it_and_something_makes_it_elsewhere(self) -> None:
        widgets = [machine(1, "widget", 0.2, {"gizmo": 0.8})]
        nobody = diagnose(survey(widgets), book=BOOK)
        leaf = find(nobody.causes[0], "nobody_makes")
        assert leaf.numbers["made_elsewhere_per_s"] == 0
        outside = diagnose(survey(widgets, items={"gizmo": {"made": 2.0, "used": 1.0}}), book=BOOK)
        leaf = find(outside.causes[0], "nobody_makes")
        assert leaf.numbers["made_elsewhere_per_s"] == pytest.approx(2.0)
        assert any("arrive from outside" in limit for limit in outside.limits)


class TestStops:
    def test_power_short_names_the_shortfall_in_watts(self) -> None:
        machines = [machine(i, "gear", states={"working": 0.4, "low_power": 0.6}) for i in range(1, 4)]
        power = [{"network": 1, "made_w": 30e6, "used_w": 40e6, "satisfaction": 0.75, "machines": 3}]
        found = diagnose(survey(machines, power=power), book=BOOK)
        leaf = find(found.causes[0], "power")
        assert leaf.numbers["short_w"] == pytest.approx(10e6)
        assert leaf.units == [1, 2, 3]
        assert "10 MW" in leaf.text
        assert found.standalone[0].kind == "power"

    def test_no_power_with_every_network_fine_means_not_connected(self) -> None:
        machines = [machine(1, "gear", states={"working": 0.2, "no_power": 0.8})]
        power = [{"network": 1, "made_w": 5e6, "used_w": 1e6, "satisfaction": 1.0, "machines": 3}]
        found = diagnose(survey(machines, power=power), book=BOOK)
        assert "no_power" in kinds(found.causes[0])
        assert found.standalone == []

    def test_output_full_at_the_end_of_a_chain(self) -> None:
        machines = [machine(1, "panel", states={"working": 0.2, "output_full": 0.8})]
        found = diagnose(survey(machines), book=BOOK)
        leaf = find(found.causes[0], "output_full_end")
        assert leaf.share == pytest.approx(0.8) and leaf.units == [1]
        assert "nowhere" in leaf.text

    def test_output_full_followed_to_users_that_starve(self) -> None:
        machines = [machine(1, "gear", states={"working": 0.2, "output_full": 0.8}),
                    machine(2, "panel", 0.3, {"plate": 0.7})]
        found = diagnose(survey(machines), book=BOOK)
        assert [c.recipe for c in found.causes] == ["gear"]  # panel is below it, not listed again
        assert kinds(found.causes[0]) == ["low_output", "output_full", "low_output", "waiting", "nobody_makes"]

    def test_output_full_that_users_cannot_take_faster(self) -> None:
        machines = [machine(1, "gear", states={"working": 0.2, "output_full": 0.8}), machine(2, "panel", 1.0)]
        found = diagnose(survey(machines), book=BOOK)
        assert "backed_up" in kinds(found.causes[0])

    def test_no_fuel_and_disabled_are_leaves(self) -> None:
        machines = [machine(1, "gear", states={"working": 0.1, "no_fuel": 0.9}),
                    machine(2, "widget", states={"working": 0.1, "disabled": 0.9})]
        found = diagnose(survey(machines), book=BOOK)
        all_kinds = {k for c in found.causes for k in kinds(c)}
        assert {"no_fuel", "disabled"} <= all_kinds

    def test_machines_with_no_recipe_are_standalone(self) -> None:
        found = diagnose(survey([machine(5, None, states={"no_recipe": 1.0})]), book=BOOK)
        assert found.standalone[0].kind == "no_recipe" and found.standalone[0].units == [5]


class TestTargets:
    def test_a_target_gap_is_a_cause_and_goes_down_the_same_way(self) -> None:
        gears = [machine(i, "gear", 1.0) for i in range(1, 3)]  # 2 machines x 2/s, working all the time
        found = diagnose(survey(gears, items={"gear": {"made": 4.0, "used": 0.0}}), {"gear": 10.0}, book=BOOK)
        gap = found.causes[0]
        assert gap.kind == "target_gap"
        assert gap.short_per_s == pytest.approx(6.0)
        leaf = find(gap, "too_few")
        assert leaf.numbers["machines_needed"] == 3  # 6/s at 2/s a machine
        assert found.shortfalls[0].item == "gear"

    def test_a_target_already_met_adds_nothing(self) -> None:
        gears = [machine(1, "gear", 1.0)]
        found = diagnose(survey(gears, items={"gear": {"made": 2.0, "used": 0.0}}), {"gear": 2.0}, book=BOOK)
        assert found.causes == []

    def test_a_target_nobody_works_on(self) -> None:
        found = diagnose(survey([machine(1, "gear", 1.0)]), [("panel", 0.5)], book=BOOK)
        assert found.causes[0].kind == "target_gap"
        assert "nobody_makes" in kinds(found.causes[0])

    def test_the_groups_a_target_explains_are_not_listed_again(self) -> None:
        gears = [machine(i, "gear", 0.4, {"plate": 0.6}) for i in range(1, 5)]
        smelters = [machine(100 + i, "smelt", 1.0) for i in range(3)]
        found = diagnose(survey(gears + smelters), {"gear": 8.0}, book=BOOK)
        assert [c.kind for c in found.causes] == ["target_gap"]


class TestOutput:
    def test_the_diagnosis_is_plain_json(self) -> None:
        found = diagnose(TestChains().starved_gears(), {"gear": 20}, book=BOOK)
        data = json.loads(json.dumps(found.to_dict()))
        assert data["verdict"] and data["causes"] and data["shortfalls"] and data["limits"]

    def test_the_verdict_is_one_line(self) -> None:
        found = diagnose(TestChains().starved_gears(), book=BOOK)
        assert "\n" not in found.verdict and "gear" in found.verdict

    def test_an_empty_survey(self) -> None:
        found = diagnose(survey(), book=BOOK)
        assert "nothing to judge" in found.verdict


# --------------------------------------------------------------------------
# against the active game data
# --------------------------------------------------------------------------


class TestActiveData:
    def test_starved_second_step_of_a_chain_of_the_active_recipes(self) -> None:
        from test_route import chained_recipes

        (first, first_machine), (second, second_machine) = chained_recipes()
        from draftsman.data import recipes

        item = recipes.raw[second]["ingredients"][0]["name"]
        makers = [machine(100 + i, first, 1.0, name=first_machine, speed=1.0) for i in range(3)]
        users = [machine(i, second, 0.2, {item: 0.8}, name=second_machine, speed=1.0) for i in range(1, 4)]
        found = diagnose(survey(users + makers))
        root = found.causes[0]
        assert root.recipe == second
        leaf = find(root, "too_few")
        assert leaf.recipe == first and leaf.numbers["machines_needed"] >= 1
        assert found.shortfalls[0].item == item
        # the amounts follow the recipe's own numbers: 0.8 of the time starved
        entry = recipes.raw[second]
        per_craft = next(i["amount"] for i in entry["ingredients"] if i["name"] == item)
        energy = float(entry.get("energy_required", 0.5) or 0.5)
        assert root.children[0].short_per_s == pytest.approx(3 * 0.8 * per_craft / energy)
