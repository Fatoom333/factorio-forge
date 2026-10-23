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

    def test_causes_come_in_order_of_idle_machine_time_not_of_units(self) -> None:
        # steam is counted in hundreds per second, a gear in single ones; one boiler half idle is a small
        # cause next to four gear makers that idle four fifths of the time.
        recipes = {**RECIPES, "boil": {"ingredients": [{"name": "water", "amount": 1}],
                                       "results": [{"name": "steam", "amount": 600}], "energy_required": 1}}
        machines = [machine(1, "boil", 0.5, {"water": 0.5})]
        machines += [machine(10 + i, "gear", 0.2, {"plate": 0.8}) for i in range(4)]
        found = diagnose(survey(machines), book=RecipeBook(recipes))
        assert [c.recipe for c in found.causes] == ["gear", "boil"]
        gear, boil = found.causes
        assert boil.lost_per_s > 10 * gear.lost_per_s  # by units the fluid would come first
        assert gear.numbers["impact_idle_machines"] == pytest.approx(3.2)
        assert boil.numbers["impact_idle_machines"] == pytest.approx(0.5)
        assert "machine(s) of idle time" in gear.text

    def test_the_idle_time_is_summed_down_the_tree(self) -> None:
        machines = [machine(1, "panel", 0.5, {"gear": 0.5})]
        machines += [machine(10 + i, "gear", 0.5, {"plate": 0.5}) for i in range(2)]
        found = diagnose(survey(machines), book=BOOK)
        assert [c.recipe for c in found.causes] == ["panel"]
        root = found.causes[0]
        assert root.numbers["impact_idle_machines"] == pytest.approx(0.5 + 2 * 0.5)
        assert find(root, "low_output").numbers["explains_idle_machines"] == pytest.approx(1.5)

    def test_a_fluid_with_a_full_output_does_not_outrank_the_ore_its_user_misses(self) -> None:
        recipes = {**RECIPES,
                   "boil": {"ingredients": [{"name": "water", "amount": 1}],
                            "results": [{"name": "steam", "amount": 600}], "energy_required": 1},
                   "steamer": {"ingredients": [{"name": "steam", "amount": 10}, {"name": "tungsten", "amount": 1}],
                               "results": [{"name": "motor", "amount": 1}], "energy_required": 1}}
        machines = [machine(1, "boil", states={"working": 0.1, "output_full": 0.9}, speed=5.0)]
        machines += [machine(10 + i, "steamer", 0.3, {"tungsten": 0.7}) for i in range(4)]
        found = diagnose(survey(machines), book=RecipeBook(recipes, is_mined=lambda i: i == "tungsten"))
        assert [c.recipe for c in found.causes] == ["steamer", "boil"]
        steamer, boil = found.causes
        assert find(steamer, "no_field").item == "tungsten"
        assert steamer.numbers["impact_idle_machines"] == pytest.approx(2.8)
        assert boil.numbers["impact_idle_machines"] == pytest.approx(0.0) and not boil.numbers["has_shortage"]
        assert [s.item for s in found.shortfalls] == ["tungsten"]

    def test_a_group_is_counted_once_not_in_every_cause_that_reaches_it(self) -> None:
        # two full outputs both point at the same starving user: its idle time is credited to one cause only
        machines = [machine(1, "smelt", states={"working": 0.1, "output_full": 0.9}),
                    machine(2, "gear", states={"working": 0.1, "output_full": 0.9}),
                    machine(3, "panel", 0.2, {"ore": 0.8})]
        found = diagnose(survey(machines), book=BOOK)
        assert sum(c.numbers["impact_idle_machines"] for c in found.causes) == pytest.approx(0.8)

    def test_a_full_output_is_not_idle_time_for_a_shortage(self) -> None:
        full = {"working": 0.2, "output_full": 0.8}
        found = diagnose(survey([machine(1, "gear", states=full), machine(2, "panel", 0.2, {"gear": 0.8})]),
                         book=BOOK)
        gear = next(c for root in found.causes for c in root.walk() if c.recipe == "gear" and c.kind == "low_output")
        assert gear.numbers["idle_machines"] == pytest.approx(0.0)
        assert gear.numbers["blocked_machines"] == pytest.approx(0.8)

    def test_more_made_than_possible_is_said_to_be_a_lower_bound(self) -> None:
        m = machine(1, "gear", 0.8, {"plate": 0.2})
        m["crafts_per_s"] = 2.6  # a gear takes 0.5 s: 2 a second is all a speed-1 machine can do
        found = diagnose(survey([m]), book=BOOK)
        node = found.causes[0]
        assert node.numbers["possible_is_lower_bound"] is True
        assert "lower bound" in node.text and "made of" not in node.text

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

    def test_a_field_cut_by_the_search_bound_is_no_alarm(self) -> None:
        cut = {**field(amount=1000), "truncated": True}
        found = self.starved_smelters([drill(i) for i in range(1, 5)], [cut], {"ore": {"made": 2.0, "used": 2.0}})
        assert "field_depleting" not in kinds(found.causes[0])

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
        # the user that waits is the cause; the full output is its symptom, listed after and pointing at it
        assert [c.recipe for c in found.causes] == ["panel", "gear"]
        assert kinds(found.causes[0]) == ["low_output", "waiting", "nobody_makes"]
        assert kinds(found.causes[1]) == ["low_output", "output_full", "seen_above"]
        assert found.causes[1].numbers["impact_idle_machines"] == pytest.approx(0.0)

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


