"""Draw a blueprint as a picture you can actually read.

Everything upstream of here is invisible: a blueprint is a list of names and
coordinates, and a mistake in it looks exactly like correctness. This module
turns one into an SVG laid out on the game's own grid, so that a belt pointing
the wrong way or a machine overlapping its neighbour is obvious at a glance.

It is deliberately not a picture of the game. There are no sprites — shipping
them would be redistributing Wube's art, and they would not help anyway, because
what matters while building a generator is footprint, orientation and type, and
those read better as flat colour than as artwork.

The output is a single self-contained HTML file: no scripts fetched, no fonts
loaded, nothing to serve. Open it, or send it to someone.
"""

from __future__ import annotations

import colorsys
import hashlib
import html
import io
import json
import math
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import draftsman.data.entities as entity_data
import draftsman.data.items as item_data

# The checker already worked out how to read an inserter's real reach and how
# to walk an underground run to its partner -- both facts that took a real
# base to get right (see `inserter_reach`, `undergrounds_without_a_pair`).
# The picture wants the same facts for a different purpose (where to draw,
# rather than whether to complain), so it reads them from the same place
# rather than re-deriving them and risking the two disagreeing.
from .inspection import STEP, inserter_reach, underground_direction, underground_reach
from . import paths

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow is a normal dependency
    Image = None

# --------------------------------------------------------------------------
# categories
# --------------------------------------------------------------------------

# Prototype type -> the family it is drawn as. Grouped by what a person looks
# for when reading a blueprint, which is not the same as Factorio's own
# taxonomy: a pump and a pipe belong together here even though the game treats
# them as unrelated prototypes.
FAMILY_OF_TYPE: dict[str, str] = {
    # things that move items
    "transport-belt": "transport",
    "underground-belt": "transport",
    "splitter": "transport",
    "loader": "transport",
    "loader-1x1": "transport",
    "linked-belt": "transport",
    "lane-splitter": "transport",
    # things that lift items
    "inserter": "inserter",
    # things that make items
    "assembling-machine": "production",
    "furnace": "production",
    "rocket-silo": "production",
    "mining-drill": "production",
    "lab": "production",
    "beacon": "production",
    "agricultural-tower": "production",
    "asteroid-collector": "production",
    "crusher": "production",
    # things that store items
    "container": "storage",
    "logistic-container": "storage",
    "infinity-container": "storage",
    "linked-container": "storage",
    "proxy-container": "storage",
    "roboport": "storage",
    # things that carry fluid
    "pipe": "fluid",
    "pipe-to-ground": "fluid",
    "storage-tank": "fluid",
    "pump": "fluid",
    "offshore-pump": "fluid",
    "valve": "fluid",
    "infinity-pipe": "fluid",
    # things that make or carry power
    "electric-pole": "power",
    "solar-panel": "power",
    "accumulator": "power",
    "boiler": "power",
    "generator": "power",
    "reactor": "power",
    "heat-pipe": "power",
    "heat-interface": "power",
    "burner-generator": "power",
    "fusion-reactor": "power",
    "fusion-generator": "power",
    "lightning-attractor": "power",
    # things that think
    "arithmetic-combinator": "circuit",
    "decider-combinator": "circuit",
    "constant-combinator": "circuit",
    "selector-combinator": "circuit",
    "power-switch": "circuit",
    "programmable-speaker": "circuit",
    "lamp": "circuit",
    "display-panel": "circuit",
    # things trains use
    "straight-rail": "rail",
    "curved-rail-a": "rail",
    "curved-rail-b": "rail",
    "half-diagonal-rail": "rail",
    "elevated-straight-rail": "rail",
    "elevated-curved-rail-a": "rail",
    "elevated-curved-rail-b": "rail",
    "elevated-half-diagonal-rail": "rail",
    "rail-ramp": "rail",
    "rail-support": "rail",
    "train-stop": "rail",
    "rail-signal": "rail",
    "rail-chain-signal": "rail",
    "locomotive": "rail",
    "cargo-wagon": "rail",
    "fluid-wagon": "rail",
    "artillery-wagon": "rail",
    # things that shoot
    "ammo-turret": "military",
    "electric-turret": "military",
    "fluid-turret": "military",
    "artillery-turret": "military",
    "wall": "military",
    "gate": "military",
    "radar": "military",
    "land-mine": "military",
}

FAMILY_COLOUR: dict[str, str] = {
    "transport": "#d9a441",
    "inserter": "#3f8fd0",
    "production": "#2f9e8f",
    "storage": "#9b7653",
    "fluid": "#5bb8d4",
    "power": "#d2683f",
    "circuit": "#9663c4",
    "rail": "#8a8f98",
    "military": "#b04a4a",
    "other": "#6f7680",
}

FAMILY_LABEL: dict[str, str] = {
    "transport": "belts",
    "inserter": "inserters",
    "production": "production",
    "storage": "storage & robots",
    "fluid": "fluids",
    "power": "power & heat",
    "circuit": "circuits & lamps",
    "rail": "rails & trains",
    "military": "military",
    "other": "other",
}


def family_of(entity) -> str:
    """Which drawing family an entity belongs to."""
    prototype_type = getattr(entity, "type", None)
    return FAMILY_OF_TYPE.get(prototype_type, "other")


# Sub-types that flat family colour cannot tell apart on its own, but that a
# reader needs to tell apart without hovering: an underground belt looks
# exactly like a plain belt in the same colour and the same square, and every
# rail piece -- straight, curved, signal, chain signal -- has so far been the
# same grey block.

# Buried pieces: the tile shown is not where the item actually is, which a
# solid fill the same as its above-ground neighbour does not say.
UNDERGROUND_TYPES = {"underground-belt", "pipe-to-ground"}

# Track: drawn as rails and ties rather than a filled block, because the
# block is usually bigger than the actual track (a curve's bounding box
# covers tiles the curve does not touch) and a solid fill there overstates
# the footprint.
TRACK_TYPES = {
    "straight-rail",
    "curved-rail-a",
    "curved-rail-b",
    "half-diagonal-rail",
    "elevated-straight-rail",
    "elevated-curved-rail-a",
    "elevated-curved-rail-b",
    "elevated-half-diagonal-rail",
    "rail-ramp",
}

CURVED_TRACK_TYPES = {
    "curved-rail-a",
    "curved-rail-b",
    "half-diagonal-rail",
    "elevated-curved-rail-a",
    "elevated-curved-rail-b",
    "elevated-half-diagonal-rail",
}

