"""Follow what each belt lane carries, and say where that cannot work.

Every belt has two lanes, left and right as seen in the direction of travel,
and what lies on them is decided by geometry: which way a belt is fed, whether
it bends, which side an inserter drops on. A block's input port names the
items it expects; this module traces every belt-like entity of a finished
build -- block belts, hand-placed belts and routed pieces alike -- from where
items come on to where they stop, and checks the two against each other. It
is the only place lane findings come from.

## The model (Factorio 2.0)

One node per belt tile: transport belts, underground entrances and exits, and
the two halves of a splitter. Each node pushes into the tile ahead of it (an
entrance into its exit). How the receiving node takes that push:

- from behind: it continues, left lane to left lane;
- head-on, into an underground's hood, or into a splitter's side: the items
  stop there (a dead end);
- from a side, onto a transport belt: if the belt is fed from behind too, or
  from both sides, the feeder side-loads -- both its lanes go onto the near
  lane. Fed from one side only, by a plain belt, it is a curve and keeps the
  lanes. Fed from one side only by anything else, it is not tracked.
- from a side, onto an underground entrance: the hood lets only the feeder's
  lane on the entrance's open side through (`hood_passes`), onto the near lane;
  the other lane is a dead end.
- from a side, onto an underground exit: not tracked.

A splitter sends each input half's left lane to the left lane of both outputs,
and the same for the right. Items come on from a `Feed` (a point start or a
plan's `inputs`), from an inserter's drop -- what a machine makes, what it took
off another belt, or something unknown -- from a drill, and as "unknown" at a
belt head that starts at the edge of the build or behind a loader, and at
the head of a hand-placed belt anywhere. Consumers are inserters that
take from a belt; what they take is the recipe of the machine they feed.

Where something is not modelled the lanes past it become unknown and a
`lanes-unchecked` note says why. Unknown content never makes a finding on its
own: it can only stop one from being certain.

## Ends

A push into an empty tile is open -- the belt may go on in the world, the
rule `inspection` uses for fragments -- except at the tail of a block's input
line, which is meant to end there and is closed. Dead ends are closed too,
and so is a belt pushing into anything that is not belt-like (a pole, a
machine, a chest, an inserter).
At a closed end every item must be taken somewhere upstream, or it fills the
lane and stops what shares it.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from draftsman.data import entities as entity_data
from draftsman.data import recipes as recipe_data

from . import fluids, inspection
from .inspection import STEP, Finding, Severity

LEFT, RIGHT = 0, 1
# What a belt may push into without stopping; anything else in the way is a dead end.
_BELT_LIKE = {"transport-belt", "underground-belt", "splitter", "loader", "loader-1x1", "linked-belt"}
_SIDE = ("left", "right")
CW = {0: 4, 4: 8, 8: 12, 12: 0}
CCW = {0: 12, 4: 0, 8: 4, 12: 8}
OPPOSITE = {0: 8, 4: 12, 8: 0, 12: 4}

Tile = tuple[int, int]
Key = tuple[Tile, int]  # (tile, lane)


@dataclass(frozen=True)
class Content:
    """What one lane carries as it leaves a tile."""

    items: frozenset[str] = frozenset()
    unknown: frozenset[Tile] = frozenset()  # tiles where undeclared items come on

    def union(self, other: "Content") -> "Content":
        return Content(self.items | other.items, self.unknown | other.unknown)

    def show(self) -> str:
        text = "+".join(sorted(self.items))
        if self.unknown:
            return f"{text}+?" if text else "?"
        return text or "-"


@dataclass(frozen=True)
class Feed:
    """Items entering a belt head from outside the plan (a point start, or plan 'inputs')."""

    tile: Tile
    lanes: tuple[str, str] | None  # None = something unknown comes in here


@dataclass(frozen=True)
class Expectation:
    tile: Tile  # in port: its first tile; point goal: the route's last tile
    lanes: tuple[str, str]  # (left, right); (a, a) = "the belt carries a"
    ordered: bool  # False for a block in port (inserters take both lanes), True for a point
    label: str  # "block 1 port 0" / "(40, 6)"
    routed: bool  # a route ends here
    route_id: str | None = None
    predicted: tuple[str, str] | None = None  # the router's belief, for route-lanes-disagree


@dataclass(frozen=True)
class Consumer:
    inserter: int  # entity index
    takes: frozenset[str] | None  # None = anything (chest, belt, lab, furnace without recipe ...)
    machine: int | None  # entity index of the crafting machine it feeds, if any


@dataclass(frozen=True)
class Filter:
    """An inserter's item filter: a whitelist keeps only `items`, a blacklist drops them."""

    items: frozenset[str]
    blacklist: bool = False

    def apply(self, content: Content) -> Content:
        kept = content.items - self.items if self.blacklist else content.items & self.items
        return Content(kept, content.unknown)

    def passes(self, item: str) -> bool:
        return (item in self.items) != self.blacklist