# --------------------------------------------------------------------------
# the corners: shared shortages, unknown states, fluids, power, size
# --------------------------------------------------------------------------

SHARED = {
    **RECIPES,
    "rod": {"ingredients": [{"name": "plate", "amount": 1}], "results": [{"name": "rod", "amount": 1}],
            "energy_required": 0.5},
    "brew": {"ingredients": [{"name": "water", "amount": 10, "type": "fluid"}],
             "results": [{"name": "beer", "amount": 1}], "energy_required": 1},
    "pump": {"ingredients": [], "results": [{"name": "water", "amount": 100, "type": "fluid"}],
             "energy_required": 1},
}
SHARED_BOOK = RecipeBook(SHARED, is_mined=lambda item: item == "ore")


class TestSharedShortage:
    def snapshot(self):
        # gear needs 4 plates/s at full work and misses 25% of it: 1/s; rod needs 2/s and misses 50%: 1/s
        gear = machine(1, "gear", states={"working": 0.75, "no_ingredients": 0.25}, missing={"plate": 0.25})
        rod = machine(2, "rod", states={"working": 0.5, "no_ingredients": 0.5}, missing={"plate": 0.5})
        smelter = machine(3, "smelt", 0.5, speed=3.2)  # 1 plate/s at full work, so 0.5/s still to give
        return survey([gear, rod, smelter])

    def test_the_headroom_of_the_makers_is_taken_off_once_for_all_who_wait(self) -> None:
        found = diagnose(self.snapshot(), book=SHARED_BOOK)
        (short,) = found.shortfalls
        assert short.item == "plate" and short.kind == "too_few"
        assert short.consumers == {"gear": pytest.approx(1.0), "rod": pytest.approx(1.0)}
        assert short.short_per_s == pytest.approx(2.0 - 0.5)
        assert short.machines_needed == 2  # 1.5/s at 1/s a machine

    def test_the_tree_shows_the_item_once_and_its_figures_are_the_items(self) -> None:
        found = diagnose(self.snapshot(), book=SHARED_BOOK)
        leaves = [c for root in found.causes for c in root.walk() if c.kind == "too_few"]
        assert len(leaves) == 1
        assert leaves[0].short_per_s == pytest.approx(1.5)
        assert "2 users" in leaves[0].text
        assert any(c.kind == "seen_above" for root in found.causes for c in root.walk())


class TestWaitingWhateverItIsCalled:
    def test_a_fluid_starved_machine_reaches_the_water_makers(self) -> None:
        brew = machine(1, "brew", states={"working": 0.3, "no_input_fluid": 0.7}, missing={"water": 0.7})
        pumps = [machine(10 + i, "pump", 1.0) for i in range(2)]
        found = diagnose(survey([brew] + pumps), book=SHARED_BOOK)
        root = found.causes[0]
        assert kinds(root) == ["low_output", "waiting", "too_few"]
        assert find(root, "too_few").item == "water"

    def test_an_unlisted_state_with_missing_data_is_still_followed(self) -> None:
        gear = machine(1, "gear", states={"working": 0.3, "waiting_on_the_moon": 0.7}, missing={"plate": 0.7})
        found = diagnose(survey([gear]), book=BOOK)
        assert "waiting" in kinds(found.causes[0]) and "other" not in kinds(found.causes[0])

    def test_missing_with_no_state_loss_is_followed_too(self) -> None:
        gear = machine(1, "gear", states={"working": 0.5}, missing={"plate": 0.5})
        found = diagnose(survey([gear]), book=BOOK)
        assert "waiting" in kinds(found.causes[0])

    def test_a_machine_recharging_after_an_outage_is_a_power_leaf(self) -> None:
        gear = machine(1, "gear", states={"working": 0.2, "recharging_after_power_outage": 0.8})
        found = diagnose(survey([gear]), book=BOOK)
        assert {"no_power", "power"} & set(kinds(found.causes[0]))


