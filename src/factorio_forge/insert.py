"""Library blocks: a block somebody built and kept, pasted into a plan.

A profile can hold a `library/` folder next to its notes, one JSON file per
block (`library/<name>.json`):

    {"name": "gears", "source": "player" | "forge" | "approved",
     "found_from": "where it came from", "why": "why it is good (forge-found)",
     "blueprint": "<blueprint string of the block alone>", "size": [w, h],
     "ports": [{"id": "in-0", "kind": "belt" | "pipe", "dir": "in" | "out",
                "at": [x, y], "direction": 0 | 4 | 8 | 12,
                "items": [left, right] or ["fluid"], "rate": 1.5}],
     "shape": {"machine": "...", "recipe": "...", "solid_in": 1, "fluid_in": 0,
               "solid_out": 1, "fluid_out": 0, "tiers": {"belt": "...", ...}},
     "measured": {"working_share": 0.9, "rate": 1.2}}

`at` is relative to the block's top-left tile. A plan block

    {"library": "gears", "at": [x, y], "rotate": 0, "mirror": false, "id": "gears",
     "recipe": "other-recipe"}

pastes it: mirrored first (left to right), then turned clockwise, then put with
its corner at `at`. Its ports come out transformed, numbered as in the entry, and
work in `connections` and `sources` like the ports of a rows block. `recipe`
swaps the recipe of the block's machines when the new recipe has the same
number of solid and fluid ingredients and products; the port goods and rates
follow.

`choose` is what `chain` asks: a block of the same shape as a stage, and so
whether it can stand in for the rows block the chain would have made.

Only the block is kept. The wires a blueprint carries between its entities
(circuit wires) are not: the entities are copied one by one.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from draftsman.data import entities as entity_data
from draftsman.data import recipes as recipe_data

from . import layout, rows
from .categories import crafts
from .plan import PlanError

LIBRARY_DIR = "library"

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DIRECTIONS = (0, 4, 8, 12)


def check_name(name) -> str:
    """A library name: lowercase letters, digits and dashes. Never a path."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise PlanError(f"library block name {name!r}: lowercase letters, digits and dashes only")
    return name


def library_dir() -> Path:
    """The active profile's library folder (it need not exist yet)."""
    from . import paths
    from .profile import Profile

    active = Profile.active_profile_name()
    if not active:
        raise PlanError("no profile is active, so there is no block library; activate one with activate-profile")
    return paths.profile_dir(active) / LIBRARY_DIR


def read_entry(name: str, directory: Path | None = None) -> dict:
    """The library entry `name`, checked; one-line PlanError when missing or malformed."""
    check_name(name)
    folder = Path(directory) if directory is not None else library_dir()
    path = folder / f"{name}.json"
    if not path.is_file():
        have = sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []
        raise PlanError(f"library block {name!r} does not exist (the library has: {', '.join(have) or 'nothing'})")
    try:
        entry = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise PlanError(f"library block {name!r} cannot be read: {exc}") from exc
    _check_entry(name, entry)
    entry["name"] = name
    return entry


def entries(directory: Path | None = None, skipped: list[str] | None = None) -> list[dict]:
    """Every readable entry of the library, by name; broken files are left out.

    When `skipped` is a list, a line for each file left out ("library: <name> skipped: <why>") is added to it.
    """
    try:
        folder = Path(directory) if directory is not None else library_dir()
    except (PlanError, OSError):
        return []
    found = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if not _NAME.fullmatch(path.stem):
            continue
        try:
            found.append(read_entry(path.stem, folder))
        except PlanError as exc:
            if skipped is not None:
                skipped.append(f"library: {path.stem} skipped: {str(exc).split(': ', 1)[-1]}")
            continue
    return found