# Signal and chain signal sit on the same small footprint as track and used
# to be told apart only by hovering; a shape fixes that without needing a
# state (red/green) the blueprint does not actually store.
RAIL_POINT_ICON = {"rail-signal": "circle", "rail-chain-signal": "diamond"}


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Bounds:
    """The tile rectangle a blueprint occupies, in game coordinates."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def __str__(self) -> str:
        return f"{self.width}×{self.height} tiles"


def measure(entities: Iterable, tiles: Iterable = ()) -> Bounds:
    """The tile rectangle covering everything, or a single tile if empty."""
    lefts, tops, rights, bottoms = [], [], [], []
    for entity in entities:
        x, y = int(entity.tile_position.x), int(entity.tile_position.y)
        lefts.append(x)
        tops.append(y)
        rights.append(x + entity.tile_width)
        bottoms.append(y + entity.tile_height)
    for tile in tiles:
        x, y = int(tile.position.x), int(tile.position.y)
        lefts.append(x)
        tops.append(y)
        rights.append(x + 1)
        bottoms.append(y + 1)
    if not lefts:
        return Bounds(0, 0, 1, 1)
    return Bounds(min(lefts), min(tops), max(rights), max(bottoms))


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------

# Factorio's directions run 0 to 15 around the compass, so one step is a
# sixteenth of a turn. Named because `* 22.5` in the middle of drawing code
# reads as a magic number rather than as the thing it is.
DEGREES_PER_STEP = 360 / 16

CELL = 26  # pixels per tile
MARGIN = 34  # room for the coordinate ruler


def _short_label(name: str) -> str:
    """A couple of letters that hint at the entity, for drawing inside it."""
    parts = [p for p in name.replace("_", "-").split("-") if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    # "assembling-machine-2" -> "AM2", "medium-electric-pole" -> "MEP"
    letters = "".join(p[0] for p in parts if not p.isdigit()).upper()[:3]
    trailing = next((p for p in reversed(parts) if p.isdigit()), "")
    return (letters + trailing)[:4]


def _arrow(cx: float, cy: float, direction: int, size: float) -> str:
    """A chevron at (cx, cy) pointing where `direction` points.

    Factorio's 2.0 directions are sixteenths of a turn with north at zero, so
    the angle is a plain multiplication; SVG rotates clockwise from the same
    north, which happens to match.
    """
    angle = int(direction) * DEGREES_PER_STEP
    half = size / 2
    points = f"0,{-half} {half * 0.8},{half * 0.6} 0,{half * 0.15} {-half * 0.8},{half * 0.6}"
    return (
        f'<polygon points="{points}" class="dir" '
        f'transform="translate({cx:.1f} {cy:.1f}) rotate({angle:g})"/>'
    )


def _direction_vector(direction: int) -> tuple[float, float]:
    """Unit vector a compass direction points, in the same rotation `_arrow` uses."""
    angle = math.radians(int(direction) * DEGREES_PER_STEP)
    return math.sin(angle), -math.cos(angle)


def _tie(x: float, y: float, ux: float, uy: float, half: float) -> str:
    """A short cross-tick perpendicular to (ux, uy), centred at (x, y)."""
    return (
        f'<line class="rail-tie" x1="{x - ux * half:.1f}" y1="{y - uy * half:.1f}" '
        f'x2="{x + ux * half:.1f}" y2="{y + uy * half:.1f}"/>'
    )


def _box_reach(ux: float, uy: float, half_w: float, half_h: float) -> float:
    """How far from the centre a ray in direction (ux, uy) travels before it
    leaves an axis-aligned box of half-width/half-height (half_w, half_h).

    A curved rail's box is rarely square -- 3x6, 5x5, 4x5 -- so a single
    radius from its diagonal (as a circle would use) overshoots the short
    side and undershoots the long one. This instead asks, per direction, how
    far that particular ray actually gets.
    """
    limits = [half_w / abs(ux)] if abs(ux) > 1e-9 else []
    limits += [half_h / abs(uy)] if abs(uy) > 1e-9 else []
    return min(limits) if limits else min(half_w, half_h)


def _rail_glyph(cx: float, cy: float, w: float, h: float, direction: int, curved: bool, bend: float) -> str:
    """A schematic pair of rails through an entity's footprint, with ties.

    This is not the game's actual curve geometry -- that differs by rail type
    and by direction in ways not worth reproducing for a diagram. The point is
    only that a straight piece and a curve read differently at a glance, and
    that a chain of track pieces reads as a path rather than as identical grey
    blocks.
    """
    ux, uy = _direction_vector(direction)
    tx, ty = -uy, ux  # perpendicular to travel: rail gauge and tie direction
    half_w, half_h = w / 2, h / 2
    reach_u = _box_reach(ux, uy, half_w, half_h) * 0.88
    reach_t = _box_reach(tx, ty, half_w, half_h) * 0.88
    gauge = 3.2
    lines: list[str] = []

    if curved:
        entry = (cx - ux * reach_u, cy - uy * reach_u)
        exit_ = (cx + tx * reach_t * bend, cy + ty * reach_t * bend)
        corner = (entry[0] + tx * reach_t * bend, entry[1] + ty * reach_t * bend)

        def point(t: float) -> tuple[float, float]:
            mt = 1 - t
            return (
                mt * mt * entry[0] + 2 * mt * t * corner[0] + t * t * exit_[0],
                mt * mt * entry[1] + 2 * mt * t * corner[1] + t * t * exit_[1],
            )

        def tangent(t: float) -> tuple[float, float]:
            dx = 2 * (1 - t) * (corner[0] - entry[0]) + 2 * t * (exit_[0] - corner[0])
            dy = 2 * (1 - t) * (corner[1] - entry[1]) + 2 * t * (exit_[1] - corner[1])
            length = math.hypot(dx, dy) or 1.0
            return dx / length, dy / length

        for sign in (1, -1):
            off = (tx * gauge * sign, ty * gauge * sign)
            e = (entry[0] + off[0], entry[1] + off[1])
            o = (exit_[0] + off[0], exit_[1] + off[1])
            c = (corner[0] + off[0], corner[1] + off[1])
            lines.append(
                f'<path class="rail-line" d="M{e[0]:.1f},{e[1]:.1f} '
                f'Q{c[0]:.1f},{c[1]:.1f} {o[0]:.1f},{o[1]:.1f}"/>'
            )
        for t in (0.22, 0.5, 0.78):
            tpx, tpy = point(t)
            dux, duy = tangent(t)
            lines.append(_tie(tpx, tpy, -duy, dux, gauge + 2.5))
    else:
        entry = (cx - ux * reach_u, cy - uy * reach_u)
        exit_ = (cx + ux * reach_u, cy + uy * reach_u)
        for sign in (1, -1):
            off = (tx * gauge * sign, ty * gauge * sign)
            e = (entry[0] + off[0], entry[1] + off[1])
            o = (exit_[0] + off[0], exit_[1] + off[1])
            lines.append(
                f'<line class="rail-line" x1="{e[0]:.1f}" y1="{e[1]:.1f}" '
                f'x2="{o[0]:.1f}" y2="{o[1]:.1f}"/>'
            )
        for t in (-0.55, 0.0, 0.55):
            tpx, tpy = cx + ux * reach_u * t, cy + uy * reach_u * t
            lines.append(_tie(tpx, tpy, tx, ty, gauge + 2.5))

    return f'<g class="rail-glyph">{"".join(lines)}</g>'


def _rail_point_icon(cx: float, cy: float, r: float, shape: str) -> str:
    """A small distinct mark for a rail signal or chain signal.

    Both used to share the same grey "rail" family colour and the same small
    footprint as plain track, indistinguishable without hovering. A shape
    fixes that without inventing a red/green state the blueprint does not
    actually store.
    """
    if shape == "diamond":
        pts = (
            f"{cx:.1f},{cy - r:.1f} {cx + r:.1f},{cy:.1f} "
            f"{cx:.1f},{cy + r:.1f} {cx - r:.1f},{cy:.1f}"
        )
        return f'<polygon class="rail-point" points="{pts}"/>'
    return f'<circle class="rail-point" cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}"/>'


def _hatch(index: int, x: float, y: float, w: float, h: float) -> str:
    """Diagonal stripes clipped to a footprint, for a connection that breaks.

    An underground belt or pipe-to-ground looks, in flat colour, exactly like
    the ordinary belt or pipe beside it -- same colour, same square, same
    arrow. The stripes are the one thing that reads as "the item is not
    actually here, it re-appears somewhere else" without relying on the hover
    text.
    """
    clip_id = f"buried-{index}"
    step = 7
    span = w + h
    segments = []
    i = -1
    offset = i * step
    while offset < span:
        segments.append(
            f'<line x1="{x + offset:.1f}" y1="{y + h:.1f}" '
            f'x2="{x + offset + h:.1f}" y2="{y:.1f}"/>'
        )
        i += 1
        offset = i * step
    return (
        f'<clipPath id="{clip_id}"><rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}"/></clipPath>'
        f'<g class="hatch" clip-path="url(#{clip_id})">{"".join(segments)}</g>'
    )


def _wire_endpoint(part):
    """The entity a wire's endpoint refers to.

    A freshly built blueprint stores each endpoint as an `Association`, a
    callable that dereferences to the entity; one parsed from a string can
    carry the same. Anything else -- a bare index, say -- passes through
    unresolved and is dropped by the caller, since a wire this picture cannot
    place is better left out than drawn to the wrong entity.
    """
    try:
        return part()
    except TypeError:
        return part
    except Exception:
        return None


WIRE_KIND: dict[int, str] = {1: "red", 2: "green", 3: "red", 4: "green", 5: "copper", 6: "copper"}


def _underground_pairs(entities: list) -> dict[int, int]:
    """Which underground belt or pipe-to-ground index reaches which other.

    The same search `undergrounds_without_a_pair` runs to report a missing
    partner, kept separate because that check only wants to know whether a
    partner exists; the picture wants to know exactly which entity it is, to
    draw a line to it.
    """
    by_tile: dict[tuple[int, int], list[int]] = {}
    for index, entity in enumerate(entities):
        if getattr(entity, "type", "") not in ("underground-belt", "pipe-to-ground"):
            continue
        tile = (int(entity.tile_position.x), int(entity.tile_position.y))
        by_tile.setdefault(tile, []).append(index)

    pairs: dict[int, int] = {}
    for index, entity in enumerate(entities):
        if getattr(entity, "type", "") not in ("underground-belt", "pipe-to-ground"):
            continue
        direction = int(getattr(entity, "direction", 0) or 0)
        step = STEP.get(direction)
        if step is None:
            continue

        reach = underground_reach(entity)
        if reach is None:
            continue
        offset = underground_direction(entity)
        if offset is not None:
            step = STEP.get((direction + offset) % 16)
            if step is None:
                continue
        elif getattr(entity, "io_type", None) == "output":
            step = (-step[0], -step[1])

        x, y = int(entity.tile_position.x), int(entity.tile_position.y)
        for distance in range(1, reach + 1):
            tx, ty = x + step[0] * distance, y + step[1] * distance
            match = next(
                (i for i in by_tile.get((tx, ty), ()) if i != index and entities[i].name == entity.name),
                None,
            )
            if match is not None:
                pairs[index] = match
                break
    return pairs


def _inserter_glyph(pickup: tuple[float, float], drop: tuple[float, float], r: float) -> str:
    """Where an inserter actually reaches: a hollow ring at the pickup tile, a
    solid dot at the drop tile, and a line between them.

    Deliberately not the compass arrow every other entity gets. An inserter's
    `direction` points at the side it picks up from, not the side it drops
    onto -- the opposite of what a belt's arrow means -- so drawing the same
    arrow for both reads backwards for exactly the entity where getting it
    backwards matters most.
    """
    (px1, py1), (px2, py2) = pickup, drop
    return (
        f'<line class="inserter-reach" x1="{px1:.1f}" y1="{py1:.1f}" '
        f'x2="{px2:.1f}" y2="{py2:.1f}"/>'
        f'<circle class="inserter-pickup" cx="{px1:.1f}" cy="{py1:.1f}" r="{r:.1f}"/>'
        f'<circle class="inserter-drop" cx="{px2:.1f}" cy="{py2:.1f}" r="{r * 0.85:.1f}"/>'
    )


PARAMETER = re.compile(r"parameter-\d+")


def _signal_name(value) -> str | None:
    """The name out of a signal, however it is represented."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("name")
    return getattr(value, "name", None)


