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

import html
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

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

    # Entities, drawn at their real footprint.
    for index, entity in enumerate(entities):
        family = family_of(entity)
        colour = FAMILY_COLOUR[family]
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
        parts.append(
            f'<g class="{classes}" data-i="{index}"{data_params} '
            f"data-info='{html.escape(tooltip, quote=True)}'>"
        )
        parts.append(
            f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{w - 2}" height="{h - 2}" '
            f'rx="3" fill="{colour}" class="body"/>'
        )
        if uses:
            # A parameterised entity is the point of a parameterised blueprint,
            # so it gets a mark that survives being one tile across.
            parts.append(
                f'<rect class="param-ring" x="{x + 1:.1f}" y="{y + 1:.1f}" '
                f'width="{w - 2}" height="{h - 2}" rx="3"/>'
            )

        direction = getattr(entity, "direction", None)
        if direction is not None:
            parts.append(_arrow(x + w / 2, y + h / 2, direction, min(w, h) * 0.55))

        if w >= CELL * 2 and h >= CELL * 2:
            parts.append(
                f'<text class="label" x="{x + w / 2:.1f}" y="{y + h / 2 + 4:.1f}" '
                f'text-anchor="middle">{html.escape(_short_label(entity.name))}</text>'
            )

        # A native tooltip as well, so the picture still explains itself if the
        # file is opened somewhere that does not run scripts.
        summary = f"{entity.name} at {int(entity.tile_position.x)}, {int(entity.tile_position.y)}"
        if details:
            summary += "\n" + "\n".join(f"{k}: {v}" for k, v in details.items())
        parts.append(f"<title>{html.escape(summary)}</title>")
        parts.append("</g>")

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