class TestPowerWithoutWatts:
    def test_a_satisfaction_below_one_is_not_turned_into_watts(self) -> None:
        machines = [machine(i, "gear", states={"working": 0.4, "low_power": 0.6}) for i in range(1, 4)]
        power = [{"network": 1, "made_w": 50e6, "used_w": 40e6, "satisfaction": 0.8, "machines": 3}]
        found = diagnose(survey(machines, power=power), book=BOOK)
        leaf = find(found.causes[0], "power")
        assert leaf.numbers["short_w"] is None
        assert "short by" not in leaf.text and "not in the snapshot" in leaf.text
        assert leaf.units == [1, 2, 3] and leaf.share == pytest.approx(0.6)
        assert found.standalone[0].kind == "power" and found.standalone[0].numbers["short_w"] is None

    def test_watts_are_given_only_where_used_exceeds_made(self) -> None:
        machines = [machine(1, "gear", states={"working": 0.4, "low_power": 0.6})]
        power = [{"network": 1, "made_w": 30e6, "used_w": 40e6, "satisfaction": 0.9, "machines": 1},
                 {"network": 2, "made_w": 9e6, "used_w": 1e6, "satisfaction": 0.7, "machines": 1}]
        found = diagnose(survey(machines, power=power), book=BOOK)
        leaf = find(found.causes[0], "power")
        assert leaf.numbers["short_w"] == pytest.approx(10e6)
        assert leaf.numbers["networks"][1]["short_w"] is None


class TestRevisits:
    def test_a_maker_met_again_through_its_users_is_not_called_a_loop(self) -> None:
        machines = [machine(1, "smelt", states={"working": 0.2, "output_full": 0.8}),
                    machine(2, "gear", 0.3, {"plate": 0.7})]
        found = diagnose(survey(machines), book=BOOK)
        all_kinds = {k for c in found.causes for k in kinds(c)}
        assert "seen_above" in all_kinds and "cycle" not in all_kinds

    def test_a_real_loop_is_still_one(self) -> None:
        found = diagnose(survey([machine(1, "ping", 0.3, {"pong": 0.7}), machine(2, "pong", 0.3, {"ping": 0.7})]),
                         book=BOOK)
        assert "cycle" in kinds(found.causes[0])


class TestSize:
    def test_a_layered_chain_costs_nodes_in_step_with_its_groups_not_its_paths(self) -> None:
        width, layers = 8, 10
        recipes = {}
        machines = []
        for k in range(layers):
            previous = ["ore"] if k == 0 else [f"i{k - 1}_{t}" for t in range(width)]
            for j in range(width):
                recipes[f"r{k}_{j}"] = {
                    "ingredients": [{"name": name, "amount": 1} for name in previous],
                    "results": [{"name": f"i{k}_{j}", "amount": 1}], "energy_required": 1}
                machines.append(machine(k * width + j + 1, f"r{k}_{j}", 0.2, {name: 0.8 for name in previous}))
        found = diagnose(survey(machines), book=RecipeBook(recipes, is_mined=lambda item: item == "ore"))
        nodes = sum(1 for root in found.causes for _ in root.walk())
        assert nodes <= 25 * len(machines)
        # every group is expanded in full exactly once
        expanded = [c.recipe for root in found.causes for c in root.walk() if c.kind == "low_output"]
        assert len(expanded) == len(set(expanded)) == len(machines)


class TestAreaLimits:
    def test_with_an_area_no_rate_or_hours_are_read_from_the_surface_flow(self) -> None:
        smelters = [machine(100 + i, "smelt", 0.5, {"ore": 0.5}) for i in range(4)]
        drills = [drill(i, states={"working": 0.5, "no_minable_resources": 0.5}) for i in range(1, 3)]
        found = diagnose(survey(smelters, miners=drills, fields=[field(amount=1000)], area=[[0, 0], [100, 100]],
                                items={"ore": {"made": 50.0, "used": 50.0}}), book=BOOK)
        node = find(found.causes[0], "field_depleting")
        assert node.numbers["hours_left"] is None and node.numbers["rate_per_s"] is None
        assert "not known" in node.text
        assert any("no mining rate" in limit for limit in found.limits)

    def test_with_an_area_the_drill_count_is_not_guessed_either(self) -> None:
        smelters = [machine(100 + i, "smelt", 0.5, {"ore": 0.5}) for i in range(4)]
        found = diagnose(survey(smelters, miners=[drill(i) for i in range(1, 5)], fields=[field(amount=9e9)],
                                area=[[0, 0], [100, 100]], items={"ore": {"made": 50.0, "used": 50.0}}), book=BOOK)
        node = find(found.causes[0], "too_few_drills")
        assert node.numbers["machines_needed"] is None and node.numbers["per_drill_per_s"] is None