def _describe_condition(condition) -> str | None:
    """A circuit condition as something readable: 'iron-plate < 100'."""
    if condition is None:
        return None
    first = _signal_name(getattr(condition, "first_signal", None))
    if first is None:
        return None
    comparator = getattr(condition, "comparator", "") or ""
    second = _signal_name(getattr(condition, "second_signal", None))
    right = second if second is not None else getattr(condition, "constant", 0)
    return f"{first} {comparator} {right}"


def _describe_filters(entity) -> str | None:
    """Item filters, as on a filter inserter."""
    filters = getattr(entity, "filters", None) or []
    names = [n for n in (_signal_name(f) for f in filters) if n]
    if not names:
        return None
    mode = getattr(entity, "filter_mode", "whitelist")
    prefix = "" if mode == "whitelist" else f"{mode}: "
    return prefix + ", ".join(names)


def _describe_sections(entity) -> str | None:
    """Logistic or combinator sections, as on a constant combinator."""
    sections = getattr(entity, "sections", None) or []
    pieces: list[str] = []
    for section in sections:
        filters = getattr(section, "filters", None) or {}
        entries = filters.values() if isinstance(filters, dict) else filters
        for entry in entries:
            name = _signal_name(entry)
            if not name:
                continue
            count = getattr(entry, "count", None)
            pieces.append(f"{name}×{count}" if count is not None else name)
    return ", ".join(pieces) if pieces else None


def _describe_decider(entity) -> str | None:
    conditions = getattr(entity, "conditions", None) or []
    described = [d for d in (_describe_condition(c) for c in conditions) if d]
    return " and ".join(described) if described else None


def _entity_details(entity) -> dict[str, str]:
    """Everything worth knowing about an entity, as label to text.

    This is where a parameterised blueprint becomes legible: a parameter shows
    up as `parameter-0` sitting in a filter, a combinator signal or a recipe,
    and unless those are read out there is no way to tell from the picture
    which entities the parameters actually reach.
    """
    details: dict[str, str] = {}

    for attribute, label in (
        ("recipe", "recipe"),
        ("io_type", "type"),
        ("input_priority", "input priority"),
        ("output_priority", "output priority"),
        ("filter", "filter"),
        ("override_stack_size", "stack size"),
        ("bar", "bar"),
        ("station", "station"),
        ("orientation", "orientation"),
        ("operation", "operation"),
    ):
        value = getattr(entity, attribute, None)
        if value not in (None, "none", "", []):
            details[label] = str(value)

    for describe, label in (
        (_describe_filters, "filters"),
        (_describe_sections, "signals"),
        (_describe_decider, "when"),
    ):
        described = describe(entity)
        if described:
            details[label] = described

    enabled = _describe_condition(getattr(entity, "circuit_condition", None))
    if enabled:
        details["enabled when"] = enabled

    output = _signal_name(getattr(entity, "output_signal", None))
    if output:
        details["output"] = output

    quality = getattr(entity, "quality", None)
    if quality and quality != "normal":
        details["quality"] = str(quality)
    recipe_quality = getattr(entity, "recipe_quality", None)
    if recipe_quality and recipe_quality != "normal" and "recipe" in details:
        details["recipe"] += f" ({recipe_quality})"

    return details


def parameters_used_by(details: dict[str, str]) -> list[str]:
    """Which blueprint parameters an entity's settings refer to."""
    found: list[str] = []
    for value in details.values():
        for match in PARAMETER.findall(value):
            if match not in found:
                found.append(match)
    return sorted(found)