@dataclass
class Node:
    tile: Tile
    entity: int
    kind: str  # "belt" | "entrance" | "exit" | "splitter"
    direction: int
    shape: str  # belts: "straight" | "curve" | "unchecked"; others: "straight"


@dataclass
class LaneMap:
    nodes: dict[Tile, Node]
    carries: dict[Key, Content]
    edges: dict[Key, list[tuple[Tile, int, Filter | None]]]  # forward, optional filter
    consumers: dict[Tile, list[Consumer]]
    closed: set[Key]  # lane-nodes whose items stop (dead ends)
    unchecked: list[tuple[Tile, str]]  # (tile, reason)
    # Where items come on (before propagation), for saying so in findings.
    sources: dict[Key, Content] = field(default_factory=dict)
    # Which tile feeds each node from straight behind (a tunnel counts).
    behind: dict[Tile, list[Tile]] = field(default_factory=dict)
    # A splitter half's other half.
    halves: dict[Tile, Tile] = field(default_factory=dict)
    # Which tile feeds each transport belt from each side (LEFT/RIGHT).
    sides: dict[Tile, dict[int, Tile]] = field(default_factory=dict)
    # Crafting machines with a recipe: index -> (recipe, name, tile), and the
    # inserters feeding each: (pickup tile if it is a belt tile else None, filter).
    machines: dict[int, tuple[str, str, Tile]] = field(default_factory=dict)
    fed_by: dict[int, list[tuple[Tile | None, Filter | None]]] = field(default_factory=dict)
    # Heads of hand-placed belts nobody named the load of: what reaches a port from one is worth a note.
    loose_heads: set[Tile] = field(default_factory=set)

    def at(self, tile) -> tuple[Content, Content] | None:
        tile = tuple(tile)
        if tile not in self.nodes:
            return None
        return self.carries[(tile, LEFT)], self.carries[(tile, RIGHT)]

    def downstream(self, tile) -> set[Tile]:
        """Tiles reachable forward from this one, itself included."""
        tile = tuple(tile)
        seen: set[Key] = set()
        todo = [(tile, LEFT), (tile, RIGHT)]
        while todo:
            key = todo.pop()
            if key in seen:
                continue
            seen.add(key)
            todo.extend((t, lane) for t, lane, _ in self.edges.get(key, ()))
        return {key[0] for key in seen}


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


def _step(tile: Tile, direction: int, k: int = 1) -> Tile:
    dx, dy = STEP[direction]
    return tile[0] + dx * k, tile[1] + dy * k


def lane_of_point(tile: Tile, direction: int, point: tuple[float, float]) -> int | None:
    """Which lane of the belt on `tile`, carrying `direction`, a point lies over; None on the centre line."""
    vx, vy = point[0] - (tile[0] + 0.5), point[1] - (tile[1] + 0.5)
    sx, sy = STEP[direction]
    cross = sx * vy - sy * vx
    if cross < -1e-6:
        return LEFT
    if cross > 1e-6:
        return RIGHT
    return None


