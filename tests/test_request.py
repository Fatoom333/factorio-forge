"""Tests for step 1: bonuses from the game, the player's words, the request review.

The arithmetic and the matching use small synthetic worlds; the review itself
runs against the active profile, with the export made up to say that
everything, or nothing, is unlocked.
"""

from __future__ import annotations

import json
from pathlib import Path

import draftsman.data.items as item_data
import pytest
from draftsman.data import recipes as real_recipes

import prototypes
from factorio_forge import bom, environment, layout, names, paths, request
from factorio_forge.environment import Bonuses, Environment, read_environment
from factorio_forge.save import ModRef


# --------------------------------------------------------------------------
# the export
# --------------------------------------------------------------------------


def write_export(folder: Path, payload: dict) -> None:
    target = folder / "factorio-forge" / "environment.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


BASE_EXPORT = {"game_version": "2.0.77", "tick": 5, "force": "player", "mods": {"base": "2.0.77"}}


class TestBonusesInTheExport:
    def test_reads_bonuses(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(paths, "script_output_dir", lambda: tmp_path)
        write_export(tmp_path, {**BASE_EXPORT, "bonuses": {
            "force": {"inserter_stack_size_bonus": 1, "bulk_inserter_capacity_bonus": 4},
            "recipe_productivity": {"steel-plate": 0.2},
            "ammo_damage": {"bullet": 1.45},
            "gun_speed": [],  # the game's encoder writes an empty table as a list
            "turret_attack": {},
        }})
        found = read_environment()
        assert found.bonuses.get("bulk_inserter_capacity_bonus") == 4
        assert found.bonuses.recipe_productivity == {"steel-plate": 0.2}
        assert found.bonuses.gun_speed == {}

    def test_an_older_export_has_no_bonuses_rather_than_zero_ones(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(paths, "script_output_dir", lambda: tmp_path)
        write_export(tmp_path, BASE_EXPORT)
        assert read_environment().bonuses is None

    def test_the_companion_mod_does_not_break_a_mod_set_match(self) -> None:
        found = Environment("2.0.77", 1, "player", mods={"base": "2.0.77", "factorio-forge-companion": "0.7.0"})
        assert found.matches_mods((ModRef("base", (2, 0, 77)),))


class TestHandSize:
    ENTITIES = {
        "arm": {"type": "inserter", "rotation_speed": 0.04, "pickup_position": [0, -1], "insert_position": [0, 1.2]},
        "bulk-arm": {"type": "inserter", "bulk": True, "rotation_speed": 0.04,
                     "pickup_position": [0, -1], "insert_position": [0, 1.2]},
        "gifted-arm": {"type": "inserter", "stack_size_bonus": 2, "rotation_speed": 0.04,
                       "pickup_position": [0, -1], "insert_position": [0, 1.2]},
    }

    @pytest.fixture(autouse=True)
    def data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(layout.entity_data, "raw", self.ENTITIES)

    def test_ordinary_and_bulk_read_different_bonuses(self) -> None:
        bonuses = Bonuses(force={"inserter_stack_size_bonus": 1, "bulk_inserter_capacity_bonus": 4})
        assert layout.inserter_hand_size("arm", bonuses) == 2
        assert layout.inserter_hand_size("bulk-arm", bonuses) == 5

    def test_a_prototype_bonus_adds_to_research(self) -> None:
        assert layout.inserter_hand_size("gifted-arm", Bonuses(force={"inserter_stack_size_bonus": 1})) == 4


# --------------------------------------------------------------------------
# the bill of materials, against what the player has
# --------------------------------------------------------------------------

RECIPES = {
    "craft-gear": {
        "name": "craft-gear", "category": "crafting", "energy_required": 1, "allow_productivity": True,
        "ingredients": [{"type": "item", "name": "plate", "amount": 2}],
        "results": [{"type": "item", "name": "gear", "amount": 1}],
    },
    "make-assembler": {
        "name": "make-assembler", "category": "crafting", "energy_required": 1,
        "ingredients": [{"type": "item", "name": "gear", "amount": 5}],
        "results": [{"type": "item", "name": "assembler", "amount": 1}],
    },
    "make-super": {
        "name": "make-super", "category": "crafting", "energy_required": 1,
        "ingredients": [{"type": "item", "name": "gear", "amount": 50}],
        "results": [{"type": "item", "name": "super-assembler", "amount": 1}],
    },
}
ENTITIES = {
    "assembler": {"name": "assembler", "type": "assembling-machine", "crafting_categories": ["crafting"],
                  "crafting_speed": 1, "energy_usage": 1000},
    "super-assembler": {"name": "super-assembler", "type": "assembling-machine",
                        "crafting_categories": ["crafting"], "crafting_speed": 10, "energy_usage": 1000},
}
ITEMS = {
    "assembler": {"place_result": "assembler"},
    "super-assembler": {"place_result": "super-assembler"},
    "plate": {}, "gear": {},
}


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bom.recipe_data, "raw", RECIPES)
    monkeypatch.setattr(bom.entity_data, "raw", ENTITIES)
    monkeypatch.setattr(item_data, "raw", ITEMS)


BURNER = {"energy_source": {"type": "burner", "fuel_categories": ["snacks"]}}


@pytest.fixture
def hungry(world: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fastest machine of all burns fuel, the way Space Age's biochamber eats nutrients."""
    monkeypatch.setattr(bom.recipe_data, "raw", {**RECIPES, "make-hungry": {
        "name": "make-hungry", "category": "crafting", "energy_required": 1,
        "ingredients": [{"type": "item", "name": "gear", "amount": 50}],
        "results": [{"type": "item", "name": "hungry-assembler", "amount": 1}],
    }})
    monkeypatch.setattr(bom.entity_data, "raw", {**ENTITIES, "hungry-assembler": {
        "name": "hungry-assembler", "type": "assembling-machine", "crafting_categories": ["crafting"],
        "crafting_speed": 20, "energy_usage": 1000, **BURNER,
    }})
    monkeypatch.setattr(item_data, "raw", {
        **ITEMS, "hungry-assembler": {"place_result": "hungry-assembler"}, "snack-bar": {"fuel_category": "snacks"},
    })


class TestBillAgainstTheGame:
    def test_without_an_export_the_most_basic_machine_is_taken(self, world: None) -> None:
        # The fastest in the mod set is usually late-game; nothing says the
        # player owns it, so the slowest is assumed and the choice reported.
        result = bom.compute(bom.Request(targets=(bom.Target("gear", 1.0),), boundary=frozenset({"plate"})))
        assert result.lines[0].machine == "assembler"
        assert any(a.kind == "machine" and "most basic" in a.detail for a in result.ambiguities)

    def test_a_tie_goes_to_the_machine_that_needs_least(self, world: None, monkeypatch: pytest.MonkeyPatch) -> None:
        entities = {**ENTITIES, "a-burner": {**ENTITIES["assembler"], "name": "a-burner", **BURNER}}
        monkeypatch.setattr(bom.entity_data, "raw", entities)
        result = bom.compute(bom.Request(targets=(bom.Target("gear", 1.0),), boundary=frozenset({"plate"})))
        assert result.lines[0].machine == "assembler"
        assert result.machine_needs == {}

    def test_a_fuelled_machine_says_what_it_burns(self, hungry: None) -> None:
        found = Environment("2.0", 1, "player", recipes_enabled=tuple(RECIPES) + ("make-hungry",))
        result = bom.compute(bom.Request(
            targets=(bom.Target("gear", 1.0),), boundary=frozenset({"plate"}), environment=found,
        ))
        assert result.lines[0].machine == "hungry-assembler"
        (needs,) = result.machine_needs["hungry-assembler"]
        assert "snacks" in needs and "snack-bar" in needs

    def test_a_pinned_machine_is_used_and_its_needs_still_reported(self, hungry: None) -> None:
        result = bom.compute(bom.Request(
            targets=(bom.Target("gear", 1.0),), boundary=frozenset({"plate"}),
            machine_choices={"crafting": "hungry-assembler"},
        ))
        assert result.lines[0].machine == "hungry-assembler"
        assert not any(a.kind == "machine" for a in result.ambiguities)
        assert "hungry-assembler" in result.machine_needs

    def test_review_asks_about_faster_machines_without_an_export(self, hungry: None) -> None:
        spec = request.parse({
            "targets": [{"item": "gear", "per_second": 1}], "boundary": ["plate"], "plot": {}, "style": ["x"],
        })
        result = request.review(spec, None, "no export")
        (asked,) = [q for q in result.questions if q.startswith("Which machine for crafting")]
        assert "assembler (speed 1)" in asked
        assert "super-assembler" in asked and "hungry-assembler" in asked and "snacks" in asked
        assert not any(a.startswith("machine for") for a in result.assumptions)

    def test_review_asks_how_a_pinned_burner_is_fed(self, hungry: None) -> None:
        spec = request.parse({
            "targets": [{"item": "gear", "per_second": 1}], "boundary": ["plate"],
            "machine_choices": {"crafting": "hungry-assembler"}, "plot": {}, "style": ["x"],
        })
        result = request.review(spec, None, "no export")
        assert any(q.startswith("hungry-assembler needs more than power") for q in result.questions)

    def test_review_asks_per_category_even_when_one_machine_serves_both(
        self, world: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # machine_choices is keyed by category: one question for the shared
        # machine would leave the other category's answer unasked.
        monkeypatch.setattr(bom.recipe_data, "raw", {**RECIPES, "craft-widget": {
            "name": "craft-widget", "category": "advanced", "energy_required": 1,
            "ingredients": [{"type": "item", "name": "gear", "amount": 2}],
            "results": [{"type": "item", "name": "widget", "amount": 1}],
        }})
        both = ["crafting", "advanced"]
        monkeypatch.setattr(bom.entity_data, "raw", {
            name: {**entity, "crafting_categories": both} for name, entity in ENTITIES.items()
        })
        monkeypatch.setattr(item_data, "raw", {**ITEMS, "widget": {}})
        spec = request.parse({
            "targets": [{"item": "widget", "per_second": 1}], "boundary": ["plate"], "plot": {}, "style": ["x"],
        })
        result = request.review(spec, None, "no export")
        asked = sorted(q.split("?")[0] for q in result.questions if q.startswith("Which machine for"))
        assert asked == ["Which machine for advanced", "Which machine for crafting"]

    def test_review_rejects_a_pinned_machine_that_cannot_work_on_the_surface(
        self, world: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bom.entity_data, "raw", {**ENTITIES, "fussy-assembler": {
            **ENTITIES["assembler"], "name": "fussy-assembler",
            "surface_conditions": [{"property": "pressure", "min": 10}],
        }})
        # One made-up surface where every surface condition fails.
        monkeypatch.setattr(request.surfaces, "names", lambda: ["rock"])
        monkeypatch.setattr(request.surfaces, "exists", lambda name: name == "rock")
        monkeypatch.setattr(request.surfaces, "sources", lambda name: {})
        monkeypatch.setattr(request.surfaces, "allows", lambda conditions, name: not conditions)
        monkeypatch.setattr(request.surfaces, "requires_heating", lambda name: False)
        spec = request.parse({
            "targets": [{"item": "gear", "per_second": 1}], "boundary": ["plate"], "surface": "rock",
            "machine_choices": {"crafting": "fussy-assembler"}, "plot": {}, "style": ["x"],
        })
        result = request.review(spec, None, "no export")
        assert any(p.startswith("fussy-assembler needs more than power") for p in result.problems)
        assert not result.ready
        assert not any("fussy-assembler needs more than power" in q for q in result.questions)

    def test_with_an_export_the_fastest_machine_the_player_can_build(self, world: None) -> None:
        found = Environment("2.0", 1, "player", recipes_enabled=("craft-gear", "make-assembler"))
        result = bom.compute(bom.Request(
            targets=(bom.Target("gear", 1.0),), boundary=frozenset({"plate"}), environment=found,
        ))
        assert result.lines[0].machine == "assembler"

    def test_nothing_buildable_is_said_not_hidden(self, world: None) -> None:
        found = Environment("2.0", 1, "player", recipes_enabled=("craft-gear",))
        result = bom.compute(bom.Request(
            targets=(bom.Target("gear", 1.0),), boundary=frozenset({"plate"}), environment=found,
        ))
        assert any("none of these can be built" in a.detail for a in result.ambiguities)

    def test_researched_recipe_productivity_saves_ingredients(self, world: None) -> None:
        plain = bom.compute(bom.Request(targets=(bom.Target("gear", 1.0),), boundary=frozenset({"plate"})))
        found = Environment(
            "2.0", 1, "player", recipes_enabled=tuple(RECIPES),
            bonuses=Bonuses(recipe_productivity={"craft-gear": 0.5}),
        )
        researched = bom.compute(bom.Request(
            targets=(bom.Target("gear", 1.0),), boundary=frozenset({"plate"}), environment=found,
        ))
        assert researched.raw_materials["plate"] == pytest.approx(plain.raw_materials["plate"] / 1.5)

    def test_productivity_is_capped_by_the_recipe(self, world: None, monkeypatch: pytest.MonkeyPatch) -> None:
        capped = {k: dict(v) for k, v in RECIPES.items()}
        capped["craft-gear"]["maximum_productivity"] = 0.1
        monkeypatch.setattr(bom.recipe_data, "raw", capped)
        found = Environment("2.0", 1, "player", bonuses=Bonuses(recipe_productivity={"craft-gear": 0.5}))
        assert bom.Request(targets=(), environment=found).productivity_for("craft-gear") == pytest.approx(0.1)


class TestRunningNeeds:
    """What a machine needs besides power, read from the prototype alone."""

    @pytest.fixture(autouse=True)
    def machines(self, monkeypatch: pytest.MonkeyPatch) -> None:
        base = {"type": "assembling-machine", "crafting_categories": ["crafting"], "crafting_speed": 1}
        monkeypatch.setattr(bom.entity_data, "raw", {
            "plain": {**base, "energy_source": {"type": "electric"}, "heating_energy": "100kW"},
            "free": {**base, "energy_source": {"type": "void"}},
            "spent": {**base, "energy_source": {"type": "burner", "fuel_categories": ["rods"], "burnt_inventory_size": 1}},
            "oily": {**base, "energy_source": {"type": "fluid", "burns_fluid": True}},
            "piped": {**base, "energy_source": {"type": "fluid", "fluid_box": {"filter": "steam"}}},
            "hot": {**base, "energy_source": {"type": "heat", "min_working_temperature": 500}},
            "fussy": {**base, "energy_source": {"type": "electric"},
                      "surface_conditions": [{"property": "pressure", "min": 10}]},
        })
        monkeypatch.setattr(item_data, "raw", {"rod": {"fuel_category": "rods"}})

    def test_an_electric_or_void_machine_needs_nothing(self) -> None:
        assert bom.running_needs("plain") == []
        assert bom.running_needs("free") == []

    def test_a_burner_names_its_fuel_and_its_spent_fuel(self) -> None:
        (need,) = bom.running_needs("spent")
        assert "rods" in need and "rod" in need and "spent fuel" in need

    def test_fluid_and_heat_sources(self) -> None:
        assert "fluid fuel" in bom.running_needs("oily")[0]
        assert "steam" in bom.running_needs("piped")[0]
        assert "500" in bom.running_needs("hot")[0]

    def test_surface_conditions_while_the_surface_is_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert "pressure" in bom.running_needs("fussy")[0]
        monkeypatch.setattr(bom.surface_rules, "allows", lambda conditions, surface: True)
        monkeypatch.setattr(bom.surface_rules, "requires_heating", lambda surface: False)
        assert bom.running_needs("fussy", "somewhere") == []

    def test_heating_only_where_the_surface_freezes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(bom.surface_rules, "requires_heating", lambda surface: surface == "cold")
        assert bom.running_needs("plain", "warm") == []
        assert "heating" in bom.running_needs("plain", "cold")[0]


# --------------------------------------------------------------------------
# the player's words
# --------------------------------------------------------------------------


class TestLocale:
    def test_parses_sections_and_keys(self) -> None:
        into: dict[str, str] = {}
        names.parse_cfg("; comment\n[item-name]\nplate=Пластина\n\n[recipe-name]\ncrush=Измельчить __1__\n", into)
        assert into == {"item-name.plate": "Пластина", "recipe-name.crush": "Измельчить __1__"}

    def test_resolves_parameters(self) -> None:
        locale = names.Locale({"ru": {"recipe-name.crush": "Измельчить __1__", "item-name.plate": "Пластина"}})
        assert names.resolve(["recipe-name.crush", ["item-name.plate"]], "ru", locale) == "Измельчить Пластина"


class TestFind:
    @pytest.fixture(autouse=True)
    def words(self, monkeypatch: pytest.MonkeyPatch) -> None:
        locale = names.Locale({
            "ru": {"item-name.red-card": "Автоматизационная технологическая карта",
                   "item-name.plate": "Железная плита", "entity-name.fast-belt": "Быстрый конвейер"},
            "en": {"item-name.red-card": "Automation tech card", "item-name.plate": "Iron plate",
                   "entity-name.fast-belt": "Fast transport belt"},
        })
        monkeypatch.setattr(names, "load_locale", lambda languages=("ru", "en"), with_mods=True: locale)
        monkeypatch.setattr(names.item_data, "raw", {"red-card": {}, "plate": {}, "fast-belt": {"place_result": "fast-belt"}})
        monkeypatch.setattr(names.fluid_data, "raw", {})
        monkeypatch.setattr(names.recipe_data, "raw", {"red-card": {"results": [{"name": "red-card"}]}})
        monkeypatch.setattr(names.entity_data, "raw", {"fast-belt": {"type": "transport-belt"}})

    def test_finds_by_the_translated_name_with_other_word_endings(self) -> None:
        found = names.find("технологические карты автоматизации")
        assert found[0].name == "red-card"
        assert set(found[0].kinds) == {"item", "recipe"}

    def test_finds_by_the_internal_name(self) -> None:
        assert names.find("fast belt")[0].name == "fast-belt"

    def test_word_endings_meet(self) -> None:
        assert names._stem("синие") == names._stem("синяя") == names._stem("синий")
        assert names._stem("колбы") == names._stem("колба")
        assert names._stem("circuits") == "circuit"

    def test_says_what_is_unlocked(self) -> None:
        found = Environment("2.0", 1, "player", recipes_enabled=("red-card",))
        by_name = {c.name: c.unlocked for c in names.find("карта", environment=found)}
        assert by_name["red-card"] is True
        assert names.is_unlocked("item", "plate", found) is False
        assert names.is_unlocked("item", "plate", found, raw=frozenset({"plate"})) is True


class TestSlangAndBaseGameNames:
    """A mod that renames things, and players who do not use the names at all."""

    @pytest.fixture(autouse=True)
    def renamed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        modded = names.Locale({
            "ru": {"item-name.automation-science-pack": "Автоматизационный исследовательский пакет",
                   "item-name.kr-card": "Автоматизационная технологическая карта",
                   "item-name.processing-unit": "Процессор",
                   "item-name.electronic-circuit": "Электросхема"},
            "en": {},
        })
        vanilla = names.Locale({
            "ru": {"item-name.automation-science-pack": "Автоматизационный исследовательский пакет",
                   "item-name.processing-unit": "Процессор",
                   "item-name.electronic-circuit": "Электросхема"},
            "en": {},
        })
        monkeypatch.setattr(
            names, "load_locale", lambda languages=("ru", "en"), with_mods=True: modded if with_mods else vanilla
        )
        monkeypatch.setattr(names.item_data, "raw", {
            # The mod keeps the internal name and points the shown name elsewhere.
            "automation-science-pack": {"localised_name": ["item-name.kr-card"]},
            "processing-unit": {}, "electronic-circuit": {},
        })
        monkeypatch.setattr(names.fluid_data, "raw", {})
        monkeypatch.setattr(names.recipe_data, "raw", {})
        monkeypatch.setattr(names.entity_data, "raw", {})

    def test_slang_finds_what_no_name_contains(self) -> None:
        found = names.find("красные колбы")
        assert found[0].name == "automation-science-pack"
        assert found[0].matched == "slang"

    def test_the_specific_phrase_beats_the_vague_one(self) -> None:
        found = names.find("нужны синие схемы")
        assert found[0].name == "processing-unit"

    def test_a_vague_term_offers_its_options(self) -> None:
        found = names.find("схемы")
        assert {c.name for c in found} >= {"processing-unit", "electronic-circuit"}
        assert all("which" in c.via for c in found if c.matched == "slang")

    def test_the_base_game_name_finds_a_renamed_thing_and_says_so(self) -> None:
        found = names.find("автоматизационный исследовательский пакет")
        assert found[0].name == "automation-science-pack"
        assert found[0].matched == "vanilla"
        assert "Автоматизационная технологическая карта" in found[0].via
        assert "which" not in found[0].via

    def test_slang_for_something_the_mod_set_lacks_is_reported(self) -> None:
        assert names.find("rcu") == []
        assert names.absent_slang("rcu")[0][1] == "rocket-control-unit"


def test_every_slang_name_is_a_real_base_game_name() -> None:
    """The table must not point at names that never existed.

    Checked against the game's own locale files, which name every prototype of
    the base game and its expansions whether or not a profile is active. Terms
    for things 2.0 removed say so in their note.
    """
    data_dir = paths.factorio_data_dir()
    if data_dir is None:
        pytest.skip("no Factorio installation to read the base game's names from")
    known: dict[str, str] = {}
    for package in data_dir.iterdir():
        folder = package / "locale" / "en"
        if folder.is_dir():
            for cfg in folder.glob("*.cfg"):
                names.parse_cfg(cfg.read_text(encoding="utf-8", errors="replace"), known)
    from factorio_forge import slang

    unknown = [
        name
        for term in slang.TERMS
        for name in term.names
        if not any(f"{section}.{name}" in known for section in ("item-name", "entity-name", "fluid-name"))
        and "removed" not in term.note
    ]
    assert unknown == []


# --------------------------------------------------------------------------
# the request
# --------------------------------------------------------------------------


class TestParse:
    def test_per_minute_becomes_per_second(self) -> None:
        spec = request.parse({"targets": [{"item": "x", "per_minute": 90}]})
        assert spec.targets == [("x", 1.5)]

    @pytest.mark.parametrize("data", [
        {"targets": []},
        {"targets": [{"item": "x"}]},
        {"targets": [{"item": "x", "per_second": 1}], "colour": "red"},
        {"targets": [{"item": "x", "per_second": 1}], "tiers": {"rail": "x"}},
    ])
    def test_malformed_requests_are_refused(self, data: dict) -> None:
        with pytest.raises(request.RequestError):
            request.parse(data)


def _product(recipe: str) -> str:
    return next(r["name"] for r in real_recipes.raw[recipe]["results"] if r.get("type") != "fluid")


class TestReview:
    def test_everything_unlocked_fills_tiers_and_runs_the_bill(self) -> None:
        # The ingredients arrive from outside, so the bill is this one recipe:
        # the test is about the review, not about whichever chain the first
        # recipe of this shape happens to pull in under a given mod set.
        recipe, _ = prototypes.crafting_setup(1, 0, 1, 0)
        found = Environment("2.0", 1, "player", recipes_enabled=tuple(real_recipes.raw), bonuses=Bonuses())
        boundary = [i["name"] for i in real_recipes.raw[recipe]["ingredients"]]
        spec = request.parse({
            "targets": [{"item": _product(recipe), "per_second": 1}],
            "boundary": boundary, "plot": {}, "style": ["x"],
        })
        result = request.review(spec, found, "made up for the test")
        assert result.problems == []
        assert set(result.tiers) >= {"belt", "inserter", "pole"}
        assert result.bill is not None and result.bill.lines

    def test_a_locked_target_is_a_problem(self) -> None:
        recipe, _ = prototypes.crafting_setup(1, 0, 1, 0)
        found = Environment("2.0", 1, "player", recipes_enabled=(), bonuses=Bonuses())
        spec = request.parse({"targets": [{"item": _product(recipe), "per_second": 1}]})
        result = request.review(spec, found, "made up")
        assert any("not unlocked" in p for p in result.problems)

    def test_a_misspelt_target_gets_suggestions(self) -> None:
        spec = request.parse({"targets": [{"item": prototypes.belt() + "x", "per_second": 1}]})
        result = request.review(spec, None, "no export")
        assert any("did you mean" in p for p in result.problems)

    def test_without_an_export_tiers_become_questions(self) -> None:
        recipe, _ = prototypes.crafting_setup(1, 0, 1, 0)
        spec = request.parse({"targets": [{"item": _product(recipe), "per_second": 1}]})
        result = request.review(spec, None, "no export")
        assert any("Which belt" in q for q in result.questions)
        assert any("How much room" in q for q in result.questions)
