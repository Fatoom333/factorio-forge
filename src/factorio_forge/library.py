"""The block library: working blocks of a base, kept in the profile and found again by name.

A block is a piece of a base that works on its own: machines, the belts and
pipes between them, and a few places where belts and pipes cross its edge to
the rest of the base. The library keeps such a block as a blueprint of the
block alone, together with what a plan needs to know to put it somewhere and
join it up: its size, its **ports**, and its **shape** (the machine, the
recipe, how many solid and fluid goods go in and out).

One file per block, ``library/<name>.json`` in the profile folder, next to the
profile's notes::

    {"name": "engines", "source": "player" | "forge" | "approved",
     "found_from": "where it came from", "why": "why it is good (forge-found)",
     "blueprint": "<blueprint string of the block alone>", "size": [w, h],
     "ports": [{"id": "belt-in-1", "kind": "belt" | "pipe", "dir": "in" | "out",
                "at": [x, y],        # relative to the block's top-left tile
                "direction": 0 | 4 | 8 | 12,   # the way the flow goes
                "items": [left, right] | [fluid],  "rate": items (or units) per second}],
     "shape": {"machine", "recipe", "solid_in", "fluid_in", "solid_out", "fluid_out", "tiers": {...}},
     "measured": {"working_share", "rate"}}

``items`` of a belt port is the contents of its two lanes seen along the flow,
``""`` for a lane nothing uses; a name stands for one item and ``a+b`` for
several. A pipe port holds the one fluid's name. Optional fields: ``item_rates``,
``lanes_unknown`` and ``also_at`` (the other ends of the same pipe line) on a port, ``machines`` and ``recipes`` in the shape,
``plan_targets`` in ``measured`` (the whole plan's delivered rates; ``rate`` is the block's own),
``notes`` and ``check`` on the block.

Ports found in a cut-out blueprint
----------------------------------

The block's box is the bounding box of all its entities. A belt tile on the
box's edge is a port where the flow crosses the edge: **out** when the tile
ahead of it lies outside the box, **in** when the tile behind it does and no
belt inside the box feeds it. A pipe is a port where a pipe line ends on the
edge and its open end points out of the box (a pipe-to-ground whose above-ground
side points out is one too). Which way a fluid goes is the recipe's: a network
whose machine boxes take the fluid as an ingredient is **in**, one that the
machines make is **out**; a network that does both is left out with a note.
Lanes of an out port come from the lane tracer. An in port holds what the
machines downstream take (an inserter picks from either lane, so the lanes of an
in port are not told apart). Rates are what the machines could take or make at
their base speed: a capacity, not a measured flow.
"""

from __future__ import annotations

import json
import os
import re
import warnings
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from draftsman.data import entities as entity_data
from draftsman.data import recipes as recipe_data

from . import fluids, inspection, lanes, layout
from .inspection import STEP, Layout, Severity

SOURCES = ("player", "forge", "approved")
OPPOSITE = {0: 8, 4: 12, 8: 0, 12: 4}
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# names Windows will not take as a file name, whatever the extension
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class LibraryError(Exception):
    """The block cannot be saved, found or read; the message says why."""


# --------------------------------------------------------------------------
# names and files
# --------------------------------------------------------------------------


def check_name(name: str) -> str:
    """The name if it is a safe library name: lowercase letters, digits and dashes, no path."""
    if not isinstance(name, str) or not _NAME.match(name) or name.endswith("-") or "--" in name:
        raise LibraryError(
            f"{name!r} is not a library name: use lowercase letters, digits and single dashes "
            "(for example 'engine-line'), at most 64 characters, starting with a letter or digit"
        )
    if name in _RESERVED:
        raise LibraryError(f"{name!r} is a name Windows keeps for devices; pick another")
    return name


def path_of(folder: Path, name: str) -> Path:
    """Where the block called `name` lives; never a path built from a raw name."""
    check_name(name)
    folder = Path(folder)
    path = folder / f"{name}.json"
    if path.parent != folder:  # cannot happen for a checked name; kept as the last guard
        raise LibraryError(f"{name!r} does not name a file in the library")
    return path