def hood_passes(side: str) -> str:
    """The feeder lane an underground entrance lets through, for a feeder on its `side`.

    Derived from the geometry: the entrance's hood covers the half of the tile
    in front of the dive, so of a belt pointing into its side only the lane
    nearer the open back gets on -- the right lane of a feeder from the left,
    the left lane of one from the right. Still to be confirmed in the game;
    if it is the other way, this is the one line to flip.
    """
    return "right" if side == "left" else "left"


def _floor(point) -> Tile:
    return math.floor(point[0]), math.floor(point[1])


def _solid(parts) -> frozenset[str]:
    return frozenset(p["name"] for p in parts or [] if p.get("type") != "fluid" and p.get("name"))


def _recipe_of(entity) -> str | None:
    recipe = getattr(entity, "recipe", None)
    if not recipe or recipe not in recipe_data.raw:
        return None
    if "crafting_speed" not in (entity_data.raw.get(entity.name) or {}):
        return None
    return recipe


def _filter_of(entity) -> Filter | None:
    if not getattr(entity, "use_filters", False):
        return None
    names = frozenset(
        getattr(f, "name", None) or (f.get("name") if isinstance(f, dict) else None)
        for f in getattr(entity, "filters", None) or []
    ) - {None}
    if not names:
        return None
    return Filter(names, getattr(entity, "filter_mode", "whitelist") == "blacklist")


def _parts(lane: str) -> frozenset[str]:
    return frozenset(part for part in (lane or "").split("+") if part)


# --------------------------------------------------------------------------
# the trace
# --------------------------------------------------------------------------


