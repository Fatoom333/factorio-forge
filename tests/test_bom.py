"""Tests for the bill-of-materials solver.

These use a small hand-built recipe set rather than the active profile's real
data. Unlike layout or naming, correctness here is about the algorithm --
multi-output recipes, self-referencing loops, ambiguous choices -- and those
specific shapes are rare and mod-specific; there is no reliable way to find
"a recipe with two outputs" generically across an arbitrary mod set the way
`tests/prototypes.py` finds "a belt". A synthetic world makes every ratio and
edge case exact and mod-independent instead.
"""

from __future__ import annotations

import pytest

from factorio_forge import bom
from factorio_forge.environment import Environment


# --------------------------------------------------------------------------
# a small synthetic game: ore -> plate -> gear, plus a byproduct and a loop
# --------------------------------------------------------------------------

RECIPES = {
    "smelt-plate": {
        "name": "smelt-plate",
        "category": "smelting",
        "energy_required": 1,
        "ingredients": [{"type": "item", "name": "ore", "amount": 1}],
        "results": [{"type": "item", "name": "plate", "amount": 1}],
    },
    "craft-gear": {
        "name": "craft-gear",
        "category": "crafting",
        "energy_required": 0.5,
        "ingredients": [{"type": "item", "name": "plate", "amount": 2}],
        "results": [{"type": "item", "name": "gear", "amount": 1}],
    },
    "crack-oil": {
        "name": "crack-oil",
        "category": "chemistry",
        "energy_required": 5,
        "ingredients": [{"type": "item", "name": "crude", "amount": 1}],
        "results": [
            {"type": "item", "name": "output-a", "amount": 2},
            {"type": "item", "name": "output-b", "amount": 1},
        ],
    },
    "loop-recipe": {
        "name": "loop-recipe",
        "category": "centrifuging",
        "energy_required": 60,
        "allow_productivity": True,
        "ingredients": [
            {"type": "item", "name": "loop-item", "amount": 30},
            {"type": "item", "name": "fuel", "amount": 3},
        ],
        "results": [
            {
                "type": "item",
                "name": "loop-item",
                "amount": 31,
                "ignored_by_productivity": 30,
            }
        ],
    },
    "widget-a": {
        "name": "widget-a",
        "category": "crafting",
        "energy_required": 1,
        "ingredients": [{"type": "item", "name": "ore", "amount": 1}],
        "results": [{"type": "item", "name": "widget", "amount": 1}],
    },
    "widget-b": {
        "name": "widget-b",
        "category": "crafting",
        "energy_required": 2,
        "ingredients": [{"type": "item", "name": "ore", "amount": 1}],
        "results": [{"type": "item", "name": "widget", "amount": 3}],
    },
    # A genuine two-recipe cycle: recipe-m needs n-item (owned by recipe-n),
    # recipe-n needs m-item (owned by recipe-m) right back. m-item and n-item
    # between them pin both rates exactly, leaving no freedom to also satisfy
    # an independent, separately sized demand on recipe-m's other output.
    "recipe-m": {
        "name": "recipe-m",
        "category": "crafting",
        "energy_required": 1,
        "ingredients": [{"type": "item", "name": "n-item", "amount": 1}],
        "results": [
            {"type": "item", "name": "m-item", "amount": 2},
            {"type": "item", "name": "byproduct", "amount": 1},
        ],
    },
    "recipe-n": {
        "name": "recipe-n",
        "category": "crafting",
        "energy_required": 1,
        "ingredients": [{"type": "item", "name": "m-item", "amount": 1}],
        "results": [{"type": "item", "name": "n-item", "amount": 1}],
    },
    # Two ways to get powder. Crushing a gear yields ten at a time and looks
    # unbeatable per craft -- but a gear costs two plates, and those cost ore
    # and furnace time, so the whole route is dearer in machine-seconds than
    # simply making powder directly. This is the Krastorio 2 "crush a finished
    # inserter for imersite powder" shape in miniature.
    "make-powder": {
        "name": "make-powder",
        "category": "crafting",
        "energy_required": 0.1,
        "ingredients": [{"type": "item", "name": "ore", "amount": 1}],
        "results": [{"type": "item", "name": "powder", "amount": 1}],
    },
    "crush-gear": {
        "name": "crush-gear",
        "category": "crafting",
        "energy_required": 0.5,
        "ingredients": [{"type": "item", "name": "gear", "amount": 1}],
        "results": [{"type": "item", "name": "powder", "amount": 10}],
    },
    # Consumes exactly what it produces: no rate yields a spare unit.
    "null-loop": {
        "name": "null-loop",
        "category": "crafting",
        "energy_required": 1,
        "ingredients": [{"type": "item", "name": "null-item", "amount": 1}],
        "results": [{"type": "item", "name": "null-item", "amount": 1}],
    },
    # 2 of the 5 are a catalyst returned to the same recipe, not a net
    # product; only the remaining 3 may benefit from a productivity bonus.
    "catalytic-recipe": {
        "name": "catalytic-recipe",
        "category": "crafting",
        "energy_required": 1,
        "allow_productivity": True,
        "ingredients": [{"type": "item", "name": "ore", "amount": 1}],
        "results": [
            {"type": "item", "name": "catalytic-item", "amount": 5, "catalyst_amount": 2}
        ],
    },
    # A single producer with a stated output temperature: informational, no
    # conflict to report.
    "vent-hot-steam": {
        "name": "vent-hot-steam",
        "category": "crafting",
        "energy_required": 1,
        "ingredients": [{"type": "item", "name": "ore", "amount": 1}],
        "results": [{"type": "fluid", "name": "hot-steam", "amount": 10, "temperature": 500}],
    },
    # Two producers of the same fluid name at genuinely different
    # temperatures -- each also makes a byproduct nothing else can, so
    # demanding both byproducts forces both into use at once.
    "vent-steam-a": {
        "name": "vent-steam-a",
        "category": "crafting",
        "energy_required": 1,
        "ingredients": [{"type": "item", "name": "ore", "amount": 1}],
        "results": [
            {"type": "fluid", "name": "dual-steam", "amount": 10, "temperature": 500},
            {"type": "item", "name": "byproduct-a", "amount": 1},
        ],
    },
    "vent-steam-b": {
        "name": "vent-steam-b",
        "category": "crafting",
        "energy_required": 1,
        "ingredients": [{"type": "item", "name": "ore", "amount": 1}],
        "results": [
            {"type": "fluid", "name": "dual-steam", "amount": 10, "temperature": 300},
            {"type": "item", "name": "byproduct-b", "amount": 1},
        ],
    },
}

