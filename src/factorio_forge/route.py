"""Join one block's port to another's with belts or pipes, and say so if it cannot.

A plan names its connections by port -- "block 0 port 3 to block 1 port 0" --
and this module lays the pieces between them. It is a search, not a drawing:
A* over the tiles the plan leaves free, with every rule the game would hold
the result to checked before a piece is allowed, and checked again on the
finished path. What it cannot do it reports, with the tile where it got stuck
and why; it never places half a route.

## What a piece may not do

Belts and pipes fail in different ways, so they have different rules.

- A belt piece never stands where something already pushes items -- a belt
  pointing at the tile, an underground exit, a splitter, a drill's drop -- or
  where an inserter takes or drops; the one exception is the first piece,
  fed from straight behind by the port it leaves. Either would side-load into the route or
  bend it. Each piece points into the next free tile, so the route never
  side-loads into anything else either. The last piece points into an in
  port's first tile, or past a point into a tile left open; if something
  stands there it must be a straight continuation -- a belt, underground
  entrance or splitter carrying the same way, and not a curve the route
  would straighten -- or the route is refused.
- A pipe joins whatever it touches. A piece is refused when one of its own
  connections meets a foreign connection pointing back with a category in
  common: the rule `fluids.networks` joins pieces by, so router and checker
  agree. Only the first piece may join what stands behind the start (an out
  port's pipe), and only the last what stands where it points (an in port's,
  or past a point a connection facing back; a point into anything without
  one is refused).
  A pipe-to-ground has no connections at its sides, which is why it is the
  generic way past a pipe of another fluid.
- An underground hop must not pass an end of the same prototype on its line
  and axis, nor end inside an existing pair of that prototype: either would
  pair with the wrong partner. An unpaired end of that prototype within reach
  is refused as well. Other prototypes, and crossings at right angles, are
  free.

## Lanes

A straight belt, a curve fed by one plain belt, and an underground pair all
keep the left lane on the left. The route ends with a straight join into the
port, and turns only after a plain belt -- never right after an underground
exit, which is still to be confirmed in the game -- so it delivers the
source's lanes unchanged. A route cannot swap them. A destination that names
one item for both lanes (a block's single-ingredient input) takes it from
either lane, so a source with that item on one lane fits; only the
throughput check sees the difference.

## Reach

Read from the prototype on every call (`inspection.underground_reach`): the
largest difference, in tiles along the axis, between the two ends of a pair.
The checker walks the same `range(1, reach + 1)`.

## Not done

Splitters, merges, one source to several destinations, lane swaps, ripping up
other routes, map terrain, pipe throughput, pieces larger than one tile.
Routes go greedily in plan order: an earlier one is an obstacle for a later
one. The only rip-up is of a route's own path, when via tiles make it come
back over itself (see `route`).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from types import SimpleNamespace

from draftsman.data import entities as entity_data

from . import fluids, inspection, layout
from .inspection import STEP, Finding, Severity

DEFAULT_MARGIN = 3  # search tuning only; no game data lives here
DEFAULT_MAX_NODES = 200_000
DEFAULT_TURN_COST = 1.0
DEFAULT_HOP_COST = 2.0

OPPOSITE = fluids.OPPOSITE
_BURIED = 1e-3  # tie-break only: see the hop cost in `route`
CW = {0: 4, 4: 8, 8: 12, 12: 0}
CCW = {0: 12, 4: 0, 8: 4, 12: 8}


class RouteError(Exception):
    """A route spec or prototype choice that cannot be routed; plan.py turns it into a PlanError."""


# --------------------------------------------------------------------------
# what goes in, what comes out
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Endpoint:
    x: int
    y: int
    direction: int
    items: tuple[str, ...] = ()  # belt: (left, right); pipe: (fluid,)
    rate: float | None = None
    port: tuple[int, int] | None = None  # (block index, port index); None for a point
    pipe_name: str | None = None  # the port's own pipe prototype

    def describe(self) -> str:
        if self.port is not None:
            return f"block {self.port[0]} port {self.port[1]}"
        return f"({self.x}, {self.y})"

    def to_dict(self) -> dict:
        if self.port is not None:
            return {"block": self.port[0], "port": self.port[1]}
        return {"at": [self.x, self.y], "direction": self.direction}


@dataclass(frozen=True)
class RouteSpec:
    id: str
    kind: str  # "belt" | "pipe"
    start: Endpoint  # an out port or a point
    goal: Endpoint  # an in port or a point
    surface: str  # belt or pipe prototype
    underground: str | None  # None: no hops
    via: tuple[tuple[int, int], ...] = ()
    turn_cost: float = DEFAULT_TURN_COST
    hop_cost: float = DEFAULT_HOP_COST

    @property
    def start_tile(self) -> tuple[int, int]:
        """Where the first piece goes: past an out port's last tile, or the point itself."""
        if self.start.port is None:
            return self.start.x, self.start.y
        dx, dy = STEP[self.start.direction]
        return self.start.x + dx, self.start.y + dy

    @property
    def last_tile(self) -> tuple[int, int]:
        """Where the last piece goes: before an in port's first tile, or the point itself."""
        if self.goal.port is None:
            return self.goal.x, self.goal.y
        dx, dy = STEP[self.goal.direction]
        return self.goal.x - dx, self.goal.y - dy

    @property
    def goal_tile(self) -> tuple[int, int]:
        """The tile the last piece points into."""
        dx, dy = STEP[self.goal.direction]
        x, y = self.last_tile
        return x + dx, y + dy