def _check_entry(name: str, entry) -> None:
    def bad(why: str):
        return PlanError(f"library block {name!r}: {why}")

    if not isinstance(entry, dict):
        raise bad("the file is not an object")
    if not isinstance(entry.get("blueprint"), str) or not entry["blueprint"]:
        raise bad("'blueprint' is missing")
    size = entry.get("size")
    if size is not None and not (isinstance(size, list) and len(size) == 2
                                 and all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in size)):
        raise bad("'size' is [width, height]")
    ports = entry.get("ports", [])
    if not isinstance(ports, list):
        raise bad("'ports' is a list")
    for i, port in enumerate(ports):
        if not isinstance(port, dict):
            raise bad(f"port {i} is not an object")
        at = port.get("at")
        if not (isinstance(at, list) and len(at) == 2 and all(isinstance(n, int) and not isinstance(n, bool) for n in at)):
            raise bad(f"port {i}: 'at' is [x, y]")
        if port.get("kind") not in ("belt", "pipe") or port.get("dir") not in ("in", "out"):
            raise bad(f"port {i}: 'kind' is belt or pipe, 'dir' is in or out")
        if port.get("direction") not in _DIRECTIONS or isinstance(port.get("direction"), bool):
            raise bad(f"port {i}: 'direction' is 0, 4, 8 or 12")
        items = port.get("items", [])
        if not (isinstance(items, list) and all(isinstance(t, str) for t in items)):
            raise bad(f"port {i}: 'items' is a list of names")
        rate = port.get("rate", 0)
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate < 0:
            raise bad(f"port {i}: 'rate' is a number of at least 0")
    shape = entry.get("shape")
    if shape is not None:
        if not isinstance(shape, dict):
            raise bad("'shape' is an object")
        for key in ("machine", "recipe"):
            if shape.get(key) is not None and not isinstance(shape[key], str):
                raise bad(f"shape: '{key}' is a name")
        for key in ("solid_in", "fluid_in", "solid_out", "fluid_out"):
            value = shape.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise bad(f"shape: '{key}' is a whole number of at least 0")
        tiers = shape.get("tiers")
        if tiers is not None and not (isinstance(tiers, dict)
                                      and all(isinstance(k, str) and isinstance(v, str) for k, v in tiers.items())):
            raise bad("shape: 'tiers' maps a role to a name")


# --------------------------------------------------------------------------
# recipes: the shape, and what a swap changes
# --------------------------------------------------------------------------


def _count(value) -> int:
    return int(value or 0)


def recipe_shape(recipe: str) -> dict[str, int]:
    """How many solid and fluid ingredients and products a recipe has."""
    data = recipe_data.raw.get(recipe)
    if data is None:
        raise PlanError(f"{recipe!r} is not a recipe in the active data")
    counts = {"solid_in": 0, "fluid_in": 0, "solid_out": 0, "fluid_out": 0}
    for key, parts in (("in", data.get("ingredients") or []), ("out", _products(data))):
        for part in parts:
            counts[("fluid_" if part.get("type") == "fluid" else "solid_") + key] += 1
    return counts


def _products(data: dict) -> list[dict]:
    if data.get("results"):
        return list(data["results"])
    return [{"name": data["result"], "type": "item"}] if data.get("result") else []


def _goods(recipe: str, direction: str) -> list[str]:
    """Solid goods first, then fluids, each in the recipe's own order."""
    data = recipe_data.raw[recipe]
    parts = data.get("ingredients") or [] if direction == "in" else _products(data)
    return ([p["name"] for p in parts if p.get("type") != "fluid"]
            + [p["name"] for p in parts if p.get("type") == "fluid"])


def _good_map(old: str, new: str, direction: str) -> dict[str, str]:
    """The good in `new` that takes the place of each good in `old`, by position."""
    return {a: b for a, b in zip(_goods(old, direction), _goods(new, direction)) if a != b}


def swappable(machine: str, old: str, new: str) -> str | None:
    """None when `machine` can make `new` where it made `old`, otherwise why not."""
    if new not in recipe_data.raw:
        return f"{new!r} is not a recipe in the active data"
    if recipe_shape(old) != recipe_shape(new):
        return f"{new} and {old} differ in how many goods they take and make"
    raw = entity_data.raw.get(machine)
    if raw is None or not crafts(raw, recipe_data.raw[new]):
        return f"{machine} cannot make {new}"
    return None


