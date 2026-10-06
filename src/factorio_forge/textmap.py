"""A blueprint as characters, for looking at while a design is still changing.

The HTML drawing is the thing to show a person, but each look at it costs a
screenshot. While a layout is being iterated on, what needs checking is which
tile holds what and which way it points -- a belt running the wrong way, an
inserter dropping onto the floor, a gap where a pole should be -- and a grid
of characters answers that as text.

One character per tile, chosen by what the entity does rather than what it is
called, so a modded belt reads as a belt. Footprints come from the loaded
prototypes; an entity the data does not know is still shown, as ``?``, and
the legend names it. Two entities on one tile show as ``!``.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass

from .inspection import inserter_reach, is_known
from .render import FAMILY_OF_TYPE

# Belts and inserters say which way they move things; everything else only
# says what it is. Inserters use different arrows from belts so the two never
# read alike, and a doubled arrow for one that reaches past the next tile.
BELT_ARROW = {0: "^", 4: ">", 8: "v", 12: "<"}
INSERTER_ARROW = {(0, -1): "↑", (1, 0): "→", (0, 1): "↓", (-1, 0): "←"}
LONG_INSERTER_ARROW = {(0, -1): "⇑", (1, 0): "⇒", (0, 1): "⇓", (-1, 0): "⇐"}

# Prototype type -> (symbol, meaning). A machine spans several tiles; its
# centre tile carries the capital and the rest the lower case, so two
# machines side by side still read as two.
SYMBOL_OF_TYPE: dict[str, tuple[str, str]] = {
    "splitter": ("S", "splitter"),
    "lane-splitter": ("S", "splitter"),
    "loader": ("L", "loader"),
    "loader-1x1": ("L", "loader"),
    "assembling-machine": ("A", "assembling machine"),
    "furnace": ("F", "furnace"),
    "mining-drill": ("M", "mining drill"),
    "beacon": ("B", "beacon"),
    "lab": ("R", "lab"),
    "container": ("C", "chest"),
    "logistic-container": ("C", "chest"),
    "linked-container": ("C", "chest"),
    "infinity-container": ("C", "chest"),
    "electric-pole": ("P", "electric pole"),
    "pipe": ("+", "pipe"),
    "infinity-pipe": ("+", "pipe"),
    "pipe-to-ground": ("T", "pipe to ground"),
    "pump": ("p", "pump"),
    "storage-tank": ("O", "storage tank"),
}

# Whatever the table above does not name falls back to its family's symbol.
SYMBOL_OF_FAMILY: dict[str, tuple[str, str]] = {
    "transport": ("=", "other belt part"),
    "inserter": ("i", "inserter (direction unknown)"),
    "production": ("X", "other machine"),
    "storage": ("C", "chest"),
    "fluid": ("f", "other fluid part"),
    "power": ("E", "power"),
    "circuit": ("K", "circuit / lamp"),
    "rail": ("#", "rail"),
    "military": ("W", "wall / turret"),
    "other": ("?", "other"),
}

EMPTY = "."
OVERLAP = "!"


@dataclass(frozen=True)
class Mark:
    symbol: str
    meaning: str


def mark_of(entity) -> Mark:
    """The character for an entity's tiles, and what it means in the legend."""
    kind = getattr(entity, "type", None)
    if not is_known(entity):
        return Mark("?", "not in the loaded game data")
    if kind in ("transport-belt", "linked-belt"):
        arrow = BELT_ARROW.get(int(entity.direction))
        return Mark(arrow, "belt, pointing the way it moves") if arrow else Mark("=", "belt, diagonal")
    if kind == "underground-belt":
        if getattr(entity, "io_type", None) == "output":
            return Mark("u", "underground belt exit")
        return Mark("U", "underground belt entrance")
    if kind == "inserter":
        return _inserter_mark(entity)
    if kind in SYMBOL_OF_TYPE:
        return Mark(*SYMBOL_OF_TYPE[kind])
    return Mark(*SYMBOL_OF_FAMILY[FAMILY_OF_TYPE.get(kind, "other")])