ENTITIES = {
    "furnace": {
        "name": "furnace",
        "type": "furnace",
        "crafting_categories": ["smelting"],
        "crafting_speed": 2,
        "energy_usage": 100000,
    },
    "assembler": {
        "name": "assembler",
        "type": "assembling-machine",
        "crafting_categories": ["crafting"],
        "crafting_speed": 1.25,
        "energy_usage": "150kW",
    },
    "chem-plant": {
        "name": "chem-plant",
        "type": "assembling-machine",
        "crafting_categories": ["chemistry"],
        "crafting_speed": 1,
        "energy_usage": 210000,
    },
    "centrifuge": {
        "name": "centrifuge",
        "type": "assembling-machine",
        "crafting_categories": ["centrifuging"],
        "crafting_speed": 1,
        "energy_usage": 60000,
    },
}


@pytest.fixture(autouse=True)
def synthetic_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bom.recipe_data, "raw", RECIPES)
    monkeypatch.setattr(bom.entity_data, "raw", ENTITIES)


def by_recipe(result: bom.BillOfMaterials, recipe: str) -> bom.MachineLine:
    return next(line for line in result.lines if line.recipe == recipe)


# --------------------------------------------------------------------------
# small pure helpers
# --------------------------------------------------------------------------


class TestWatts:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("90kW", 90_000),
            ("483.4MW", 483_400_000),
            ("500W", 500),
            (1234, 1234),
        ],
    )
    def test_parses_unit_suffixes(self, value, expected) -> None:
        assert bom._watts(value) == pytest.approx(expected)

    def test_rejects_nonsense(self) -> None:
        with pytest.raises(bom.BillOfMaterialsError):
            bom._watts("not-a-number")