# --------------------------------------------------------------------------
# machines that make buildings, labs, whole-base surveys, patches nobody mines
# --------------------------------------------------------------------------

MALL_RECIPES = {
    **RECIPES,
    "carbide": {"ingredients": [{"name": "ore", "amount": 1}], "results": [{"name": "carbide", "amount": 1}],
                "energy_required": 1},
    "make-foundry": {"ingredients": [{"name": "carbide", "amount": 20}],
                     "results": [{"name": "foundry-item", "amount": 1}], "energy_required": 1},
    "red": {"ingredients": [{"name": "plate", "amount": 1}], "results": [{"name": "red-pack", "amount": 1}],
            "energy_required": 5},
    "green": {"ingredients": [{"name": "plate", "amount": 1}], "results": [{"name": "green-pack", "amount": 1}],
              "energy_required": 5},
    "make-belt": {"ingredients": [{"name": "plate", "amount": 1}], "results": [{"name": "belt", "amount": 1}],
                  "energy_required": 1},
    "needs-belt": {"ingredients": [{"name": "belt", "amount": 1}], "results": [{"name": "gadget", "amount": 1}],
                   "energy_required": 1},
}


def mall_book(**extra) -> RecipeBook:
    return RecipeBook(MALL_RECIPES, is_mined=lambda item: item in ("ore", "tungsten"),
                      is_building=lambda item: item in ("foundry-item", "belt"), **extra)


class TestBuildings:
    def survey(self):
        makers = [machine(100 + i, "carbide", 1.0) for i in range(3)]
        mall = [machine(1, "make-foundry", 0.1, {"carbide": 0.9})]
        return survey(makers + mall, items={"carbide": {"made": 3.0, "used": 3.0}})

    def test_machines_that_make_buildings_raise_no_shortfall(self) -> None:
        found = diagnose(self.survey(), book=mall_book())
        assert found.causes == [] and found.shortfalls == []
        assert not any(g["recipe"] == "make-foundry" for g in found.good)

    def test_without_the_building_flag_the_same_waiting_would_order_more_of_the_ingredient(self) -> None:
        found = diagnose(self.survey(), book=RecipeBook(MALL_RECIPES, is_mined=lambda item: item == "ore"))
        assert [s.item for s in found.shortfalls] == ["carbide"]

    def test_the_buildings_are_listed_once_as_a_fact(self) -> None:
        found = diagnose(self.survey(), book=mall_book())
        facts = [limit for limit in found.limits if "make buildings" in limit]
        assert len(facts) == 1 and "make-foundry" in facts[0] and "1 machine(s)" in facts[0]

    def test_a_full_output_whose_only_user_makes_buildings_is_normal(self) -> None:
        makers = [machine(100 + i, "carbide", states={"working": 0.2, "output_full": 0.8}) for i in range(3)]
        found = diagnose(survey(makers + [machine(1, "make-foundry", 0.1, {"carbide": 0.9})]), book=mall_book())
        assert found.causes == []

    def test_a_building_that_is_also_an_ingredient_still_has_its_makers(self) -> None:
        machines = [machine(1, "needs-belt", 0.5, {"belt": 0.5}), machine(2, "make-belt", 0.5, {"plate": 0.5})]
        found = diagnose(survey(machines), book=mall_book())
        leaves = [c for r in found.causes for c in r.walk() if c.kind == "nobody_makes"]
        assert [c.item for c in leaves] == ["plate"]  # the chain goes on to what the belt maker lacks
        assert not any(s.item == "belt" for s in found.shortfalls)  # and no order for more belt makers
        assert [s.item for s in found.shortfalls] == ["plate"]


def lab(unit, **states):
    return {"unit": unit, "name": "lab", "position": [200 + unit, 0], "states": states}