def _inserter_mark(entity) -> Mark:
    reach = inserter_reach(entity)
    if reach is None:
        return Mark(*SYMBOL_OF_FAMILY["inserter"])
    (_, _), (drop_x, drop_y) = reach
    dx, dy = drop_x - entity.position.x, drop_y - entity.position.y
    if abs(dx) == abs(dy):
        return Mark(*SYMBOL_OF_FAMILY["inserter"])
    step = (int(math.copysign(1, dx)), 0) if abs(dx) > abs(dy) else (0, int(math.copysign(1, dy)))
    if max(abs(dx), abs(dy)) > 1.5:
        return Mark(LONG_INSERTER_ARROW[step], "long inserter, pointing where it drops")
    return Mark(INSERTER_ARROW[step], "inserter, pointing where it drops")


def _tiles(entity):
    x, y = int(entity.tile_position.x), int(entity.tile_position.y)
    # An unknown entity has no footprint in the data; one tile still shows it.
    width, height = max(1, entity.tile_width), max(1, entity.tile_height)
    centre = (x + (width - 1) // 2, y + (height - 1) // 2)
    for dx in range(width):
        for dy in range(height):
            yield (x + dx, y + dy), (x + dx, y + dy) == centre


def text_map(blueprint) -> str:
    """The blueprint as a grid of characters, with axes and a legend."""
    grid: dict[tuple[int, int], str] = {}
    used: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for entity in blueprint.entities:
        mark = mark_of(entity)
        used[(mark.symbol, mark.meaning)][entity.name] += 1
        large = entity.tile_width * entity.tile_height > 1 and mark.symbol.isalpha()
        for tile, centre in _tiles(entity):
            symbol = mark.symbol.lower() if large and not centre else mark.symbol
            grid[tile] = OVERLAP if tile in grid else symbol

    if not grid:
        return "(the blueprint has no entities)"

    xs = [x for x, _ in grid]
    ys = [y for _, y in grid]
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    margin = max(len(str(top)), len(str(bottom))) + 1
    lines = [f"x {left}..{right}, y {top}..{bottom} ({right - left + 1}×{bottom - top + 1} tiles)"]
    lines.extend(_x_axis(left, right, margin))
    for y in range(top, bottom + 1):
        row = "".join(grid.get((x, y), EMPTY) for x in range(left, right + 1))
        lines.append(f"{y:>{margin - 1}} {row}")

    lines.append("")
    lines.append("legend (a capital marks the centre of a larger entity; the rest is lower case):")
    # One line per meaning: the four belt arrows are one kind of thing.
    by_meaning: dict[str, tuple[list[str], Counter]] = {}
    for (symbol, meaning), names in used.items():
        symbols, counts = by_meaning.setdefault(meaning, ([], Counter()))
        symbols.append(symbol)
        counts.update(names)
    for meaning, (symbols, counts) in sorted(by_meaning.items()):
        listed = ", ".join(f"{name} ×{count}" for name, count in sorted(counts.items()))
        lines.append(f"  {' '.join(sorted(symbols)):<7} {meaning}: {listed}")
    if OVERLAP in grid.values():
        lines.append(f"  {OVERLAP:<7} two entities on one tile")
    lines.append(f"  {EMPTY:<7} empty")
    return "\n".join(lines)


def _x_axis(left: int, right: int, margin: int) -> list[str]:
    """Two header rows: the full number at every tenth column, then the last digit."""
    labels = [" "] * (right - left + 1)
    for x in range(left, right + 1):
        if x % 10 == 0:
            for i, char in enumerate(str(x)):
                if x - left + i < len(labels):
                    labels[x - left + i] = char
    digits = "".join(str(abs(x) % 10) for x in range(left, right + 1))
    pad = " " * margin
    return [pad + "".join(labels).rstrip(), pad + digits]
