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
    """How many tiles a prototype covers, from its collision box."""
    box = data.get("collision_box") or [[0, 0], [0, 0]]
    (left, top), (right, bottom) = box[0], box[1]
    return max(1, round(right - left)), max(1, round(bottom - top))


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