class TestLabs:
    PACKS = frozenset({"red-pack", "green-pack"})

    def book(self):
        return RecipeBook(MALL_RECIPES, lab_inputs=self.PACKS, is_mined=lambda item: item == "ore")

    def red(self):
        return [machine(i, "red", states={"working": 0.3, "output_full": 0.7}) for i in range(1, 4)]

    def test_labs_that_work_make_a_full_pack_output_no_cause(self) -> None:
        found = diagnose(survey(self.red(), research="logistics",
                                labs=[lab(1, working=0.8, no_research_in_progress=0.2),
                                      lab(2, working=0.9, missing_science_packs=0.1)]), book=self.book())
        assert found.causes == []
        entry = next(g for g in found.good if g["recipe"] == "research")
        assert entry["machines"] == 2 and entry["working"] == pytest.approx(0.85)

    def test_without_labs_in_the_survey_it_is_not_a_cause_and_the_limit_says_so(self) -> None:
        found = diagnose(survey(self.red()), book=self.book())
        assert found.causes == []
        assert any("Labs were not surveyed" in limit for limit in found.limits)

    def test_labs_with_no_research_are_told_so(self) -> None:
        found = diagnose(survey(self.red(), labs=[lab(1, no_research_in_progress=1.0)]), book=self.book())
        node = find(found.causes[0], "no_research")
        assert "no research is in progress" in node.text

    def test_labs_short_of_a_pack_make_that_pack_a_real_shortfall(self) -> None:
        greens = [machine(20 + i, "green", 0.4, {"plate": 0.6}) for i in range(2)]
        plates = [machine(30 + i, "smelt", 1.0) for i in range(3)]
        short = dict(working=0.4, missing_science_packs=0.5, no_research_in_progress=0.1)
        found = diagnose(survey(
            self.red() + greens + plates, labs=[lab(1, **short), lab(2, **short)], research="logistics",
            items={"red-pack": {"made": 0.6, "used": 0.6}, "green-pack": {"made": 0.3, "used": 0.3},
                   "plate": {"made": 3.0, "used": 3.0}}), book=self.book())
        root = next(c for c in found.causes if c.numbers.get("consumer") == "labs")
        assert root.numbers["backed_up_packs"] == ["red-pack"]
        assert root.numbers["candidates"] == ["green-pack"]
        assert not any(c.recipe == "red" for c in found.causes)  # the held-back pack is not the fault
        assert any(c.kind == "too_few" or c.kind == "nobody_makes" for c in root.walk())
        assert "plate" in [s.item for s in found.shortfalls]

    def test_labs_that_miss_packs_while_every_pack_is_held_back_point_at_the_way_to_the_labs(self) -> None:
        found = diagnose(survey(self.red(), research="logistics",
                                labs=[lab(1, working=0.4, missing_science_packs=0.6)]), book=self.book())
        root = next(c for c in found.causes if c.numbers.get("consumer") == "labs")
        assert root.numbers["candidates"] == [] and "is the limit" in root.text


    def test_labs_working_with_the_research_just_finished_are_still_fed(self) -> None:
        found = diagnose(survey(self.red(), labs=[lab(1, working=0.9, missing_science_packs=0.05)]),
                         book=self.book())  # research is empty at the end of the window only
        assert found.causes == []
        assert any(g["recipe"] == "research" for g in found.good)
        assert any("No research was in progress" in limit for limit in found.limits)

    def test_a_maker_whose_only_trouble_is_a_pack_the_labs_do_not_need_is_no_cause(self) -> None:
        plates = [machine(30 + i, "smelt", states={"working": 0.2, "output_full": 0.8}) for i in range(2)]
        found = diagnose(survey(plates + self.red(), research="logistics",
                                labs=[lab(1, working=0.9), lab(2, working=0.8)]), book=self.book())
        assert found.causes == []

    def test_with_several_candidate_packs_none_is_ordered(self) -> None:
        reds = [machine(i, "red", 0.4, {"plate": 0.6}) for i in range(1, 3)]
        greens = [machine(20 + i, "green", 0.4, {"plate": 0.6}) for i in range(2)]
        short = dict(working=0.05, missing_science_packs=0.9)
        found = diagnose(survey(
            reds + greens, labs=[lab(1, **short)], research="logistics",
            items={"red-pack": {"made": 0.4, "used": 0.4}, "green-pack": {"made": 0.4, "used": 0.4}}),
            book=self.book())
        root = next(c for c in found.causes if c.numbers.get("consumer") == "labs")
        assert root.numbers["candidates"] == ["green-pack", "red-pack"]
        assert all(c.short_per_s == 0 and c.numbers["guess"] for c in root.children)
        assert not any("labs" in s.consumers for s in found.shortfalls)

    def test_a_single_candidate_is_an_estimate_that_stays_bounded(self) -> None:
        greens = [machine(20 + i, "green", 0.4, {"plate": 0.6}) for i in range(2)]
        short = dict(working=0.01, missing_science_packs=0.9)
        found = diagnose(survey(
            self.red() + greens, labs=[lab(1, **short)], research="logistics",
            items={"red-pack": {"made": 0.6, "used": 0.6}, "green-pack": {"made": 1.0, "used": 1.0}}),
            book=self.book())
        root = next(c for c in found.causes if c.numbers.get("consumer") == "labs")
        (child,) = root.children
        assert "an estimate" in child.text and child.short_per_s <= 1.0 + 1e-9  # never more than doubling