class TestExpectedAmount:
    def test_exact_amount(self) -> None:
        assert bom._expected_amount({"amount": 3}) == 3

    def test_range_is_the_midpoint(self) -> None:
        assert bom._expected_amount({"amount_min": 1, "amount_max": 3}) == 2

    def test_probability_scales_it_down(self) -> None:
        assert bom._expected_amount({"amount": 4, "probability": 0.5}) == 2


# --------------------------------------------------------------------------
# a simple chain: raw -> smelting -> crafting
# --------------------------------------------------------------------------


class TestSimpleChain:
    def test_machine_counts_and_raw_demand(self) -> None:
        request = bom.Request(targets=(bom.Target("gear", 1.0),))
        result = bom.compute(request)

        gear_line = by_recipe(result, "craft-gear")
        assert gear_line.rate == pytest.approx(1.0)
        # 1.25 crafts/s capacity per assembler, needs 1/s -> 1 machine
        assert gear_line.machines == 1
        assert gear_line.machine == "assembler"

        plate_line = by_recipe(result, "smelt-plate")
        # gear needs 2 plate/s; furnace does 2 crafts/s -> exactly 1 machine
        assert plate_line.rate == pytest.approx(2.0)
        assert plate_line.machines == 1

        assert result.raw_materials["ore"] == pytest.approx(2.0)

    def test_power_sums_across_machines(self) -> None:
        request = bom.Request(targets=(bom.Target("gear", 5.0),))
        result = bom.compute(request)
        gear_line = by_recipe(result, "craft-gear")
        assert gear_line.power > 0
        assert result.total_power == pytest.approx(
            sum(line.power for line in result.lines)
        )

    def test_boundary_item_is_not_expanded(self) -> None:
        request = bom.Request(
            targets=(bom.Target("gear", 1.0),), boundary=frozenset({"plate"})
        )
        result = bom.compute(request)

        assert not any(line.recipe == "smelt-plate" for line in result.lines)
        assert result.raw_materials["plate"] == pytest.approx(2.0)
        assert "ore" not in result.raw_materials


# --------------------------------------------------------------------------
# a recipe with more than one output at once
# --------------------------------------------------------------------------


class TestByproducts:
    def test_driving_output_sizes_the_line(self) -> None:
        request = bom.Request(targets=(bom.Target("output-a", 4.0),))
        result = bom.compute(request)
        line = by_recipe(result, "crack-oil")
        # 4 output-a/s at 2 per craft -> 2 crafts/s -> 1 output-b/craft -> 2/s
        assert line.rate == pytest.approx(2.0)
        assert line.outputs["output-b"] == pytest.approx(2.0)

    def test_the_more_demanding_output_drives_the_rate(self) -> None:
        """Two independent demands on one recipe never conflict by themselves.

        With fixed positive output ratios, running fast enough for whichever
        output needs the higher rate always leaves the other one with a
        surplus, never a shortfall -- see the comment on `primary_item` in
        `_solve_component`. output-b here implies 100 crafts/s (100 wanted at
        1/craft), output-a only 2 (4 wanted at 2/craft), so output-b wins and
        output-a comes out well over what was asked.
        """
        request = bom.Request(
            targets=(bom.Target("output-a", 4.0), bom.Target("output-b", 100.0))
        )
        result = bom.compute(request)

        line = by_recipe(result, "crack-oil")
        assert line.rate == pytest.approx(100.0)
        assert line.outputs["output-a"] == pytest.approx(200.0)
        assert not any(a.kind == "conflict" for a in result.ambiguities)