def snap_cell(blueprint, bounds: Bounds) -> tuple[float, float, float, float] | None:
    """The grid cell a blueprint declares, in blueprint tile coordinates.

    Returns ``(left, top, width, height)``, or None when no grid is declared.
    The cell is placed so the blueprint's top-left corner sits `offset` inside
    it, which is what `position-relative-to-grid` describes.
    """
    snap = getattr(blueprint, "snapping_grid_size", None)
    if not snap or not (snap.x and snap.y):
        return None
    offset = getattr(blueprint, "position_relative_to_grid", None)
    offset_x = float(offset.x) if offset is not None else 0.0
    offset_y = float(offset.y) if offset is not None else 0.0
    return (
        bounds.left - offset_x,
        bounds.top - offset_y,
        float(snap.x),
        float(snap.y),
    )


SEVERITY_COLOUR = {"problem": "#e0574f", "suspect": "#e0a23f", "note": "#5b9bd5"}


def _variant_colour(base_hex: str, name: str) -> str:
    """A colour for one specific prototype, near its family's own hue.

    The family palette has ten entries and always will -- it is a hand-picked
    grouping of what a reader looks for, not a catalogue of prototypes. A mod
    that adds another belt tier, another furnace, another turret is still just
    "transport" or "production" to that table, so within a family every
    prototype used to render identically and only the label (or a hover) said
    which was which.

    This nudges the family's hue and lightness by an amount derived from the
    prototype's own name, so a mod's own tiers spread out into visibly
    different shades of the same family colour without this module ever
    having heard of them. The name's bytes are hashed rather than parsed --
    reading "fast-" or "mk2" out of a string would be exactly the kind of
    hardcoding this replaces, and plenty of mods do not name tiers that way at
    all. Two entities of the same prototype always match, because the name is
    all the input there is; two different prototypes collide only by the
    ordinary chance of a hash.
    """
    r, g, b = (int(base_hex[i : i + 2], 16) / 255 for i in (1, 3, 5))
    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    digest = hashlib.sha256(name.encode()).digest()
    hue = (hue + (digest[0] / 255 - 0.5) * (44 / 360)) % 1.0
    lightness = min(0.82, max(0.18, lightness + (digest[1] / 255 - 0.5) * 0.34))
    r2, g2, b2 = colorsys.hls_to_rgb(hue, lightness, saturation)
    return "#" + "".join(f"{round(c * 255):02x}" for c in (r2, g2, b2))


# --------------------------------------------------------------------------
# reading colour off the game's own icons
# --------------------------------------------------------------------------

# name -> resolved colour, or None if nothing could be resolved. Reading and
# decoding a PNG, possibly out of a mod's zip archive, is not free, and every
# instance of the same prototype -- there can be thousands, in one blueprint
# -- asks the same question.
_ICON_COLOUR_CACHE: dict[tuple[str | None, str], str | None] = {}


def _active_profile_mods_dir() -> Path | None:
    """Where the active profile keeps its mod archives, if one is active.

    A plain import at module scope would be fine -- `profile` does not import
    this module back -- but the lookup is wrapped anyway, on the same
    reasoning `inspection.active_dataset` uses: resolving "the active
    profile" can fail in more ways than this function's callers should have
    to think about, and none of them should stop a blueprint from being
    drawn in its fallback colours.
    """
    try:
        from .profile import Profile

        name = Profile.active_profile_name()
        if not name:
            return None
        return Profile.load(name).mods_dir
    except Exception:
        return None


def _icon_bytes(name: str, mods_dir: Path | None) -> bytes | None:
    """The raw bytes of a prototype's own icon file, wherever it lives.

    The path comes from the prototype itself -- `__base__/graphics/...` or
    `__some-mod__/...` -- never guessed from the prototype's name, since a mod
    is free to organise its files however it likes. `__base__`, `__core__`
    and the other packages the game ships with live inside its own data
    directory; anything else is a mod, shipped as a zip archive in the active
    profile's mod folder, its contents under a top-level `name_version` folder
    whose version this does not need to know to search past it.
    """
    icon = entity_data.raw.get(name, {}).get("icon") or item_data.raw.get(name, {}).get("icon")
    if not icon or not icon.startswith("__"):
        return None
    mod_name, marker, rest = icon[2:].partition("__/")
    if not marker or not rest:
        return None

    data_dir = paths.factorio_data_dir()
    if data_dir is not None:
        candidate = data_dir / mod_name / rest
        if candidate.is_file():
            return candidate.read_bytes()

    if mods_dir is None:
        return None
    for archive_path in sorted(mods_dir.glob(f"{mod_name}_*.zip")):
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for member in archive.namelist():
                    _, _, tail = member.partition("/")
                    if tail == rest:
                        return archive.read(member)
        except (OSError, zipfile.BadZipFile):
            continue
    return None


def _dominant_icon_colour(data: bytes) -> str | None:
    """The colour a person would point at and call this icon's colour.

    A plain pixel average washes out to whatever covers the most area, which
    for most Factorio icons is a dark steel frame or drop shadow, not the
    coloured part that actually identifies the thing -- averaging a red belt's
    icon gives a muddy brown, not red. Weighting each pixel by how saturated
    and how bright it is before averaging its hue instead finds the accent
    colour, the way a "dominant colour" picker would.

    Icons that carry mipmaps store several copies of themselves at shrinking
    sizes side by side in one image; only the first, full-size square -- as
    wide as the image is tall -- is read, so the smaller copies do not just
    repeat the same vote.
    """
    if Image is None:
        return None
    try:
        with Image.open(io.BytesIO(data)) as source:
            image = source.convert("RGBA")
            side = min(image.size)
            if side <= 0:
                return None
            square = image.crop((0, 0, side, side)).load()
            pixels = [square[x, y] for y in range(side) for x in range(side)]
    except Exception:
        return None

    sin_sum = cos_sum = sat_sum = val_sum = weight_sum = 0.0
    r_sum = g_sum = b_sum = a_sum = 0.0
    for r, g, b, a in pixels:
        if a < 8:
            continue
        r_sum += r * a
        g_sum += g * a
        b_sum += b * a
        a_sum += a
        hue, sat, val = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        weight = (a / 255) * (sat**1.5) * val
        sin_sum += math.sin(hue * 2 * math.pi) * weight
        cos_sum += math.cos(hue * 2 * math.pi) * weight
        sat_sum += sat * weight
        val_sum += val * weight
        weight_sum += weight

    if a_sum <= 0:
        return None

    if weight_sum <= 0:
        # Nothing saturated at all -- a rail, a slab of concrete. A plain
        # alpha-weighted average is the honest answer for a colourless icon.
        return "#" + "".join(f"{round(c / a_sum):02x}" for c in (r_sum, g_sum, b_sum))

    hue = (math.atan2(sin_sum, cos_sum) / (2 * math.pi)) % 1.0
    sat = min(0.85, sat_sum / weight_sum)
    val = min(0.88, max(0.32, val_sum / weight_sum))
    r2, g2, b2 = colorsys.hsv_to_rgb(hue, sat, val)
    return "#" + "".join(f"{round(c * 255):02x}" for c in (r2, g2, b2))


def _icon_colour(name: str) -> str | None:
    """A prototype's real colour, read from its own icon, if one resolves.

    This is the tool's answer to "the belt is red on the render, why isn't it
    red" -- the family palette groups by category on purpose and was never
    going to match the game's own colours, and a per-tier lookup table would
    just be the same hardcoding by another name, wrong the moment a mod adds
    a tier this table has never seen. Reading the colour out of the icon the
    mod itself shipped needs no table at all.
    """
    mods_dir = _active_profile_mods_dir()
    cache_key = (str(mods_dir) if mods_dir else None, name)
    if cache_key in _ICON_COLOUR_CACHE:
        return _ICON_COLOUR_CACHE[cache_key]

    colour: str | None = None
    try:
        data = _icon_bytes(name, mods_dir)
        if data:
            colour = _dominant_icon_colour(data)
    except Exception:
        colour = None
    _ICON_COLOUR_CACHE[cache_key] = colour
    return colour