def _write(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def load(folder: Path, name: str) -> dict:
    path = path_of(folder, name)
    if not path.is_file():
        have = ", ".join(n for n, _ in entries(folder)[0]) or "none"
        raise LibraryError(f"no block called {name!r} in {Path(folder)} (the library has: {have})")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise LibraryError(f"cannot read {path}: {exc}") from exc


def entries(folder: Path) -> tuple[list[tuple[str, dict]], list[str]]:
    """Every block in the folder, by name, and a line for each file that could not be read."""
    folder = Path(folder)
    found, problems = [], []
    if not folder.is_dir():
        return found, problems
    for path in sorted(folder.glob("*.json")):
        name = path.stem
        try:
            check_name(name)
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
        except (LibraryError, OSError, ValueError) as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        found.append((name, data))
    return found, problems


def remove(folder: Path, name: str) -> None:
    path = path_of(folder, name)
    if not path.is_file():
        raise LibraryError(f"no block called {name!r} in {Path(folder)}")
    path.unlink()


# --------------------------------------------------------------------------
# reading a blueprint
# --------------------------------------------------------------------------


def is_file(source: str) -> bool:
    """Whether `source` names an existing file (and so is not a blueprint string, whatever it starts with)."""
    try:
        return Path(source).is_file()
    except (OSError, ValueError):  # a long blueprint string is no file name; neither is one with a NUL
        return False


def read_blueprint(source: str, index: int | None = None):
    """A Blueprint from a string or from a file holding one (a region export, a saved string)."""
    from draftsman.blueprintable import Blueprint, BlueprintBook, get_blueprintable_from_string

    text = source
    if is_file(source):
        text = Path(source).read_text(encoding="utf-8-sig")
    elif not source.lstrip().startswith("0"):
        raise LibraryError(f"{source!r} is neither a blueprint string nor a file")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # overlaps and unknown names are the check's to report
            found = get_blueprintable_from_string(text.strip())
    except Exception as exc:  # noqa: BLE001 - the library underneath says nothing a person can use
        raise LibraryError(f"that is not a blueprint string ({exc.__class__.__name__}); an export that was cut short does not decode") from exc
    if isinstance(found, BlueprintBook):
        contents = list(found.blueprints)
        if index is None:
            raise LibraryError(f"that is a blueprint book of {len(contents)} entries; choose one with --index 0..{len(contents) - 1}")
        if not 0 <= index < len(contents):
            raise LibraryError(f"the book has {len(contents)} entries, numbered 0 to {len(contents) - 1}")
        found = contents[index]
    if not isinstance(found, Blueprint):
        raise LibraryError(f"a {type(found).__name__} holds no entities")
    if not len(found.entities):
        raise LibraryError("the blueprint has no entities")
    return found


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Box:
    left: int
    top: int
    right: int  # inclusive
    bottom: int

    @property
    def size(self) -> list[int]:
        return [self.right - self.left + 1, self.bottom - self.top + 1]

    def has(self, tile) -> bool:
        return self.left <= tile[0] <= self.right and self.top <= tile[1] <= self.bottom

    def corner(self, tile) -> bool:
        return tile[0] in (self.left, self.right) and tile[1] in (self.top, self.bottom)

    def rel(self, tile) -> list[int]:
        return [int(tile[0]) - self.left, int(tile[1]) - self.top]


def bounding_box(entities: list) -> Box:
    tiles = [t for e in entities for t in Layout.tiles_of(e)]
    xs, ys = [t[0] for t in tiles], [t[1] for t in tiles]
    return Box(min(xs), min(ys), max(xs), max(ys))


def entity_tile(entity) -> tuple[int, int]:
    return Layout.tile_of(entity)


def _step(tile, direction: int):
    dx, dy = STEP[direction]
    return tile[0] + dx, tile[1] + dy


# --------------------------------------------------------------------------
# what the machines do
# --------------------------------------------------------------------------


def _amount(part: dict) -> float:
    if "amount" in part:
        value = float(part["amount"])
    else:
        value = (float(part.get("amount_min", 0)) + float(part.get("amount_max", 0))) / 2
    return value * float(part.get("probability", 1))


def _flows(machines: list) -> tuple[dict[str, float], dict[str, float]]:
    """What the machines take and make per second at their base speed: (needs, makes) by good."""
    needs: Counter = Counter()
    makes: Counter = Counter()
    for entity in machines:
        recipe = recipe_data.raw.get(entity.recipe) or {}
        try:
            per_second = layout.crafts_per_second(entity.recipe, entity.name, 0.0)
        except Exception:  # noqa: BLE001 - a machine the layout code cannot rate adds no rate
            continue
        for part in recipe.get("ingredients") or []:
            needs[part["name"]] += _amount(part) * per_second
        for part in recipe.get("results") or []:
            makes[part["name"]] += _amount(part) * per_second
    return dict(needs), dict(makes)


def _parts(recipe: str | None, key: str) -> tuple[int, int]:
    """(solid, fluid) goods in a recipe's ingredients or results."""
    parts = (recipe_data.raw.get(recipe) or {}).get(key) or []
    fluid = sum(1 for p in parts if p.get("type") == "fluid")
    return len(parts) - fluid, fluid


def _commonest(names) -> str | None:
    counted = Counter(n for n in names if n)
    return counted.most_common(1)[0][0] if counted else None


def shape_of(entities: list, ports: list[dict], hint: tuple[str, str] | None = None) -> dict:
    """The block's form: machine, recipe, counts of goods in and out, and the tiers of what it is built from.

    `hint` is (machine, recipe) from the builder of the block, for machines that carry no recipe in the blueprint
    (a furnace takes its recipe from what it is given).
    """
    machines = [e for e in entities if getattr(e, "recipe", None)]
    machine = _commonest(e.name for e in machines)
    recipe = _commonest(e.recipe for e in machines if e.name == machine)
    if hint and machine is None and any(e.name == hint[0] for e in entities):
        machine, recipe = hint
        machines = [e for e in entities if e.name == machine]
    if recipe is not None:
        solid_in, fluid_in = _parts(recipe, "ingredients")
        solid_out, fluid_out = _parts(recipe, "results")
    else:
        # machines that pick their own recipe (furnaces): the goods at the ports are all there is to count
        def count(io: str, kind: str) -> int:
            goods = {i for p in ports if p["dir"] == io and p["kind"] == kind for i in p["items"] for i in i.split("+") if i}
            return len(goods)
        solid_in, fluid_in = count("in", "belt"), count("in", "pipe")
        solid_out, fluid_out = count("out", "belt"), count("out", "pipe")
    by_type = {
        "belt": "transport-belt", "underground": "underground-belt", "splitter": "splitter",
        "inserter": "inserter", "pole": "electric-pole", "pipe": "pipe", "pipe_to_ground": "pipe-to-ground",
    }
    tiers = {"machine": machine} if machine else {}
    for label, kind in by_type.items():
        name = _commonest(e.name for e in entities if getattr(e, "type", "") == kind)
        if name:
            tiers[label] = name
    shape = {
        "machine": machine, "recipe": recipe,
        "solid_in": solid_in, "fluid_in": fluid_in, "solid_out": solid_out, "fluid_out": fluid_out,
        "tiers": tiers, "machines": sum(1 for e in machines if e.name == machine),
    }
    others = sorted({e.recipe for e in machines if getattr(e, "recipe", None)} - {recipe})
    if others:
        shape["recipes"] = sorted({e.recipe for e in machines if getattr(e, "recipe", None)})
    return shape


# --------------------------------------------------------------------------
# ports
# --------------------------------------------------------------------------


def _belt_ports(entities: list, box: Box, lane_map: lanes.LaneMap, notes: list[str]) -> list[dict]:
    """Belts that cross the edge carrying something: an out port holds what the block puts on it, an in port feeds machines.

    A line of the block that merely ends at the edge (the tail of an input line, the head of an output line) crosses
    it too, but takes or gives nothing, so it is no port. A machine that picks its own recipe (a furnace) feeds a belt
    with something the tracer cannot name: such a port is kept with empty lanes and `lanes_unknown`.
    """
    drops = set()
    for entity in entities:
        if getattr(entity, "type", "") == "inserter":
            reach = inspection.inserter_reach(entity)
            if reach is not None:
                drops.add(lanes._floor(reach[1]))
    # an underground entrance and exit that belong together, both inside the box: a tunnel, not a way out of the box
    paired = set()
    for target, sources in lane_map.behind.items():
        if lane_map.nodes[target].kind == "exit":
            paired.add(target)
            paired.update(sources)
    found = []
    for tile, node in sorted(lane_map.nodes.items()):
        ahead = _step(tile, node.direction)
        behind = _step(tile, OPPOSITE[node.direction])
        fed = bool(lane_map.behind.get(tile)) or bool(lane_map.sides.get(tile))
        # an entrance whose exit is not in the block (it lies beyond the edge) is where the line leaves
        leaves = node.kind in ("belt", "splitter", "exit") or (node.kind == "entrance" and tile not in paired)
        if leaves and not box.has(ahead):
            left, right = lane_map.at(tile)
            put_here = bool((left.unknown | right.unknown) & drops)
            if left.items or right.items or put_here:
                found.append({
                    "kind": "belt", "dir": "out", "tile": tile, "direction": node.direction,
                    "items": [_join(left.items), _join(right.items)], "lanes_unknown": put_here,
                })
            else:
                notes.append(f"the belt leaving the box at {tuple(box.rel(tile))} carries nothing the block makes; taken for the tail of an input line")
        # an exit whose entrance is not in the block (it lies beyond the edge) is where the line comes in
        arrives = (node.kind in ("belt", "splitter", "entrance") and not box.has(behind) and not fed) or (
            node.kind == "exit" and tile not in paired)
        if arrives:
            if node.kind == "exit" and not (box.has(ahead) and not box.has(behind)):
                notes.append(f"the underground exit at {tuple(box.rel(tile))} has its entrance outside the block; its port stands at the exit")
            wanted, anything = _taken_downstream(lane_map, tile)
            if wanted or anything:
                found.append({
                    "kind": "belt", "dir": "in", "tile": tile, "direction": node.direction,
                    "items": [_join(wanted)] * 2, "lanes_unknown": len(wanted) > 1 or anything,
                })
            else:
                notes.append(f"the belt entering the box at {tuple(box.rel(tile))} feeds no machine; taken for the head of an output line")
    return found


def _join(items) -> str:
    return "+".join(sorted(items))


def _taken_downstream(lane_map: lanes.LaneMap, tile) -> tuple[frozenset[str], bool]:
    """What the machines on and beyond this belt tile take from it, and whether something takes whatever comes."""
    taken: set[str] = set()
    anything = False
    for there in lane_map.downstream(tile):
        for consumer in lane_map.consumers.get(there, ()):
            if consumer.takes:
                taken |= consumer.takes
            else:
                anything = True  # a furnace that picks its recipe from what it is given, a chest
    return frozenset(taken), anything


def _pipe_ports(entities: list, box: Box, notes: list[str]) -> list[dict]:
    """Pipe lines that end on the edge and carry on straight out of the box (and pipes-to-ground facing out).

    A line has two ends; both are exposed when it runs along the box, and either can be joined. One port is made per
    fluid network, the others are listed in its `also_at`.
    """
    nets, _ = fluids.networks(entities)
    net_of = {node: net for net in nets for node in net.nodes}
    mouths: dict[tuple[int, int], list[fluids.Connection]] = {}
    for index, entity in enumerate(entities):
        for c in fluids.connections_of(index, entity):
            mouths.setdefault(c.inner, []).append(c)

    def joined(c: fluids.Connection) -> bool:
        """Something on the other side of the connection links back to it."""
        return any(
            o.target == c.inner and not o.underground and o.direction == OPPOSITE[c.direction]
            and o.node[0] != c.node[0] and o.categories & c.categories
            for o in mouths.get(c.target, ())
        )

    ends: dict[int, list[fluids.Connection]] = {}
    nets_by_id: dict[int, object] = {}
    for index, entity in enumerate(entities):
        kind = getattr(entity, "type", "")
        if kind not in ("pipe", "pipe-to-ground"):
            continue
        normal = [c for c in fluids.connections_of(index, entity) if not c.underground]
        linked = [c for c in normal if joined(c)]
        open_out = [c for c in normal if c not in linked and not box.has(c.target)]
        if kind == "pipe":
            # a line that ends on the edge and carries on straight out of it: a stub with one link, or a run
            # ending in a corner of the box. Other open sides of a pipe on the edge (a run along the edge, a branch)
            # are not told from a bare pipe, so they are not ports.
            straight = {OPPOSITE[c.direction] for c in linked}
            lone = len(linked) == 1 or box.corner(entity_tile(entity))
            open_out = [c for c in open_out if c.direction in straight and lone]
        for mouth in open_out:
            net = net_of.get(mouth.node)
            if net is not None:
                ends.setdefault(id(net), []).append(mouth)
                nets_by_id[id(net)] = net

    found = []
    for key, group in ends.items():
        net = nets_by_id[key]
        group.sort(key=lambda c: (c.inner[1], c.inner[0], -c.direction))
        mouth = group[0]
        others = sorted({c.inner for c in group[1:]} - {mouth.inner}, key=lambda t: (t[1], t[0]))
        if len(net.fluids) != 1:
            if len(net.fluids) > 1:
                notes.append(f"the pipe end at {tuple(box.rel(mouth.inner))} joins several fluids ({', '.join(sorted(net.fluids))}); not made a port")
            continue
        flows = set()
        for machine, box_index in net.machine_boxes:
            boxes = fluids.active_boxes(entities[machine].name, getattr(entities[machine], "recipe", None))
            flows.add((boxes[box_index] or {}).get("production_type"))
        (fluid,) = net.fluids
        if flows == {"input"}:
            io, direction = "in", OPPOSITE[mouth.direction]
        elif flows == {"output"}:
            io, direction = "out", mouth.direction
        else:
            notes.append(f"the pipe end at {tuple(box.rel(mouth.inner))} carries {fluid}, which the machines both take and make; not made a port")
            continue
        found.append({"kind": "pipe", "dir": io, "tile": mouth.inner, "direction": direction,
                      "items": [fluid], "lanes_unknown": False,
                      "also": others})
    return found


def find_ports(entities: list, box: Box, notes: list[str]) -> list[dict]:
    """Every place a belt or pipe crosses the block's edge, with what flows there and a capacity rate."""
    machines = [e for e in entities if getattr(e, "recipe", None)]
    lane_map = lanes.trace(entities)
    raw = _belt_ports(entities, box, lane_map, notes) + _pipe_ports(entities, box, notes)
    needs, makes = _flows(machines)
    raw.sort(key=lambda p: (p["dir"] != "in", p["kind"], p["tile"][1], p["tile"][0]))
    shared = Counter()
    for p in raw:
        for good in {i for part in p["items"] for i in part.split("+") if i}:
            shared[(p["dir"], good)] += 1
    counts: Counter = Counter()
    ports = []
    for p in raw:
        counts[(p["kind"], p["dir"])] += 1
        goods = sorted({i for part in p["items"] for i in part.split("+") if i})
        table = needs if p["dir"] == "in" else makes
        item_rates = {g: round(table.get(g, 0.0) / shared[(p["dir"], g)], 6) for g in goods if table.get(g)}
        port = {
            "id": f"{p['kind']}-{p['dir']}-{counts[(p['kind'], p['dir'])]}",
            "kind": p["kind"], "dir": p["dir"], "at": box.rel(p["tile"]), "direction": p["direction"],
            "items": p["items"], "rate": round(sum(item_rates.values()), 6),
        }
        if item_rates:
            port["item_rates"] = item_rates
        if p["lanes_unknown"]:
            port["lanes_unknown"] = True
        if p.get("also"):
            port["also_at"] = [box.rel(t) for t in p["also"]]
        ports.append(port)
    return ports


# --------------------------------------------------------------------------
# the check, and the entry
# --------------------------------------------------------------------------


def problems_in(blueprint) -> list[inspection.Finding]:
    """What forge's own check calls an error: the inspection's problems and the fluid check's."""
    entities = list(blueprint.entities)
    found = list(inspection.inspect(blueprint).findings) + fluids.check(entities)
    return [f for f in found if f.severity is Severity.PROBLEM]


def _describe(finding) -> str:
    where = f" at {tuple(finding.position)}" if getattr(finding, "position", None) else ""
    return f"{finding.code}: {finding.summary}{where}"


def make_entry(name: str, blueprint, *, source: str, found_from: str, why: str = "",
               ports: list[dict] | None = None, hint: tuple[str, str] | None = None, allow_errors: bool = False) -> dict:
    """The library entry for a blueprint of one block; refuses a block the check finds errors in.

    `ports` replaces the ones read from the edge when the block's builder knows them better
    (a plan's own ports), given as entries already in library form; `hint` is the builder's (machine, recipe).
    """
    check_name(name)
    if source not in SOURCES:
        raise LibraryError(f"source must be one of {', '.join(SOURCES)}, not {source!r}")
    errors = problems_in(blueprint)
    if errors and not allow_errors:
        lines = "\n".join(f"  - {_describe(f)}" for f in errors[:12])
        more = f"\n  ... and {len(errors) - 12} more" if len(errors) > 12 else ""
        raise LibraryError(
            f"the check finds {len(errors)} error(s) in this block, so it is not saved:\n{lines}{more}\n"
            "Fix them, or cut the region again so it holds whole machines and lines; "
            "--force saves it anyway, marked as unchecked"
        )
    entities = list(blueprint.entities)
    box = bounding_box(entities)
    notes: list[str] = []
    found_ports = find_ports(entities, box, notes) if ports is None else ports
    entry = {
        "name": name, "source": source, "found_from": found_from, "why": why,
        "blueprint": blueprint.to_string(), "size": box.size, "ports": found_ports,
        "shape": shape_of(entities, found_ports, hint),
    }
    if not any(p["dir"] == "in" for p in found_ports) and not any(p["dir"] == "out" for p in found_ports):
        notes.append("no belt or pipe crosses the edge of this block, so it has no ports to join it up with")
    if errors:
        entry["check"] = f"saved with {len(errors)} error(s) the check found"
        notes.extend(_describe(f) for f in errors)
    if notes:
        entry["notes"] = notes
    return entry


def save(folder: Path, entry: dict, replace: bool = False) -> Path:
    path = path_of(folder, entry["name"])
    if path.exists() and not replace:
        raise LibraryError(f"the library already has a block called {entry['name']!r}; remove it, pick another name, or pass --replace")
    _write(path, entry)
    return path


def add_blueprint(folder: Path, name: str, source: str, *, index: int | None = None, origin: str | None = None,
                  kind: str = "player", why: str = "", allow_errors: bool = False, replace: bool = False) -> dict:
    """Save a blueprint the player cut from their base."""
    check_name(name)
    blueprint = read_blueprint(source, index)
    found_from = origin or (f"blueprint file {Path(source).name}" if is_file(source) else "a blueprint string")
    entry = make_entry(name, blueprint, source=kind, found_from=found_from, why=why, allow_errors=allow_errors)
    save(folder, entry, replace)
    return entry


# --------------------------------------------------------------------------
# blocks forge built
# --------------------------------------------------------------------------


def _block_index(plan_data: dict, ref) -> int:
    from . import plan as planning

    blocks = plan_data.get("blocks") or []
    ids = planning.block_ids(plan_data)
    text = str(ref).strip()
    if text in ids:
        return ids[text]
    if text.lstrip("-").isdigit():
        index = int(text)
        if 0 <= index < len(blocks):
            return index
    known = ", ".join(sorted(ids)) or f"0..{len(blocks) - 1}"
    raise LibraryError(f"the plan has no block {text!r} (blocks: {known})")


def _alone(block: dict):
    """The block built alone at the origin: (its blueprint, its ports in blueprint coordinates)."""
    from . import plan as planning

    spec = {k: v for k, v in block.items() if k not in ("id", "at")}
    try:
        built = planning.build({"blocks": [spec]})
    except planning.PlanError as exc:
        raise LibraryError(f"block cannot be built on its own: {exc}") from exc
    return built.blueprint, built.blocks[0].ports


def _hint(block: dict) -> tuple[str, str] | None:
    return (block["machine"], block["recipe"]) if block.get("machine") and block.get("recipe") else None


def _library_ports(plan_ports: list[dict], box: Box) -> list[dict]:
    """A plan block's ports in library form, positions relative to the block's top-left tile."""
    counts: Counter = Counter()
    out = []
    for p in sorted(plan_ports, key=lambda p: (p["io"] != "in", p["kind"], p["y"], p["x"])):
        counts[(p["kind"], p["io"])] += 1
        port = {
            "id": f"{p['kind']}-{p['io']}-{counts[(p['kind'], p['io'])]}",
            "kind": p["kind"], "dir": p["io"], "at": box.rel((p["x"], p["y"])), "direction": p["direction"],
            "items": list(p["items"]), "rate": p.get("rate"),
        }
        if p.get("item_rates"):
            port["item_rates"] = dict(p["item_rates"])
        out.append(port)
    return out


def add_from_plan(folder: Path, name: str, plan_data: dict, block_ref, *, origin: str = "", kind: str = "approved",
                  why: str = "", measured: dict | None = None, allow_errors: bool = False, replace: bool = False) -> dict:
    """Save a block forge built, taken from a plan by index or id."""
    check_name(name)
    index = _block_index(plan_data, block_ref)
    blueprint, ports = _alone(plan_data["blocks"][index])
    box = bounding_box(list(blueprint.entities))
    label = plan_data["blocks"][index].get("id") or index
    entry = make_entry(name, blueprint, source=kind, found_from=origin or f"plan block {label}", why=why,
                       ports=_library_ports(ports, box), hint=_hint(plan_data["blocks"][index]), allow_errors=allow_errors)
    if measured:
        entry["measured"] = measured
    save(folder, entry, replace)
    return entry


@dataclass
class SaveReport:
    saved: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # one line each, with the reason

    def lines(self) -> list[str]:
        out = [f"library: saved {name}" for name in self.saved]
        out += [f"library: skipped {line}" for line in self.skipped]
        return out


def _free_name(folder: Path, base: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:56] or "block"
    if base in _RESERVED:
        base += "-block"
    n = 1
    while path_of(folder, f"{base}-{n}").exists():
        n += 1
    return f"{base}-{n}"


def _share(group) -> float | None:
    return group.shares.get("working") if group is not None else None


def _block_rate(entry: dict, produced: dict, share: float | None) -> dict[str, float]:
    """What this block makes, per second, by good: the run's production figure for its products, else its capacity times the share.

    The production statistics count an item over the whole plan, so a good that two blocks make shows the sum.
    """
    shape = entry.get("shape") or {}
    goods = [p["name"] for p in (recipe_data.raw.get(shape.get("recipe")) or {}).get("results") or []]
    capacity: Counter = Counter()
    for port in entry.get("ports") or []:
        if port.get("dir") == "out":
            for good, rate in (port.get("item_rates") or {}).items():
                capacity[good] += rate
    goods = goods or sorted(capacity)
    rate = {g: round(float(produced[g]), 6) for g in goods if produced.get(g)}
    if not rate and share is not None:
        rate = {g: round(r * share, 6) for g, r in capacity.items()}
    return rate


def save_measured(plan_data: dict, result, folder: Path | None = None, found_from: str | None = None) -> SaveReport:
    """When a measured run met every target, keep the plan's blocks as blocks forge found working.

    `result` is a `measure.Comparison`. Blocks whose machines stood starved or blocked in the run are left
    out (they did not work well even though the whole did), as are blocks whose machines had no power, blocks with
    no measured machines of their own, blocks the library already holds (same blueprint) and blocks that cannot be
    built alone. Each is saved with source "forge", `why` saying how much of the measured time its machines worked,
    and `measured`: the working share, this block's own `rate` by good, and the plan's delivered rates under `plan_targets`.
    """
    report = SaveReport()
    if folder is None:
        folder = active_folder()
    if not result.all_met or not result.targets:
        report.skipped.append("the whole plan: not every target was met, so no block is offered")
        return report
    held = {data.get("blueprint") for _, data in entries(folder)[0]}
    plan_rates = {t.item: round(t.measured, 6) for t in result.targets}
    produced = getattr(result, "produced", None) or {}
    for index, block in enumerate(plan_data.get("blocks") or []):
        label = block.get("id") or f"block {index}"
        recipe, machine = block.get("recipe"), block.get("machine")
        group = next((g for g in result.groups if g.name == machine and (recipe is None or g.recipe == recipe)), None)
        if group is None:
            report.skipped.append(f"{label}: no measured machines of this block")
            continue
        if group.starved or group.blocked:
            why = "starved" if group.starved else "blocked"
            report.skipped.append(f"{label}: its machines stood {why} for a good part of the run")
            continue
        if group.unpowered:
            report.skipped.append(f"{label}: its machines stood without power for part of the run")
            continue
        share = _share(group)
        try:
            blueprint, ports = _alone(block)
            if blueprint.to_string() in held:
                report.skipped.append(f"{label}: the library already holds this block")
                continue
            name = _free_name(folder, f"{recipe or 'block'}")
            box = bounding_box(list(blueprint.entities))
            share_text = f"{share * 100:.0f}% of the measured time" if share is not None else "an unmeasured share of the time"
            lib_ports = _library_ports(ports, box)
            entry = make_entry(
                name, blueprint, source="forge",
                found_from=found_from or f"measure run of plan block {label}",
                why=f"every target of the plan was met in a measured run; {group.count} x {group.name} worked {share_text}",
                ports=lib_ports, hint=_hint(block))
        except LibraryError as exc:
            report.skipped.append(f"{label}: {str(exc).splitlines()[0]}")
            continue
        entry["measured"] = {
            "working_share": None if share is None else round(share, 4),
            "rate": _block_rate(entry, produced, share),
            "plan_targets": plan_rates,
        }
        save(folder, entry)
        held.add(entry["blueprint"])
        report.saved.append(name)
    return report


# --------------------------------------------------------------------------
# blocks found in a survey of the base
# --------------------------------------------------------------------------

# Machines of one recipe belong to one block when their boxes are at most this many tiles apart. Machines
# in a row stand flush or with a pole column (1-2 tiles) between them, and two rows face each other across
# inserter, belt(s), inserter (up to 4 tiles); the gap is that largest spacing rows.py builds.
CLUSTER_GAP = 4
# The cut takes in this many tiles around the machines: an inserter, the belt beside it and a second belt.
CUT_MARGIN = 3
# A machine that waited this share of the window (starved or blocked) makes its block a poor one to keep.
MAX_WAITING = 0.10


def _machine_box(machine, origin: tuple[float, float]) -> Box:
    """The tiles a surveyed machine fills, in blueprint tiles."""
    try:
        width, height = layout.machine_size(machine.name, machine.direction)
    except Exception:  # noqa: BLE001 - a machine the data does not know is a one-tile point
        width = height = 1
    left = round(machine.position[0] - origin[0] - width / 2)
    top = round(machine.position[1] - origin[1] - height / 2)
    return Box(left, top, left + width - 1, top + height - 1)


def _gap(a: Box, b: Box) -> int:
    """Empty tiles between two boxes along the axis where they are farthest apart (0 when they touch or overlap)."""
    across_x = max(a.left - b.right, b.left - a.right, 1) - 1
    across_y = max(a.top - b.bottom, b.top - a.bottom, 1) - 1
    return max(across_x, across_y)


def _clusters(machines: list, boxes: list[Box]) -> list[list[int]]:
    """Indexes of machines grouped by neighbours of one recipe (single linkage on CLUSTER_GAP)."""
    parent = list(range(len(machines)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(machines)):
        for j in range(i + 1, len(machines)):
            if machines[i].recipe == machines[j].recipe and _gap(boxes[i], boxes[j]) <= CLUSTER_GAP:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(machines)):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: (boxes[g[0]].top, boxes[g[0]].left))