@dataclass(frozen=True)
class Piece:
    name: str
    x: int
    y: int
    direction: int  # belts: the way they carry; pipes and pipe-to-ground: the entity's own
    io_type: str | None = None  # "input" / "output" for underground belts


@dataclass(frozen=True)
class Tunnel:
    """An existing underground pair, or a lone end when lo == hi."""

    name: str
    axis: str  # "x" | "y": the axis the run goes along
    line: int  # the other coordinate
    lo: int
    hi: int


@dataclass
class RouteResult:
    id: str
    kind: str
    ok: bool
    pieces: list[Piece]
    length: int = 0
    turns: int = 0
    hops: int = 0
    lanes: tuple[str, ...] | None = None
    rate: float | None = None
    reason: str | None = None
    near: tuple[int, int] | None = None
    spec: RouteSpec | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        spec = self.spec
        return {
            "id": self.id,
            "kind": self.kind,
            "ok": self.ok,
            "from": spec.start.to_dict() if spec else None,
            "to": spec.goal.to_dict() if spec else None,
            "surface": spec.surface if spec else None,
            "underground": spec.underground if spec else None,
            "pieces": len(self.pieces),
            "length": self.length,
            "turns": self.turns,
            "hops": self.hops,
            "lanes": list(self.lanes) if self.lanes is not None else None,
            "rate": self.rate,
            "reason": self.reason,
            "near": list(self.near) if self.near is not None else None,
        }


# --------------------------------------------------------------------------
# prototypes
# --------------------------------------------------------------------------


def hop_reach(prototype: str) -> int:
    reach = inspection.underground_reach(prototype)
    if reach is None or reach < 1:
        raise RouteError(f"{prototype!r} states no underground reach")
    return reach


def _visible_direction(prototype: str) -> int | None:
    for connection in (entity_data.raw.get(prototype, {}).get("fluid_box") or {}).get("pipe_connections") or []:
        if connection.get("connection_type") != "underground" and connection.get("direction") is not None:
            return int(connection["direction"])
    return None


def ptg_directions(prototype: str, travel: int) -> tuple[int, int]:
    """The entity directions of the end a fluid dives at and the end it surfaces at.

    Computed from where the prototype puts its underground connection, never
    assumed: the dive's run leaves the way the fluid travels, the surfacing
    end's run leaves back towards the dive.
    """
    offset = inspection.underground_direction(prototype)
    visible = _visible_direction(prototype)
    if offset is None or visible is None or offset % 4 or (visible - offset) % 16 != 8:
        raise RouteError(
            f"unsupported pipe-to-ground geometry in {prototype!r}: its visible and underground "
            "connections do not point in opposite directions"
        )
    return (travel - offset) % 16, (OPPOSITE[travel] - offset) % 16


def related_underground(belt: str) -> str | None:
    """The underground belt that goes with a belt: the one it names, else the one of its speed."""
    entry = entity_data.raw.get(belt) or {}
    named = entry.get("related_underground_belt")
    if named and (entity_data.raw.get(named) or {}).get("type") == "underground-belt":
        return named
    speed = entry.get("speed")
    same = sorted(
        name
        for name, data in entity_data.raw.items()
        if data.get("type") == "underground-belt" and data.get("max_distance") and data.get("speed") == speed
    )
    if len(same) > 1:
        raise RouteError(
            f"{belt} names no underground belt and several share its speed: {', '.join(same)}; "
            "choose one with 'underground'"
        )
    return same[0] if same else None


def _states_underground_mask(prototype: str) -> bool:
    entry = entity_data.raw.get(prototype) or {}
    if entry.get("underground_collision_mask"):
        return True
    return any(
        c.get("underground_collision_mask")
        for c in (entry.get("fluid_box") or {}).get("pipe_connections") or []
    )


def connections_for(name: str, tile: tuple[int, int], direction: int) -> list[fluids.Connection]:
    """A one-tile piece's above-ground fluid connections if it stood at `tile`.

    Asked of `fluids.connections_of` with a stand-in entity, so the router
    reads connections exactly the way the checker does.
    """
    stand_in = SimpleNamespace(
        name=name, direction=direction, position=SimpleNamespace(x=tile[0] + 0.5, y=tile[1] + 0.5), recipe=None
    )
    return [c for c in fluids.connections_of(-1, stand_in) if not c.underground]


# --------------------------------------------------------------------------
# the obstacle grid
# --------------------------------------------------------------------------


def _step(tile: tuple[int, int], direction: int, k: int = 1) -> tuple[int, int]:
    dx, dy = STEP[direction]
    return tile[0] + dx * k, tile[1] + dy * k


def _axis(direction: int) -> str:
    return "x" if direction in (4, 12) else "y"


def _along(tile: tuple[int, int], axis: str) -> tuple[int, int]:
    """(line, position along the axis) of a tile."""
    return (tile[1], tile[0]) if axis == "x" else (tile[0], tile[1])


def _where(tile) -> str:
    return f"({tile[0]}, {tile[1]})"