class TestWholeBase:
    def run(self, **extra):
        smelters = [machine(100 + i, "smelt", 0.5, {"ore": 0.5}) for i in range(4)]
        drills = [drill(i, states={"working": 0.5, "no_minable_resources": 0.5}) for i in range(1, 3)]
        return diagnose(survey(smelters, miners=drills, fields=[field(amount=1000)],
                               items={"ore": {"made": 50.0, "used": 50.0}}, **extra), book=BOOK)

    def test_a_whole_base_survey_with_bounds_gives_rates_and_hours(self) -> None:
        found = self.run(bounds=[[0, 0], [100, 100]])
        node = find(found.causes[0], "field_depleting")
        assert node.numbers["hours_left"] is not None and node.numbers["rate_per_s"] == pytest.approx(50.0)
        assert not any("Only the area" in limit for limit in found.limits)

    def test_a_real_area_keeps_the_limit_text(self) -> None:
        found = self.run(area=[[0, 0], [100, 100]], bounds=[[0, 0], [100, 100]])
        assert any("Only the area" in limit for limit in found.limits)


class TestUnmined:
    RECIPES = {"widget": {"ingredients": [{"name": "tungsten", "amount": 1}],
                          "results": [{"name": "widget", "amount": 1}], "energy_required": 1}}

    def run(self, unmined, fields=()):
        data = survey([machine(1, "widget", 0.2, {"tungsten": 0.8})], fields=list(fields), unmined=unmined)
        return diagnose(data, book=RecipeBook(self.RECIPES, is_mined=lambda item: item == "tungsten"))

    def patch(self, enemies, distance=310.0):
        return {"resource": "tungsten", "position": [500, -20], "distance": distance, "amount": 900000,
                "truncated": False, "enemies": enemies}

    def test_a_patch_with_worms_says_to_clear_the_enemies_first(self) -> None:
        found = self.run([self.patch({"spawners": 0, "worms": 14}), self.patch({"worms": 0}, distance=900.0)])
        leaf = find(found.causes[0], "no_field")
        assert "310 tiles" in leaf.text and "14 worms" in leaf.text and "clear the enemies first" in leaf.text
        assert leaf.numbers["clear_enemies_first"] is True and leaf.numbers["patch"]["position"] == [500, -20]
        assert "clear the enemies first" in found.shortfalls[0].note

    def test_a_patch_without_enemies_says_none_were_found(self) -> None:
        leaf = find(self.run([self.patch({})]).causes[0], "no_field")
        assert "no enemies found" in leaf.text and leaf.numbers["clear_enemies_first"] is False

    def test_enemies_not_looked_for_are_not_called_none(self) -> None:
        leaf = find(self.run([self.patch(None)]).causes[0], "no_field")
        assert "not looked at" in leaf.text

    def test_a_resource_missing_from_a_survey_that_lists_patches_was_not_found(self) -> None:
        other = {**self.patch({}), "resource": "coal"}
        leaf = find(self.run([other]).causes[0], "no_field")
        assert "search radius" in leaf.text and leaf.numbers["patch"] is None

    def test_an_old_survey_without_patches_degrades_gracefully(self) -> None:
        leaf = find(self.run([]).causes[0], "no_field")
        assert "older survey file" in leaf.text

    def test_fields_with_enemies_are_a_fact(self) -> None:
        f = {**field(fid=7, drills=0, resource="tungsten"), "enemies": {"spawners": 2, "worms": 0}}
        found = self.run([], fields=[f])
        assert any("Enemies near fields" in limit and "2 spawners" in limit for limit in found.limits)
        assert "clear the enemies first" in find(found.causes[0], "no_drills").text