def _safe_part(text: str, limit: int) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:limit].strip("-")


def _survey_name(recipe: str, x: float, y: float) -> str:
    """'<recipe>-<x>-<y>' with a negative coordinate written 'm12', so the name has no double dash."""
    def coord(v: float) -> str:
        v = round(v)
        return f"m{-v}" if v < 0 else str(v)
    tail = f"-{coord(x)}-{coord(y)}"
    base = _safe_part(recipe, 64 - len(tail)) or "block"
    return base + tail


def _product_rate(recipe: str, crafts_per_s: float) -> str:
    results = (recipe_data.raw.get(recipe) or {}).get("results") or []
    if results:
        return f"{crafts_per_s * _amount(results[0]):.3g} {results[0]['name']}/s"
    return f"{crafts_per_s:.3g} crafts/s"


def _cut(blueprint, area: Box):
    """A blueprint of the entities lying wholly inside `area`."""
    import copy

    from draftsman.blueprintable import Blueprint

    cut = Blueprint()
    for entity in blueprint.entities:
        if all(area.has(t) for t in Layout.tiles_of(entity)):
            cut.entities.append(copy.deepcopy(entity))
    if not len(cut.entities):
        raise LibraryError("the area holds no entities of the survey's blueprint")
    return cut


def save_from_survey(survey, profile: str | None = None, *, min_share: float = 0.9, dry_run: bool = False,
                     folder: Path | None = None) -> list[dict]:
    """Keep the blocks of a surveyed base that run evenly, cut out of the survey's blueprint.

    Machines of one recipe standing within CLUSTER_GAP tiles of each other make a cluster. A cluster with at
    least two machines whose mean working share is `min_share` or more, and none of which waited over
    MAX_WAITING of the window for input or for room to put its product, is cut out (its machines' box plus
    CUT_MARGIN tiles, whole entities only) and saved by the same path as `library add`: the same check, the
    same ports, source "forge". Single machines are not reported.

    Returns one dict per cluster: name, recipe, machines, size, why, saved, reason. A cluster is not saved when
    the cut fails the check, when other machines stand in the cut, when the library holds the name, or when it
    already holds a block of the same recipe and shape. `dry_run` writes nothing. `folder` replaces the
    profile's library folder (for tests and tools).
    """
    if folder is None:
        folder = library_folder(profile)[0]
    machines = [m for m in survey.machines if m.recipe]
    boxes = [_machine_box(m, survey.blueprint_origin) for m in machines]
    others = [_machine_box(m, survey.blueprint_origin) for m in survey.machines if not m.recipe]
    blueprint = None
    held = [data for _, data in entries(folder)[0]]
    held_names = {data.get("name") for data in held}
    report: list[dict] = []
    for group in _clusters(machines, boxes):
        if len(group) < 2:
            continue
        members = [machines[i] for i in group]
        recipe = members[0].recipe
        box = Box(min(boxes[i].left for i in group), min(boxes[i].top for i in group),
                  max(boxes[i].right for i in group), max(boxes[i].bottom for i in group))
        centre = ((box.left + box.right + 1) / 2 + survey.blueprint_origin[0],
                  (box.top + box.bottom + 1) / 2 + survey.blueprint_origin[1])
        name = _survey_name(recipe, *centre)
        share = sum(m.working_share for m in members) / len(members)
        rate = sum(m.crafts_per_s for m in members)
        row = {"name": name, "recipe": recipe, "machines": len(members), "size": None, "saved": False, "reason": "",
               "why": f"{share * 100:.0f}% working over {survey.window_s:g} s, {len(members)} machines, {_product_rate(recipe, rate)}"}
        report.append(row)
        if share < min_share:
            row["reason"] = f"machines worked {share * 100:.0f}% of the window, under {min_share * 100:.0f}%"
            continue
        waiting = [m for m in members if max(m.states.get("output_full", 0.0), m.states.get("no_ingredients", 0.0)) > MAX_WAITING]
        if waiting:
            row["reason"] = f"{len(waiting)} of {len(members)} machines waited over {MAX_WAITING * 100:.0f}% of the window for input or for room"
            continue
        if name in held_names:
            row["reason"] = "the library already has a block of that name"
            continue
        area = Box(box.left - CUT_MARGIN, box.top - CUT_MARGIN, box.right + CUT_MARGIN, box.bottom + CUT_MARGIN)
        crowd = [b for i, b in enumerate(boxes) if i not in group] + others
        if any(not (b.right < area.left or b.left > area.right or b.bottom < area.top or b.top > area.bottom) for b in crowd):
            row["reason"] = "other machines stand inside the cut area, so the block cannot be cut apart from them"
            continue
        try:
            if blueprint is None:
                if not survey.blueprint:
                    raise LibraryError("the survey carries no blueprint to cut from")
                blueprint = read_blueprint(survey.blueprint)
            entry = make_entry(name, _cut(blueprint, area), source="forge",
                               found_from=f"survey tick {survey.tick}, area {centre[0]:g},{centre[1]:g}", why=row["why"])
        except LibraryError as exc:
            row["reason"] = str(exc).splitlines()[0]
            continue
        row["size"] = entry["size"]
        twin = next((h for h in held if (h.get("shape") or {}).get("recipe") == recipe
                     and h.get("shape") == entry["shape"] and h.get("size") == entry["size"]), None)
        if twin is not None:
            row["reason"] = f"the library already holds a block of this shape and recipe ({twin.get('name')})"
            continue
        entry["measured"] = {"working_share": round(share, 4), "rate": {}}
        if not dry_run:
            save(folder, entry)
        held.append(entry)
        held_names.add(name)
        row["saved"] = True
        row["reason"] = "would be saved" if dry_run else "saved"
    return report


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------


