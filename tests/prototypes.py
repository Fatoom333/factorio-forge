"""Prototypes chosen by what they do, not by what they are called.

The tool promises to work with whatever mod set a player has, so its tests
should not insist on vanilla names. Draftsman keeps prototype data in one fixed
directory holding whichever profile was activated last, and that is what the
suite runs against: it asks the active data for a belt, for an inserter that
filters, for a pipe that goes underground, rather than for ``stack-inserter``.

Under Krastorio 2 the old tests failed with ``'Entity' object has no attribute
'set_item_filter'``, which says nothing about the real cause -- that mod set has
no ``stack-inserter``, and draftsman hands back a plain entity for a name it
does not know. Where the active data genuinely has nothing suitable, these
helpers skip and say so in words. A skip that names its reason is information;
an AttributeError from inside a library is not.
"""

from __future__ import annotations

import pytest

from draftsman.data import entities, items, recipes

from factorio_forge.categories import crafts, recipe_categories


def _buildable() -> set[str]:
    """Entities some item places, which is what a player can actually build.

    The editor's chests and interfaces are containers by type and behave like
    nothing in a real base. No item places them, and that is the difference
    that matters: a test which quietly picked one would be testing something
    the tool will never be handed.
    """
    return {
        data["place_result"]
        for data in items.raw.values()
        if isinstance(data, dict) and data.get("place_result")
    }


def _first(source: dict, matches, buildable_only: bool = True) -> str | None:
    """The first name that matches, in a fixed order so runs agree."""
    placeable = _buildable() if buildable_only else None
    for name in sorted(source):
        if placeable is not None and name not in placeable:
            continue
        if matches(name, source[name]):
            return name
    return None


def prototype(kind: str, *, requires: tuple[str, ...] = (), skip: str | None = None) -> str:
    """A prototype of this type from the active data, or a skip explaining why not."""

    def matches(_name, data) -> bool:
        if data.get("type") != kind:
            return False
        return all(data.get(key) for key in requires)

    found = _first(entities.raw, matches)
    if found is None:
        wanted = f"{kind}" + (f" with {', '.join(requires)}" if requires else "")
        pytest.skip(skip or f"the active game data has no {wanted}")
    return found


def another(kind: str, besides: str) -> str:
    """A second prototype of the same type, for tests that need two."""
    found = _first(
        entities.raw,
        lambda name, data: data.get("type") == kind and name != besides,
    )
    if found is None:
        pytest.skip(f"the active game data has only one {kind}")
    return found


# --- the kinds the tests ask for ------------------------------------------


def belt() -> str:
    return prototype("transport-belt")


def underground_belt() -> str:
    return prototype("underground-belt", requires=("max_distance",))


def splitter() -> str:
    return prototype("splitter")


def belt_set() -> tuple[str, str, str]:
    """A buildable belt with an underground belt and a splitter of the same speed."""
    placeable = _buildable()

    def of_speed(kind: str, speed) -> list[str]:
        return sorted(
            n for n, d in entities.raw.items()
            if d.get("type") == kind and d.get("speed") == speed and n in placeable
            and (kind != "underground-belt" or d.get("max_distance"))
        )

    for name in sorted(entities.raw):
        data = entities.raw[name]
        if data.get("type") != "transport-belt" or name not in placeable or not data.get("speed"):
            continue
        undergrounds, splitters = of_speed("underground-belt", data["speed"]), of_speed("splitter", data["speed"])
        if len(undergrounds) == 1 and len(splitters) == 1:
            return name, undergrounds[0], splitters[0]
    pytest.skip("the active game data has no belt with exactly one underground belt and one splitter of its speed")


def inserter() -> str:
    return prototype("inserter")


def filtering_inserter() -> str:
    return prototype("inserter", requires=("filter_count",))


def chest() -> str:
    return prototype("container", requires=("inventory_size",))


def other_chest() -> str:
    return another("container", chest())


def pipe() -> str:
    return prototype("pipe")


def pipe_to_ground() -> str:
    return prototype("pipe-to-ground", requires=("fluid_box",))