def trace(entities: list, feeds: Sequence[Feed] = (), in_ports: Sequence[Tile] = (),
          recipes: Mapping[int, str] | None = None, loose: frozenset[int] | set[int] = frozenset()) -> LaneMap:
    """Every belt lane's content in a finished build, and where items stop.

    `recipes` names the recipe of machines that store none but are known to
    run one -- a furnace in a block picks its recipe from what it is given.
    `loose` are the indices of hand-placed entities: a head of one of their
    belts carries something unknown unless a feed names it, wherever it is.
    """
    occupied: dict[Tile, list[int]] = {}
    for index, entity in enumerate(entities):
        for tile in inspection.Layout.tiles_of(entity):
            occupied.setdefault(tile, []).append(index)
    xs = [t[0] for t in occupied] or [0]
    ys = [t[1] for t in occupied] or [0]

    def outside(tile: Tile) -> bool:
        return not (min(xs) <= tile[0] <= max(xs) and min(ys) <= tile[1] <= max(ys))

    nodes: dict[Tile, Node] = {}
    halves: dict[Tile, Tile] = {}
    unchecked: list[tuple[Tile, str]] = []
    for index, entity in enumerate(entities):
        kind = getattr(entity, "type", "")
        direction = int(getattr(entity, "direction", 0) or 0)
        if kind not in ("transport-belt", "underground-belt", "splitter") or direction not in STEP:
            continue
        tile = inspection.Layout.tile_of(entity)
        if kind == "transport-belt":
            nodes[tile] = Node(tile, index, "belt", direction, "straight")
        elif kind == "underground-belt":
            io = "entrance" if getattr(entity, "io_type", None) == "input" else "exit"
            nodes[tile] = Node(tile, index, io, direction, "straight")
        else:
            own = sorted(inspection.Layout.tiles_of(entity))
            pair = [t for t in own if _step(t, CW[direction]) in own]
            if len(own) != 2 or len(pair) != 1:
                unchecked.append((tile, f"{entity.name} is not two tiles across its flow and one along it"))
                continue
            left = pair[0]
            right = _step(left, CW[direction])
            for half in (left, right):
                nodes[half] = Node(half, index, "splitter", direction, "straight")
            halves[left], halves[right] = right, left
            if (getattr(entity, "filter", None) or getattr(entity, "input_priority", "none") not in (None, "none")
                    or getattr(entity, "output_priority", "none") not in (None, "none")):
                unchecked.append((left, "splitter filter or priority is not modelled"))

    partners = inspection.underground_partners(entities)
    exit_of = {
        inspection.Layout.tile_of(entities[a]): inspection.Layout.tile_of(entities[b])
        for a, b in partners.items()
    }

    edges: dict[Key, list[tuple[Tile, int, Filter | None]]] = {}
    seeds: dict[Key, Content] = {}
    closed: set[Key] = set()
    behind: dict[Tile, list[Tile]] = {}
    sides: dict[Tile, dict[int, Tile]] = {}

    def edge(src: Key, dst: Key, filt: Filter | None = None) -> None:
        edges.setdefault(src, []).append((dst[0], dst[1], filt))

    def inject(key: Key, content: Content) -> None:
        seeds[key] = seeds.get(key, Content()).union(content)

    def unknown_both(tile: Tile) -> None:
        for lane in (LEFT, RIGHT):
            inject((tile, lane), Content(unknown=frozenset({tile})))

    def push_tile(node: Node) -> Tile | None:
        if node.kind == "entrance":
            return exit_of.get(node.tile)
        return _step(node.tile, node.direction)

    def blocked(tile: Tile) -> bool:
        """Something that is not belt-like stands on `tile`: a belt pushing into it stops there."""
        kinds = {getattr(entities[i], "type", "") for i in occupied.get(tile, ())}
        return bool(kinds) and not kinds & _BELT_LIKE

    for tile in sorted(nodes):
        node = nodes[tile]
        if node.kind == "entrance":
            target = exit_of.get(tile)
            if target is not None and target in nodes:
                behind.setdefault(target, []).append(tile)
            continue
        target = _step(tile, node.direction)
        receiver = nodes.get(target)
        if receiver is None:
            if blocked(target):
                # Against a pole, a machine, a chest: a dead end like a head-on belt.
                closed.update({(tile, LEFT), (tile, RIGHT)})
            continue
        p, r = node.direction, receiver.direction
        side = LEFT if p == CW[r] else RIGHT
        if receiver.kind in ("belt", "splitter", "entrance") and p == r:
            behind.setdefault(target, []).append(tile)
        elif receiver.kind == "belt" and p != OPPOSITE[r]:
            sides.setdefault(target, {})[side] = tile
        elif receiver.kind == "entrance" and p != OPPOSITE[r]:
            passes = _SIDE.index(hood_passes(_SIDE[side]))
            edge((tile, passes), (target, side))
            closed.add((tile, 1 - passes))
        elif receiver.kind == "exit" and p not in (r, OPPOSITE[r]):
            unchecked.append((target, "side-loading onto an underground exit is not confirmed"))
            unknown_both(target)
        else:
            # Head-on, into a hood, into a splitter's side: the items stop here.
            closed.update({(tile, LEFT), (tile, RIGHT)})

    by_feed: dict[Tile, list[Feed]] = {}
    loose_heads: set[Tile] = set()
    for feed in feeds:
        by_feed.setdefault(tuple(feed.tile), []).append(feed)

    for tile in sorted(nodes):
        node = nodes[tile]
        fed_behind = behind.get(tile, [])
        for source in fed_behind:
            edge((source, LEFT), (tile, LEFT))
            edge((source, RIGHT), (tile, RIGHT))
        from_side = sides.get(tile, {})
        if node.kind == "belt" and not fed_behind and tile not in by_feed and len(from_side) == 1:
            ((side, source),) = from_side.items()
            if nodes[source].kind == "belt":
                node.shape = "curve"
                edge((source, LEFT), (tile, LEFT))
                edge((source, RIGHT), (tile, RIGHT))
            else:
                node.shape = "unchecked"
                what = {"exit": "an underground exit", "splitter": "a splitter"}.get(nodes[source].kind, "a belt")
                unchecked.append((tile, f"a belt fed only from its side by {what}; "
                                        "whether the game curves it is not confirmed"))
                unknown_both(tile)
        else:
            for side, source in sorted(from_side.items()):
                edge((source, LEFT), (tile, side))
                edge((source, RIGHT), (tile, side))
        for feed in by_feed.get(tile, ()):
            if feed.lanes is None:
                if not fed_behind:
                    unknown_both(tile)
                continue
            for lane in (LEFT, RIGHT):
                inject((tile, lane), Content(_parts(feed.lanes[lane])))
        if not fed_behind and not from_side and tile not in by_feed:
            # A head. From the edge of the build, from a loader, or of a hand-placed
            # belt nobody named the load of, anything may come on.
            back = _step(tile, OPPOSITE[node.direction])
            there = [getattr(entities[i], "type", "") for i in occupied.get(back, ())]
            if node.entity in loose:
                loose_heads.add(tile)
            if (outside(back) or node.entity in loose
                    or any(k in ("loader", "loader-1x1", "linked-belt") for k in there)):
                unknown_both(tile)
    for left, right in sorted(halves.items()):
        for lane in (LEFT, RIGHT):
            edge((left, lane), (right, lane))

    consumers: dict[Tile, list[Consumer]] = {}
    machines: dict[int, tuple[str, str, Tile]] = {}
    fed_by: dict[int, list[tuple[Tile | None, Filter | None]]] = {}
    for index, entity in enumerate(entities):
        recipe = _recipe_of(entity) or (recipes or {}).get(index)
        if recipe is not None:
            machines[index] = (recipe, entity.name, inspection.Layout.tile_of(entity))

    def machine_at(tile: Tile) -> int | None:
        return next((i for i in occupied.get(tile, ()) if i in machines), None)

    for index, entity in enumerate(entities):
        kind = getattr(entity, "type", "")
        if kind == "inserter":
            reach = inspection.inserter_reach(entity)
            if reach is None:
                continue
            pickup, drop = reach
            pick, put = _floor(pickup), _floor(drop)
            filt = _filter_of(entity)
            if put in nodes:
                node = nodes[put]
                lane = lane_of_point(put, node.direction, drop)
                if node.shape == "curve":
                    unchecked.append((put, "inserter drops onto a curve"))
                    unknown_both(put)
                elif lane is None:
                    unchecked.append((put, "inserter drops on the belt's centre line"))
                    unknown_both(put)
                elif pick in nodes:
                    edge((pick, LEFT), (put, lane), filt)
                    edge((pick, RIGHT), (put, lane), filt)
                else:
                    source = machine_at(pick)
                    if source is not None:
                        made = Content(_solid(recipe_data.raw[machines[source][0]].get("results")))
                        inject((put, lane), filt.apply(made) if filt else made)
                    else:
                        inject((put, lane), Content(unknown=frozenset({put})))
            target = machine_at(put)
            if pick in nodes:
                takes = None
                if target is not None:
                    takes = _solid(recipe_data.raw[machines[target][0]].get("ingredients"))
                    if filt is not None:
                        takes = frozenset(i for i in takes if filt.passes(i))
                consumers.setdefault(pick, []).append(Consumer(index, takes, target))
            if target is not None:
                fed_by.setdefault(target, []).append((pick if pick in nodes else None, filt))
        elif kind == "mining-drill":
            vector = (entity_data.raw.get(entity.name) or {}).get("vector_to_place_result")
            direction = int(getattr(entity, "direction", 0) or 0)
            if not vector or direction not in STEP:
                continue
            dx, dy = fluids.rotate(tuple(vector), direction)
            point = (entity.position.x + dx, entity.position.y + dy)
            put = _floor(point)
            if put in nodes:
                lane = lane_of_point(put, nodes[put].direction, point)
                if lane is None:
                    unknown_both(put)
                else:
                    inject((put, lane), Content(unknown=frozenset({put})))

    # The tail of a block's input line is meant to end where it does.
    for start in in_ports:
        tile, seen = tuple(start), set()
        while tile in nodes and tile not in seen:
            seen.add(tile)
            ahead = push_tile(nodes[tile])
            if ahead in nodes and tile in behind.get(ahead, ()):
                tile = ahead
                continue
            if ahead is None or ahead not in nodes and (ahead not in occupied or blocked(ahead)):
                closed.update({(tile, LEFT), (tile, RIGHT)})
            break

    carries: dict[Key, Content] = {(t, lane): Content() for t in nodes for lane in (LEFT, RIGHT)}
    for key, content in seeds.items():
        if key in carries:
            carries[key] = carries[key].union(content)
    queue = sorted(key for key in carries if carries[key] != Content())
    heapq.heapify(queue)
    queued = set(queue)
    while queue:
        key = heapq.heappop(queue)
        queued.discard(key)
        content = carries[key]
        for tile, lane, filt in edges.get(key, ()):
            dst = (tile, lane)
            if dst not in carries:
                continue
            merged = carries[dst].union(filt.apply(content) if filt else content)
            if merged != carries[dst]:
                carries[dst] = merged
                if dst not in queued:
                    heapq.heappush(queue, dst)
                    queued.add(dst)

    return LaneMap(nodes, carries, edges, consumers, closed, unchecked,
                   {k: v for k, v in seeds.items() if k in carries}, behind, halves, sides, machines, fed_by,
                   loose_heads)


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------