def library_folder(profile_name: str | None) -> tuple[Path, str]:
    """The library folder of a profile (the active one when none is named), and the profile's name."""
    from .profile import Profile, ProfileError

    from . import paths

    name = profile_name or Profile.active_profile_name()
    if not name:
        raise LibraryError("no active profile: name one with --profile, or activate one first")
    if (name in (".", "..") or any(c in name for c in "/\\:\0") or name != name.strip()):
        raise LibraryError(f"{name!r} is not a profile name (profiles: {', '.join(Profile.list_all()) or 'none'})")
    try:
        folder = Profile.load(name).library_dir
    except ProfileError as exc:
        raise LibraryError(str(exc)) from exc
    root = paths.profiles_root().resolve()
    if root not in folder.resolve().parents:
        raise LibraryError(f"the library of {name!r} would lie outside the profiles folder; not used")
    return folder, name


def active_folder() -> Path:
    return library_folder(None)[0]


def shape_line(entry: dict) -> str:
    shape = entry.get("shape") or {}
    if not shape.get("machine") and not shape.get("recipe"):
        return "no machines"
    counts = (f"{shape.get('solid_in', 0)}+{shape.get('fluid_in', 0)} in, "
              f"{shape.get('solid_out', 0)}+{shape.get('fluid_out', 0)} out")
    many = f"{shape['machines']} x " if shape.get("machines") else ""
    return f"{many}{shape.get('machine')} making {shape.get('recipe') or '(various)'} ({counts})"