@dataclass
class Grid:
    area: tuple[int, int, int, int]
    occupied: dict[tuple[int, int], str]
    reserved: set[tuple[int, int]]
    belt_fed: dict[tuple[int, int], str]
    hands: dict[tuple[int, int], str]
    fluid_targets: dict[tuple[int, int], list]  # tile -> [fluids.Connection]
    tunnels: list[Tunnel]
    # Which tiles push into each fed tile, for the out-port exemption.
    feeders: dict[tuple[int, int], set[tuple[int, int]]] = field(default_factory=dict, repr=False)
    # Who owns each fluid connection, for messages.
    fluid_owner: dict[tuple[int, int], str] = field(default_factory=dict, repr=False)
    # Tiles that take items straight from behind -- a belt, an underground
    # entrance, a splitter -- and the way they carry them: what a point goal
    # may point into.
    belt_entry: dict[tuple[int, int], int] = field(default_factory=dict, repr=False)

    def inside(self, tile: tuple[int, int]) -> bool:
        x0, y0, x1, y1 = self.area
        return x0 <= tile[0] <= x1 and y0 <= tile[1] <= y1

    def feed(self, tile, source, name) -> None:
        self.belt_fed.setdefault(tile, f"{name} at {_where(source)} pushes items into it")
        self.feeders.setdefault(tile, set()).add(source)

    def add(self, pieces: list[Piece]) -> None:
        """Make a placed route an obstacle for the routes after it."""
        pending: Piece | None = None
        for piece in pieces:
            tile = (piece.x, piece.y)
            self.occupied[tile] = piece.name
            kind = (entity_data.raw.get(piece.name) or {}).get("type")
            if kind == "transport-belt" or (kind == "underground-belt" and piece.io_type == "output"):
                self.feed(_step(tile, piece.direction), tile, piece.name)
            if kind == "transport-belt" or (kind == "underground-belt" and piece.io_type == "input"):
                self.belt_entry[tile] = piece.direction
            for c in connections_for(piece.name, tile, piece.direction):
                self.fluid_targets.setdefault(c.target, []).append(c)
                self.fluid_owner[c.inner] = piece.name
            if kind in ("underground-belt", "pipe-to-ground"):
                if pending is not None and pending.name == piece.name:
                    axis = "x" if pending.y == piece.y else "y"
                    line, a = _along((pending.x, pending.y), axis)
                    _, b = _along(tile, axis)
                    self.tunnels.append(Tunnel(piece.name, axis, line, min(a, b), max(a, b)))
                    pending = None
                else:
                    pending = piece

    def why(self, tile: tuple[int, int], piece: Piece | None = None) -> str | None:
        """The first rule that keeps this tile, or this piece on it, out of a route."""
        if not self.inside(tile):
            x0, y0, x1, y1 = self.area
            return f"outside the search area ({x0}, {y0})-({x1}, {y1})"
        if tile in self.occupied:
            return f"taken by {self.occupied[tile]}"
        if tile in self.reserved:
            return "reserved"
        kind = (entity_data.raw.get(piece.name) or {}).get("type") if piece else None
        if piece is None or kind in ("transport-belt", "underground-belt"):
            if tile in self.belt_fed:
                return self.belt_fed[tile]
            if tile in self.hands:
                return self.hands[tile]
        if piece is None:
            if self.fluid_targets.get(tile):
                o = self.fluid_targets[tile][0]
                return f"touches {self.fluid_owner.get(o.inner, 'a pipe')}'s fluid connection"
            for t in self.tunnels:
                line, at = _along(tile, t.axis)
                if line == t.line and t.lo < at < t.hi:
                    return f"inside the {t.name} tunnel from {self._end(t, t.lo)} to {self._end(t, t.hi)}"
            return None
        for c in connections_for(piece.name, tile, piece.direction):
            for o in self.joins(c):
                return f"touches {self.fluid_owner.get(o.inner, 'a pipe')}'s fluid connection at {_where(o.inner)}"
        return None

    @staticmethod
    def _end(t: Tunnel, at: int) -> str:
        return _where((at, t.line) if t.axis == "x" else (t.line, at))

    def joins(self, c: fluids.Connection) -> list:
        """Foreign connections this one of ours would join, by the rule `fluids.networks` uses."""
        return [
            o
            for o in self.fluid_targets.get(c.inner, ())
            if o.inner == c.target and o.direction == OPPOSITE[c.direction] and o.categories & c.categories
        ]

    def tunnel_conflict(self, name: str, a: tuple[int, int], b: tuple[int, int], reach: int) -> str | None:
        """Why a hop of `name` from a to b would pair with something it should not, or None."""
        axis = "x" if a[1] == b[1] else "y"
        line, pa = _along(a, axis)
        _, pb = _along(b, axis)
        lo, hi = min(pa, pb), max(pa, pb)
        for t in self.tunnels:
            if t.name != name or t.axis != axis or t.line != line:
                continue
            for end in {t.lo, t.hi}:
                if lo < end < hi:
                    return f"the hop passes the {name} end at {self._end(t, end)}, which would pair with it"
            for at in (lo, hi):
                if t.lo < at < t.hi:
                    return f"inside the {name} tunnel from {self._end(t, t.lo)} to {self._end(t, t.hi)}"
            if t.lo == t.hi and (0 < lo - t.lo <= reach or 0 < t.lo - hi <= reach):
                return f"a lone {name} at {self._end(t, t.lo)} is within reach and could pair with it"
        return None


def search_area(entities: list, endpoints: list[tuple[int, int]], margin: int) -> tuple[int, int, int, int]:
    tiles = [t for e in entities for t in inspection.Layout.tiles_of(e)] + list(endpoints)
    if not tiles:
        return (-margin, -margin, margin, margin)
    xs = [t[0] for t in tiles]
    ys = [t[1] for t in tiles]
    return min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin


def _belt_pushes(entity, own: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """Tiles a belt-like entity pushes items into."""
    kind = getattr(entity, "type", "")
    direction = int(getattr(entity, "direction", 0) or 0)
    if direction not in STEP:
        return []
    if kind == "transport-belt" or (kind == "underground-belt" and getattr(entity, "io_type", None) == "output"):
        return [_step(inspection.Layout.tile_of(entity), direction)]
    if kind == "splitter":
        return [t for t in (_step(o, direction) for o in own) if t not in own]
    if kind in ("loader", "loader-1x1"):
        # Which end is the belt end depends on the loader's mode; both are kept clear.
        ahead = [_step(o, direction) for o in own] + [_step(o, OPPOSITE[direction]) for o in own]
        return [t for t in ahead if t not in own]
    if kind == "mining-drill":
        vector = (entity_data.raw.get(entity.name) or {}).get("vector_to_place_result")
        if vector:
            dx, dy = fluids.rotate(tuple(vector), direction)
            return [(math.floor(entity.position.x + dx), math.floor(entity.position.y + dy))]
    return []


def _takes_from_behind(entity) -> int | None:
    """The way a belt-like entity carries items, if a belt pointing into its back continues it."""
    kind = getattr(entity, "type", "")
    direction = int(getattr(entity, "direction", 0) or 0)
    if direction not in STEP:
        return None
    if kind in ("transport-belt", "splitter"):
        return direction
    if kind == "underground-belt" and getattr(entity, "io_type", None) == "input":
        return direction
    return None


def _underground_ends(entities: list) -> list[Tunnel]:
    """Every underground pair and lone end among the entities, paired the way the checks pair them."""
    by_tile: dict[tuple[int, int], list[int]] = {}
    for index, entity in enumerate(entities):
        for tile in inspection.Layout.tiles_of(entity):
            by_tile.setdefault(tile, []).append(index)

    ends: dict[int, tuple[str, str, int, int]] = {}  # index -> (name, axis, line, at)
    pairs: set[tuple[int, int]] = set()
    for index, entity in enumerate(entities):
        kind = getattr(entity, "type", "")
        tile = inspection.Layout.tile_of(entity)
        if kind == "underground-belt":
            direction = int(getattr(entity, "direction", 0) or 0)
            if direction not in STEP:
                continue
            reach = inspection.underground_reach(entity) or 0
            axis = _axis(direction)
            ends[index] = (entity.name, axis, *_along(tile, axis))
            walk = direction if getattr(entity, "io_type", None) != "output" else OPPOSITE[direction]
            for k in range(1, reach + 1):
                other = next(
                    (i for i in by_tile.get(_step(tile, walk, k), ()) if i != index and entities[i].name == entity.name),
                    None,
                )
                if other is not None:
                    pairs.add((min(index, other), max(index, other)))
                    break
            continue
        for c in fluids.connections_of(index, entity):
            if not c.underground:
                continue
            axis = _axis(c.direction)
            ends[index] = (entity.name, axis, *_along(c.inner, axis))
            for k in range(1, c.reach + 1):
                spot = _step(c.inner, c.direction, k)
                partner = next(
                    (
                        o
                        for i in by_tile.get(spot, ())
                        if i != index and entities[i].name == entity.name
                        for o in fluids.connections_of(i, entities[i])
                        if o.underground and o.inner == spot and o.direction == OPPOSITE[c.direction]
                    ),
                    None,
                )
                if partner is not None:
                    pairs.add((min(index, partner.node[0]), max(index, partner.node[0])))
                    break

    tunnels: list[Tunnel] = []
    paired: set[int] = set()
    for a, b in sorted(pairs):
        if a not in ends or b not in ends:
            continue
        name, axis, line, pa = ends[a]
        _, _, _, pb = ends[b]
        tunnels.append(Tunnel(name, axis, line, min(pa, pb), max(pa, pb)))
        paired.update((a, b))
    for index in sorted(set(ends) - paired):
        name, axis, line, at = ends[index]
        tunnels.append(Tunnel(name, axis, line, at, at))
    return tunnels


def grid_of(entities: list, area: tuple[int, int, int, int], reserved: set[tuple[int, int]]) -> Grid:
    grid = Grid(area, {}, set(reserved), {}, {}, {}, [])
    for entity in entities:
        own = set(inspection.Layout.tiles_of(entity))
        for tile in own:
            grid.occupied.setdefault(tile, entity.name)
        source = inspection.Layout.tile_of(entity)
        for tile in _belt_pushes(entity, own):
            grid.feed(tile, source, entity.name)
        entry = _takes_from_behind(entity)
        if entry is not None:
            for tile in own:
                grid.belt_entry[tile] = entry
        if getattr(entity, "type", "") == "inserter":
            reach = inspection.inserter_reach(entity)
            if reach is not None:
                for spot in reach:
                    tile = (math.floor(spot[0]), math.floor(spot[1]))
                    grid.hands.setdefault(tile, f"in reach of inserter {entity.name} at {_where(source)}")
    for index, entity in enumerate(entities):
        for c in fluids.connections_of(index, entity):
            if c.underground:
                continue
            grid.fluid_targets.setdefault(c.target, []).append(c)
            grid.fluid_owner.setdefault(c.inner, entity.name)
    grid.tunnels = _underground_ends(entities)
    return grid


# --------------------------------------------------------------------------
# placement rules
# --------------------------------------------------------------------------


class _Rules:
    """Whether a piece may stand where the search wants it, for one route."""

    def __init__(self, spec: RouteSpec, grid: Grid) -> None:
        self.spec = spec
        self.grid = grid
        self.belt = spec.kind == "belt"
        self.start_tile = spec.start_tile
        self.last_tile = spec.last_tile
        # The first piece may take what stands behind the start -- an out
        # port's last tile, or whatever continues into a point -- and the last
        # piece may join what stands where it points: an in port's first tile,
        # or past a point whatever `goal_conflict` lets it continue.
        self.behind_start = _step(self.start_tile, OPPOSITE[spec.start.direction])
        self.goal_tile = spec.goal_tile
        self.via = set(spec.via)
        self._cache: dict[tuple, str | None] = {}

    def tile(self, tile: tuple[int, int]) -> str | None:
        """What keeps any piece of this route off a tile, whatever its direction."""
        key = ("tile", tile)
        if key in self._cache:
            return self._cache[key]
        grid = self.grid
        reason = None
        if not grid.inside(tile) or tile in grid.occupied or tile in grid.reserved:
            reason = grid.why(tile)
        elif tile == self.goal_tile:
            # A point goal leaves the tile it points into open; a piece of the
            # route there would have the last piece feed the route itself.
            reason = "is kept open for the last piece to point into"
        elif self.belt:
            fed = grid.feeders.get(tile)
            if fed and not (tile == self.start_tile and fed == {self.behind_start}):
                reason = grid.belt_fed[tile]
            elif tile in grid.hands:
                reason = grid.hands[tile]
        self._cache[key] = reason
        return reason

    def piece(self, piece: Piece) -> str | None:
        tile = (piece.x, piece.y)
        reason = self.tile(tile)
        if reason is not None or self.belt:
            return reason
        key = ("pipe", piece.name, tile, piece.direction)
        if key in self._cache:
            return self._cache[key]
        for c in connections_for(piece.name, tile, piece.direction):
            for o in self.grid.joins(c):
                if tile == self.start_tile and o.inner == self.behind_start:
                    continue
                if tile == self.last_tile and o.inner == self.goal_tile:
                    continue
                reason = (
                    f"touches {self.grid.fluid_owner.get(o.inner, 'a pipe')}'s fluid connection "
                    f"at {_where(o.inner)}"
                )
                break
            if reason:
                break
        self._cache[key] = reason
        return reason

    def goal_conflict(self) -> str | None:
        """Why a point goal's last piece may not point where it points, or None.

        An in port's first tile continues the route by construction. A point
        leaves its tile open, but if something already stands there the route
        joins it, and that must be a straight continuation: a belt, underground
        entrance or splitter carrying the same way (anything else would be a
        side-load, a head-on meeting or a dead end), or for a pipe a fluid
        connection facing back.
        """
        spec, grid, tile = self.spec, self.grid, self.goal_tile
        if spec.goal.port is not None or tile not in grid.occupied:
            return None
        name = grid.occupied[tile]
        d = spec.goal.direction
        if self.belt:
            if grid.belt_entry.get(tile) != d:
                return (f"holds {name}, which the last piece would side-load into, meet head-on or run into; "
                        "only a belt, underground entrance or splitter carrying the same way can be continued")
            if (entity_data.raw.get(name) or {}).get("type") == "transport-belt":
                # Fed from one side only, that belt is a curve; feeding its back
                # straightens it and turns the side feed into a side-load.
                sides = [
                    s for s in (_step(tile, CW[d]), _step(tile, CCW[d]))
                    if s in grid.feeders.get(tile, ())
                    and (entity_data.raw.get(grid.occupied.get(s)) or {}).get("type") == "transport-belt"
                ]
                if len(sides) == 1:
                    return (f"holds {name}, a curve fed from {_where(sides[0])}; feeding its back would "
                            "straighten it and make that feed a side-load")
            return None
        towards = [c for c in connections_for(spec.surface, self.last_tile, 0) if c.direction == d]
        if not any(o.inner == tile for c in towards for o in grid.joins(c)):
            return f"holds {name}, which has no fluid connection facing the route there"
        return None

    def surface_piece(self, tile, flow) -> Piece:
        return Piece(self.spec.surface, tile[0], tile[1], flow if self.belt else 0)

    def hop_pieces(self, entry, exit_, flow) -> tuple[Piece, Piece]:
        name = self.spec.underground
        if self.belt:
            return Piece(name, *entry, flow, "input"), Piece(name, *exit_, flow, "output")
        dive, surface = ptg_directions(name, flow)
        return Piece(name, *entry, dive), Piece(name, *exit_, surface)


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------


def _manhattan(a, b) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _fail(spec: RouteSpec, reason: str, near) -> RouteResult:
    return RouteResult(spec.id, spec.kind, False, [], reason=reason, near=near, spec=spec,
                       lanes=_lanes(spec), rate=_rate(spec))


def _lanes(spec: RouteSpec) -> tuple[str, ...] | None:
    if spec.start.items:
        return spec.start.items
    if spec.kind == "pipe" and spec.goal.items:
        return spec.goal.items
    return None


def _rate(spec: RouteSpec) -> float | None:
    return spec.start.rate if spec.start.rate is not None else spec.goal.rate


MAX_ATTEMPTS = 8  # search tuning: how often a route may set its own earlier path aside and search again


def route(spec: RouteSpec, grid: Grid, max_nodes: int = DEFAULT_MAX_NODES) -> RouteResult:
    """The cheapest path for one connection, or the reason there is none. Places nothing.

    The search state does not remember the path, so a path through via tiles
    can come back over its own earlier tiles. When it does, the tiles laid
    before the stage that came back are kept out of that stage and later ones,
    and the search runs again -- a bounded rip-up of the route's own path,
    never of anything else. If that does not settle it, the first reason is
    reported.
    """
    rules = _Rules(spec, grid)
    start, last = spec.start_tile, spec.last_tile

    for label, tile in (("start", start), ("goal", last)):
        reason = rules.tile(tile)
        if reason is not None:
            return _fail(spec, f"{label} tile {_where(tile)} {reason}", tile)
    for tile in spec.via:
        reason = rules.tile(tile)
        if reason is not None:
            return _fail(spec, f"via tile {_where(tile)} {reason}", tile)
    reason = rules.goal_conflict()
    if reason is not None:
        return _fail(spec, f"goal tile {_where(spec.goal_tile)} {reason}", spec.goal_tile)

    banned: dict[tuple[int, int], int] = {}  # tile -> the stage from which the route keeps off it
    first_problem: str | None = None
    for _ in range(MAX_ATTEMPTS):
        searched = _search(spec, grid, rules, max_nodes, banned)
        if isinstance(searched, RouteResult):
            if first_problem is None:
                return searched
            break
        pieces, stages = searched
        repeat = _first_repeat(pieces, stages)
        if repeat is None:
            problem = validate(pieces, grid, spec)
            if problem is None:
                return _summarise(spec, pieces)
            first_problem = first_problem or problem
            break
        tile, stage, same_stage = repeat
        first_problem = first_problem or f"the path uses {_where(tile)} twice"
        earlier = [tile] if same_stage else [(p.x, p.y) for p, s in zip(pieces, stages) if s < stage]
        changed = False
        for spot in earlier:
            if banned.get(spot, math.inf) > stage:
                banned[spot] = stage
                changed = True
        if not changed:
            break
    return _fail(spec, f"found a path that breaks a rule on inspection: {first_problem}", start)


def _first_repeat(pieces: list[Piece], stages: list[int]) -> tuple[tuple[int, int], int, bool] | None:
    """The first tile the path comes back to, the stage it came back in, and whether it left in that stage."""
    seen: dict[tuple[int, int], int] = {}
    for piece, stage in zip(pieces, stages):
        tile = (piece.x, piece.y)
        if tile in seen:
            return tile, stage, seen[tile] == stage
        seen[tile] = stage
    return None


def _search(spec: RouteSpec, grid: Grid, rules: _Rules, max_nodes: int,
            banned: dict[tuple[int, int], int]) -> RouteResult | tuple[list[Piece], list[int]]:
    """A* for one attempt: the pieces and the via stage each was laid in, or a failed result."""
    via = list(spec.via)
    start, goal = spec.start_tile, spec.goal_tile
    goal_dir = spec.goal.direction

    def kept_off(tile, s) -> bool:
        return banned.get(tile, math.inf) <= s

    reach = hop_reach(spec.underground) if spec.underground else 0
    rest = [0] * (len(via) + 1)
    for s in range(len(via) - 1, -1, -1):
        following = via[s + 1] if s + 1 < len(via) else goal
        rest[s] = _manhattan(via[s], following) + rest[s + 1]

    def h(tile, s) -> int:
        return _manhattan(tile, via[s]) + rest[s] if s < len(via) else _manhattan(tile, goal)

    # A state: the next tile to fill, the way things move into it, via tiles
    # covered so far, and whether the piece before it is an underground exit.
    first = (start[0], start[1], spec.start.direction, 0, False)
    best: dict[tuple, float] = {first: 0.0}
    parent: dict[tuple, tuple[tuple, tuple[Piece, ...]]] = {}
    queue: list = []
    counter = 0

    def push(state, g, turns, hops) -> None:
        nonlocal counter
        x, y, d, s, _ = state
        counter += 1
        heapq.heappush(queue, (g + h((x, y), s), g, turns, hops, y, x, d, s, state[4], counter, state))

    push(first, 0.0, 0, 0)
    expanded = 0
    closest = (h(start, 0), start, 0)
    found = None
    while queue:
        _, g, turns, hops, *_, state = heapq.heappop(queue)
        if g > best.get(state, math.inf):
            continue
        x, y, d, s, after_exit = state
        t = (x, y)
        if t == goal and d == goal_dir and s == len(via):
            found = state
            break
        expanded += 1
        if expanded > max_nodes:
            return _fail(spec, f"search stopped after {max_nodes} states", closest[1])
        if h(t, s) < closest[0]:
            closest = (h(t, s), t, s)
        if kept_off(t, s):
            continue

        moves: list[tuple[tuple, float, int, int, tuple[Piece, ...]]] = []
        at_point_start = t == start and spec.start.port is None and state == first
        for flow in (d, CW[d], CCW[d]):
            turned = flow != d
            if turned and (at_point_start or (rules.belt and after_exit)):
                continue
            if t in rules.via and (s >= len(via) or t != via[s]):
                continue
            piece = rules.surface_piece(t, flow)
            if rules.piece(piece) is not None:
                continue
            covered = s + (1 if s < len(via) and t == via[s] else 0)
            nxt = (*_step(t, flow), flow, covered, False)
            moves.append((nxt, 1 + (spec.turn_cost if turned else 0), int(turned), 0, (piece,)))
        if spec.underground and t not in rules.via:
            for k in range(1, reach + 1):
                exit_ = _step(t, d, k)
                if exit_ in rules.via or not grid.inside(exit_) or kept_off(exit_, s):
                    continue
                entry_piece, exit_piece = rules.hop_pieces(t, exit_, d)
                if rules.piece(entry_piece) is not None or rules.piece(exit_piece) is not None:
                    continue
                if grid.tunnel_conflict(spec.underground, t, exit_, reach) is not None:
                    continue
                nxt = (*_step(exit_, d), d, s, True)
                # A hair more per buried tile, so that of two hops past the same
                # obstacle the shorter one wins and the entry is the last free
                # tile before it.
                moves.append((nxt, k + 1 + spec.hop_cost + k * _BURIED, 0, 1, (entry_piece, exit_piece)))
        for nxt, cost, turn, hop, pieces in moves:
            g2 = g + cost
            if g2 < best.get(nxt, math.inf):
                best[nxt] = g2
                parent[nxt] = (state, pieces)
                push(nxt, g2, turns + turn, hops + hop)

    if found is None:
        x0, y0, x1, y1 = grid.area
        near, stage = closest[1], closest[2]
        why = _why_stuck(rules, near, via[stage] if stage < len(via) else goal)
        return _fail(
            spec,
            f"no path inside ({x0}, {y0})-({x1}, {y1}); came closest at {_where(near)}, "
            f"{closest[0]} tiles short; blocked there by: {why}",
            near,
        )

    pieces: list[Piece] = []
    stages: list[int] = []
    state = found
    while state in parent:
        state, placed = parent[state]
        pieces[:0] = placed
        stages[:0] = [state[3]] * len(placed)
    return pieces, stages


def _why_stuck(rules: _Rules, tile, target) -> str:
    """What stands in the way next to the tile the search got closest from, nearest the target first."""
    around = sorted((_step(tile, d) for d in (0, 4, 8, 12)), key=lambda t: (_manhattan(t, target), t[1], t[0]))
    for spot in [tile] + around:
        reason = rules.tile(spot)
        if reason is None and not rules.belt:
            reason = rules.piece(rules.surface_piece(spot, 0))
        if reason is not None:
            return f"{_where(spot)} {reason}"
    return "every way on is blocked"


def _flows(pieces: list[Piece], spec: RouteSpec) -> list[int]:
    """The way things leave each piece, read from where the next one stands."""
    flows = []
    for i, piece in enumerate(pieces):
        if i + 1 < len(pieces):
            nx, ny = pieces[i + 1].x - piece.x, pieces[i + 1].y - piece.y
            unit = (int(math.copysign(1, nx)) if nx else 0, int(math.copysign(1, ny)) if ny else 0)
            flows.append(next(d for d, v in STEP.items() if v == unit))
        else:
            flows.append(spec.goal.direction)
    return flows


def _hop_pairs(pieces: list[Piece], spec: RouteSpec) -> list[tuple[int, int]]:
    indices = [i for i, p in enumerate(pieces) if spec.underground and p.name == spec.underground]
    return [(indices[j], indices[j + 1]) for j in range(0, len(indices) - 1, 2)]


def _summarise(spec: RouteSpec, pieces: list[Piece]) -> RouteResult:
    flows = _flows(pieces, spec)
    pairs = _hop_pairs(pieces, spec)
    in_hop = {i for pair in pairs for i in pair}
    turns = sum(
        1
        for i in range(len(pieces))
        if i not in in_hop and flows[i] != (flows[i - 1] if i else spec.start.direction)
    )
    length = len(pieces) - len(in_hop) + sum(
        _manhattan((pieces[a].x, pieces[a].y), (pieces[b].x, pieces[b].y)) + 1 for a, b in pairs
    )
    return RouteResult(spec.id, spec.kind, True, pieces, length, turns, len(pairs), _lanes(spec), _rate(spec),
                       spec=spec)


def validate(pieces: list[Piece], grid: Grid, spec: RouteSpec) -> str | None:
    """Every rule, once more, against the finished path and its own pieces."""
    if not pieces:
        return "the path is empty"
    rules = _Rules(spec, grid)
    belt = spec.kind == "belt"
    flows = _flows(pieces, spec)
    tiles = [(p.x, p.y) for p in pieces]
    if tiles[0] != spec.start_tile:
        return f"the first piece is not at {_where(spec.start_tile)}"
    if tiles[-1] != spec.last_tile:
        return f"the last piece is not at {_where(spec.last_tile)}"
    seen: set = set()
    for tile in tiles:
        if tile in seen:
            return f"the path uses {_where(tile)} twice"
        seen.add(tile)
    covered = 0
    for tile in tiles:
        if covered < len(spec.via) and tile == spec.via[covered]:
            covered += 1
    if covered < len(spec.via):
        return f"via tile {_where(spec.via[covered])} is not covered in order"

    for piece in pieces:
        reason = rules.piece(piece)
        if reason is not None:
            return f"{reason} at {_where((piece.x, piece.y))}"
    reason = rules.goal_conflict()
    if reason is not None:
        return f"the goal tile {_where(spec.goal_tile)} {reason}"

    pairs = _hop_pairs(pieces, spec)
    in_hop = {i for pair in pairs for i in pair}
    via_tiles = set(spec.via)
    for i in in_hop:
        if tiles[i] in via_tiles:
            return f"an underground end stands on via tile at {_where(tiles[i])}"

    if belt:
        # Each piece is fed only by the piece before it.
        own_push: dict[tuple[int, int], list[int]] = {}
        for i, piece in enumerate(pieces):
            if piece.io_type == "input":
                continue
            own_push.setdefault(_step(tiles[i], flows[i]), []).append(i)
        for i, tile in enumerate(tiles):
            if any(j != i - 1 for j in own_push.get(tile, ())):
                return f"the route side-loads into itself at {_where(tile)}"
        for i in range(1, len(pieces)):
            if i not in in_hop and flows[i] != flows[i - 1] and pieces[i - 1].io_type == "output":
                return f"a curve right after an underground exit at {_where(tiles[i])}"

    reach = hop_reach(spec.underground) if pairs else 0
    for a, b in pairs:
        if pieces[a].y != pieces[b].y and pieces[a].x != pieces[b].x:
            return f"an underground pair at {_where(tiles[a])} is not on one line"
        if _manhattan(tiles[a], tiles[b]) > reach:
            return f"the underground pair from {_where(tiles[a])} is longer than its reach"
    for a, b in pairs:
        others = Grid(grid.area, {}, set(), {}, {}, {}, list(grid.tunnels))
        for c, e in pairs:
            if (c, e) != (a, b):
                others.add([pieces[c], pieces[e]])
        conflict = others.tunnel_conflict(spec.underground, tiles[a], tiles[b], reach)
        if conflict is not None:
            return f"{conflict} at {_where(tiles[a])}"
    return None


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------

_LEVERS = (
    "Ways out: add a 'via' tile to steer it, raise routing.margin, move or rotate a block, "
    "route this connection earlier in the plan, or allow undergrounds."
)


def _carries(lane: str) -> set[str]:
    return {part for part in lane.split("+") if part}


def findings(spec: RouteSpec, result: RouteResult) -> list[Finding]:
    found: list[Finding] = []
    label = f"route {spec.id} ({spec.start.describe()} -> {spec.goal.describe()})"
    if not result.ok:
        found.append(Finding(Severity.PROBLEM, "route-failed", f"{label}: {result.reason}", _LEVERS, result.near))

    if spec.kind == "belt":
        wants = spec.goal.items
        if any(wants):
            have = spec.start.items
            shown = " | ".join(i or "-" for i in wants)
            if not have:
                found.append(Finding(
                    Severity.NOTE, "route-lanes-unknown",
                    f"{label}: lanes are whatever arrives at {_where((spec.start.x, spec.start.y))}: "
                    f"the destination wants {shown}",
                ))
            else:
                def fits(source) -> bool:
                    if len(wants) == 2 and wants[0] and wants[0] == wants[1]:
                        # One item on both lanes means "the belt carries it": the
                        # inserters take from either lane, so one lane will do and
                        # only throughput suffers (route-belt-slow says so).
                        return all(w in _carries(source[0]) | _carries(source[1]) for w in _carries(wants[0]))
                    return all(not w or w in _carries(s) for w, s in zip(wants, source))

                if not fits(have):
                    detail = ""
                    if fits(tuple(reversed(have))):
                        detail = ("the lanes are swapped; v1 cannot swap lanes -- feed the source the other "
                                  "way round or hand-build a lane swap")
                    found.append(Finding(
                        Severity.PROBLEM, "route-lanes",
                        f"{label}: {spec.goal.describe()} wants {shown} on its lanes, the route delivers "
                        + " | ".join(i or "-" for i in have),
                        detail, (spec.goal.x, spec.goal.y),
                    ))

        supply, demand = spec.start.rate, spec.goal.rate
        need = max(r for r in (supply, demand, 0.0) if r is not None)
        lanes_used = [lane for lane in (spec.start.items or spec.goal.items) if lane]
        speeds = [spec.surface] + ([spec.underground] if spec.underground else [])
        slowest = min(speeds, key=lambda n: (entity_data.raw.get(n) or {}).get("speed") or math.inf)
        try:
            capacity = (layout.lane_throughput(slowest) if len(lanes_used) == 1
                        else layout.belt_throughput(slowest))
        except layout.LayoutError:
            capacity = None
        if capacity is not None and need > capacity + 1e-9:
            found.append(Finding(
                Severity.SUSPECT, "route-belt-slow",
                f"{label}: needs {need:.4g}/s, {slowest} carries {capacity:.4g}/s"
                + (" on one lane" if len(lanes_used) == 1 else ""),
                "Choose a faster belt for this connection.",
                (spec.start.x, spec.start.y),
            ))
        if spec.underground is None:
            try:
                missing = related_underground(spec.surface) is None
            except RouteError:
                missing = False
            if missing:
                found.append(Finding(
                    Severity.NOTE, "route-no-underground",
                    f"{label}: no underground belt goes with {spec.surface}; the route stays on the surface",
                ))
        else:
            a = (entity_data.raw.get(spec.surface) or {}).get("speed")
            b = (entity_data.raw.get(spec.underground) or {}).get("speed")
            if a != b:
                found.append(Finding(
                    Severity.NOTE, "route-underground-speed",
                    f"{label}: {spec.underground} and {spec.surface} run at different speeds; "
                    "the slower one sets what the route carries",
                ))

    if spec.start.rate is not None and spec.goal.rate is not None and spec.start.rate + 1e-9 < spec.goal.rate:
        found.append(Finding(
            Severity.NOTE, "route-short-supply",
            f"{label}: supplies {spec.start.rate:.4g}/s where the destination wants {spec.goal.rate:.4g}/s",
        ))

    used = {p.name for p in result.pieces}
    masked = sorted(n for n in used if _states_underground_mask(n))
    if masked:
        found.append(Finding(
            Severity.NOTE, "route-tiles-unchecked",
            f"{label}: {', '.join(masked)} cannot go under some ground tiles; the tool does not know the map's tiles",
            "Water, lava and empty space are not obstacles here; check the route against the map.",
        ))
    return found
