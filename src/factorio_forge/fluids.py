"""Which fluid goes where, and whether the pipes agree.

A pipe has no direction and no filter: anything it touches, it joins. Two
different fluids meeting in one network is the failure that looks fine on the
drawing and stops the factory, so a generated layout needs a check that knows
where every connection of every entity actually is -- read from the
prototypes, never from what the generator intended.

## Which of a machine's fluid boxes a recipe fills

A recipe ingredient may name its fluid box with `fluidbox_index` (1-based,
counted separately among input and output boxes). When none does, the engine
does not pair the first fluid with the first box. In the words of a Factorio
developer (boskid, forums.factorio.com, p=701829): "standard logic kicks in
and assigns fluid ingredients to all available input fluidboxes. Since this
logic sees 4 input fluidboxes and 2 ingredients, first 2 fluidboxes are
assigned to heavy-oil and last 2 are assigned to 'steam'."

So boxes are dealt out in equal consecutive groups. What happens when the
count does not divide evenly was not stated, and this module refuses such a
case rather than guessing. The statement was about ingredients; results are
assumed to follow the same logic, which a real build through the companion
mod can confirm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from draftsman.data import entities as entity_data
from draftsman.data import recipes as recipe_data

from .inspection import STEP, Finding, Severity

OPPOSITE = {0: 8, 4: 12, 8: 0, 12: 4}


class FluidAssignmentError(Exception):
    """A recipe's fluids cannot be placed on a machine's boxes with certainty."""


def rotate(vector: tuple[float, float], direction: int) -> tuple[float, float]:
    """Turn an offset from north to `direction` (multiples of 4, clockwise)."""
    x, y = vector
    turn = direction % 16
    if turn == 4:
        return -y, x
    if turn == 8:
        return -x, -y
    if turn == 12:
        return y, -x
    return x, y


# --------------------------------------------------------------------------
# recipe -> boxes
# --------------------------------------------------------------------------


def active_boxes(machine: str, recipe: str | None) -> list[dict]:
    """The machine's fluid boxes, or none if the recipe switches them off."""
    entry = entity_data.raw.get(machine) or {}
    boxes = list(entry.get("fluid_boxes") or [])
    if not boxes:
        return []
    if recipe is None:
        return boxes
    parts = list((recipe_data.raw.get(recipe) or {}).get("ingredients", [])) + list(
        (recipe_data.raw.get(recipe) or {}).get("results", [])
    )
    uses_fluid = any(p.get("type") == "fluid" for p in parts)
    if not uses_fluid and entry.get("fluid_boxes_off_when_no_fluid_recipe"):
        return []
    return boxes


def assign_fluid_boxes(recipe: str, machine: str) -> dict[int, str]:
    """Box index -> fluid name, for every box the recipe fills."""
    entry = recipe_data.raw.get(recipe)
    if entry is None:
        raise FluidAssignmentError(f"{recipe!r} is not a recipe in the active data")
    boxes = active_boxes(machine, recipe)
    assigned: dict[int, str] = {}
    for key, kind in (("ingredients", "input"), ("results", "output")):
        fluids = [p for p in entry.get(key, []) if p.get("type") == "fluid"]
        if not fluids:
            continue
        slots = [i for i, box in enumerate(boxes) if box.get("production_type") == kind]
        if not slots:
            raise FluidAssignmentError(
                f"{machine!r} has no {kind} fluid box for {recipe!r}'s "
                + ", ".join(p["name"] for p in fluids)
            )
        indexed = [p for p in fluids if p.get("fluidbox_index")]
        if indexed and len(indexed) != len(fluids):
            raise FluidAssignmentError(
                f"{recipe!r} names a fluid box for some {kind} fluids and not others"
            )
        if indexed:
            for part in fluids:
                position = int(part["fluidbox_index"]) - 1
                if not 0 <= position < len(slots):
                    raise FluidAssignmentError(
                        f"{recipe!r} asks for {kind} fluid box {position + 1} of {machine!r}, "
                        f"which has {len(slots)}"
                    )
                assigned[slots[position]] = part["name"]
            continue
        if len(slots) < len(fluids) or len(slots) % len(fluids):
            raise FluidAssignmentError(
                f"{machine!r} has {len(slots)} {kind} fluid boxes for {len(fluids)} fluids in "
                f"{recipe!r}; the game's split is only known when it divides evenly"
            )
        group = len(slots) // len(fluids)
        for k, part in enumerate(fluids):
            for slot in slots[k * group:(k + 1) * group]:
                assigned[slot] = part["name"]
    return assigned


# --------------------------------------------------------------------------
# connections in world tiles
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Connection:
    node: tuple[int, int]  # (entity index, box index)
    inner: tuple[int, int]  # the tile inside the entity the connection leaves from
    direction: int  # the way it points, out of the entity
    underground: bool
    reach: int
    categories: frozenset[str]

    @property
    def target(self) -> tuple[int, int]:
        dx, dy = STEP[self.direction]
        return self.inner[0] + dx, self.inner[1] + dy