def _ports_line(entry: dict) -> str:
    ports = entry.get("ports") or []
    ins = sum(1 for p in ports if p.get("dir") == "in")
    return f"{len(ports)} ({ins} in, {len(ports) - ins} out)"


def show_text(entry: dict) -> str:
    size = entry.get("size") or ["?", "?"]
    out = [f"{entry.get('name')}: {entry.get('source')}, {size[0]} x {size[1]} tiles",
           f"  from:  {entry.get('found_from') or '-'}"]
    if entry.get("why"):
        out.append(f"  why:   {entry['why']}")
    out.append(f"  shape: {shape_line(entry)}")
    tiers = (entry.get("shape") or {}).get("tiers") or {}
    if tiers:
        out.append("  tiers: " + ", ".join(f"{k} {v}" for k, v in tiers.items()))
    measured = entry.get("measured")
    if measured:
        share = measured.get("working_share")
        out.append("  measured: " + (f"machines worked {share * 100:.0f}%" if share is not None else "no working share")
                   + (f"; " + ", ".join(f"{k} {v:g}/s" for k, v in (measured.get("rate") or {}).items()) if measured.get("rate") else ""))
    out.append(f"  ports: {_ports_line(entry)}")
    for p in entry.get("ports") or []:
        what = " | ".join(p.get("items") or []) if p.get("kind") == "belt" else (p.get("items") or ["?"])[0]
        unit = "units/s" if p.get("kind") == "pipe" else "items/s"
        rate = f", {p['rate']:g} {unit}" if p.get("rate") else ""
        out.append(f"    {p.get('id')}: {p.get('dir')} at {tuple(p.get('at') or ())} flowing {p.get('direction')}: {what or '-'}{rate}"
                   + (" (lanes not told apart)" if p.get("lanes_unknown") else ""))
    for note in entry.get("notes") or []:
        out.append(f"  note: {note}")
    return "\n".join(out)


