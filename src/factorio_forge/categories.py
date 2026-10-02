"""A recipe's crafting categories, in either form the data states them.

Factorio 2.0 gives a recipe one `category`; 2.1 replaced it with a list,
`categories`, and a recipe may name more than one (`yumako-processing` is
both `organic` and `crafting` there). A recipe that states
neither is `crafting`. Draftsman passes the prototype through as written, so
which key is present depends on the game version the data was extracted
from, and every reader goes through here instead of `recipe["category"]`.
"""

from __future__ import annotations

from typing import Any


def recipe_categories(recipe: dict[str, Any]) -> tuple[str, ...]:
    """Every category the recipe belongs to, `("crafting",)` if it states none."""
    listed = recipe.get("categories")
    if listed:
        return tuple(listed)
    return (recipe.get("category") or "crafting",)


def primary_category(recipe: dict[str, Any]) -> str:
    """The recipe's first category: the one to name it by and key choices on."""
    return recipe_categories(recipe)[0]


def crafts(machine: dict[str, Any], recipe: dict[str, Any]) -> bool:
    """Whether the machine's `crafting_categories` reach any of the recipe's."""
    offered = machine.get("crafting_categories") or ()
    return any(category in offered for category in recipe_categories(recipe))