# --------------------------------------------------------------------------
# a genuine two-recipe cycle: recipe-m <-> recipe-n (see RECIPES above)
# --------------------------------------------------------------------------


class TestCycles:
    """recipe-m and recipe-n feed each other, so neither has a rate of its own
    until both are solved together. Because the solver works in inequalities,
    a demand on recipe-m's byproduct does not fight the demand on m-item: the
    cycle simply runs fast enough for whichever binds hardest, and the rest
    comes out as surplus."""

    def test_the_cycle_is_solved_for_both_demands_at_once(self) -> None:
        # n-item forces rate_n = rate_m, so net m-item is 2*rate - rate = rate:
        # 10/s of m-item needs rate 10, which also yields 10 byproduct/s --
        # comfortably over the 8/s asked for.
        request = bom.Request(
            targets=(bom.Target("m-item", 10.0), bom.Target("byproduct", 8.0))
        )
        result = bom.compute(request)

        m_line = by_recipe(result, "recipe-m")
        n_line = by_recipe(result, "recipe-n")
        assert m_line.rate == pytest.approx(10.0)
        assert n_line.rate == pytest.approx(10.0)
        assert m_line.outputs["byproduct"] == pytest.approx(10.0)

    def test_a_heavier_byproduct_demand_speeds_the_whole_cycle_up(self) -> None:
        request = bom.Request(
            targets=(bom.Target("m-item", 10.0), bom.Target("byproduct", 25.0))
        )
        result = bom.compute(request)

        m_line = by_recipe(result, "recipe-m")
        assert m_line.rate == pytest.approx(25.0)
        # m-item is then produced well past what was asked for, and that is
        # allowed: a surplus goes to waste, it is not an error.
        assert m_line.outputs["m-item"] == pytest.approx(50.0)

    def test_a_loop_that_produces_nothing_net_is_refused(self) -> None:
        """null-loop consumes and produces the same amount, so no rate at all
        yields a single spare unit -- the solver must say so, not pick one."""
        request = bom.Request(targets=(bom.Target("null-item", 5.0),))
        with pytest.raises(bom.BillOfMaterialsError, match="no set of crafting rates"):
            bom.compute(request)


# --------------------------------------------------------------------------
# a recipe that consumes and produces the same item
# --------------------------------------------------------------------------


class TestSelfLoop:
    def test_net_production_drives_the_rate(self) -> None:
        # net gain is 1 loop-item per craft (31 - 30); demand 2/s -> 2 crafts/s
        request = bom.Request(targets=(bom.Target("loop-item", 2.0),))
        result = bom.compute(request)

        line = by_recipe(result, "loop-recipe")
        assert line.rate == pytest.approx(2.0)
        assert result.raw_materials["fuel"] == pytest.approx(6.0)  # 3/craft * 2

    def test_ignored_by_productivity_caps_the_self_boost(self) -> None:
        """Productivity may not multiply the self-sustaining 30 of the 31.

        With +100% productivity only the 1 unit above `ignored_by_productivity`
        doubles: net gain becomes 2 per craft instead of 1, so half the crafts
        are needed for the same demand -- not a quarter, which is what an
        unguarded productivity multiplier on the full result would give.
        """
        request = bom.Request(
            targets=(bom.Target("loop-item", 2.0),),
            effects={"centrifuging": bom.Effects(productivity=1.0)},
        )
        result = bom.compute(request)
        line = by_recipe(result, "loop-recipe")
        assert line.rate == pytest.approx(1.0)


class TestCatalystAmount:
    def test_baseline_amount_is_unaffected(self) -> None:
        request = bom.Request(targets=(bom.Target("catalytic-item", 5.0),))
        result = bom.compute(request)
        assert by_recipe(result, "catalytic-recipe").rate == pytest.approx(1.0)

    def test_productivity_skips_the_catalyst_portion(self) -> None:
        """Only 3 of the 5 (5 - catalyst_amount 2) may double.

        +100% productivity turns 5/craft into 5 + 3*1 = 8/craft, not the 10
        an unguarded multiplier on the whole result would give.
        """
        request = bom.Request(
            targets=(bom.Target("catalytic-item", 8.0),),
            effects={"crafting": bom.Effects(productivity=1.0)},
        )
        result = bom.compute(request)
        assert by_recipe(result, "catalytic-recipe").rate == pytest.approx(1.0)


