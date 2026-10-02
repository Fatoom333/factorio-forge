from factorio_forge.categories import crafts, primary_category, recipe_categories


class TestRecipeCategories:
    def test_a_2_0_recipe_states_one(self):
        assert recipe_categories({"category": "smelting"}) == ("smelting",)

    def test_a_2_1_recipe_states_a_list(self):
        recipe = {"categories": ["organic", "crafting"]}
        assert recipe_categories(recipe) == ("organic", "crafting")
        assert primary_category(recipe) == "organic"

    def test_a_recipe_stating_neither_is_crafting(self):
        assert recipe_categories({}) == ("crafting",)
        assert primary_category({"categories": []}) == "crafting"

    def test_a_machine_crafts_a_recipe_sharing_any_category(self):
        assembler = {"crafting_categories": ["crafting", "advanced-crafting"]}
        assert crafts(assembler, {"categories": ["organic", "crafting"]})
        assert crafts(assembler, {})
        assert not crafts(assembler, {"category": "oil-processing"})
        assert not crafts({}, {})
