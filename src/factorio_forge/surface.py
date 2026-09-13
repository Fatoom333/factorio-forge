"""Where the factory stands: what that surface offers, and what works there.

A bill of materials that treats everything minable as free is right for a
mod set with one planet and wrong with several. Space Age puts a sulfuric
acid geyser on Vulcanus and a heavy oil sea on Fulgora; a Nauvis base asking
for sulfuric acid must make it in chemical plants, and one on Vulcanus has no
water to pump at all. The same goes for what can run: a foundry recipe needs
Vulcanus's pressure, a stone furnace needs air, an asteroid collector needs a
space platform.

Everything here is read from the data. A planet lists what it places --
`map_gen_settings.autoplace_settings` names the resource entities and tiles
generated on it -- and states its `surface_properties`; a surface that is not
a planet (the space platform) has properties and places nothing. Recipes and
entities state `surface_conditions`, checked against those properties, with
each property's `default_value` where a surface does not state it.

What a surface offers for free is whatever a gatherer that works there can
take from it:

- a resource placed on it, by a mining drill whose `resource_categories`
  reach the resource's category;
- a fluid of a tile placed on it (`tile.fluid`), by an offshore pump;
- an asteroid chunk, by an asteroid collector -- which works only where
  there is no pressure, which is what keeps chunks off the planets;
- what a boiler-type entity makes from an input the surface already offers:
  steam, from water.
"""

from __future__ import annotations

from dataclasses import dataclass

from draftsman.data import entities as entity_data
from draftsman.data import planets as planet_data
from draftsman.data import resources as resource_data
from draftsman.data import tiles as tile_data

# Draftsman's own defaults for the four base properties, for data extracted
# before surface-property prototypes were (a profile from an older fork).
_FALLBACK_DEFAULTS = {"solar-power": 100, "magnetic-field": 90, "pressure": 1000, "gravity": 10}


def _surface_data():
    try:
        from draftsman.data import surfaces
    except ImportError:
        return None
    return surfaces


def _asteroid_chunks() -> dict:
    try:
        from draftsman.data import asteroid_chunks
    except ImportError:
        return {}
    return asteroid_chunks.raw


@dataclass(frozen=True)
class Source:
    item: str
    how: str  # "mined", "pumped", "collected", "boiled"
    from_: str  # the resource, tile, chunk or boiler it comes from


def names() -> list[str]:
    """Every surface the data knows: planets, then other surfaces."""
    others = sorted((_surface_data().raw if _surface_data() else {}).keys())
    return sorted(planet_data.raw) + [n for n in others if n not in planet_data.raw]


def exists(name: str) -> bool:
    return name in names()


def _prototype(name: str) -> dict:
    if name in planet_data.raw:
        return planet_data.raw[name]
    data = _surface_data()
    if data is not None and name in data.raw:
        return data.raw[name]
    raise KeyError(f"{name!r} is not a planet or surface in the active data")


def properties(name: str) -> dict[str, float]:
    """The surface's property values, with each property's default where unstated."""
    data = _surface_data()
    defaults = {
        prop: float(entry.get("default_value", 0))
        for prop, entry in ((data.properties if data is not None else {}) or {}).items()
    } or dict(_FALLBACK_DEFAULTS)
    stated = _prototype(name).get("surface_properties") or {}
    return {**defaults, **{k: float(v) for k, v in stated.items()}}


def allows(conditions, name: str) -> bool:
    """Whether `surface_conditions` hold on this surface.

    A condition on a property the data has no value for cannot be judged, and
    is taken as met rather than as a reason to rule something out.
    """
    if not conditions:
        return True
    values = properties(name)
    for condition in conditions:
        value = values.get(condition.get("property"))
        if value is None:
            continue
        if value < float(condition.get("min", float("-inf"))) or value > float(condition.get("max", float("inf"))):
            return False
    return True


def placed(name: str, kind: str) -> set[str]:
    """Names of the `kind` ("entity" or "tile") prototypes generated on a surface."""
    settings = ((_prototype(name).get("map_gen_settings") or {}).get("autoplace_settings") or {})
    return set(((settings.get(kind) or {}).get("settings") or {}))


def _working(entity_type: str, name: str) -> list[dict]:
    return [
        entry for entry in entity_data.raw.values()
        if isinstance(entry, dict) and entry.get("type") == entity_type and allows(entry.get("surface_conditions"), name)
    ]


def _yields(minable) -> list[str]:
    minable = minable or {}
    found = [minable["result"]] if minable.get("result") else []
    return found + [r["name"] for r in minable.get("results", []) or [] if r.get("name")]


def sources(name: str) -> dict[str, Source]:
    """Item or fluid -> how this surface offers it for free."""
    offered: dict[str, Source] = {}

    drills = _working("mining-drill", name)
    categories = {c for drill in drills for c in (drill.get("resource_categories") or [])}
    for resource_name in sorted(placed(name, "entity") & set(resource_data.raw)):
        resource = resource_data.raw[resource_name]
        if resource.get("category", "basic-solid") not in categories:
            continue
        for item in _yields(resource.get("minable")):
            offered.setdefault(item, Source(item, "mined", resource_name))

    if _working("offshore-pump", name):
        for tile_name in sorted(placed(name, "tile")):
            fluid = (tile_data.raw.get(tile_name) or {}).get("fluid")
            if fluid:
                offered.setdefault(fluid, Source(fluid, "pumped", tile_name))

    if _working("asteroid-collector", name):
        for chunk_name, chunk in sorted(_asteroid_chunks().items()):
            for item in _yields(chunk.get("minable")):
                offered.setdefault(item, Source(item, "collected", chunk_name))

    # A boiler turns something the surface already offers into something new;
    # repeat until nothing more appears (steam from water, and so on).
    boilers = _working("boiler", name)
    while True:
        added = False
        for boiler in boilers:
            needs = (boiler.get("fluid_box") or {}).get("filter")
            makes = (boiler.get("output_fluid_box") or {}).get("filter")
            if makes and makes not in offered and (needs is None or needs in offered):
                offered[makes] = Source(makes, "boiled", boiler.get("name", "boiler"))
                added = True
        if not added:
            break
    return offered


def where_offered(item: str) -> list[str]:
    """The surfaces that offer this item for free."""
    return [name for name in names() if item in sources(name)]