class TestTemperature:
    def test_output_temperature_is_reported(self) -> None:
        request = bom.Request(targets=(bom.Target("hot-steam", 5.0),))
        result = bom.compute(request)
        line = by_recipe(result, "vent-hot-steam")
        assert line.output_temperatures == {"hot-steam": 500}
        assert not any(a.kind == "temperature" for a in result.ambiguities)

    def test_pinning_a_matching_temperature_narrows_to_that_producer(self) -> None:
        request = bom.Request(targets=(bom.Target("dual-steam", 5.0, temperature=500),))
        result = bom.compute(request)
        assert by_recipe(result, "vent-steam-a").rate > 0
        assert not any(line.recipe == "vent-steam-b" for line in result.lines)

    def test_pinning_a_temperature_nothing_produces_is_a_named_error(self) -> None:
        request = bom.Request(targets=(bom.Target("dual-steam", 5.0, temperature=999),))
        with pytest.raises(bom.BillOfMaterialsError, match="999"):
            bom.compute(request)

    def test_conflicting_temperatures_are_reported_not_silently_pooled(self) -> None:
        # Both producers are forced into use: byproduct-a only comes from
        # vent-steam-a, byproduct-b only from vent-steam-b.
        request = bom.Request(
            targets=(bom.Target("byproduct-a", 1.0), bom.Target("byproduct-b", 1.0))
        )
        result = bom.compute(request)
        ambiguity = next(a for a in result.ambiguities if a.kind == "temperature")
        assert ambiguity.subject == "dual-steam"
        assert set(ambiguity.candidates) == {"vent-steam-a", "vent-steam-b"}


# --------------------------------------------------------------------------
# recipe choice: default, override, and environment-driven
# --------------------------------------------------------------------------


class TestRecipeChoice:
    def test_the_cheaper_recipe_wins_and_the_choice_is_reported(self) -> None:
        """Cost is machine-seconds, not output per second.

        For 3 widgets/s: widget-a takes 3 crafts of 1s = 3 machine-seconds,
        widget-b 1 craft of 2s = 2. widget-b is the cheaper way to stand up
        that rate even though it is the slower single craft.
        """
        request = bom.Request(targets=(bom.Target("widget", 3.0),))
        result = bom.compute(request)

        assert any(line.recipe == "widget-b" for line in result.lines)
        assert not any(line.recipe == "widget-a" for line in result.lines)
        picked = next(a for a in result.ambiguities if a.subject == "widget")
        assert picked.kind == "recipe"
        assert set(picked.candidates) == {"widget-a", "widget-b"}

    def test_a_wasteful_route_loses_on_what_it_costs_to_feed_it(self) -> None:
        """The whole reason candidates go to the solver rather than a heuristic.

        crush-gear makes ten powder a craft against make-powder's one, so any
        rule looking at output per craft picks it -- and it is nonsense, since
        every gear crushed has to be built out of plates first.
        """
        result = bom.compute(bom.Request(targets=(bom.Target("powder", 10.0),)))

        assert any(line.recipe == "make-powder" for line in result.lines)
        assert not any(line.recipe == "crush-gear" for line in result.lines)

    def test_explicit_override_wins_without_being_reported(self) -> None:
        request = bom.Request(
            targets=(bom.Target("widget", 1.0),),
            recipe_choices={"widget": "widget-a"},
        )
        result = bom.compute(request)

        assert any(line.recipe == "widget-a" for line in result.lines)
        assert not any(a.subject == "widget" for a in result.ambiguities)

    def test_bad_override_is_a_named_error(self) -> None:
        request = bom.Request(
            targets=(bom.Target("widget", 1.0),),
            recipe_choices={"widget": "craft-gear"},
        )
        with pytest.raises(bom.BillOfMaterialsError, match="does not produce"):
            bom.compute(request)

    def test_environment_narrows_to_what_is_unlocked(self) -> None:
        environment = Environment(
            game_version="2.0.0",
            tick=0,
            force="player",
            recipes_enabled=("widget-a",),
        )
        request = bom.Request(
            targets=(bom.Target("widget", 1.0),), environment=environment
        )
        result = bom.compute(request)

        assert any(line.recipe == "widget-a" for line in result.lines)
        assert not any(a.subject == "widget" for a in result.ambiguities)

    def test_nothing_unlocked_is_a_named_error(self) -> None:
        environment = Environment(
            game_version="2.0.0", tick=0, force="player", recipes_enabled=()
        )
        request = bom.Request(
            targets=(bom.Target("widget", 1.0),), environment=environment
        )
        with pytest.raises(bom.BillOfMaterialsError, match="none currently unlocked"):
            bom.compute(request)