def render_svg(blueprint, findings: Iterable = ()) -> str:
    """The blueprint as a standalone SVG element.

    `findings` are marked where they sit. A check can say what is wrong; only
    the picture can say where, and the two are useless apart.
    """
    entities = list(blueprint.entities)
    tiles = list(blueprint.tiles)
    bounds = measure(entities, tiles)

    # A declared grid cell is usually larger than what is in it — a city block
    # is mostly the space it reserves. Drawing only the entities would put the
    # cell boundary off the edge of the picture, which is precisely the thing
    # worth seeing, so the view is widened to hold it.
    cell = snap_cell(blueprint, bounds)
    if cell is not None:
        cell_left, cell_top, cell_w, cell_h = cell
        bounds = Bounds(
            min(bounds.left, math.floor(cell_left)),
            min(bounds.top, math.floor(cell_top)),
            max(bounds.right, math.ceil(cell_left + cell_w)),
            max(bounds.bottom, math.ceil(cell_top + cell_h)),
        )

    width = bounds.width * CELL + MARGIN * 2
    height = bounds.height * CELL + MARGIN * 2

    def px(tile_x: float, tile_y: float) -> tuple[float, float]:
        return (
            (tile_x - bounds.left) * CELL + MARGIN,
            (tile_y - bounds.top) * CELL + MARGIN,
        )

    parts: list[str] = [
        f'<svg id="canvas" viewBox="0 0 {width} {height}" '
        f'data-base="0 0 {width} {height}" width="{width}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="Blueprint, {len(entities)} entities, {bounds}">'
    ]

    # Tiles first: they are the ground everything else stands on.
    for tile in tiles:
        x, y = px(tile.position.x, tile.position.y)
        parts.append(
            f'<rect class="tile" x="{x:.1f}" y="{y:.1f}" width="{CELL}" height="{CELL}">'
            f"<title>{html.escape(tile.name)}</title></rect>"
        )

    # Grid, with a heavier line every five tiles so distances are countable.
    for i in range(bounds.width + 1):
        x, _ = px(bounds.left + i, 0)
        heavy = (bounds.left + i) % 5 == 0
        parts.append(
            f'<line class="grid{" heavy" if heavy else ""}" x1="{x:.1f}" y1="{MARGIN}" '
            f'x2="{x:.1f}" y2="{height - MARGIN}"/>'
        )
    for i in range(bounds.height + 1):
        _, y = px(0, bounds.top + i)
        heavy = (bounds.top + i) % 5 == 0
        parts.append(
            f'<line class="grid{" heavy" if heavy else ""}" x1="{MARGIN}" y1="{y:.1f}" '
            f'x2="{width - MARGIN}" y2="{y:.1f}"/>'
        )

    # Coordinate ruler, every five tiles, in the blueprint's own coordinates.
    for i in range(0, bounds.width + 1, 5):
        x, _ = px(bounds.left + i, 0)
        parts.append(
            f'<text class="ruler" x="{x:.1f}" y="{MARGIN - 8}" text-anchor="middle">'
            f"{bounds.left + i}</text>"
        )
    for i in range(0, bounds.height + 1, 5):
        _, y = px(0, bounds.top + i)
        parts.append(
            f'<text class="ruler" x="{MARGIN - 8}" y="{y + 4:.1f}" text-anchor="end">'
            f"{bounds.top + i}</text>"
        )

    # The declared grid cell, tiled across whatever the picture covers, so a
    # blueprint bigger than its own cell shows the overlap as crossed lines.
    if cell is not None:
        cell_left, cell_top, cell_w, cell_h = cell
        row = cell_top
        while row < bounds.bottom:
            column = cell_left
            while column < bounds.right:
                cx, cy = px(column, row)
                parts.append(
                    f'<rect class="snap" x="{cx:.1f}" y="{cy:.1f}" '
                    f'width="{cell_w * CELL:.1f}" height="{cell_h * CELL:.1f}"/>'
                )
                column += cell_w
            row += cell_h

    # Centres of every entity, in pixels -- needed before entities are drawn,
    # since wires are drawn underneath them, and needed again after, for the
    # underground-pair lines that are drawn on top.
    centres: dict[int, tuple[float, float]] = {}
    for index, entity in enumerate(entities):
        ex, ey = px(entity.tile_position.x, entity.tile_position.y)
        centres[index] = (
            ex + entity.tile_width * CELL / 2,
            ey + entity.tile_height * CELL / 2,
        )

    # Which underground belt or pipe-to-ground pairs with which, so a hover
    # can show the dashed line an otherwise invisible connection deserves.
    entity_pair_id: dict[int, str] = {}
    unique_pairs: list[tuple[int, int]] = []
    for i, j in _underground_pairs(entities).items():
        key = (i, j) if i < j else (j, i)
        pair_id = f"pair-{key[0]}-{key[1]}"
        if key not in unique_pairs:
            unique_pairs.append(key)
        entity_pair_id[i] = pair_id
        entity_pair_id[j] = pair_id

    # Wires -- red and green circuit, copper power -- drawn before entities
    # so a body sits over the end of a line rather than a line crossing over
    # a body.
    index_of: dict[int, int] = {id(e): i for i, e in enumerate(entities)}
    for wire in getattr(blueprint, "wires", None) or []:
        if len(wire) < 4:
            continue
        a, connector_a = _wire_endpoint(wire[0]), wire[1]
        b = _wire_endpoint(wire[2])
        ai, bi = index_of.get(id(a)), index_of.get(id(b))
        if ai is None or bi is None or ai == bi:
            continue
        kind = WIRE_KIND.get(int(connector_a), "red")
        ax, ay = centres[ai]
        bx, by = centres[bi]
        parts.append(
            f'<line class="wire wire-{kind}" x1="{ax:.1f}" y1="{ay:.1f}" '
            f'x2="{bx:.1f}" y2="{by:.1f}"/>'
        )

    # Entities, drawn at their real footprint.
    for index, entity in enumerate(entities):
        family = family_of(entity)
        colour = _icon_colour(entity.name) or _variant_colour(FAMILY_COLOUR[family], entity.name)
        prototype_type = getattr(entity, "type", None) or ""
        x, y = px(entity.tile_position.x, entity.tile_position.y)
        w = entity.tile_width * CELL
        h = entity.tile_height * CELL
        details = _entity_details(entity)

        uses = parameters_used_by(details)

        tooltip = json.dumps(
            {
                "name": entity.name,
                "type": getattr(entity, "type", "") or "",
                "at": f"{int(entity.tile_position.x)}, {int(entity.tile_position.y)}",
                "size": f"{entity.tile_width}×{entity.tile_height}",
                "family": FAMILY_LABEL[family],
                "details": details,
                "parameters": uses,
            },
            ensure_ascii=False,
        )

        classes = "entity parameterised" if uses else "entity"
        data_params = f' data-params="{html.escape(" ".join(uses), quote=True)}"' if uses else ""
        pair_id = entity_pair_id.get(index)
        data_pair = f' data-pair="{pair_id}"' if pair_id else ""
        parts.append(
            f'<g class="{classes}" data-i="{index}"{data_params}{data_pair} '
            f"data-info='{html.escape(tooltip, quote=True)}'>"
        )
        body_classes = "body"
        if prototype_type in TRACK_TYPES:
            body_classes += " track"
        if prototype_type in UNDERGROUND_TYPES:
            body_classes += " buried"
        parts.append(
            f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{w - 2}" height="{h - 2}" '
            f'rx="3" fill="{colour}" class="{body_classes}"/>'
        )
        if uses:
            # A parameterised entity is the point of a parameterised blueprint,
            # so it gets a mark that survives being one tile across.
            parts.append(
                f'<rect class="param-ring" x="{x + 1:.1f}" y="{y + 1:.1f}" '
                f'width="{w - 2}" height="{h - 2}" rx="3"/>'
            )

        direction = getattr(entity, "direction", None)
        reach = inserter_reach(entity) if prototype_type == "inserter" else None
        if prototype_type in TRACK_TYPES:
            # Track reads as rails and ties, not as a coloured block: the
            # shape itself carries straight-versus-curve, which a flat fill
            # the size of the (often oversized) bounding box cannot.
            bend = -1.0 if prototype_type.endswith("-b") else 1.0
            curved = prototype_type in CURVED_TRACK_TYPES
            parts.append(
                _rail_glyph(x + w / 2, y + h / 2, w, h, direction or 0, curved, bend)
            )
        elif reach is not None:
            # Where it actually reaches, not the compass direction -- see
            # `_inserter_glyph` for why the two disagree.
            pickup, drop = reach
            parts.append(_inserter_glyph(px(*pickup), px(*drop), CELL * 0.13))
        else:
            if prototype_type in UNDERGROUND_TYPES:
                parts.append(_hatch(index, x + 1, y + 1, w - 2, h - 2))
            if direction is not None:
                parts.append(_arrow(x + w / 2, y + h / 2, direction, min(w, h) * 0.55))
            if prototype_type in RAIL_POINT_ICON:
                parts.append(
                    _rail_point_icon(
                        x + w / 2, y + h / 2, min(w, h) * 0.28, RAIL_POINT_ICON[prototype_type]
                    )
                )

        # A label normally needs two tiles to have room, but two cases earn one
        # regardless of size: a buried piece, where the type matters most and
        # the footprint is smallest, and anything in the transport family,
        # where a belt tier or a splitter's priority side is otherwise only a
        # colour-and-arrow twin of every other belt.
        show_label = (
            (w >= CELL * 2 and h >= CELL * 2)
            or prototype_type in UNDERGROUND_TYPES
            or family == "transport"
        )
        if show_label:
            font_size = 10 if (w >= CELL * 2 and h >= CELL * 2) else 8
            parts.append(
                f'<text class="label" x="{x + w / 2:.1f}" y="{y + h / 2 + 3:.1f}" '
                f'font-size="{font_size}" text-anchor="middle">'
                f"{html.escape(_short_label(entity.name))}</text>"
            )

        # A native tooltip as well, so the picture still explains itself if the
        # file is opened somewhere that does not run scripts.
        summary = f"{entity.name} at {int(entity.tile_position.x)}, {int(entity.tile_position.y)}"
        if details:
            summary += "\n" + "\n".join(f"{k}: {v}" for k, v in details.items())
        parts.append(f"<title>{html.escape(summary)}</title>")
        parts.append("</g>")

    # Pair lines, drawn over the entities and invisible until a hover asks for
    # one -- see `_underground_pairs`. On top rather than underneath, because
    # a run can span many tiles and the line would otherwise disappear under
    # everything between its two ends.
    for i, j in unique_pairs:
        ax, ay = centres[i]
        bx, by = centres[j]
        parts.append(
            f'<line id="pair-{i}-{j}" class="pair-line" x1="{ax:.1f}" y1="{ay:.1f}" '
            f'x2="{bx:.1f}" y2="{by:.1f}"/>'
        )

    # Findings last, so a marker is never hidden under an entity.
    for finding in findings:
        position = getattr(finding, "position", None)
        if position is None:
            continue
        severity = getattr(getattr(finding, "severity", None), "value", "note")
        colour = SEVERITY_COLOUR.get(severity, SEVERITY_COLOUR["note"])
        fx, fy = px(position[0] + 0.5, position[1] + 0.5)
        summary = getattr(finding, "summary", "")
        parts.append(
            f'<g class="finding"><circle cx="{fx:.1f}" cy="{fy:.1f}" r="{CELL * 0.42:.1f}" '
            f'fill="none" stroke="{colour}" stroke-width="3"/>'
            f'<circle cx="{fx:.1f}" cy="{fy:.1f}" r="{CELL * 0.42:.1f}" fill="{colour}" '
            f'opacity=".18"/>'
            f"<title>{html.escape(f'{severity}: {summary}')}</title></g>"
        )

    parts.append("</svg>")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------

