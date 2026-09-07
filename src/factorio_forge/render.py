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
    angle = int(direction) * 22.5
    half = size / 2
    points = f"0,{-half} {half * 0.8},{half * 0.6} 0,{half * 0.15} {-half * 0.8},{half * 0.6}"
    return (
        f'<polygon points="{points}" class="dir" '
        f'transform="translate({cx:.1f} {cy:.1f}) rotate({angle:g})"/>'
    )


def _entity_details(entity) -> dict:
    """What to show when someone hovers an entity."""
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
    ):
        value = getattr(entity, attribute, None)
        if value not in (None, "none", ""):
            details[label] = str(value)
    quality = getattr(entity, "quality", None)
    if quality and quality != "normal":
        details["quality"] = str(quality)
    return details


def render_svg(blueprint) -> str:
    """The blueprint as a standalone SVG element."""
    entities = list(blueprint.entities)
    tiles = list(blueprint.tiles)
    bounds = measure(entities, tiles)

    width = bounds.width * CELL + MARGIN * 2
    height = bounds.height * CELL + MARGIN * 2

    def px(tile_x: float, tile_y: float) -> tuple[float, float]:
        return (
            (tile_x - bounds.left) * CELL + MARGIN,
            (tile_y - bounds.top) * CELL + MARGIN,
        )

    parts: list[str] = [
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
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

    # Entities, drawn at their real footprint.
    for index, entity in enumerate(entities):
        family = family_of(entity)
        colour = FAMILY_COLOUR[family]
        x, y = px(entity.tile_position.x, entity.tile_position.y)
        w = entity.tile_width * CELL
        h = entity.tile_height * CELL
        details = _entity_details(entity)

        tooltip = json.dumps(
            {
                "name": entity.name,
                "type": getattr(entity, "type", "") or "",
                "at": f"{int(entity.tile_position.x)}, {int(entity.tile_position.y)}",
                "size": f"{entity.tile_width}×{entity.tile_height}",
                "family": FAMILY_LABEL[family],
                "details": details,
            },
            ensure_ascii=False,
        )

        parts.append(
            f'<g class="entity" data-i="{index}" data-info=\'{html.escape(tooltip, quote=True)}\'>'
        )
        parts.append(
            f'<rect x="{x + 1:.1f}" y="{y + 1:.1f}" width="{w - 2}" height="{h - 2}" '
            f'rx="3" fill="{colour}" class="body"/>'
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
"""


def render_html(blueprint, title: str | None = None, blueprint_string: str | None = None) -> str:
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
  <div class="canvas">{render_svg(blueprint)}</div>
  <div class="side">
    <div class="card"><h2>about</h2><dl>{facts_html}</dl></div>
    <div class="card"><h2>legend</h2><div class="legend">{legend}</div></div>
    {string_card}
  </div>
</div>
<div id="tip"></div>
<script>{_SCRIPT}</script>
</body>
</html>
"""


def write_html(blueprint, path: str | Path, title: str | None = None) -> Path:
    """Render to a file and return where it went."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(blueprint, title=title), encoding="utf-8")
    return path
