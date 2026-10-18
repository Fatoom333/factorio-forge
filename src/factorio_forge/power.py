"""Power between blocks: which pole networks a build has, and poles that join them.

A build's blocks carry their own poles, but two blocks laid far apart share no
wire. This module does two things with that.

`link` adds the fewest poles it can that join every pole network into one, on
tiles that are free: not under a machine, belt or route piece, not on a tile
the plan reserves, not where an inserter picks up or drops, not on a pipe
connection and not on the tile in front of a port. The wire reach comes from
the pole's prototype, the same rule the blueprint's own wires follow: two
poles connect when their centres are no further apart than the shorter of
their two reaches.

`analyse` describes the networks the blueprint ended up with, from its wires,
so the report can name every pole, what each network powers and, for networks
that stay apart, the closest pair of poles and how far short of the wire it
falls.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from draftsman.data import entities as entity_data

from . import inspection, layout, route

COPPER = 5  # the connector id of an electric wire

# Poles are searched for this many tiles beyond the build, so a chain can go round it.
LINK_MARGIN = 4


class PowerError(Exception):
    """The power options of a plan are unusable."""


@dataclass
class PoleInfo:
    name: str
    x: float  # centre
    y: float
    reach: float  # maximum_wire_distance
    supply: float  # supply_area_distance
    width: int
    height: int
    tile: tuple[int, int]
    entity: object = field(default=None, repr=False)
    added: bool = False


def _raw(name: str) -> dict:
    return entity_data.raw.get(name) or {}


def pole_of(entity) -> PoleInfo:
    data = _raw(entity.name)
    return PoleInfo(
        entity.name, float(entity.position.x), float(entity.position.y),
        float(data.get("maximum_wire_distance") or 0), float(data.get("supply_area_distance") or 0),
        int(entity.tile_width), int(entity.tile_height), inspection.Layout.tile_of(entity), entity,
    )


def poles_of(entities) -> list[PoleInfo]:
    return [pole_of(e) for e in entities if getattr(e, "type", "") == "electric-pole"]


def connects(a: PoleInfo, b: PoleInfo) -> bool:
    return math.dist((a.x, a.y), (b.x, b.y)) <= min(a.reach, b.reach)


def _components(poles: list[PoleInfo]) -> list[list[int]]:
    """Pole indices grouped by wire reach alone: what the blueprint's wires would join."""
    parent = list(range(len(poles)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(poles):
        for j in range(i + 1, len(poles)):
            if connects(a, poles[j]):
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(poles)):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: (-len(g), g[0]))


def default_pole(poles: list[PoleInfo]) -> str | None:
    """The pole a build already uses most."""
    counts: dict[str, int] = {}
    for p in poles:
        counts[p.name] = counts.get(p.name, 0) + 1
    return max(sorted(counts), key=lambda n: counts[n]) if counts else None


def check_pole(name: str) -> None:
    data = _raw(name)
    if not data or data.get("type") != "electric-pole":
        raise PowerError(f"power: {name!r} is not an electric pole in this profile's data")
    if not float(data.get("maximum_wire_distance") or 0):
        raise PowerError(f"power: {name} has no wire reach")


# --------------------------------------------------------------------------
# linking
# --------------------------------------------------------------------------


@dataclass
class Link:
    """What linking did: the poles it added and what it could not join."""

    pole: str
    added: list[PoleInfo] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def _free_anchors(grid: route.Grid, width: int, height: int) -> set[tuple[int, int]]:
    """Every anchor tile a pole of this size can stand on."""
    x0, y0, x1, y1 = grid.area

    def free(t):
        return (grid.inside(t) and t not in grid.occupied and t not in grid.reserved
                and t not in grid.hands and t not in grid.fluid_targets)

    ok = {(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1) if free((x, y))}
    if width == 1 and height == 1:
        return ok
    return {(x, y) for (x, y) in ok
            if all((x + i, y + j) in ok for i in range(width) for j in range(height))}


def _near(pole: PoleInfo, reach: float, width: int, height: int) -> list[tuple[int, int]]:
    """Anchors whose pole would be wired to `pole`, given the new pole's reach."""
    r = min(reach, pole.reach)
    out = []
    for ax in range(math.floor(pole.x - r - width / 2) - 1, math.ceil(pole.x + r - width / 2) + 2):
        for ay in range(math.floor(pole.y - r - height / 2) - 1, math.ceil(pole.y + r - height / 2) + 2):
            if math.dist((ax + width / 2, ay + height / 2), (pole.x, pole.y)) <= r:
                out.append((ax, ay))
    return out