_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f6f6f4; --panel: #ffffff; --ink: #1c1d20; --muted: #6b7078;
  --line: #d9dade; --grid: #c9cbd0; --grid-heavy: #a7aab1; --ground: #e3e4e0;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #17181b; --panel: #1f2126; --ink: #e8e9ec; --muted: #9aa0a8;
    --line: #303339; --grid: #2c2f35; --grid-heavy: #454951; --ground: #24262b;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px; background: var(--bg); color: var(--ink);
  font: 14px/1.5 ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif;
}
h1 { font-size: 18px; margin: 0 0 2px; font-weight: 600; }
.sub { color: var(--muted); margin: 0 0 18px; font-size: 13px; }
.wrap { display: flex; gap: 18px; align-items: flex-start; flex-wrap: wrap; }
.canvas {
  background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  padding: 8px; overflow: auto; max-width: 100%;
}
.side { flex: 1 1 240px; min-width: 240px; max-width: 380px; }
.card {
  background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  padding: 14px; margin-bottom: 14px;
}
.card h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
           color: var(--muted); margin: 0 0 10px; font-weight: 600; }
.legend { display: grid; gap: 6px; }
.legend div { display: flex; align-items: center; gap: 8px; }
.swatch { width: 13px; height: 13px; border-radius: 3px; flex: none; }
.count { margin-left: auto; color: var(--muted); font-variant-numeric: tabular-nums; }
dl { margin: 0; display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; }
dt { color: var(--muted); }
dd { margin: 0; font-variant-numeric: tabular-nums; }
textarea {
  width: 100%; height: 92px; resize: vertical; font: 11px/1.4 ui-monospace, monospace;
  background: var(--bg); color: var(--ink); border: 1px solid var(--line);
  border-radius: 6px; padding: 8px;
}
button {
  margin-top: 8px; padding: 6px 12px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--line); background: var(--bg); color: var(--ink); font: inherit;
}
button:hover { border-color: var(--grid-heavy); }
.grid { stroke: var(--grid); stroke-width: 1; }
.grid.heavy { stroke: var(--grid-heavy); }
.snap { fill: none; stroke: #e0574f; stroke-width: 2; stroke-dasharray: 7 5; opacity: .8; }
.param-ring { fill: none; stroke: #f0c040; stroke-width: 2.5; }
.entity.parameterised .body { stroke: #7a5c00; }
.dim .entity:not(.parameterised) { opacity: .22; }
#canvas { touch-action: none; cursor: grab; }
#canvas.panning { cursor: grabbing; }
.zoom { display: flex; gap: 6px; align-items: center; margin-bottom: 8px; }
.zoom span { color: var(--muted); font-variant-numeric: tabular-nums; margin-left: auto; }
label.toggle { display: flex; gap: 6px; align-items: center; cursor: pointer; margin-top: 10px; }
.warn { color: #c2562e; }
.tile { fill: var(--ground); }
.ruler { fill: var(--muted); font-size: 10px; font-variant-numeric: tabular-nums; }
.label { fill: #10121a; font-size: 10px; font-weight: 600; opacity: .8; }
.dir { fill: #10121a; opacity: .62; }
.entity .body { stroke: rgba(0,0,0,.35); stroke-width: 1; }
.entity:hover .body { stroke: var(--ink); stroke-width: 2; }
.body.track { fill-opacity: .32; }
.body.buried { stroke-dasharray: 4 3; }
.rail-line { stroke: var(--ink); stroke-width: 2; opacity: .8; fill: none; stroke-linecap: round; }
.rail-tie { stroke: var(--ink); stroke-width: 1.6; opacity: .5; }
.rail-point { fill: var(--panel); stroke: var(--ink); stroke-width: 1.6; }
.hatch line { stroke: var(--ink); stroke-width: 1.3; opacity: .32; }
.wire { stroke-width: 1.6; opacity: .75; fill: none; }
.wire-red { stroke: #d9534f; }
.wire-green { stroke: #4caf50; }
.wire-copper { stroke: #c98a3e; }
.inserter-reach { stroke: var(--ink); stroke-width: 1.5; opacity: .5; stroke-dasharray: 2 2; }
.inserter-pickup { fill: none; stroke: var(--ink); stroke-width: 1.6; opacity: .75; }
.inserter-drop { fill: var(--ink); opacity: .85; }
.pair-line {
  stroke: #f0b73a; stroke-width: 2.4; stroke-dasharray: 6 4; fill: none;
  opacity: 0; pointer-events: none; transition: opacity .1s;
}
.pair-line.active { opacity: .9; }
#tip {
  position: fixed; pointer-events: none; opacity: 0; transition: opacity .08s;
  background: var(--panel); border: 1px solid var(--grid-heavy); border-radius: 6px;
  padding: 8px 10px; font-size: 12px; box-shadow: 0 6px 20px rgba(0,0,0,.22);
  max-width: 280px; z-index: 10;
}
#tip b { display: block; margin-bottom: 3px; }
#tip span { color: var(--muted); }
"""

_SCRIPT = """
const tip = document.getElementById('tip');
for (const g of document.querySelectorAll('.entity')) {
  g.addEventListener('mousemove', (event) => {
    const info = JSON.parse(g.dataset.info);
    let body = '<b>' + info.name + '</b>'
      + '<span>' + info.family + ' · ' + info.size + ' · at ' + info.at + '</span>';
    for (const [key, value] of Object.entries(info.details)) {
      body += '<div>' + key + ': ' + value + '</div>';
    }
    tip.innerHTML = body;
    tip.style.opacity = 1;
    const pad = 14;
    let x = event.clientX + pad, y = event.clientY + pad;
    const box = tip.getBoundingClientRect();
    if (x + box.width > innerWidth) x = event.clientX - box.width - pad;
    if (y + box.height > innerHeight) y = event.clientY - box.height - pad;
    tip.style.left = x + 'px';
    tip.style.top = y + 'px';
  });
  g.addEventListener('mouseleave', () => { tip.style.opacity = 0; });
  if (g.dataset.pair) {
    const line = document.getElementById(g.dataset.pair);
    if (line) {
      g.addEventListener('mouseenter', () => line.classList.add('active'));
      g.addEventListener('mouseleave', () => line.classList.remove('active'));
    }
  }
}
const copy = document.getElementById('copy');
if (copy) {
  copy.addEventListener('click', async () => {
    await navigator.clipboard.writeText(document.getElementById('bp').value);
    copy.textContent = 'copied';
    setTimeout(() => { copy.textContent = 'copy blueprint string'; }, 1200);
  });
}

// Pan and zoom by moving the viewBox, so the drawing stays vector-sharp at
// every scale rather than being a bitmap someone is magnifying.
const svg = document.getElementById('canvas');
if (svg) {
  const base = svg.dataset.base.split(' ').map(Number);
  let view = base.slice();
  const readout = document.getElementById('zoomlevel');

  const apply = () => {
    svg.setAttribute('viewBox', view.join(' '));
    if (readout) readout.textContent = Math.round(base[2] / view[2] * 100) + '%';
  };
  const reset = () => { view = base.slice(); apply(); };

  svg.addEventListener('wheel', (event) => {
    event.preventDefault();
    const factor = event.deltaY < 0 ? 0.85 : 1 / 0.85;
    const rect = svg.getBoundingClientRect();
    // Keep the point under the cursor where it is.
    const fx = (event.clientX - rect.left) / rect.width;
    const fy = (event.clientY - rect.top) / rect.height;
    const px = view[0] + view[2] * fx, py = view[1] + view[3] * fy;
    const w = Math.min(base[2] * 8, Math.max(base[2] / 40, view[2] * factor));
    const h = w * base[3] / base[2];
    view = [px - w * fx, py - h * fy, w, h];
    apply();
  }, { passive: false });

  let dragging = null;
  svg.addEventListener('pointerdown', (event) => {
    dragging = { x: event.clientX, y: event.clientY, view: view.slice() };
    svg.setPointerCapture(event.pointerId);
    svg.classList.add('panning');
  });
  svg.addEventListener('pointermove', (event) => {
    if (!dragging) return;
    const rect = svg.getBoundingClientRect();
    const dx = (event.clientX - dragging.x) / rect.width * dragging.view[2];
    const dy = (event.clientY - dragging.y) / rect.height * dragging.view[3];
    view = [dragging.view[0] - dx, dragging.view[1] - dy, dragging.view[2], dragging.view[3]];
    apply();
  });
  const stop = () => { dragging = null; svg.classList.remove('panning'); };
  svg.addEventListener('pointerup', stop);
  svg.addEventListener('pointercancel', stop);

  document.getElementById('zoomin')?.addEventListener('click', () => {
    const w = Math.max(base[2] / 40, view[2] * 0.7);
    view = [view[0] + (view[2] - w) / 2, view[1] + (view[3] - w * base[3] / base[2]) / 2,
            w, w * base[3] / base[2]];
    apply();
  });
  document.getElementById('zoomout')?.addEventListener('click', () => {
    const w = Math.min(base[2] * 8, view[2] / 0.7);
    view = [view[0] + (view[2] - w) / 2, view[1] + (view[3] - w * base[3] / base[2]) / 2,
            w, w * base[3] / base[2]];
    apply();
  });
  document.getElementById('zoomreset')?.addEventListener('click', reset);
  apply();
}

document.getElementById('dim')?.addEventListener('change', (event) => {
  document.querySelector('.canvas').classList.toggle('dim', event.target.checked);
});
"""


def _snapping_card(blueprint, bounds: Bounds) -> str:
    """Grid snapping, which is what makes city blocks tile instead of drift.

    A blueprint can declare a cell it occupies. With absolute snapping the cell
    is pinned to world coordinates, so every copy lands on the same lattice;
    without it the cell only fixes the blueprint's own size. Getting this wrong
    is invisible in a picture of the entities alone, and shows up in game as
    blocks that will not line up.
    """
    snap = getattr(blueprint, "snapping_grid_size", None)
    if not snap or not (snap.x and snap.y):
        return ""

    absolute = bool(getattr(blueprint, "absolute_snapping", False))
    offset = getattr(blueprint, "position_relative_to_grid", None)
    rows = [
        ("grid", f"{snap.x:g} × {snap.y:g} tiles"),
        ("snapping", "absolute — pinned to world coordinates" if absolute else "relative"),
    ]
    if absolute and offset is not None and (offset.x or offset.y):
        rows.append(("offset in grid", f"{offset.x:g}, {offset.y:g}"))
    if getattr(blueprint, "double_grid_aligned", False):
        rows.append(("alignment", "double grid (rails)"))

    note = ""
    if bounds.width > snap.x or bounds.height > snap.y:
        note = (
            '<p class="warn" style="margin:8px 0 0">The contents are larger than '
            "the declared cell, so copies will overlap.</p>"
        )

    body = "\n".join(f"<dt>{k}</dt><dd>{html.escape(v)}</dd>" for k, v in rows)
    return f'<div class="card"><h2>grid snapping</h2><dl>{body}</dl>{note}</div>'


def _field(parameter, *names):
    """Read a parameter field however it happens to be represented.

    Assigning a list of dicts leaves them as dicts; parsing a blueprint string
    yields attrs objects instead. Both reach here, and the game's own spelling
    hyphenates where Python underscores, so every accepted name is tried.
    """
    for name in names:
        if isinstance(parameter, dict):
            if name in parameter:
                return parameter[name]
        else:
            value = getattr(parameter, name, None)
            if value not in (None, ""):
                return value
    return None


def _parameters_card(blueprint) -> str:
    """Parameters, in the order the game will ask for them.

    Order is significant: a parameter's formula can only refer to ones declared
    before it, and that order is what the player is prompted in. Showing the
    list as a numbered sequence rather than a set is the point.
    """
    parameters = list(getattr(blueprint, "parameters", None) or [])
    if not parameters:
        return ""

    # How many entities each parameter actually reaches. A parameter nothing
    # refers to is usually a mistake, and is invisible without counting.
    usage: dict[str, int] = {}
    for entity in blueprint.entities:
        for used in parameters_used_by(_entity_details(entity)):
            usage[used] = usage.get(used, 0) + 1

    rows = []
    for index, parameter in enumerate(parameters):
        name = _field(parameter, "name") or f"parameter-{index}"
        signal = f"parameter-{index}"
        count = usage.get(signal, 0)
        where = (
            f'{count} entit{"y" if count == 1 else "ies"}'
            if count
            else "<span class=\"warn\">unused</span>"
        )
        kind = _field(parameter, "type") or ""
        if kind == "id":
            value = str(_field(parameter, "id") or "(any)")
            ingredient_of = _field(parameter, "ingredient_of", "ingredient-of")
            if ingredient_of:
                value += f" — ingredient of {ingredient_of}"
        else:
            value = str(_field(parameter, "number") or "")
            formula = _field(parameter, "formula")
            if formula:
                value = f"= {formula}"
            variable = _field(parameter, "variable")
            if variable:
                value += f"  ({variable})"
        rows.append(
            f'<div><span class="count" style="margin:0 8px 0 0">{index}</span>'
            f"{html.escape(str(name))}"
            f'<span class="count">{html.escape(value)}</span></div>'
            f'<div style="margin:-2px 0 4px 24px;font-size:12px;color:var(--muted)">{where}</div>'
        )

    return (
        '<div class="card"><h2>parameters, in order</h2>'
        f'<div class="legend">{"".join(rows)}</div>'
        '<label class="toggle"><input type="checkbox" id="dim"> '
        "highlight only parameterised entities</label></div>"
    )


def _findings_card(findings: list) -> str:
    """What the checks said, grouped by how sure they are."""
    if not findings:
        return ""
    groups: dict[str, list] = {}
    for finding in findings:
        severity = getattr(getattr(finding, "severity", None), "value", "note")
        groups.setdefault(severity, []).append(finding)

    blocks = []
    for severity, heading in (
        ("problem", "almost certainly wrong"),
        ("suspect", "worth a look"),
        ("note", "notes"),
    ):
        items = groups.get(severity)
        if not items:
            continue
        colour = SEVERITY_COLOUR[severity]
        rows = "".join(
            f'<div><span class="swatch" style="background:{colour}"></span>'
            f"{html.escape(getattr(f, 'summary', ''))}"
            f'<span class="count">{f.position[0]}, {f.position[1]}</span></div>'
            if getattr(f, "position", None)
            else f'<div><span class="swatch" style="background:{colour}"></span>'
            f"{html.escape(getattr(f, 'summary', ''))}</div>"
            for f in items
        )
        blocks.append(
            f'<div style="margin-bottom:10px"><div style="color:var(--muted);'
            f'font-size:12px;margin-bottom:4px">{heading} ({len(items)})</div>'
            f'<div class="legend">{rows}</div></div>'
        )

    return f'<div class="card"><h2>what the checks found</h2>{"".join(blocks)}</div>'


def render_html(
    blueprint,
    title: str | None = None,
    blueprint_string: str | None = None,
    findings: Iterable = (),
) -> str:
    """A complete, self-contained page showing the blueprint."""
    entities = list(blueprint.entities)
    bounds = measure(entities, blueprint.tiles)

    counts: dict[str, int] = {}
    for entity in entities:
        family = family_of(entity)
        counts[family] = counts.get(family, 0) + 1

    legend = "\n".join(
        f'<div><span class="swatch" style="background:{FAMILY_COLOUR[family]}"></span>'
        f"{FAMILY_LABEL[family]}<span class=\"count\">{count}</span></div>"
        for family, count in sorted(counts.items(), key=lambda kv: -kv[1])
    )

    label = title or getattr(blueprint, "label", None) or "Blueprint"
    tile_count = len(list(blueprint.tiles))

    facts = [
        ("entities", str(len(entities))),
        ("size", str(bounds)),
        ("origin", f"{bounds.left}, {bounds.top}"),
    ]
    if tile_count:
        facts.append(("tiles", str(tile_count)))

    wire_counts: dict[str, int] = {"red": 0, "green": 0, "copper": 0}
    for wire in getattr(blueprint, "wires", None) or []:
        if len(wire) >= 4:
            wire_counts[WIRE_KIND.get(int(wire[1]), "red")] += 1
    if any(wire_counts.values()):
        facts.append(
            (
                "wires",
                f"{wire_counts['red']} red, {wire_counts['green']} green, "
                f"{wire_counts['copper']} copper",
            )
        )
    facts_html = "\n".join(f"<dt>{k}</dt><dd>{html.escape(v)}</dd>" for k, v in facts)

    findings = list(findings)
    snapping_card = _snapping_card(blueprint, bounds)
    parameters_card = _parameters_card(blueprint)
    findings_card = _findings_card(findings)

    if blueprint_string is None:
        try:
            blueprint_string = blueprint.to_string()
        except Exception:  # pragma: no cover - only if the blueprint is unserialisable
            blueprint_string = ""

    string_card = ""
    if blueprint_string:
        string_card = (
            '<div class="card"><h2>blueprint string</h2>'
            f'<textarea id="bp" readonly spellcheck="false">{html.escape(blueprint_string)}</textarea>'
            '<button id="copy">copy blueprint string</button></div>'
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(label)}</title>
<style>{_STYLE}</style>
</head>
<body>
<h1>{html.escape(label)}</h1>
<p class="sub">{len(entities)} entities · {bounds} · drawn by factorio-forge</p>
<div class="wrap">
  <div class="canvas">
    <div class="zoom">
      <button id="zoomout" title="zoom out">−</button>
      <button id="zoomin" title="zoom in">+</button>
      <button id="zoomreset">fit</button>
      <span id="zoomlevel">100%</span>
    </div>
    {render_svg(blueprint, findings)}
  </div>
  <div class="side">
    <div class="card"><h2>about</h2><dl>{facts_html}</dl></div>
    {findings_card}
    {snapping_card}
    {parameters_card}
    <div class="card"><h2>legend</h2><div class="legend">{legend}</div></div>
    {string_card}
  </div>
</div>
<div id="tip"></div>
<script>{_SCRIPT}</script>
</body>
</html>
"""


def write_html(
    blueprint, path: str | Path, title: str | None = None, findings: Iterable = ()
) -> Path:
    """Render to a file and return where it went."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(blueprint, title=title, findings=findings), encoding="utf-8")
    return path