def _cmd_add(args) -> int:
    import sys

    from .profile import Profile

    try:
        folder, name = library_folder(args.profile)
        if name != Profile.active_profile_name():
            print(f"note: {name} is not the active profile; ports and shape are read with the active profile's game data "
                  f"({Profile.active_profile_name() or 'none'})", file=sys.stderr)
        if args.from_plan:
            if args.source_file or args.block is None:
                raise LibraryError("with --from-plan, give --block (an index or id) and no blueprint file")
            from . import plan as planning

            try:
                data = planning.load(Path(args.from_plan))
            except planning.PlanError as exc:
                raise LibraryError(str(exc)) from exc
            entry = add_from_plan(folder, args.name, data, args.block, origin=args.found_from or f"plan {Path(args.from_plan).name}, block {args.block}",
                                  kind=args.source or "approved", why=args.why or "", allow_errors=args.force, replace=args.replace)
        else:
            if not args.source_file:
                raise LibraryError("give a region file or blueprint string to add, or --from-plan with --block")
            entry = add_blueprint(folder, args.name, args.source_file, index=args.index, origin=args.found_from,
                                  kind=args.source or "player", why=args.why or "", allow_errors=args.force, replace=args.replace)
    except LibraryError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"saved {entry['name']} in {folder}")
    print(show_text(entry))
    return 0