# --------------------------------------------------------------------------
# pasting
# --------------------------------------------------------------------------


@dataclass
class Pasted:
    entry: str
    placed: list[rows.Placement]
    objects: list  # the draftsman entities, positioned, aligned with `placed`
    ports: list[rows.Port]
    width: int
    height: int
    machine: str
    recipe: str
    machines: int
    notes: list[str] = field(default_factory=list)
    prototypes: dict[str, str | None] = field(default_factory=dict)


def _bounds(blueprint) -> tuple[list, int, int]:
    ents = list(blueprint.entities)
    if not ents:
        raise PlanError("the library block's blueprint holds no entities")
    minx = min(int(e.tile_position.x) for e in ents)
    miny = min(int(e.tile_position.y) for e in ents)
    return ents, minx, miny


def _prototype_of(names, kind: str) -> str | None:
    seen: dict[str, int] = {}
    for n in names:
        if (entity_data.raw.get(n) or {}).get("type") == kind:
            seen[n] = seen.get(n, 0) + 1
    return max(seen, key=lambda n: seen[n]) if seen else None


def _swap_items(items: tuple[str, ...], mapping: dict[str, str]) -> tuple[str, ...]:
    return tuple("+".join(mapping.get(part, part) for part in lane.split("+")) if lane else lane for lane in items)


def _flow(recipe: str, machine: str, direction: str, goods: set[str], fluid: bool) -> float:
    func = layout.fluid_flows if fluid else layout.solid_flows
    return sum(f.rate for f in func(recipe, machine, 1) if f.direction == direction and f.item in goods)


