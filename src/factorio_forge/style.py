"""Measure how a base is actually built, from the player's own construction.

A profile's ``blueprints/`` directory holds exports the companion mod makes of
real, played construction -- an area the player drags a selection over, turned
into a blueprint by the game itself (``export_region`` in the mod's
``control.lua``). That is deliberately not the same thing as the blueprint
library: the library mixes in whatever the player imported from the internet or
another player, which says nothing about how *this* player builds. What is
measured here answers two separate questions:

    layout      the geometry -- spacing, alignment, symmetry, orientation
    identity    naming, colour and tag conventions

Tier choice (which belt, which pipe) is deliberately excluded from both: it
follows from the game stage and available recipes, not from taste, and belongs
to the bill-of-materials step instead.

Measurement is a one-shot operation, cached as ``style.json`` next to
``profile.json``. Nothing here re-measures automatically -- only the player
decides when their base has changed enough to be worth reading again.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from draftsman.blueprintable import Blueprint, BlueprintBook, get_blueprintable_from_string

STYLE_FILE = "style.json"


class StyleError(Exception):
    """Something went wrong measuring or loading a base's style."""


# --------------------------------------------------------------------------
# reading the reference blueprints
# --------------------------------------------------------------------------


def _entities_in(directory: Path) -> list:
    """Every entity across every reference blueprint in a profile's blueprints/.

    A ``.txt`` here is always what the mod's own export produces: a single
    blueprint built from a real selection. A blueprint book is still accepted,
    flattened one level, in case a player has dropped one in by hand.
    """
    entities: list = []
    for path in sorted(directory.glob("*.txt")):
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        try:
            blueprintable = get_blueprintable_from_string(text)
        except Exception as exc:
            raise StyleError(f"{path.name} is not a readable blueprint: {exc}") from exc
        blueprints = (
            list(blueprintable.blueprints)
            if isinstance(blueprintable, BlueprintBook)
            else [blueprintable]
        )
        for blueprint in blueprints:
            if isinstance(blueprint, Blueprint):
                entities.extend(blueprint.entities)
    return entities


# --------------------------------------------------------------------------
# layout: geometry that holds regardless of what the player chose to customise
# --------------------------------------------------------------------------


def _nearest_neighbor_distances(points: list[tuple[int, int]]) -> list[float]:
    distances = []
    for i, (x, y) in enumerate(points):
        best = None
        for j, (ox, oy) in enumerate(points):
            if i == j:
                continue
            distance = math.hypot(x - ox, y - oy)
            if best is None or distance < best:
                best = distance
        if best is not None:
            distances.append(round(best, 2))
    return distances


def _measure_spacing(entities: list) -> dict:
    """Typical distance between same-type neighbours, per entity type.

    The mode of nearest-neighbour distances catches "assemblers three tiles
    apart", not the mean, which a single stray placement would drag off.
    """
    by_type: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for entity in entities:
        by_type[entity.name].append((entity.tile_position.x, entity.tile_position.y))

    spacing: dict[str, dict] = {}
    for name, points in by_type.items():
        if len(points) < 2:
            continue
        distances = _nearest_neighbor_distances(points)
        if not distances:
            continue
        value, count = Counter(distances).most_common(1)[0]
        spacing[name] = {
            "tiles": value,
            "sample_size": len(distances),
            "agreement": round(count / len(distances), 2),
        }
    return spacing


def _measure_alignment(entities: list) -> dict:
    """The coarsest grid every entity's position is a multiple of.

    The greatest common divisor of positions relative to the block's own
    corner: 1 tile means no coarser pattern was found, 2 or 3 means the base is
    built in modules of that size.
    """
    xs = [entity.tile_position.x for entity in entities]
    ys = [entity.tile_position.y for entity in entities]
    if not xs:
        return {"step_x": 1, "step_y": 1}
    min_x, min_y = min(xs), min(ys)
    step_x = 0
    step_y = 0
    for x in xs:
        step_x = math.gcd(step_x, x - min_x)
    for y in ys:
        step_y = math.gcd(step_y, y - min_y)
    return {"step_x": step_x or 1, "step_y": step_y or 1}


