"""Tests for surfaces: what each offers, what works there, and the bill that follows.

A small synthetic solar system rather than Space Age itself, so the rules are
checked exactly and without Space Age installed: a home planet with ore and
water, a hot planet with a geyser and lava and no water, and a platform in
space where only a collector works.
"""

from __future__ import annotations

import types

import pytest

from factorio_forge import bom, surface

PLANETS = {
    "home": {
        "surface_properties": {"day-night-cycle": 25200},
        "map_gen_settings": {"autoplace_settings": {
            "entity": {"settings": {"ore-patch": {}, "rock": {}}},
            "tile": {"settings": {"water-tile": {}, "grass": {}}},
        }},
    },
    "hot": {
        "surface_properties": {"pressure": 4000},
        "map_gen_settings": {"autoplace_settings": {
            "entity": {"settings": {"acid-geyser": {}}},
            "tile": {"settings": {"lava-tile": {}}},
        }},
    },
}
SURFACES = types.SimpleNamespace(
    raw={"platform": {"surface_properties": {"pressure": 0, "gravity": 0}}},
    properties={"pressure": {"default_value": 1000}, "gravity": {"default_value": 10}},
)
RESOURCES = {
    "ore-patch": {"category": "basic-solid", "minable": {"result": "ore"}},
    "acid-geyser": {"category": "basic-fluid", "minable": {"results": [{"name": "acid"}]}},
}
TILES = {
    "water-tile": {"fluid": "water"},
    "lava-tile": {"fluid": "lava"},
    "grass": {},
}
CHUNKS = {"rock-chunk": {"minable": {"result": "chunk"}}, "parameter-0": {"minable": None}}
ENTITIES = {
    "drill": {"name": "drill", "type": "mining-drill", "resource_categories": ["basic-solid"]},
    "pumpjack": {"name": "pumpjack", "type": "mining-drill", "resource_categories": ["basic-fluid"]},
    "pump": {"name": "pump", "type": "offshore-pump"},
    "collector": {"name": "collector", "type": "asteroid-collector",
                  "surface_conditions": [{"property": "pressure", "max": 0}]},
    "boiler": {"name": "boiler", "type": "boiler", "fluid_box": {"filter": "water"},
               "output_fluid_box": {"filter": "steam"}, "surface_conditions": [{"property": "pressure", "min": 10}]},
    "assembler": {"name": "assembler", "type": "assembling-machine", "crafting_categories": ["crafting"],
                  "crafting_speed": 1, "energy_usage": 1000},
    "foundry": {"name": "foundry", "type": "assembling-machine", "crafting_categories": ["metallurgy"],
                "crafting_speed": 1, "energy_usage": 1000},
    "chem-plant": {"name": "chem-plant", "type": "assembling-machine", "crafting_categories": ["chemistry"],
                   "crafting_speed": 1, "energy_usage": 1000, "surface_conditions": [{"property": "pressure", "min": 1}]},
}
RECIPES = {
    "smelt": {"name": "smelt", "category": "crafting", "energy_required": 1,
              "ingredients": [{"type": "item", "name": "ore", "amount": 1}],
              "results": [{"type": "item", "name": "plate", "amount": 1}]},
    # Faster per craft, but needs something only the hot planet has.
    "cast": {"name": "cast", "category": "crafting", "energy_required": 0.1,
             "ingredients": [{"type": "fluid", "name": "lava", "amount": 1}],
             "results": [{"type": "item", "name": "plate", "amount": 1}]},
    "make-acid": {"name": "make-acid", "category": "chemistry", "energy_required": 1,
                  "ingredients": [{"type": "fluid", "name": "water", "amount": 1}],
                  "results": [{"type": "fluid", "name": "acid", "amount": 1}]},
    "hot-only": {"name": "hot-only", "category": "metallurgy", "energy_required": 1,
                 "surface_conditions": [{"property": "pressure", "min": 4000, "max": 4000}],
                 "ingredients": [{"type": "item", "name": "plate", "amount": 1}],
                 "results": [{"type": "item", "name": "alloy", "amount": 1}]},
}