def paste(entry: dict, at=(0, 0), rotate: int = 0, mirror: bool = False, recipe: str | None = None,
          name: str | None = None) -> Pasted:
    """The library entry's entities and ports, mirrored, turned clockwise by `rotate` and with the corner at `at`."""
    from draftsman.blueprintable import Blueprint

    name = name or entry.get("name") or "?"
    if isinstance(rotate, bool) or rotate not in (0, 90, 180, 270):
        raise PlanError(f"library block {name!r}: rotate is 0, 90, 180 or 270")
    if not isinstance(mirror, bool):
        raise PlanError(f"library block {name!r}: mirror is true or false")
    turns = rotate // 90
    try:
        blueprint = Blueprint.from_string(entry["blueprint"])
    except Exception as exc:  # noqa: BLE001 - draftsman raises several kinds for a bad string
        raise PlanError(f"library block {name!r}: the blueprint cannot be read ({exc.__class__.__name__})") from exc
    ents, minx, miny = _bounds(blueprint)
    width = max(max(int(e.tile_position.x) - minx + e.tile_width for e in ents), (entry.get("size") or [0, 0])[0])
    height = max(max(int(e.tile_position.y) - miny + e.tile_height for e in ents), (entry.get("size") or [0, 0])[1])

    shape = entry.get("shape") or {}
    machine = shape.get("machine") or ""
    old = shape.get("recipe") or ""
    new = old
    notes = [f"from the library: {name}"]
    mapping_in: dict[str, str] = {}
    mapping_out: dict[str, str] = {}
    if recipe and recipe != old:
        if not (machine and old):
            raise PlanError(f"library block {name!r} does not say its machine and recipe, so its recipe cannot be swapped")
        why = swappable(machine, old, recipe)
        if why:
            raise PlanError(f"library block {name!r}: recipe {recipe}: {why}")
        new = recipe
        mapping_in, mapping_out = _good_map(old, new, "in"), _good_map(old, new, "out")
        notes.append(f"recipe swapped from {old} to {new}")

    def rect(x, y, w, h):
        for _ in range(turns):
            x, y, w, h = -(y + h), x, h, w
        return x, y, w, h

    def place(x, y, w, h):
        if mirror:
            x = width - x - w
        return rect(x, y, w, h)

    corner = rect(0, 0, width, height)
    dx, dy = at[0] - corner[0], at[1] - corner[1]
    out_w, out_h = (width, height) if turns % 2 == 0 else (height, width)

    placed: list[rows.Placement] = []
    objects = []
    machines = 0
    for e in ents:
        w, h = e.tile_width, e.tile_height
        x, y, w2, h2 = place(int(e.tile_position.x) - minx, int(e.tile_position.y) - miny, w, h)
        obj = copy.deepcopy(e)
        direction = int(getattr(e, "direction", 0) or 0)
        if getattr(e, "rotatable", False):
            direction = ((16 - direction) % 16 if mirror else direction)
            direction = (direction + 4 * turns) % 16
            obj.direction = direction
        if mirror and (entity_data.raw.get(e.name) or {}).get("type") == "assembling-machine" \
                and getattr(e, "mirror", None) is not None:
            obj.mirror = not e.mirror
        if mirror:
            for attr in ("input_priority", "output_priority"):
                side = getattr(e, attr, None)
                if side in ("left", "right"):
                    setattr(obj, attr, "right" if side == "left" else "left")
        obj.tile_position = (x + dx, y + dy)
        is_machine = e.name == machine
        machines += is_machine
        if is_machine and new != old:
            obj.recipe = new
        placed.append(rows.Placement(e.name, x + dx, y + dy, direction, getattr(obj, "recipe", None), w2, h2))
        objects.append(obj)

    ports: list[rows.Port] = []
    for p in entry.get("ports") or []:
        px, py, _, _ = place(p["at"][0], p["at"][1], 1, 1)
        direction = (16 - p["direction"]) % 16 if mirror else p["direction"]
        direction = (direction + 4 * turns) % 16
        items = tuple(p.get("items") or ())
        rate = float(p.get("rate") or 0.0)
        if p["kind"] == "belt":
            items = items + ("",) * (2 - len(items)) if len(items) < 2 else items
            if mirror:
                items = tuple(reversed(items))
        if new != old:
            items = _swap_items(items, mapping_in if p["dir"] == "in" else mapping_out)
            rate = _rescaled(rate, p, old, new, machine, mapping_in if p["dir"] == "in" else mapping_out)
        ports.append(rows.Port(p["kind"], p["dir"], items, px + dx, py + dy, direction, rate))

    names = [e.name for e in ents]
    return Pasted(
        entry=name, placed=placed, objects=objects, ports=ports, width=out_w, height=out_h, machine=machine,
        recipe=new, machines=machines, notes=notes,
        prototypes={"belt": _prototype_of(names, "transport-belt"), "pipe": _prototype_of(names, "pipe"),
                    "pipe_to_ground": _prototype_of(names, "pipe-to-ground")},
    )


def _rescaled(rate: float, port: dict, old: str, new: str, machine: str, mapping: dict[str, str]) -> float:
    """A port's rate for the swapped recipe: the old rate times how much more (or less) the new goods flow."""
    if not rate or not machine:
        return rate
    direction = port["dir"]
    fluid = port["kind"] == "pipe"
    carried = {part for lane in port.get("items") or () for part in lane.split("+") if part}
    pool = {g for g in _goods(old, direction) if _is_fluid(old, g, direction) == fluid}
    if not carried & pool:
        carried = pool
    carried &= pool
    try:
        before = _flow(old, machine, direction, carried, fluid)
        after = _flow(new, machine, direction, {mapping.get(g, g) for g in carried}, fluid)
    except layout.LayoutError:
        return rate
    return rate * after / before if before else rate


def _is_fluid(recipe: str, good: str, direction: str) -> bool:
    data = recipe_data.raw[recipe]
    parts = data.get("ingredients") or [] if direction == "in" else _products(data)
    return any(p["name"] == good and p.get("type") == "fluid" for p in parts)