def assembler() -> str:
    return prototype("assembling-machine")


def constant_combinator() -> str:
    return prototype("constant-combinator")


def decider_combinator() -> str:
    return prototype("decider-combinator")


def arithmetic_combinator() -> str:
    return prototype("arithmetic-combinator")


def pole() -> str:
    return prototype("electric-pole", requires=("supply_area_distance",))


def train_stop() -> str:
    return prototype("train-stop")


def lamp() -> str:
    return prototype("lamp")


def rail() -> str:
    return prototype("straight-rail")


def rail_signal() -> str:
    return prototype("rail-signal")


def chain_signal() -> str:
    return prototype("rail-chain-signal")


def curved_rail() -> str:
    """A curved rail piece, however this mod set builds one.

    Curved rails are usually placed by a rail planner rather than by a plain
    item mapping straight to one prototype, so unlike the lookups above this
    does not require an item that places it directly -- only that the
    prototype exists in the active data.
    """
    found = _first(
        entities.raw, lambda name, data: data.get("type") == "curved-rail-a", buildable_only=False
    )
    if found is None:
        pytest.skip("the active game data has no curved-rail-a")
    return found


def item() -> str:
    """Any item, for a filter or a slot that has to hold something."""
    found = _first(items.raw, lambda name, data: bool(data), buildable_only=False)
    if found is None:
        pytest.skip("the active game data has no items")
    return found


def recipe() -> str:
    found = _first(recipes.raw, lambda name, data: bool(data), buildable_only=False)
    if found is None:
        pytest.skip("the active game data has no recipes")
    return found


def two_underground_belts() -> tuple[str, str]:
    """A shorter and a longer underground belt, whatever this mod set calls them.

    The point of the test that uses this is that reach is read rather than
    assumed, so the pair has to differ in reach -- naming a vanilla tier would
    put the assumption back. A mod set with only one underground belt cannot
    demonstrate the difference, and says so instead of passing hollowly.
    """
    reaches = {
        name: data["max_distance"]
        for name, data in entities.raw.items()
        if data.get("type") == "underground-belt" and data.get("max_distance")
    }
    if len(set(reaches.values())) < 2:
        pytest.skip("the active game data has no two underground belts of different reach")
    shortest = min(reaches, key=lambda n: reaches[n])
    longest = max(reaches, key=lambda n: reaches[n])
    return shortest, longest


def two_inserters() -> tuple[str, str]:
    """A short-armed and a long-armed inserter, by what the prototype says."""
    arms = {}
    for name, data in entities.raw.items():
        if data.get("type") != "inserter":
            continue
        pickup = data.get("pickup_position")
        if pickup:
            arms[name] = abs(pickup[1])
    if len(set(arms.values())) < 2:
        pytest.skip("the active game data has no two inserters of different reach")
    return min(arms, key=lambda n: arms[n]), max(arms, key=lambda n: arms[n])


def _footprint(data: dict) -> tuple[int, int]:
    """How many tiles a prototype covers, from its collision box.

    Rounded up: a collision box is drawn a little inside the tiles it fills
    (0.7 for a chest, 2.4 for a 3-tile machine, 1.4 for a 2-tile substation),
    and rounding to nearest calls a substation one tile wide.
    """
    import math

    box = data.get("collision_box") or [[0, 0], [0, 0]]
    (left, top), (right, bottom) = box[0], box[1]
    return max(1, math.ceil(right - left)), max(1, math.ceil(bottom - top))


def small_chest() -> str:
    """A container one tile across.

    Tests that place things around a chest need to know how much room it takes.
    Asking for the size rather than assuming it keeps them honest under a mod
    set whose first container is bigger: under Krastorio 2 the assumption put
    two chests on top of an inserter and the test failed for a reason that had
    nothing to do with what it was checking.
    """
    found = _first(
        entities.raw,
        lambda name, data: data.get("type") == "container"
        and data.get("inventory_size")
        and _footprint(data) == (1, 1),
    )
    if found is None:
        pytest.skip("the active game data has no one-tile container")
    return found