@pytest.fixture(autouse=True)
def solar_system(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(surface.planet_data, "raw", PLANETS)
    monkeypatch.setattr(surface, "_surface_data", lambda: SURFACES)
    monkeypatch.setattr(surface.resource_data, "raw", RESOURCES)
    monkeypatch.setattr(surface.tile_data, "raw", TILES)
    monkeypatch.setattr(surface.entity_data, "raw", ENTITIES)
    monkeypatch.setattr(surface, "_asteroid_chunks", lambda: CHUNKS)
    monkeypatch.setattr(bom, "_asteroid_chunks", lambda: CHUNKS)
    monkeypatch.setattr(bom.recipe_data, "raw", RECIPES)
    monkeypatch.setattr(bom.item_data, "raw", {"ore": {}, "plate": {}, "alloy": {}, "chunk": {}})


class TestWhatASurfaceIs:
    def test_every_planet_and_surface_is_known(self) -> None:
        assert surface.names() == ["home", "hot", "platform"]

    def test_unstated_properties_take_their_defaults(self) -> None:
        assert surface.properties("home")["pressure"] == 1000
        assert surface.properties("hot")["pressure"] == 4000
        assert surface.properties("platform")["pressure"] == 0

    def test_conditions_are_checked_against_them(self) -> None:
        needs_air = [{"property": "pressure", "min": 10}]
        assert surface.allows(needs_air, "home")
        assert not surface.allows(needs_air, "platform")
        assert surface.allows(None, "platform")


class TestWhatASurfaceOffers:
    def test_home_mines_its_ore_pumps_its_water_and_boils_steam(self) -> None:
        offered = surface.sources("home")
        assert {k: v.how for k, v in offered.items()} == {"ore": "mined", "water": "pumped", "steam": "boiled"}

    def test_hot_has_its_geyser_and_lava_but_no_water_so_no_steam(self) -> None:
        assert set(surface.sources("hot")) == {"acid", "lava"}

    def test_chunks_are_collected_only_where_a_collector_works(self) -> None:
        assert set(surface.sources("platform")) == {"chunk"}
        assert "chunk" not in surface.sources("home")

    def test_where_something_is_offered(self) -> None:
        assert surface.where_offered("acid") == ["hot"]


class TestTheBillOnASurface:
    def test_without_a_surface_everything_minable_is_free(self) -> None:
        result = bom.compute(bom.Request(targets=(bom.Target("acid", 1.0),)))
        assert result.lines == []  # the geyser, somewhere, is enough
        assert result.raw_materials == {"acid": 1.0}

    def test_on_home_acid_is_made_not_mined(self) -> None:
        result = bom.compute(bom.Request(targets=(bom.Target("acid", 1.0),), surface="home"))
        assert [line.recipe for line in result.lines] == ["make-acid"]
        assert result.raw_materials == {"water": 1.0}

    def test_a_cheaper_route_through_an_import_loses_to_a_local_one(self) -> None:
        # Casting is ten times faster, but lava is not on home: smelting wins.
        result = bom.compute(bom.Request(targets=(bom.Target("plate", 1.0),), surface="home"))
        assert [line.recipe for line in result.lines] == ["smelt"]
        assert result.from_elsewhere == {}

    def test_on_hot_the_local_route_is_the_other_one(self) -> None:
        result = bom.compute(bom.Request(targets=(bom.Target("plate", 1.0),), surface="hot"))
        assert [line.recipe for line in result.lines] == ["cast"]

    def test_a_named_boundary_supply_is_not_an_import(self) -> None:
        result = bom.compute(bom.Request(
            targets=(bom.Target("plate", 1.0),), surface="home", boundary=frozenset({"lava"}),
        ))
        assert [line.recipe for line in result.lines] == ["cast"]
        assert result.from_elsewhere == {}

    def test_what_must_come_from_elsewhere_says_where_it_is(self) -> None:
        result = bom.compute(bom.Request(targets=(bom.Target("plate", 1.0),), surface="platform"))
        assert "ore" in result.from_elsewhere or "lava" in result.from_elsewhere
        for item, where in result.from_elsewhere.items():
            assert where["offered_on"] == surface.where_offered(item)

    def test_a_recipe_whose_conditions_fail_is_not_used(self) -> None:
        result = bom.compute(bom.Request(targets=(bom.Target("alloy", 1.0),), surface="home"))
        assert result.lines == []
        assert result.from_elsewhere["alloy"]["made_on"] == ["hot"]

    def test_a_recipe_without_a_working_machine_is_not_used(self) -> None:
        # The chemical plant needs pressure; on the platform acid cannot be made.
        result = bom.compute(bom.Request(targets=(bom.Target("acid", 1.0),), surface="platform"))
        assert result.lines == []
        assert result.from_elsewhere["acid"]["offered_on"] == ["hot"]

    def test_an_unknown_surface_is_refused_with_the_known_ones(self) -> None:
        with pytest.raises(bom.BillOfMaterialsError, match="home, hot, platform"):
            bom.compute(bom.Request(targets=(bom.Target("plate", 1.0),), surface="moon"))


class TestTheRequestAsksForASurface:
    def test_several_surfaces_and_none_chosen_is_a_question(self) -> None:
        from factorio_forge import request

        spec = request.parse({"targets": [{"item": "plate", "per_second": 1}], "plot": {}, "style": ["x"]})
        result = request.review(spec, None, "no export")
        assert any("Which planet or surface" in q for q in result.questions)
        assert result.surface is None

    def test_an_import_becomes_a_question_naming_where_it_is(self) -> None:
        from factorio_forge import request

        spec = request.parse({"targets": [{"item": "alloy", "per_second": 1}], "surface": "home"})
        result = request.review(spec, None, "no export")
        assert any("alloy cannot be had on home" in q and "made on hot" in q for q in result.questions)

    def test_an_unknown_surface_is_a_problem(self) -> None:
        from factorio_forge import request

        spec = request.parse({"targets": [{"item": "plate", "per_second": 1}], "surface": "moon"})
        result = request.review(spec, None, "no export")
        assert any("moon" in p for p in result.problems)