def link(blueprint, grid: route.Grid, name: str) -> Link:
    """Add poles until the blueprint's poles are one network, or say what stops it.

    Greedy: the biggest network grows by the shortest chain of free tiles that
    reaches any other, until nothing is left apart. A network no chain reaches
    is set aside and the rest go on. Among chains of the same length the first
    found stands; the count of poles is what is minimal, not their spread.
    """
    check_pole(name)
    reach = float(_raw(name)["maximum_wire_distance"])
    width, height = layout.machine_size(name)
    result = Link(name)
    poles = poles_of(list(blueprint.entities))
    anchors = _free_anchors(grid, width, height)
    offsets = sorted(
        ((dx, dy) for dx in range(-int(reach) - 1, int(reach) + 2) for dy in range(-int(reach) - 1, int(reach) + 2)
         if math.hypot(dx, dy) <= reach),
        key=lambda d: (-math.hypot(*d), d),
    )
    stuck: set[int] = set()
    while True:
        groups = [g for g in _components(poles) if not any(id(poles[i]) in stuck for i in g)]
        if len(groups) < 2:
            break
        tree, others = groups[0], groups[1:]
        goal: dict[tuple[int, int], PoleInfo] = {}
        for g in others:
            for i in g:
                for a in _near(poles[i], reach, width, height):
                    if a in anchors:
                        goal.setdefault(a, poles[i])
        parent: dict[tuple[int, int], tuple[int, int] | None] = {}
        queue: deque = deque()
        for i in tree:
            for a in _near(poles[i], reach, width, height):
                if a in anchors and a not in parent:
                    parent[a] = None
                    queue.append(a)
        found = None
        while queue:
            cur = queue.popleft()
            if cur in goal:
                found = cur
                break
            for dx, dy in offsets:
                nxt = (cur[0] + dx, cur[1] + dy)
                if nxt in anchors and nxt not in parent:
                    parent[nxt] = cur
                    queue.append(nxt)
        if found is None:
            stuck.update(id(poles[i]) for i in tree)
            result.failures.append(_why_apart(poles, tree, others, name, reach, len(parent), grid.area))
            continue
        path = []
        cur = found
        while cur is not None:
            path.append(cur)
            cur = parent[cur]
        for anchor in reversed(path):
            entity = blueprint.entities.append(name, tile_position=anchor)
            info = pole_of(entity)
            info.added = True
            poles.append(info)
            result.added.append(info)
            for i in range(1 - width, width):
                for j in range(1 - height, height):
                    anchors.discard((anchor[0] + i, anchor[1] + j))
    return result


def _why_apart(poles, tree, others, name, reach, reached, area) -> str:
    near = min(
        (math.dist((poles[i].x, poles[i].y), (poles[j].x, poles[j].y)), i, j)
        for i in tree for g in others for j in g
    )
    a, b = poles[near[1]], poles[near[2]]
    where = f"({min(poles[i].x for i in tree):g}, {min(poles[i].y for i in tree):g})"
    return (
        f"The network of {len(tree)} pole(s) from {where} stays apart: its closest pole to another network is "
        f"({a.x:g}, {a.y:g}) to ({b.x:g}, {b.y:g}), {near[0]:.1f} tiles, and no chain of {name} poles (wire "
        f"reach {reach:g}) on free tiles inside x {area[0]}..{area[2]}, y {area[1]}..{area[3]} crosses it "
        f"({reached} tiles were within reach; the rest are taken by machines, routes, reserved tiles, "
        "inserter hands or port fronts)."
    )


# --------------------------------------------------------------------------
# what the networks are
# --------------------------------------------------------------------------