def inserter_reaching(distance: int) -> str:
    """An electric inserter that picks up exactly `distance` tiles from itself."""
    import math

    def matches(_name, data) -> bool:
        pickup = data.get("pickup_position")
        source = data.get("energy_source")
        return (
            data.get("type") == "inserter"
            and pickup is not None
            and round(math.hypot(*pickup)) == distance
            and isinstance(source, dict)
            and source.get("type") == "electric"
        )

    found = _first(entities.raw, matches)
    if found is None:
        pytest.skip(f"the active game data has no electric inserter reaching {distance} tile(s)")
    return found


def small_pole() -> str:
    """A one-tile pole with a supply area and wire reach worth building rows with."""
    found = _first(
        entities.raw,
        lambda name, data: data.get("type") == "electric-pole"
        and _footprint(data) == (1, 1)
        and (data.get("supply_area_distance") or 0) >= 3
        and (data.get("maximum_wire_distance") or 0) >= 7,
    )
    if found is None:
        pytest.skip("the active game data has no one-tile pole with a useful supply area")
    return found


def fastest_belt() -> str:
    belts = [n for n, d in entities.raw.items() if d.get("type") == "transport-belt" and d.get("speed")]
    placeable = _buildable()
    belts = [n for n in belts if n in placeable]
    if not belts:
        pytest.skip("the active game data has no transport belt")
    return max(belts, key=lambda n: (entities.raw[n]["speed"], n))


def crafting_setup(solids_in: int, fluids_in: int, solids_out: int, fluids_out: int) -> tuple[str, str]:
    """A recipe of exactly this shape and a three-tile machine that crafts it.

    Machines are only considered when every fluid box connects along a long
    side at their default rotation, so a refusal from the layout code is a
    real failure rather than a machine this row form cannot hold.
    """
    placeable = _buildable()

    def fluids_fit(data) -> bool:
        for box in data.get("fluid_boxes") or []:
            for connection in box.get("pipe_connections") or []:
                if connection.get("connection_type") == "underground":
                    continue
                if connection.get("direction") not in (0, 8):
                    return False
        return True

    machines = sorted(
        name
        for name, data in entities.raw.items()
        if name in placeable
        and data.get("crafting_speed")
        and data.get("crafting_categories")
        and _footprint(data) == (3, 3)
        and fluids_fit(data)
    )
    for name in sorted(recipes.raw):
        recipe = recipes.raw[name]
        if not recipe or "recycling" in recipe_categories(recipe):
            continue
        parts = lambda key, fluid: [  # noqa: E731
            p for p in recipe.get(key, []) if (p.get("type") == "fluid") == fluid
        ]
        shape = (
            len(parts("ingredients", False)),
            len(parts("ingredients", True)),
            len(parts("results", False)),
            len(parts("results", True)),
        )
        if shape != (solids_in, fluids_in, solids_out, fluids_out):
            continue
        if any("amount" not in p for p in recipe.get("results", [])):
            continue
        for machine in machines:
            data = entities.raw[machine]
            boxes = data.get("fluid_boxes") or []
            inputs = sum(1 for b in boxes if b.get("production_type") == "input")
            outputs = sum(1 for b in boxes if b.get("production_type") == "output")
            if not crafts(data, recipe):
                continue
            if fluids_in and (inputs < fluids_in or inputs % fluids_in):
                continue
            if fluids_out and (outputs < fluids_out or outputs % fluids_out):
                continue
            return name, machine
    pytest.skip(
        f"the active game data has no recipe with {solids_in} solid and {fluids_in} fluid "
        f"ingredients, {solids_out} solid and {fluids_out} fluid results, in a 3x3 machine"
    )


def underground_reach_of(name: str) -> int:
    """The reach a prototype states, for tests that must span it."""
    data = entities.raw.get(name, {})
    plain = data.get("max_distance")
    if plain:
        return int(plain)
    for connection in (data.get("fluid_box") or {}).get("pipe_connections") or []:
        if connection.get("max_underground_distance"):
            return int(connection["max_underground_distance"])
    pytest.skip(f"{name} states no underground reach")