def _box_list(entity) -> list[dict]:
    entry = entity_data.raw.get(entity.name) or {}
    if entry.get("fluid_boxes"):
        return active_boxes(entity.name, getattr(entity, "recipe", None))
    single = entry.get("fluid_box")
    return [single] if single else []


def connections_of(index: int, entity) -> list[Connection]:
    direction = int(getattr(entity, "direction", 0) or 0)
    if direction % 4:
        return []  # diagonal pieces carry no fluid
    cx, cy = entity.position.x, entity.position.y
    found = []
    for box_index, box in enumerate(_box_list(entity)):
        for raw in box.get("pipe_connections") or []:
            if raw.get("direction") is None or raw.get("position") is None:
                continue
            ox, oy = rotate(tuple(raw["position"]), direction)
            category = raw.get("connection_category") or ["default"]
            if isinstance(category, str):
                category = [category]
            found.append(
                Connection(
                    node=(index, box_index),
                    inner=(math.floor(cx + ox), math.floor(cy + oy)),
                    direction=(int(raw["direction"]) + direction) % 16,
                    underground=raw.get("connection_type") == "underground",
                    reach=int(raw.get("max_underground_distance") or 0),
                    categories=frozenset(category),
                )
            )
    return found


# --------------------------------------------------------------------------
# networks
# --------------------------------------------------------------------------


@dataclass
class Network:
    nodes: set[tuple[int, int]] = field(default_factory=set)
    fluids: set[str] = field(default_factory=set)
    machine_boxes: list[tuple[int, int]] = field(default_factory=list)


def networks(entities: list) -> tuple[list[Network], dict[tuple[int, int], str]]:
    """Every fluid network, and the fluid each machine box is meant to hold."""
    parent: dict[tuple[int, int], tuple[int, int]] = {}

    def find(node):
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a, b):
        parent[find(a)] = find(b)

    by_inner: dict[tuple[int, int], list[Connection]] = {}
    all_connections: list[Connection] = []
    intended: dict[tuple[int, int], str] = {}
    for index, entity in enumerate(entities):
        conns = connections_of(index, entity)
        for c in conns:
            find(c.node)
            by_inner.setdefault(c.inner, []).append(c)
            all_connections.append(c)
        recipe = getattr(entity, "recipe", None)
        if conns and recipe and (entity_data.raw.get(entity.name) or {}).get("fluid_boxes"):
            try:
                for box, fluid in assign_fluid_boxes(recipe, entity.name).items():
                    intended[(index, box)] = fluid
            except FluidAssignmentError:
                pass  # reported by whoever built it; nothing to compare against here

    for c in all_connections:
        if c.underground:
            dx, dy = STEP[c.direction]
            for distance in range(1, c.reach + 1):
                tile = (c.inner[0] + dx * distance, c.inner[1] + dy * distance)
                partner = next(
                    (
                        o
                        for o in by_inner.get(tile, [])
                        if o.underground
                        and o.direction == OPPOSITE[c.direction]
                        and entities[o.node[0]].name == entities[c.node[0]].name
                    ),
                    None,
                )
                if partner is not None:
                    union(c.node, partner.node)
                    break
            continue
        for other in by_inner.get(c.target, []):
            if (
                not other.underground
                and other.target == c.inner
                and other.direction == OPPOSITE[c.direction]
                and other.categories & c.categories
                and other.node[0] != c.node[0]
            ):
                union(c.node, other.node)

    groups: dict[tuple[int, int], Network] = {}
    for node in parent:
        net = groups.setdefault(find(node), Network())
        net.nodes.add(node)
        if node in intended:
            net.fluids.add(intended[node])
            net.machine_boxes.append(node)
    return list(groups.values()), intended


def check(entities: list) -> list[Finding]:
    """Mixed networks, and machines that no pipe brings a fluid they need.

    A fluid the recipe spreads over several boxes counts as connected if any
    one of those boxes is: which boxes carry it is the game's business, and a
    pipe on one of them is how players routinely build.
    """
    findings: list[Finding] = []
    nets, intended = networks(entities)
    connected: set[tuple[int, str]] = set()
    for net in nets:
        if len(net.nodes) > 1:
            for index, box in net.machine_boxes:
                connected.add((index, intended[(index, box)]))
    for index, fluid in sorted({(i, f) for (i, _), f in intended.items()} - connected):
        entity = entities[index]
        findings.append(
            Finding(
                Severity.PROBLEM,
                "fluid-box-unconnected",
                f"{entity.name} gets no {fluid}: no pipe reaches a fluid box that takes it",
                "",
                (int(entity.tile_position.x), int(entity.tile_position.y)),
                (entity.name,),
            )
        )
    for net in nets:
        if len(net.fluids) > 1:
            index = min(net.machine_boxes)[0]
            entity = entities[index]
            findings.append(
                Finding(
                    Severity.PROBLEM,
                    "fluids-mixed",
                    "one pipe network joins " + " and ".join(sorted(net.fluids)),
                    "Pipes join whatever they touch; these fluids will block each other.",
                    (int(entity.tile_position.x), int(entity.tile_position.y)),
                    tuple(sorted(net.fluids)),
                )
            )
    return findings