def _where(tile) -> str:
    return f"({tile[0]}, {tile[1]})"


def _reverse(lane_map: LaneMap) -> dict[Key, list[tuple[Key, Filter | None]]]:
    rev: dict[Key, list[tuple[Key, Filter | None]]] = {}
    for src, outs in lane_map.edges.items():
        for tile, lane, filt in outs:
            rev.setdefault((tile, lane), []).append((src, filt))
    return rev


def _back(lane_map: LaneMap, rev, key: Key, item: str) -> set[Key]:
    """Lane-nodes `item` reaches `key` from, `key` included."""
    seen: set[Key] = set()
    todo = [key]
    while todo:
        k = todo.pop()
        if k in seen:
            continue
        seen.add(k)
        for src, filt in rev.get(k, ()):
            if item in lane_map.carries.get(src, Content()).items and (filt is None or filt.passes(item)):
                todo.append(src)
    return seen


def _forward(lane_map: LaneMap, keys, item: str) -> set[Key]:
    seen: set[Key] = set()
    todo = list(keys)
    while todo:
        k = todo.pop()
        if k in seen:
            continue
        seen.add(k)
        for tile, lane, filt in lane_map.edges.get(k, ()):
            if filt is None or filt.passes(item):
                todo.append((tile, lane))
    return seen