def _wired_groups(blueprint, poles: list[PoleInfo]) -> list[list[int]]:
    """Pole indices grouped by the blueprint's own copper wires."""
    index = {id(p.entity): i for i, p in enumerate(poles)}
    parent = list(range(len(poles)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for wire in getattr(blueprint, "wires", None) or []:
        if wire[1] != COPPER or wire[3] != COPPER:
            continue
        a, b = inspection._resolve(wire[0]), inspection._resolve(wire[2])
        if id(a) in index and id(b) in index:
            parent[find(index[id(a)])] = find(index[id(b)])
    groups: dict[int, list[int]] = {}
    for i in range(len(poles)):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: (-len(g), g[0]))


def _covered(pole: PoleInfo) -> set[tuple[int, int]]:
    tiles = set()
    for tx in range(math.floor(pole.x - pole.supply), math.ceil(pole.x + pole.supply)):
        for ty in range(math.floor(pole.y - pole.supply), math.ceil(pole.y + pole.supply)):
            tiles.add((tx, ty))
    return tiles


def count_networks(blueprint) -> int:
    """How many networks the blueprint's wires leave its poles in."""
    return len(_wired_groups(blueprint, poles_of(list(blueprint.entities))))


def analyse(blueprint, owner: list[int], routes: list, link_result: Link | None) -> dict:
    """The networks of a finished build: poles, what each powers, and how far apart the separate ones are.

    `owner[i]` is the block entity `i` belongs to, for the entities the blocks
    placed first; later entities belong to no block.
    """
    entities = list(blueprint.entities)
    poles = poles_of(entities)
    placed = {id(a.entity) for a in link_result.added} if link_result else set()
    for p in poles:
        p.added = id(p.entity) in placed
    position = {id(e): i for i, e in enumerate(entities)}
    groups = _wired_groups(blueprint, poles)
    route_of: dict[tuple[int, int], str] = {}
    for r in routes:
        if not getattr(r, "ok", False):
            continue
        for piece in r.pieces:
            route_of[(piece.x, piece.y)] = r.id
            if piece.other is not None:
                route_of[tuple(piece.other)] = r.id
    needing = [(i, e) for i, e in enumerate(entities) if inspection._needs_electricity(e)]
    networks = []
    for n, group in enumerate(groups):
        covered: set[tuple[int, int]] = set()
        for i in group:
            covered |= _covered(poles[i])
        by_block: dict[int, int] = {}
        by_route: dict[str, int] = {}
        other = 0
        for i, e in needing:
            if not any(t in covered for t in inspection.Layout.tiles_of(e)):
                continue
            if i < len(owner):
                by_block[owner[i]] = by_block.get(owner[i], 0) + 1
                continue
            rid = next((route_of[t] for t in inspection.Layout.tiles_of(e) if t in route_of), None)
            if rid is not None:
                by_route[rid] = by_route.get(rid, 0) + 1
            else:
                other += 1
        networks.append({
            "index": n,
            "poles": [
                {"name": poles[i].name, "x": poles[i].x, "y": poles[i].y,
                 "block": (owner[position[id(poles[i].entity)]] if position[id(poles[i].entity)] < len(owner) else None),
                 "added": poles[i].added}
                for i in group
            ],
            "powers": {
                "blocks": [{"block": b, "entities": c} for b, c in sorted(by_block.items())],
                "routes": [{"route": r, "entities": c} for r, c in sorted(by_route.items())],
                "other": other,
            },
        })
    return {"networks": networks, "gaps": _gaps(poles, groups)}


def _gaps(poles: list[PoleInfo], groups: list[list[int]]) -> list[dict]:
    """The closest pair of poles between separate networks, joined as a spanning tree by distance."""
    if len(groups) < 2:
        return []

    def closest(a: list[int], b: list[int]):
        best = None
        for i in a:
            for j in b:
                d = math.dist((poles[i].x, poles[i].y), (poles[j].x, poles[j].y))
                if best is None or d < best[0]:
                    best = (d, i, j)
        return best

    joined = {0}
    gaps = []
    while len(joined) < len(groups):
        best = None
        for a in joined:
            for b in range(len(groups)):
                if b in joined:
                    continue
                c = closest(groups[a], groups[b])
                if best is None or c[0] < best[0][0]:
                    best = (c, a, b)
        (d, i, j), a, b = best
        reach = min(poles[i].reach, poles[j].reach)
        gaps.append({
            "networks": [a, b],
            "from": {"name": poles[i].name, "x": poles[i].x, "y": poles[i].y},
            "to": {"name": poles[j].name, "x": poles[j].x, "y": poles[j].y},
            "distance": round(d, 2), "reach": reach, "short_by": round(max(0.0, d - reach), 2),
        })
        joined.add(b)
    return gaps