# --------------------------------------------------------------------------
# machine choice and effects
# --------------------------------------------------------------------------


class TestEffects:
    def test_speed_bonus_reduces_machine_count(self) -> None:
        request = bom.Request(targets=(bom.Target("gear", 5.0),))
        baseline = by_recipe(bom.compute(request), "craft-gear").machines

        boosted_request = bom.Request(
            targets=(bom.Target("gear", 5.0),), effects={"crafting": bom.Effects(speed=1.0)}
        )
        boosted = by_recipe(bom.compute(boosted_request), "craft-gear").machines
        assert boosted < baseline

    def test_consumption_bonus_changes_power(self) -> None:
        request = bom.Request(targets=(bom.Target("gear", 1.0),))
        baseline = by_recipe(bom.compute(request), "craft-gear").power

        request_with_modules = bom.Request(
            targets=(bom.Target("gear", 1.0),),
            effects={"crafting": bom.Effects(consumption=0.5)},
        )
        boosted = by_recipe(bom.compute(request_with_modules), "craft-gear").power
        assert boosted == pytest.approx(baseline * 1.5)

    def test_machine_override_is_used_without_being_reported(self) -> None:
        request = bom.Request(
            targets=(bom.Target("gear", 1.0),),
            machine_choices={"crafting": "assembler"},
        )
        result = bom.compute(request)
        assert by_recipe(result, "craft-gear").machine == "assembler"
        assert not any(a.kind == "machine" for a in result.ambiguities)


class TestNoTargets:
    def test_is_a_named_error(self) -> None:
        with pytest.raises(bom.BillOfMaterialsError, match="no targets"):
            bom.compute(bom.Request(targets=()))


# --------------------------------------------------------------------------
# what the solver may pick from, and what counts as raw
# --------------------------------------------------------------------------