def _takes(lane_map: LaneMap, tiles, item: str) -> bool:
    return any(c.takes is None or item in c.takes for t in tiles for c in lane_map.consumers.get(t, ()))


def _dead_ends(lane_map: LaneMap) -> list[Finding]:
    found: list[Finding] = []
    rev = _reverse(lane_map)
    for key in sorted(lane_map.closed):
        if key not in lane_map.carries:
            continue
        tile, lane = key
        items = lane_map.carries[key].items
        if not items:
            continue
        side = _SIDE[lane]
        kept, stranded, back = [], [], {}
        for item in sorted(items):
            back[item] = _back(lane_map, rev, key, item)
            (kept if _takes(lane_map, {k[0] for k in back[item]}, item) else stranded).append(item)
        if stranded and kept:
            found.append(Finding(
                Severity.PROBLEM, "lanes-not-taken",
                f"{', '.join(stranded)} shares the {side} lane ending at {_where(tile)} with {', '.join(kept)}, "
                f"and nothing takes {', '.join(stranded)}",
                f"The lane fills with {', '.join(stranded)}, and then {', '.join(kept)} stops arriving.",
                tile,
            ))
        elif stranded:
            spill = None
            for item in stranded:
                for half in sorted({k[0] for k in back[item]} & set(lane_map.halves)):
                    starts = [(t, ln) for t in (half, lane_map.halves[half]) for ln in (LEFT, RIGHT)]
                    if _takes(lane_map, {k[0] for k in _forward(lane_map, starts, item)}, item):
                        spill = min(half, lane_map.halves[half])
                        break
                if spill is None:
                    break
            if spill is not None:
                found.append(Finding(
                    Severity.NOTE, "lanes-overflow",
                    f"{', '.join(stranded)} fills the {side} lane ending at {_where(tile)}, then the splitter at "
                    f"{_where(spill)} sends it all the other way",
                    "", tile,
                ))
            else:
                where = sorted({
                    k[0] for item in stranded for k in back[item]
                    if item in lane_map.sources.get(k, Content()).items
                }) or [tile]
                found.append(Finding(
                    Severity.SUSPECT, "lanes-not-taken",
                    f"nothing takes {', '.join(stranded)} on the {side} lane ending at {_where(tile)}",
                    f"It fills the lane back to where it comes on ({', '.join(_where(t) for t in where)}), "
                    "and whatever puts it there then stops.",
                    tile,
                ))
        if len(kept) >= 2:
            found.append(Finding(
                Severity.SUSPECT, "lanes-mixed",
                f"{' and '.join(kept)} share the {side} lane ending at {_where(tile)}",
                "Unless they arrive in the ratio they are used, the one in excess fills the lane and the other "
                "stops arriving. A lane each is safe.",
                tile,
            ))
    return found