def _cmd_add_from_survey(args) -> int:
    import sys

    from . import survey as surveying
    from .profile import Profile

    try:
        found = surveying.load(args.survey)
        folder, name = library_folder(args.profile)
        if name != Profile.active_profile_name():
            print(f"note: {name} is not the active profile; ports and shape are read with the active profile's game data "
                  f"({Profile.active_profile_name() or 'none'})", file=sys.stderr)
        report = save_from_survey(found, args.profile, min_share=args.min_share, dry_run=args.dry_run, folder=folder)
    except (LibraryError, surveying.SurveyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not report:
        print("no cluster of two or more machines of one recipe in this survey")
    for row in report:
        verdict = ("would save" if args.dry_run else "saved") if row["saved"] else "skipped"
        extra = "" if row["saved"] else f": {row['reason']}"
        print(f"{verdict} {row['name']} ({row['machines']} x {row['recipe']}){extra}")
    return 0


def _cmd_list(args) -> int:
    import sys

    try:
        folder, _ = library_folder(args.profile)
    except LibraryError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    found, problems = entries(folder)
    for line in problems:
        print(f"warning: {line}", file=sys.stderr)
    if not found:
        print(f"the library is empty ({folder})")
        return 0
    width = max(len(n) for n, _ in found)
    for name, entry in found:
        print(f"{name.ljust(width)}  {str(entry.get('source')).ljust(8)}  ports {_ports_line(entry).ljust(16)}  {shape_line(entry)}")
    return 0


def _cmd_show(args) -> int:
    import sys

    try:
        folder, _ = library_folder(args.profile)
        entry = load(folder, args.name)
    except LibraryError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(show_text(entry))
    return 0


def _cmd_remove(args) -> int:
    import sys

    try:
        folder, _ = library_folder(args.profile)
        remove(folder, args.name)
    except LibraryError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"removed {args.name}")
    return 0