class TestHiddenRecipes:
    """Space Age's recycling recipes are hidden, and "make" every component
    back out of whatever contains it; offered to the solver they blew the chain
    for a barrel up to 470 recipes."""

    def test_a_hidden_recipe_is_not_offered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recipes = dict(RECIPES)
        recipes["recycle-gear"] = {
            "name": "recycle-gear", "category": "crafting", "energy_required": 0.01, "hidden": True,
            "ingredients": [{"type": "item", "name": "ore", "amount": 1}],
            "results": [{"type": "item", "name": "plate", "amount": 100}],
        }
        monkeypatch.setattr(bom.recipe_data, "raw", recipes)
        result = bom.compute(bom.Request(targets=(bom.Target("gear", 1.0),)))
        assert not any(line.recipe == "recycle-gear" for line in result.lines)

    def test_unless_a_machine_runs_it_by_itself(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recipes = dict(RECIPES)
        recipes["reactor-plate"] = {
            "name": "reactor-plate", "category": "crafting", "energy_required": 1, "hidden": True,
            "ingredients": [], "results": [{"type": "item", "name": "plate", "amount": 1}],
        }
        entities = dict(ENTITIES)
        entities["reactor"] = {"name": "reactor", "type": "assembling-machine", "fixed_recipe": "reactor-plate",
                               "crafting_categories": ["crafting"], "crafting_speed": 1, "energy_usage": 1}
        monkeypatch.setattr(bom.recipe_data, "raw", recipes)
        monkeypatch.setattr(bom.entity_data, "raw", entities)
        assert "reactor-plate" in bom._build_result_index()["plate"]


class TestRawSources:
    def test_a_fluid_a_tile_offers_and_no_recipe_makes_is_raw(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(bom.tile_data, "raw", {"lava": {"fluid": "lava"}, "oil-sea": {"fluid": "output-a"}})
        raw = bom._mined_items()
        assert "lava" in raw
        # output-a is made by crack-oil, so a sea of it somewhere does not make it free.
        assert "output-a" not in raw

    def test_what_a_boiler_outputs_is_raw(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entities = dict(ENTITIES)
        entities["boiler"] = {"name": "boiler", "type": "boiler", "output_fluid_box": {"filter": "steam"}}
        monkeypatch.setattr(bom.entity_data, "raw", entities)
        assert "steam" in bom._mined_items()


class TestSolverUnderStrain:
    """Recipe amounts in a real chain run from 0.007 to 10 000. The first
    solver let its tableau drift on such a chain and declared a problem with
    non-negative costs unbounded -- which it cannot be. These programs are
    random, denser and nastier than any real chain, and have a known feasible
    point by construction."""

    @staticmethod
    def program(seed: int):
        import random

        rng = random.Random(seed)
        rows, columns = rng.randint(8, 30), rng.randint(10, 60)

        def magnitude() -> float:
            return 10 ** rng.uniform(-2.2, 4)

        matrix = [
            [(magnitude() * rng.choice((1, -1)) if rng.random() < 0.25 else 0.0) for _ in range(columns)]
            for _ in range(rows)
        ]
        known = [rng.random() * 3 for _ in range(columns)]
        made = [sum(a * x for a, x in zip(row, known)) for row in matrix]
        # Flip every row the known point makes negative, and never ask more
        # than it makes: the known point satisfies every constraint.
        matrix = [row if m >= 0 else [-a for a in row] for row, m in zip(matrix, made)]
        rhs = [abs(m) * rng.random() if rng.random() < 0.3 else 0.0 for m in made]
        cost = [magnitude() for _ in range(columns)]
        return cost, matrix, rhs, known

    def test_an_answer_is_never_wrong_and_nearly_every_program_is_answered(self) -> None:
        solved = 0
        for seed in range(300):
            cost, matrix, rhs, known = self.program(seed)
            try:
                solution = bom._simplex(cost, matrix, rhs)
            except bom.BillOfMaterialsError as exc:
                assert "unbounded" not in str(exc) and "no set of crafting rates" not in str(exc), (seed, exc)
                continue
            solved += 1
            for row, need in zip(matrix, rhs):
                assert sum(a * x for a, x in zip(row, solution)) >= need - 1e-6 * max(1.0, need), seed
            assert all(x >= 0 for x in solution), seed
            best_known = sum(c * x for c, x in zip(cost, known))
            assert sum(c * x for c, x in zip(cost, solution)) <= best_known * (1 + 1e-6) + 1e-9, seed
        # 298 when this was written. The previous solver answered 52, two of them
        # wrongly -- short of the demand, returned as if it were a bill.
        assert solved >= 285


class TestAsteroidChunks:
    def test_what_a_collector_gathers_is_raw(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(bom, "_asteroid_chunks", lambda: {
            "metallic-asteroid-chunk": {"minable": {"result": "metallic-asteroid-chunk"}},
            "parameter-0": {"minable": None},  # Space Age's placeholders have nothing to gather
        })
        assert "metallic-asteroid-chunk" in bom._mined_items()