def paste_block(block: dict, index: int, directory: Path | None = None) -> Pasted:
    """A plan's library block, pasted; PlanError (one line, with the block's number) when it cannot be."""
    name = block.get("library")
    try:
        entry = read_entry(name, directory)
        at = block.get("at") or (0, 0)
        if not (isinstance(at, (list, tuple)) and len(at) == 2
                and all(isinstance(n, int) and not isinstance(n, bool) for n in at)):
            raise PlanError("'at' is [x, y] in whole tiles")
        return paste(entry, tuple(at), block.get("rotate", 0), block.get("mirror", False), block.get("recipe"), name)
    except PlanError as exc:
        text = str(exc)
        raise PlanError(text if text.startswith("block ") else f"block {index}: {text}") from exc


# --------------------------------------------------------------------------
# what chain asks
# --------------------------------------------------------------------------


@dataclass
class Choice:
    name: str
    entry: dict
    swapped: bool
    machines: int
    rotate: int = 0
    mirror: bool = False


def west_to_east(entry: dict) -> tuple[int, bool] | None:
    """The first (rotate, mirror) that makes every belt in port enter from the west and every out port leave east.

    That is how a rows block stands at rotate 0 and what the chain's layout assumes. None when no turn fits.
    """
    for mirror in (False, True):
        for rotate in (0, 90, 180, 270):
            try:
                pasted = paste(entry, (0, 0), rotate, mirror)
            except PlanError:
                return None
            belts = [p for p in pasted.ports if p.kind == "belt"]
            if all(p.direction == 4 and p.x == (0 if p.io == "in" else pasted.width - 1) for p in belts):
                return rotate, mirror
    return None


def _machines_in(entry: dict, machine: str) -> int:
    from draftsman.blueprintable import Blueprint

    try:
        return sum(e.name == machine for e in Blueprint.from_string(entry["blueprint"]).entities)
    except Exception:  # noqa: BLE001
        return 0


def choose(recipe: str, machine: str, tiers: dict[str, str], machines_needed: int,
           directory: Path | None = None) -> tuple[Choice | None, list[str]]:
    """A library block of the stage's shape: the same machine, the same count of goods in and out, the same tiers.

    A block that already makes `recipe` goes first, then one whose recipe can be swapped for it; among those,
    the smallest with at least `machines_needed` machines. Also says why blocks of the right shape were passed over.
    """
    skipped: list[str] = []
    try:
        wanted = recipe_shape(recipe)
    except PlanError:
        return None, skipped
    found: list[tuple[int, int, Choice]] = []
    for entry in entries(directory, skipped):
        shape = entry.get("shape") or {}
        if shape.get("machine") != machine or not shape.get("recipe"):
            continue
        if any(_count(shape.get(k)) != n for k, n in wanted.items() if k in shape) \
                or any(k not in shape for k in wanted):
            continue
        listed = shape.get("tiers") or {}
        if any(tiers.get(role) not in (None, tier) for role, tier in listed.items() if role in tiers):
            continue
        swapped = shape["recipe"] != recipe
        if swapped:
            try:
                why = swappable(machine, shape["recipe"], recipe)
            except PlanError:
                continue
            if why:
                continue
        count = _machines_in(entry, machine)
        if count < machines_needed:
            skipped.append(f"library block {entry.get('name')} has {count} machine(s), the stage needs {machines_needed}")
            continue
        if count > 2 * machines_needed:
            skipped.append(f"library block {entry.get('name')} has {count} machine(s), far more than the "
                           f"{machines_needed} the stage needs")
            continue
        turn = west_to_east(entry)
        if turn is None:
            skipped.append(f"library block {entry.get('name')}: ports do not run west to east")
            continue
        found.append((int(swapped), count, Choice(str(entry.get("name")), entry, swapped, count, *turn)))
    if not found:
        return None, skipped
    found.sort(key=lambda t: (t[0], t[1], t[2].name))
    return found[0][2], skipped