def _measure_symmetry(entities: list) -> dict:
    """The fraction of entities that have a mirror partner across each axis.

    Cheap and approximate: it only asks whether an entity of the same name
    exists at the reflected position, not whether the whole structure lines up
    piece for piece.
    """
    if not entities:
        return {"horizontal": 0.0, "vertical": 0.0}
    xs = [entity.tile_position.x for entity in entities]
    ys = [entity.tile_position.y for entity in entities]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    present = {(e.name, e.tile_position.x, e.tile_position.y) for e in entities}
    horizontal = sum(
        1
        for e in entities
        if (e.name, min_x + max_x - e.tile_position.x, e.tile_position.y) in present
    )
    vertical = sum(
        1
        for e in entities
        if (e.name, e.tile_position.x, min_y + max_y - e.tile_position.y) in present
    )
    return {
        "horizontal": round(horizontal / len(entities), 2),
        "vertical": round(vertical / len(entities), 2),
    }


def _measure_orientation(entities: list) -> dict:
    """How facings are distributed among entities that have one at all.

    Unlike colour or a label, a directional entity always has *some* facing --
    there is no "unset" to tell apart from a default -- so this reads the live
    attribute directly rather than checking what got serialised.
    """
    counts: Counter = Counter()
    for entity in entities:
        direction = getattr(entity, "direction", None)
        if direction is not None:
            counts[int(direction)] += 1
    total = sum(counts.values())
    if not total:
        return {}
    return {str(k): round(v / total, 2) for k, v in sorted(counts.items())}


def measure_layout(entities: list) -> dict:
    return {
        "spacing": _measure_spacing(entities),
        "alignment": _measure_alignment(entities),
        "symmetry": _measure_symmetry(entities),
        "orientation": _measure_orientation(entities),
    }


# --------------------------------------------------------------------------
# identity: naming, colour and tags -- all of them optional customisations
# --------------------------------------------------------------------------


def measure_identity(entities: list) -> dict:
    """Naming, colour and tag conventions, read only where the player set them.

    Draftsman fills every optional field with the game's own default the
    moment an entity exists in memory -- a train stop reads back a definite
    red even when nobody touched its colour. That default never appears in
    ``to_dict()``, which mirrors exactly what the game itself would write into
    a blueprint string: a field shows up there only when it differs from the
    default. So presence in that dict, not the live attribute, is what tells a
    deliberate choice apart from one nobody made.
    """
    station_names: list[str] = []
    annotations: list[str] = []
    colors: Counter = Counter()
    tag_keys: Counter = Counter()

    for entity in entities:
        serialized = entity.to_dict()

        station = serialized.get("station")
        if station:
            station_names.append(station)

        description = serialized.get("player_description")
        if description:
            annotations.append(description)

        color = serialized.get("color")
        if color:
            key = (round(color["r"], 2), round(color["g"], 2), round(color["b"], 2))
            colors[key] += 1

        tags = serialized.get("tags")
        if tags:
            tag_keys.update(tags.keys())

    return {
        "station_names": station_names,
        "annotations": annotations,
        "colors": {str(k): v for k, v in colors.items()},
        "tag_keys": dict(tag_keys),
    }


# --------------------------------------------------------------------------
# the cache
# --------------------------------------------------------------------------


@dataclass
class Style:
    """A base's measured style, cached next to its profile."""

    measured_at: str
    source_files: tuple[str, ...]
    layout: dict
    identity: dict

    def to_dict(self) -> dict:
        return {
            "measured_at": self.measured_at,
            "source_files": list(self.source_files),
            "layout": self.layout,
            "identity": self.identity,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Style":
        return cls(
            measured_at=data.get("measured_at", ""),
            source_files=tuple(data.get("source_files", [])),
            layout=data.get("layout", {}),
            identity=data.get("identity", {}),
        )

    def write(self, directory: Path) -> Path:
        target = directory / STYLE_FILE
        with target.open("w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        return target

    @classmethod
    def load(cls, directory: Path) -> "Style":
        path = directory / STYLE_FILE
        if not path.is_file():
            raise StyleError(f"no measured style at {path}")
        with path.open(encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))


def measure(blueprints_dir: Path) -> Style:
    """Measure style from every reference blueprint in a profile's blueprints/."""
    files = sorted(p.name for p in blueprints_dir.glob("*.txt"))
    if not files:
        raise StyleError(
            f"no reference blueprints in {blueprints_dir}; "
            "select an area of real construction in-game first"
        )
    entities = _entities_in(blueprints_dir)
    return Style(
        measured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source_files=tuple(files),
        layout=measure_layout(entities),
        identity=measure_identity(entities),
    )


def new_reference_blueprints(style: Style, blueprints_dir: Path) -> list[str]:
    """Reference blueprints added since ``style`` was last measured.

    Informational only. Style is never remeasured automatically -- this is for
    a caller to say "there is new material, remeasure?" and nothing more.
    """
    current = {p.name for p in blueprints_dir.glob("*.txt")}
    return sorted(current - set(style.source_files))