def _fits(wants: tuple[str, str], have: tuple[frozenset[str], frozenset[str]]) -> bool:
    if wants[0] and wants[0] == wants[1]:
        # One item on both lanes means "the belt carries it": either lane will do.
        return _parts(wants[0]) <= have[0] | have[1]
    return all(_parts(w) <= h for w, h in zip(wants, have))


def _expectations(lane_map: LaneMap, expectations: Sequence[Expectation]) -> tuple[list[Finding], list]:
    found: list[Finding] = []
    missing_at: list[tuple[set[Tile], set[str]]] = []
    for exp in expectations:
        lanes = lane_map.at(exp.tile)
        if lanes is None:
            continue
        left, right = lanes
        from_hand = (left.unknown | right.unknown) & lane_map.loose_heads
        if not (left.items or right.items) and not exp.routed and not from_hand:
            continue  # an unconnected fragment
        shown = f"{left.show()} | {right.show()}"
        unknown_tiles = sorted(left.unknown | right.unknown)
        if not exp.ordered:
            needed = sorted({w for w in exp.lanes if w})
            arriving = left.items | right.items
            missing = [n for n in needed if n not in arriving and not unknown_tiles]
            unsure = [n for n in needed if n not in arriving and unknown_tiles]
            if missing:
                missing_at.append((lane_map.downstream(exp.tile), set(missing)))
                found.append(Finding(
                    Severity.PROBLEM, "lanes-missing",
                    f"{exp.label} wants {exp.lanes[0] or '-'} | {exp.lanes[1] or '-'}; "
                    f"{', '.join(missing)} never arrives",
                    f"Arriving: {shown}. Bring it on with a merge onto the route, or name a hand-placed belt's "
                    "load with 'inputs'.",
                    exp.tile,
                ))
            elif unsure:
                found.append(Finding(
                    Severity.NOTE, "lanes-unknown",
                    f"{exp.label}: what enters at {', '.join(_where(t) for t in unknown_tiles)} is not known, "
                    f"so {', '.join(unsure)} cannot be checked",
                    "Name it with a point's 'items', or 'inputs': [{\"at\": [x, y], \"items\": [...]}] for a "
                    "hand-placed belt.",
                    exp.tile,
                ))
            elif (len(needed) == 2 and exp.lanes[0] != exp.lanes[1]
                  and not (exp.lanes[0] in left.items and exp.lanes[1] in right.items)
                  and exp.lanes[0] in right.items and exp.lanes[1] in left.items):
                found.append(Finding(
                    Severity.NOTE, "lanes-swapped",
                    f"{exp.label} is laid out for {exp.lanes[0]} | {exp.lanes[1]} and gets {shown}; its inserters "
                    "take from both lanes, so it runs as built",
                    "", exp.tile,
                ))
        else:
            have = (left.items, right.items)
            wanted = f"{exp.lanes[0] or '-'} | {exp.lanes[1] or '-'}"
            if not _fits(exp.lanes, have):
                if unknown_tiles:
                    found.append(Finding(
                        Severity.NOTE, "lanes-unknown",
                        f"{exp.label}: what enters at {', '.join(_where(t) for t in unknown_tiles)} is not known, "
                        f"so {wanted} cannot be checked",
                        "Name it with a point's 'items', or 'inputs': [{\"at\": [x, y], \"items\": [...]}] for a "
                        "hand-placed belt.",
                        exp.tile,
                    ))
                else:
                    detail = ""
                    if _fits(exp.lanes, (right.items, left.items)):
                        detail = ("the lanes are swapped; a lane join (\"from\": [a, b]) puts each source on the "
                                  "lane you choose")
                    found.append(Finding(
                        Severity.PROBLEM, "lanes-wrong",
                        f"{exp.label} wants {wanted} on its lanes, the belt brings {shown}",
                        detail, exp.tile,
                    ))
        if exp.predicted is not None:
            # A lane with something unknown on it cannot be compared.
            if any(not lane.unknown and _parts(p) != lane.items for p, lane in zip(exp.predicted, (left, right))):
                found.append(Finding(
                    Severity.PROBLEM, "route-lanes-disagree",
                    f"route {exp.route_id}: the router expected {exp.predicted[0] or '-'} | "
                    f"{exp.predicted[1] or '-'} at {_where(exp.tile)}, the lane tracker finds {shown}",
                    "One of them is wrong about the game; report it.",
                    exp.tile,
                ))
    return found, missing_at