def register(sub) -> None:
    """Add the `library` command to the CLI's subparsers."""
    lib = sub.add_parser("library", help="the profile's library of working blocks: add, list, show, remove")
    kinds = lib.add_subparsers(dest="what", required=True)

    add = kinds.add_parser("add", help="save a block: a region cut from the base, or a block of a plan")
    add.add_argument("name", help="lowercase letters, digits and dashes")
    add.add_argument("source_file", nargs="?", help="a region file or blueprint string holding the block alone")
    add.add_argument("--from-plan", metavar="PLAN", help="take the block from this plan file instead")
    add.add_argument("--block", help="with --from-plan: the block's index or id")
    add.add_argument("--index", type=int, help="which entry to take, when the string is a blueprint book")
    add.add_argument("--source", choices=SOURCES, help="who found it (default: player for a cut region, approved for a plan block)")
    add.add_argument("--found-from", help="where it came from (default: the file or plan)")
    add.add_argument("--why", help="why it is good")
    add.add_argument("--replace", action="store_true", help="overwrite a block of the same name")
    add.add_argument("--force", action="store_true", help="save even if the check finds errors, marked as unchecked")
    add.add_argument("--profile", help="the profile (default: the active one)")
    add.set_defaults(func=_cmd_add)

    from_survey = kinds.add_parser("add-from-survey", help="save the blocks of a base survey that ran evenly")
    from_survey.add_argument("survey", help="survey.json written by the companion mod")
    from_survey.add_argument("--min-share", type=float, default=0.9, help="least mean working share of a block (default 0.9)")
    from_survey.add_argument("--dry-run", action="store_true", help="say what would be saved, write nothing")
    from_survey.add_argument("--profile", help="the profile (default: the active one)")
    from_survey.set_defaults(func=_cmd_add_from_survey)

    listing = kinds.add_parser("list", help="every block: name, source, ports, shape")
    listing.add_argument("--profile")
    listing.set_defaults(func=_cmd_list)

    show = kinds.add_parser("show", help="one block in full: shape, tiers, ports")
    show.add_argument("name")
    show.add_argument("--profile")
    show.set_defaults(func=_cmd_show)

    drop = kinds.add_parser("remove", help="delete a block from the library")
    drop.add_argument("name")
    drop.add_argument("--profile")
    drop.set_defaults(func=_cmd_remove)