def _components(lane_map: LaneMap) -> dict[Tile, Tile]:
    """Each belt tile's connected belt component, named by its smallest tile."""
    parent = {t: t for t in lane_map.nodes}

    def find(t):
        while parent[t] != t:
            parent[t] = parent[parent[t]]
            t = parent[t]
        return t

    for (src, _), outs in lane_map.edges.items():
        for tile, _, _ in outs:
            if src in parent and tile in parent:
                a, b = sorted((find(src), find(tile)))
                parent[b] = a
    return {t: find(t) for t in parent}


def _starved(lane_map: LaneMap, missing_at) -> list[Finding]:
    groups: dict[tuple, list[int]] = {}
    available_of: dict[tuple, set[str]] = {}
    component = _components(lane_map)
    for index in sorted(lane_map.fed_by):
        feeding = lane_map.fed_by[index]
        if any(pick is None for pick, _ in feeding):
            continue
        lanes = [lane_map.at(pick) for pick, _ in feeding]
        if any(c.unknown for pair in lanes for c in pair):
            continue
        available: set[str] = set()
        for (pick, filt), (left, right) in zip(feeding, lanes):
            content = left.union(right)
            available |= (filt.apply(content) if filt else content).items
        if not available:
            continue  # nothing known reaches it: an unconnected fragment
        recipe, name, _ = lane_map.machines[index]
        missing = set(_solid(recipe_data.raw[recipe].get("ingredients"))) - available
        picks = {pick for pick, _ in feeding}
        for downstream, reported in missing_at:
            if picks & downstream:
                missing -= reported
        if not missing:
            continue
        key = (recipe, name, frozenset(missing), component[min(picks)])
        groups.setdefault(key, []).append(index)
        available_of.setdefault(key, set()).update(available)
    found = []
    for key in sorted(groups, key=lambda k: lane_map.machines[groups[k][0]][2]):
        recipe, name, missing, _ = key
        machines = groups[key]
        found.append(Finding(
            Severity.PROBLEM, "lanes-starved",
            f"{len(machines)} x {name} on {recipe} never get {', '.join(sorted(missing))}",
            f"The belts their inserters take from carry {', '.join(sorted(available_of[key]))}.",
            lane_map.machines[machines[0]][2],
        ))
    return found


def check(lane_map: LaneMap, expectations: Sequence[Expectation]) -> list[Finding]:
    """Dead ends, expectations, starved machines and untracked spots, in that order."""
    found = _dead_ends(lane_map)
    said, missing_at = _expectations(lane_map, expectations)
    found += said
    found += _starved(lane_map, missing_at)
    seen: set[Tile] = set()
    for tile, reason in lane_map.unchecked:
        if tile in seen:
            continue
        seen.add(tile)
        found.append(Finding(Severity.NOTE, "lanes-unchecked", f"lanes past {_where(tile)} are not tracked: {reason}",
                             "", tile))
    return found
