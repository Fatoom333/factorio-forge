"""A layout plan in, a checked blueprint and a report out.

The plan is where the decisions live, written by whoever designs the layout
-- Claude, through the skill. It names blocks by their parameters, not by
tiles; `rows.py` expands each block exactly; extra entities (the belts that
join blocks, a chest, a train stop) can be placed by hand. Everything is then
checked together, and the report says what was built, what each row can
really do, where every port is, and what looks wrong -- the feedback that
makes the next revision of the plan better.

    {
      "label": "Battery block",
      "request": "request.json",               (optional: planned rates from its bill)
      "blocks": [
        {"type": "rows", "recipe": "battery", "machine": "chemical-plant",
         "rows": 4, "per_row": 12,              (or "rows": [10, 12, 12, 10])
         "belt": "fast-transport-belt", "inserter": "fast-inserter",
         "long_inserter": "long-handed-inserter", "pole": "medium-electric-pole",
         "stack": "mirror", "align": "start", "at": [0, 0], "rotate": 0,
         "planned": 1.5}                        (optional: crafts/s it is meant to run at)
      ],
      "entities": [{"name": "fast-transport-belt", "position": [-1, 3], "direction": 4}],
      "inputs": [{"at": [-1, 3], "items": ["iron-plate"]}],
      "connections": [
        {"id": "out", "from": {"block": 0, "port": 3, "items": ["battery"]},
         "to": [{"at": [60, 2], "direction": 4}, {"at": [60, 12], "direction": 4, "via": [[50, 8]]}]},
        {"id": "more", "from": {"at": [30, -6], "direction": 8, "items": ["battery"]},
         "onto": {"route": "out/1", "lane": "left"}}
      ],
      "routing": {"margin": 3, "reserve": [[44, -4, 46, 0]], "candidates": 12}
    }

A connection joins an out port (or a point) to an in port (or a point) with
belts or pipes; `route.py` finds the path, after the blocks and hand-placed
entities and in plan order, so each route is an obstacle for the next. Ports
are named by their index in the block's report. Belt and pipe prototypes not
named come from the blocks at either end.

A belt connection may also split (`to` is a list: one splitter per extra
destination, sub-routes `<id>/0`, `<id>/1`, ...), merge (`onto` instead of
`to`: side-loaded onto a lane of an earlier route, the route into an in port,
or a belt at a tile) or join two sources a lane each (`from` is a list of
two). `inputs` names what enters a hand-placed belt head. After routing,
`lanes.py` traces every belt and the report says what arrives at each in port.
"""

from __future__ import annotations

import dataclasses
import json
import math
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path

from draftsman.data import entities as entity_data

from . import fluids, inspection, lanes, layout, route, rows
from .layout import LayoutError, crafts_per_second


class PlanError(Exception):
    """The plan itself is malformed or asks for something impossible."""


@dataclass
class BuiltBlock:
    index: int
    recipe: str
    machine: str
    at: tuple[int, int]
    width: int
    height: int
    machines: int
    rows: list[dict]
    ports: list[dict]
    crafts_per_second: float
    notes: list[str]
    # The belt, pipe and pipe-to-ground the block was built with; connections
    # default to them.
    prototypes: dict[str, str | None] = field(default_factory=dict)
    # What the block is meant to run at: its own 'planned', or its share of
    # the request's bill of materials. None when neither says.
    planned_crafts_per_second: float | None = None


@dataclass
class BuildResult:
    label: str
    blueprint: object
    blocks: list[BuiltBlock]
    findings: list[inspection.Finding]
    power_networks: int
    warnings: list[str] = field(default_factory=list)
    routes: list[route.RouteResult] = field(default_factory=list)

    @property
    def problems(self) -> list[inspection.Finding]:
        return [f for f in self.findings if f.severity is inspection.Severity.PROBLEM]

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "entities": len(self.blueprint.entities),
            "power_networks": self.power_networks,
            "blocks": [asdict(b) for b in self.blocks],
            "findings": [
                {"severity": f.severity.value, "code": f.code, "summary": f.summary,
                 "detail": f.detail, "position": f.position}
                for f in self.findings
            ],
            "routes": [r.to_dict() for r in self.routes],
            "warnings": self.warnings,
            "blueprint": self.blueprint.to_string(),
        }


def _rows_of(block: dict) -> list[int]:
    value = block.get("rows")
    if isinstance(value, list):
        return [int(n) for n in value]
    if isinstance(value, int) and "per_row" in block:
        return [int(block["per_row"])] * value
    raise PlanError("a rows block needs 'rows' as a list of machine counts, or 'rows' and 'per_row'")


def _power_networks(entities) -> int:
    poles = [e for e in entities if getattr(e, "type", "") == "electric-pole"]
    parent = list(range(len(poles)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(poles):
        reach_a = (entity_data.raw.get(a.name) or {}).get("maximum_wire_distance") or 0
        for j in range(i + 1, len(poles)):
            b = poles[j]
            reach_b = (entity_data.raw.get(b.name) or {}).get("maximum_wire_distance") or 0
            if math.dist((a.position.x, a.position.y), (b.position.x, b.position.y)) <= min(reach_a, reach_b):
                parent[find(i)] = find(j)
    return len({find(i) for i in range(len(poles))})


# --------------------------------------------------------------------------
# connections
# --------------------------------------------------------------------------

_CONNECTION_FIELDS = {"id", "kind", "from", "to", "onto", "split", "splitter", "belt", "underground", "pipe",
                      "pipe_to_ground", "via", "turn_cost", "hop_cost"}
_PORT_FIELDS = {"block", "port", "items"}
_POINT_FIELDS = {"at", "direction", "items", "rate"}
_ONTO_FIELDS = {"route", "block", "port", "at", "lane"}
_ROUTING_FIELDS = {"margin", "max_nodes", "reserve", "candidates"}

Spec = route.RouteSpec | route.SplitSpec | route.MergeSpec | route.JoinSpec


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _tile(value, what: str) -> tuple[int, int]:
    if not (isinstance(value, (list, tuple)) and len(value) == 2 and all(_is_int(v) for v in value)):
        raise PlanError(f"{what} must be a pair of whole numbers, not {value!r}")
    return int(value[0]), int(value[1])


def _one_tile(name: str) -> None:
    if layout.machine_size(name) != (1, 1):
        raise PlanError(f"{name} is larger than one tile; only 1x1 pipes and belts route in v1")


def _of_type(name, kind: str, role: str) -> str:
    try:
        return rows._require(kind, name, role)
    except LayoutError as exc:
        raise PlanError(str(exc)) from exc


def _surface_connections(name: str) -> list[dict]:
    return [
        c
        for c in (entity_data.raw.get(name, {}).get("fluid_box") or {}).get("pipe_connections") or []
        if c.get("connection_type") != "underground"
    ]


def _categories(name: str) -> set[str]:
    """The connection categories of a prototype's above-ground fluid connections."""
    found: set[str] = set()
    for c in _surface_connections(name):
        category = c.get("connection_category") or ["default"]
        found.update([category] if isinstance(category, str) else category)
    return found


def _ident(conn, index: int) -> str:
    return conn.get("id") if isinstance(conn, dict) and isinstance(conn.get("id"), str) else f"c{index}"


def _endpoint(raw, side: str, built: list[BuiltBlock]) -> tuple[route.Endpoint, str | None, dict | None]:
    """An endpoint, the kind its port fixes (None for a point), and that block's prototypes."""
    if not isinstance(raw, dict):
        raise PlanError(f"'{side}' must be a port {{block, port}} or a point {{at, direction}}")
    if "block" in raw or "port" in raw:
        unknown = set(raw) - _PORT_FIELDS
        if unknown:
            raise PlanError(f"'{side}' has unknown field(s) {', '.join(sorted(unknown))}")
        b, j = raw.get("block"), raw.get("port")
        if not (_is_int(b) and 0 <= b < len(built)):
            raise PlanError(f"'{side}' names block {b!r}; the plan has {len(built)} block(s)")
        ports = built[b].ports
        if not (_is_int(j) and 0 <= j < len(ports)):
            raise PlanError(f"'{side}' names port {j!r} of block {b}, which has {len(ports)} port(s)")
        port = ports[j]
        wanted = "out" if side == "from" else "in"
        if port["io"] != wanted:
            raise PlanError(f"block {b} port {j} is an {port['io']!r} port; '{side}' needs an {wanted!r} port")
        if "items" in raw:
            carried = {part for lane in port["items"] for part in lane.split("+") if part}
            expected = {i for i in (raw["items"] or []) if i}
            if carried != expected:
                raise PlanError(
                    f"block {b} port {j} carries {', '.join(sorted(carried)) or 'nothing'}, the plan expected "
                    f"{', '.join(sorted(expected)) or 'nothing'}; ports renumber when a block changes"
                )
        pipe = built[b].prototypes.get("pipe") if port["kind"] == "pipe" else None
        # Routes are sized for what the block is meant to make, where that is known.
        rate = port.get("planned_rate")
        rate = port["rate"] if rate is None else rate
        point = route.Endpoint(port["x"], port["y"], port["direction"], tuple(port["items"]),
                               float(rate), (b, j), pipe)
        return point, port["kind"], built[b].prototypes
    unknown = set(raw) - _POINT_FIELDS
    if unknown:
        raise PlanError(f"'{side}' has unknown field(s) {', '.join(sorted(unknown))}")
    x, y = _tile(raw.get("at"), f"'{side}' at")
    direction = raw.get("direction")
    if not _is_int(direction) or direction not in inspection.STEP:
        raise PlanError(f"'{side}' direction must be 0, 4, 8 or 12, not {direction!r}")
    items = raw.get("items") or []
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        raise PlanError(f"'{side}' items must be a list of names")
    rate = raw.get("rate")
    if rate is not None and (isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate < 0):
        raise PlanError(f"'{side}' rate must be a number of at least 0")
    return route.Endpoint(x, y, direction, tuple(items), None if rate is None else float(rate)), None, None


def _normalise(point: route.Endpoint, kind: str) -> route.Endpoint:
    """A point's items as (left, right) for a belt, (fluid,) for a pipe."""
    if point.port is not None or not point.items:
        return point
    items = point.items
    if kind == "belt":
        if len(items) > 2:
            raise PlanError("a belt point's items are [left, right], or one item for both lanes")
        items = items * 2 if len(items) == 1 else items
    elif len(items) != 1:
        raise PlanError("a pipe point's items are [fluid]")
    return route.Endpoint(point.x, point.y, point.direction, tuple(items), point.rate)


def _sized(goal: route.Endpoint, sources: list[route.Endpoint], built: list[BuiltBlock]) -> route.Endpoint:
    """An in port's demand counted only for the items its sources bring; the rest come another way."""
    items = {part for s in sources for lane in s.items for part in lane.split("+") if part}
    if goal.port is None or not items:
        return goal
    rates = built[goal.port[0]].ports[goal.port[1]].get("item_rates") or {}
    known = [rates[i] for i in items if i in rates]
    if not known:
        return goal
    return dataclasses.replace(goal, rate=sum(known))


def _costs(conn: dict) -> dict:
    costs = {}
    for key in ("turn_cost", "hop_cost"):
        if key in conn:
            value = conn[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise PlanError(f"{key} must be a number of at least 0")
            costs[key] = float(value)
    return costs


def _vias(raw) -> tuple[tuple[int, int], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PlanError("via is a list of [x, y] tiles")
    return tuple(_tile(v, "a via tile") for v in raw)


def _belt_prototypes(conn: dict, protos: list[dict]) -> tuple[str, str | None]:
    """The belt and underground belt a connection routes with: named, else from the blocks at its ends."""
    surface = conn.get("belt") or next((p["belt"] for p in protos if p.get("belt")), None)
    surface = _of_type(surface, "transport-belt", "belt")
    _one_tile(surface)
    underground = conn.get("underground")
    if underground is False:
        underground = None
    elif underground is None:
        underground = route.related_underground(surface)
    else:
        entry = entity_data.raw.get(underground) or {}
        if entry.get("type") != "underground-belt" or not entry.get("max_distance"):
            raise PlanError(f"{underground!r} is not an underground belt that states max_distance")
    if underground:
        _one_tile(underground)
    return surface, underground


def _kind_of(conn: dict, kinds: set[str]) -> str:
    stated = conn.get("kind")
    if stated is not None and stated not in ("belt", "pipe"):
        raise PlanError(f"kind is 'belt' or 'pipe', not {stated!r}")
    if len(kinds) > 1:
        raise PlanError("one end is a belt port and the other a pipe port")
    if kinds and stated and stated not in kinds:
        raise PlanError(f"kind {stated!r} does not match the {next(iter(kinds))} port")
    kind = stated or (next(iter(kinds)) if kinds else None)
    if kind is None:
        raise PlanError("neither end is a port, so 'kind' must say 'belt' or 'pipe'")
    wrong = ({"pipe", "pipe_to_ground"} if kind == "belt" else {"belt", "underground"}) & set(conn)
    if wrong:
        raise PlanError(f"a {kind} connection does not take {', '.join(sorted(wrong))}")
    return kind


@dataclass
class _Routes:
    """Which route ends where and which id is whose, across the whole plan, for merges to find their target."""

    index_of: dict[str, int]  # route or sub-route id -> connection index
    resolved: dict[str, str]  # a name a merge may use -> the sub-route id it means
    ending_at: dict[tuple[int, int], str]  # in port -> the sub-route id ending there
    # The belt each route resolved so far runs on, and the belt-like entity on
    # each tile before routing: what a merge onto them defaults to.
    surface_of: dict[str, str] = field(default_factory=dict)
    belt_at: dict[tuple[int, int], str] = field(default_factory=dict)


def _survey(connections: list) -> _Routes:
    survey = _Routes({}, {}, {})
    for index, conn in enumerate(connections):
        if not isinstance(conn, dict):
            continue
        ident = _ident(conn, index)
        to, sources = conn.get("to"), conn.get("from")
        if isinstance(to, list):
            subs = [f"{ident}/{k}" for k in range(len(to))]
            ends = list(zip(to, subs))
        elif isinstance(sources, list):
            subs = [f"{ident}/0", f"{ident}/1"]
            ends = [(to, subs[0])]
        else:
            subs = [ident]
            ends = [(to, ident)]
        survey.resolved[ident] = subs[0]
        for sub in subs:
            survey.resolved[sub] = sub
            survey.index_of[sub] = index
        for raw, sub in ends:
            if isinstance(raw, dict) and _is_int(raw.get("block")) and _is_int(raw.get("port")):
                survey.ending_at.setdefault((raw["block"], raw["port"]), sub)
    return survey


def _route_spec(conn: dict, index: int, built: list[BuiltBlock], survey: _Routes) -> Spec:
    """One plan connection, resolved against the built blocks into what the router needs."""
    ident = _ident(conn, index)
    try:
        return _resolve_connection(conn, ident, index, built, survey)
    except (PlanError, route.RouteError, LayoutError) as exc:
        raise PlanError(f"connection {index} ({ident}): {exc}") from exc


def _resolve_connection(conn, ident: str, index: int, built: list[BuiltBlock], survey: _Routes) -> Spec:
    if not isinstance(conn, dict):
        raise PlanError("a connection is an object with 'from' and 'to'")
    unknown = set(conn) - _CONNECTION_FIELDS
    if unknown:
        raise PlanError(f"unknown field(s) {', '.join(sorted(unknown))}")
    if "from" not in conn or ("to" not in conn and "onto" not in conn):
        raise PlanError("a connection needs 'from' and 'to' (or 'onto', for a merge)")
    if "to" in conn and "onto" in conn:
        raise PlanError("a connection has 'to' or 'onto', not both")
    if isinstance(conn.get("to"), list):
        return _resolve_split(conn, ident, built)
    if "onto" in conn:
        return _resolve_merge(conn, ident, index, built, survey)
    if isinstance(conn["from"], list):
        return _resolve_join(conn, ident, built)
    for field_ in ("split", "splitter"):
        if field_ in conn:
            raise PlanError(f"'{field_}' belongs to a split, whose 'to' is a list")

    start, start_kind, start_protos = _endpoint(conn["from"], "from", built)
    goal, goal_kind, goal_protos = _endpoint(conn["to"], "to", built)
    kind = _kind_of(conn, {k for k in (start_kind, goal_kind) if k})
    start, goal = _normalise(start, kind), _normalise(goal, kind)
    if kind == "pipe" and start.items and goal.items and start.items[0] != goal.items[0]:
        raise PlanError(f"it would join {start.items[0]} to {goal.items[0]}")
    via = _vias(conn.get("via"))
    costs = _costs(conn)

    protos = [p for p in (start_protos, goal_protos) if p]
    if kind == "belt":
        surface, underground = _belt_prototypes(conn, protos)
        goal = _sized(goal, [start], built)
    else:
        surface = conn.get("pipe") or next((p["pipe"] for p in protos if p.get("pipe")), None)
        surface = _of_type(surface or rows._default_pipe("pipe"), "pipe", "pipe")
        _one_tile(surface)
        if sorted(int(c.get("direction", -1)) for c in _surface_connections(surface)) != [0, 4, 8, 12]:
            raise PlanError(f"{surface} does not connect on all four sides; only such pipes route in v1")
        underground = conn.get("pipe_to_ground")
        if underground is False:
            underground = None
        else:
            if underground is None:
                underground = next((p["pipe_to_ground"] for p in protos if p.get("pipe_to_ground")), None)
                underground = underground or rows._default_pipe("pipe-to-ground")
            underground = _of_type(underground, "pipe-to-ground", "pipe-to-ground")
            _one_tile(underground)
            route.hop_reach(underground)
            route.ptg_directions(underground, 0)
            if not _categories(underground) & _categories(surface):
                raise PlanError(f"{underground} and {surface} share no connection category")
        for point in (start, goal):
            if point.pipe_name and not _categories(point.pipe_name) & _categories(surface):
                raise PlanError(f"{surface} shares no connection category with the port's {point.pipe_name}")

    return route.RouteSpec(ident, kind, start, goal, surface, underground, via, **costs)


_PIPES_DO_NOT_SPLIT = "pipes join whatever they touch; route to a point beside the pipe instead"


def _resolve_split(conn: dict, ident: str, built: list[BuiltBlock]) -> route.SplitSpec:
    raws = conn["to"]
    if len(raws) < 2:
        raise PlanError("a split's 'to' lists two or more destinations")
    source, source_kind, source_protos = _endpoint(conn["from"], "from", built)
    goals, kinds, protos, vias = [], {source_kind} - {None}, [source_protos] if source_protos else [], []
    for raw in raws:
        if not isinstance(raw, dict):
            raise PlanError("each destination of a split is a port or a point")
        bare = {k: v for k, v in raw.items() if k != "via"}
        goal, kind, goal_protos = _endpoint(bare, "to", built)
        goals.append(goal)
        if kind:
            kinds.add(kind)
        if goal_protos:
            protos.append(goal_protos)
        vias.append(_vias(raw.get("via")))
    vias[0] = _vias(conn.get("via")) + vias[0]
    if conn.get("kind") == "pipe" or "pipe" in kinds:
        raise PlanError(_PIPES_DO_NOT_SPLIT)
    kind = _kind_of(conn, kinds)
    source = _normalise(source, kind)
    goals = [_sized(_normalise(g, kind), [source], built) for g in goals]
    surface, underground = _belt_prototypes(conn, protos)

    splitter = conn.get("splitter")
    if splitter is None:
        splitter = route.related_splitter(surface)
        if splitter is None:
            raise PlanError(f"no splitter has the speed of {surface}; name one with 'splitter'")
    else:
        splitter = _of_type(splitter, "splitter", "splitter")
    route.check_splitter(splitter)

    fixed_raw = conn.get("split") or []
    if not isinstance(fixed_raw, list) or len(fixed_raw) > len(goals) - 1:
        raise PlanError("'split' is a list aligned with the destinations after the first: null or {at, side}")
    fixed = []
    for entry in list(fixed_raw) + [None] * (len(goals) - 1 - len(fixed_raw)):
        if entry is None:
            fixed.append(None)
            continue
        if not isinstance(entry, dict) or set(entry) - {"at", "side"} or entry.get("side") not in ("left", "right"):
            raise PlanError("a 'split' entry is {\"at\": [x, y], \"side\": \"left\" | \"right\"}")
        fixed.append((_tile(entry.get("at"), "split at"), entry["side"]))
    return route.SplitSpec(ident, "belt", source, tuple(goals), tuple(vias), tuple(fixed), surface, underground,
                           splitter, **_costs(conn))


def _resolve_merge(conn: dict, ident: str, index: int, built: list[BuiltBlock], survey: _Routes) -> route.MergeSpec:
    for field_ in ("split", "splitter"):
        if field_ in conn:
            raise PlanError(f"'{field_}' belongs to a split, whose 'to' is a list")
    source, source_kind, source_protos = _endpoint(conn["from"], "from", built)
    onto = conn["onto"]
    if not isinstance(onto, dict):
        raise PlanError("'onto' is {\"route\": id}, {\"block\": b, \"port\": j} or {\"at\": [x, y]}, with 'lane'")
    unknown = set(onto) - _ONTO_FIELDS
    if unknown:
        raise PlanError(f"'onto' has unknown field(s) {', '.join(sorted(unknown))}")
    named = [k for k in ("route", "at") if k in onto] + (["port"] if "block" in onto or "port" in onto else [])
    if len(named) != 1:
        raise PlanError("'onto' names exactly one of a route, an in port or a tile")
    lane = onto.get("lane", "auto")
    if lane not in ("left", "right", "auto"):
        raise PlanError(f"'onto' lane is 'left', 'right' or 'auto', not {lane!r}")
    protos = [source_protos] if source_protos else []
    onto_route = onto_tile = None
    if named[0] == "at":
        onto_tile = _tile(onto["at"], "'onto' at")
        if lane == "auto":
            raise PlanError("a merge onto a tile names its lane: 'left' or 'right'")
        if onto_tile in survey.belt_at:
            protos.append({"belt": survey.belt_at[onto_tile]})
    else:
        if named[0] == "route":
            name = onto["route"]
            if not isinstance(name, str) or name not in survey.resolved:
                raise PlanError(f"'onto' names route {name!r}, which no connection makes")
            onto_route = survey.resolved[name]
        else:
            b, j = onto.get("block"), onto.get("port")
            if not (_is_int(b) and 0 <= b < len(built)) or not (_is_int(j) and 0 <= j < len(built[b].ports)):
                raise PlanError(f"'onto' names block {b!r} port {j!r}, which the plan does not have")
            if built[b].ports[j]["io"] != "in":
                raise PlanError(f"'onto' names block {b} port {j}, an out port; a merge goes onto the route into an in port")
            if (b, j) not in survey.ending_at:
                raise PlanError(f"no connection ends at block {b} port {j}, so there is no route to merge onto")
            onto_route = survey.ending_at[(b, j)]
        if onto_route in survey.surface_of:
            protos.insert(0, {"belt": survey.surface_of[onto_route]})
        at = survey.index_of[onto_route]
        if at == index:
            raise PlanError("a connection cannot merge onto itself")
        if at > index:
            raise PlanError(f"connection {index} merges onto route {onto_route}, which is routed later; "
                            f"list it after {onto_route}")
    if source_kind == "pipe" or conn.get("kind") == "pipe":
        raise PlanError(_PIPES_DO_NOT_SPLIT)
    _kind_of({**conn, "kind": "belt"}, {source_kind} - {None})
    source = _normalise(source, "belt")
    surface, underground = _belt_prototypes(conn, protos)
    return route.MergeSpec(ident, source, onto_route, onto_tile, lane, surface, underground,
                           _vias(conn.get("via")), **_costs(conn))


def _resolve_join(conn: dict, ident: str, built: list[BuiltBlock]) -> route.JoinSpec:
    for field_ in ("split", "splitter"):
        if field_ in conn:
            raise PlanError(f"'{field_}' belongs to a split, whose 'to' is a list")
    raws = conn["from"]
    if len(raws) != 2:
        raise PlanError("a lane join's 'from' lists exactly two sources")
    sources, kinds, protos = [], set(), []
    for raw in raws:
        point, kind, found = _endpoint(raw, "from", built)
        sources.append(point)
        if kind:
            kinds.add(kind)
        if found:
            protos.append(found)
    goal, goal_kind, goal_protos = _endpoint(conn["to"], "to", built)
    if goal_kind:
        kinds.add(goal_kind)
    if goal_protos:
        protos.append(goal_protos)
    if conn.get("kind") == "pipe" or "pipe" in kinds:
        raise PlanError(_PIPES_DO_NOT_SPLIT)
    kind = _kind_of(conn, kinds)
    sources = [_normalise(s, kind) for s in sources]
    goal = _sized(_normalise(goal, kind), sources, built)
    surface, underground = _belt_prototypes(conn, protos)
    return route.JoinSpec(ident, (sources[0], sources[1]), goal, surface, underground, _vias(conn.get("via")),
                          **_costs(conn))


def _routing_options(plan: dict) -> tuple[int, int, set, int]:
    options = plan.get("routing") or {}
    if not isinstance(options, dict):
        raise PlanError("routing: must be an object")
    unknown = set(options) - _ROUTING_FIELDS
    if unknown:
        raise PlanError(f"routing: unknown field(s) {', '.join(sorted(unknown))}")
    margin = options.get("margin", route.DEFAULT_MARGIN)
    max_nodes = options.get("max_nodes", route.DEFAULT_MAX_NODES)
    candidates = options.get("candidates", route.DEFAULT_CANDIDATES)
    if not _is_int(margin) or margin < 0:
        raise PlanError("routing: margin must be a whole number of at least 0")
    if not _is_int(max_nodes) or max_nodes < 1:
        raise PlanError("routing: max_nodes must be a whole number of at least 1")
    if not _is_int(candidates) or candidates < 1:
        raise PlanError("routing: candidates must be a whole number of at least 1")
    reserved: set[tuple[int, int]] = set()
    for rect in options.get("reserve") or []:
        if not (isinstance(rect, (list, tuple)) and len(rect) == 4 and all(_is_int(v) for v in rect)):
            raise PlanError(f"routing: reserve takes [x0, y0, x1, y1] rectangles, not {rect!r}")
        x0, y0, x1, y1 = rect
        reserved.update(
            (x, y) for x in range(min(x0, x1), max(x0, x1) + 1) for y in range(min(y0, y1), max(y0, y1) + 1)
        )
    return margin, max_nodes, reserved, candidates


def _uses(spec: Spec) -> list[tuple[route.Endpoint, str]]:
    """The ports a connection uses, with the role each plays; a merge's target port is not a use."""
    if isinstance(spec, route.RouteSpec):
        return [(spec.start, "from"), (spec.goal, "to")]
    if isinstance(spec, route.SplitSpec):
        return [(spec.source, "from")] + [(g, "to") for g in spec.goals]
    if isinstance(spec, route.MergeSpec):
        return [(spec.source, "from")]
    return [(s, "from") for s in spec.sources] + [(spec.goal, "to")]


def _tiles_of(spec: Spec) -> list[tuple[int, int]]:
    """Tiles the search area must cover for this connection."""
    if isinstance(spec, route.RouteSpec):
        return [spec.start_tile, spec.last_tile, spec.goal_tile, *spec.via]
    points = []
    if isinstance(spec, route.SplitSpec):
        points = [spec.source, *spec.goals]
        extra = [t for vias in spec.vias for t in vias] + [f[0] for f in spec.fixed if f]
    elif isinstance(spec, route.MergeSpec):
        points = [spec.source]
        extra = list(spec.via) + ([spec.onto_tile] if spec.onto_tile else [])
    else:
        points = [*spec.sources, spec.goal]
        extra = list(spec.via)
    return [(p.x, p.y) for p in points] + extra


def _place(blueprint, pieces: list[route.Piece]) -> None:
    for piece in pieces:
        if piece.other is not None:
            # A splitter is placed by its centre, the mean of its two tile centres.
            centre = ((piece.x + piece.other[0]) / 2 + 0.5, (piece.y + piece.other[1]) / 2 + 0.5)
            entity = blueprint.entities.append(piece.name, position=centre, direction=piece.direction)
            if set(inspection.Layout.tiles_of(entity)) != {(piece.x, piece.y), piece.other}:
                raise route.RouteError(f"{piece.name} at {centre} does not cover the tiles the router chose")
            continue
        kwargs = {"tile_position": (piece.x, piece.y)}
        if piece.io_type:
            kwargs["io_type"] = piece.io_type
        entity = blueprint.entities.append(piece.name, **kwargs)
        # Whether an entity turns at all is the entity class's to say.
        if getattr(entity, "rotatable", False):
            entity.direction = piece.direction


def _route_all(plan: dict, built: list[BuiltBlock], blueprint) -> tuple[list[route.RouteResult], list]:
    """Resolve every connection first, then route them in plan order, placing what succeeds."""
    connections = plan.get("connections") or []
    if not isinstance(connections, list):
        raise PlanError("connections must be a list")
    survey = _survey(connections)
    for entity in blueprint.entities:
        if getattr(entity, "type", "") == "transport-belt":
            survey.belt_at[inspection.Layout.tile_of(entity)] = entity.name
    specs = []
    for index, conn in enumerate(connections):
        spec = _route_spec(conn, index, built, survey)
        specs.append(spec)
        if getattr(spec, "kind", "belt") == "belt":
            for sub, at in survey.index_of.items():
                if at == index:
                    survey.surface_of[sub] = spec.surface
    used: dict[tuple[tuple[int, int], str], int] = {}
    for index, spec in enumerate(specs):
        for point, role in _uses(spec):
            if point.port is None:
                continue
            if (point.port, role) in used:
                raise PlanError(
                    f"connection {index} ({spec.id}): block {point.port[0]} port {point.port[1]} is already "
                    f"used by connection {used[(point.port, role)]}; one source to several destinations is a "
                    "split ('to': [...])"
                )
            used[(point.port, role)] = index
    margin, max_nodes, reserved, candidates = _routing_options(plan)
    if not specs:
        return [], []

    entities = list(blueprint.entities)
    endpoints = [t for s in specs for t in _tiles_of(s)]
    grid = route.grid_of(entities, route.search_area(entities, endpoints, margin), reserved)
    results: list[route.RouteResult] = []
    found: list = []
    by_id: dict[str, route.RouteResult] = {}
    for index, spec in enumerate(specs):
        try:
            if isinstance(spec, route.RouteSpec):
                done = [route.route(spec, grid, max_nodes)]
                done[0].connection = spec.id
                if done[0].ok:
                    grid.add(done[0].pieces)
            elif isinstance(spec, route.SplitSpec):
                done = route.route_split(spec, grid, max_nodes, candidates)
            elif isinstance(spec, route.MergeSpec):
                target = by_id.get(spec.onto_route) if spec.onto_route else next(
                    (r for r in results if r.ok and any((p.x, p.y) == spec.onto_tile for p in r.pieces)), None)
                if spec.onto_route and not (target and target.ok):
                    point = (spec.source.x, spec.source.y)
                    stand_in = route.RouteSpec(spec.id, "belt", spec.source, route.Endpoint(*point, spec.source.direction),
                                               spec.surface, spec.underground)
                    done = [route._failed(spec.id, stand_in, spec.id,
                                          f"route {spec.onto_route}, which it merges onto, was not built", point)]
                else:
                    done = [route.route_merge(spec, target, grid, max_nodes, candidates)]
            else:
                done = route.route_join(spec, grid, max_nodes, candidates)
        except route.RouteError as exc:
            raise PlanError(f"connection {index} ({spec.id}): {exc}") from exc
        if all(r.ok for r in done):
            for r in done:
                _place(blueprint, r.pieces)
        for r in done:
            by_id[r.id] = r
            results.append(r)
    for r in results:
        found.extend(route.findings(r.spec, r))
    return results, found


# --------------------------------------------------------------------------
# lanes
# --------------------------------------------------------------------------


def _inputs(plan: dict) -> list[lanes.Feed]:
    feeds = []
    raw = plan.get("inputs") or []
    if not isinstance(raw, list):
        raise PlanError("inputs is a list of {\"at\": [x, y], \"items\": [...]}")
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) - {"at", "items"}:
            raise PlanError(f"an input is {{\"at\": [x, y], \"items\": [...]}}, not {entry!r}")
        tile = _tile(entry.get("at"), "an input's at")
        items = entry.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 2 or not all(isinstance(i, str) for i in items):
            raise PlanError(f"input at {list(tile)}: items are [left, right], or one item for both lanes")
        feeds.append(lanes.Feed(tile, (items[0], items[-1])))
    return feeds


def _check_lanes(plan: dict, built: list[BuiltBlock], heads: list[tuple[int, int]], routes: list[route.RouteResult],
                 entities: list, known_recipes: dict[int, str]) -> list[inspection.Finding]:
    """Trace every belt lane of the build, fill in what the report says arrives, and check it."""
    inputs = _inputs(plan)
    feeds = list(inputs)
    for r in routes:
        if not r.ok or r.spec is None or r.spec.start.port is not None:
            continue
        if r.junction is not None and r.junction.kind == "splitter":
            continue  # a branch starts at its splitter, inside the plan
        items = r.spec.start.items
        feeds.append(lanes.Feed(r.spec.start_tile, (items[0], items[1]) if len(items) == 2 else None))
    # Nothing enters a block's output line from behind: what it carries, its inserters put there.
    feeds.extend(lanes.Feed(tile, ("", "")) for tile in heads)
    in_ports = [(p["x"], p["y"]) for b in built for p in b.ports if p["kind"] == "belt" and p["io"] == "in"]
    lane_map = lanes.trace(entities, feeds, in_ports, known_recipes)

    for feed in inputs:
        if feed.tile not in lane_map.nodes:
            raise PlanError(f"input at {list(feed.tile)}: no belt stands there")
        if lane_map.behind.get(feed.tile):
            source = lane_map.behind[feed.tile][0]
            name = entities[lane_map.nodes[source].entity].name
            raise PlanError(f"input at {list(feed.tile)} is fed from behind by {name} at {list(source)}; "
                            "an input names what enters a belt head")

    ending = {r.spec.goal.port: r for r in routes if r.ok and r.spec and r.spec.goal.port is not None}

    def predicted(r: route.RouteResult | None):
        if r is None or not r.lanes or len(r.lanes) != 2:
            return None
        return route.lanes_at(r, len(r.pieces) - 1)

    expectations = []
    for block in built:
        for port in block.ports:
            if port["kind"] != "belt" or port["io"] != "in":
                continue
            r = ending.get((block.index, port["index"]))
            items = tuple(port["items"])
            expectations.append(lanes.Expectation(
                (port["x"], port["y"]), (items[0], items[-1]), False, f"block {block.index} port {port['index']}",
                r is not None, r.id if r else None, predicted(r),
            ))
            arrived = lane_map.at((port["x"], port["y"]))
            port["arrives"] = [arrived[0].show(), arrived[1].show()] if arrived else None
    for r in routes:
        if not r.ok or r.spec is None or r.spec.kind != "belt" or not r.pieces:
            continue
        last = (r.pieces[-1].x, r.pieces[-1].y)
        arrived = lane_map.at(last)
        r.delivered = (arrived[0].show(), arrived[1].show()) if arrived else None
        goal = r.spec.goal
        if goal.port is None and not goal.sideload and any(goal.items):
            expectations.append(lanes.Expectation(
                last, (goal.items[0], goal.items[-1]), True, f"({goal.x}, {goal.y})", True, r.id, predicted(r),
            ))
    return lanes.check(lane_map, expectations)


# --------------------------------------------------------------------------
# planned rates
# --------------------------------------------------------------------------


def planned_from_request(plan: dict, plan_path: Path) -> tuple[dict[str, float], list[str]]:
    """Crafts per second per recipe from the plan's request's bill of materials, and why not if it has none."""
    from . import request

    ref = plan.get("request")
    if not ref:
        return {}, []
    path = Path(plan_path).resolve().parent / ref
    try:
        spec = request.load(path)
    except (OSError, request.RequestError) as exc:
        return {}, [f"request {ref}: {exc}"]
    review = request.review(spec)
    if review.bill is None:
        return {}, [f"request not ready: {'; '.join(review.problems) or 'no bill of materials'}"]
    planned: dict[str, float] = {}
    for line in review.bill.lines:
        planned[line.recipe] = planned.get(line.recipe, 0.0) + line.rate
    return planned, []


def _plan_rates(built: list[BuiltBlock], wanted: dict[int, float], planned: dict[str, float] | None) -> None:
    """Each block's planned crafts per second, and each port's planned rate to match."""
    capacity: dict[str, float] = {}
    for block in built:
        if block.index not in wanted:
            capacity[block.recipe] = capacity.get(block.recipe, 0.0) + block.crafts_per_second
    for block in built:
        rate = wanted.get(block.index)
        if rate is None and planned:
            if block.recipe in planned:
                share = capacity.get(block.recipe) or 0.0
                rate = planned[block.recipe] * (block.crafts_per_second / share if share else 0.0)
            else:
                block.notes.append("not in the request's bill of materials")
        block.planned_crafts_per_second = rate
        # Per item, in proportion to the recipe: a route that brings one of a
        # port's two ingredients is sized for that one.
        per_craft = {(f.direction, f.item): f.rate for f in layout.solid_flows(block.recipe, block.machine, 1)}
        for port in block.ports:
            if rate is None or not block.crafts_per_second:
                port["planned_rate"] = None
            else:
                port["planned_rate"] = port["rate"] * rate / block.crafts_per_second
            if port["kind"] != "belt":
                continue
            items = sorted({part for lane in port["items"] for part in lane.split("+") if part})
            weights = {i: per_craft.get((port["io"], i), 0.0) for i in items}
            total = sum(weights.values())
            flow = port["rate"] if port["planned_rate"] is None else port["planned_rate"]
            port["item_rates"] = {i: flow * w / total for i, w in weights.items()} if total else {}


def _out_line_heads(placed: list[rows.Placement], ports: list[rows.Port], belt: str | None) -> list[tuple[int, int]]:
    """The first tile of each of a block's output belt lines."""
    belts = {(p.x, p.y): p.direction for p in placed if p.name == belt}
    heads = []
    for port in ports:
        if port.kind != "belt" or port.io != "out":
            continue
        dx, dy = inspection.STEP[port.direction]
        tile = (port.x, port.y)
        while belts.get((tile[0] - dx, tile[1] - dy)) == port.direction:
            tile = (tile[0] - dx, tile[1] - dy)
        heads.append(tile)
    return heads


def build(plan: dict, planned: dict[str, float] | None = None) -> BuildResult:
    """Build a plan. `planned` (recipe -> crafts/s, from `planned_from_request`) sizes routes for what is wanted."""
    from draftsman.blueprintable import Blueprint

    label = plan.get("label") or "factorio-forge layout"
    blueprint = Blueprint()
    blueprint.label = label
    built: list[BuiltBlock] = []
    collected: list[str] = []
    wanted: dict[int, float] = {}
    heads: list[tuple[int, int]] = []
    known_recipes: dict[int, str] = {}  # entity index -> recipe, for block machines that store none

    placements: list[rows.Placement] = []
    for index, block in enumerate(plan.get("blocks") or []):
        kind = block.get("type", "rows")
        if kind != "rows":
            raise PlanError(f"block {index}: unknown type {kind!r}; only 'rows' exists so far")
        known = set(rows.RowBlockSpec.__dataclass_fields__) | {"type", "per_row", "at", "rotate", "planned"}
        unknown = set(block) - known
        if unknown:
            raise PlanError(f"block {index}: unknown field(s) {', '.join(sorted(unknown))}")
        if "planned" in block:
            value = block["planned"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise PlanError(f"block {index}: planned is crafts per second, a number of at least 0")
            wanted[index] = float(value)
        fields = {k: v for k, v in block.items() if k in rows.RowBlockSpec.__dataclass_fields__}
        fields["rows"] = _rows_of(block)
        try:
            spec = rows.RowBlockSpec(**fields)
            expanded = rows.build_block(spec)
            at = tuple(block.get("at") or (0, 0))
            placed, ports, width, height = rows.transform(expanded, at, int(block.get("rotate", 0)))
        except LayoutError as exc:
            raise PlanError(f"block {index} ({block.get('recipe')}): {exc}") from exc
        known_recipes.update(
            (len(placements) + k, spec.recipe) for k, p in enumerate(placed) if p.name == spec.machine and not p.recipe
        )
        placements.extend(placed)
        heads.extend(_out_line_heads(placed, ports, spec.belt))
        built.append(
            BuiltBlock(
                index=index,
                recipe=spec.recipe,
                machine=spec.machine,
                at=at,
                width=width,
                height=height,
                machines=sum(spec.rows),
                rows=[asdict(r) for r in expanded.rows],
                ports=[{**asdict(p), "index": j} for j, p in enumerate(ports)],
                crafts_per_second=crafts_per_second(spec.recipe, spec.machine, spec.speed_bonus) * sum(spec.rows),
                notes=expanded.notes,
                prototypes={"belt": spec.belt, "pipe": spec.pipe, "pipe_to_ground": spec.pipe_to_ground},
            )
        )
    _plan_rates(built, wanted, planned)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for p in placements:
            entity = blueprint.entities.append(p.name, tile_position=(p.x, p.y))
            # Whether an entity turns at all is the entity class's to say.
            if getattr(entity, "rotatable", False):
                entity.direction = p.direction
            if p.recipe:
                entity.recipe = p.recipe
        for extra in plan.get("entities") or []:
            try:
                name = extra["name"]
                x, y = extra["position"]
            except (KeyError, TypeError, ValueError) as exc:
                raise PlanError(f"an entity needs 'name' and 'position': {extra!r}") from exc
            kwargs = {"tile_position": (int(x), int(y))}
            for key in ("direction", "recipe", "io_type", "type"):
                if key in extra:
                    kwargs[key] = extra[key]
            blueprint.entities.append(name, **kwargs)
        routes, route_findings = _route_all(plan, built, blueprint)
        blueprint.generate_power_connections()
    collected.extend(str(w.message) for w in caught)

    entities = list(blueprint.entities)
    report = inspection.inspect(blueprint)
    findings = (list(report.findings) + fluids.check(entities) + route_findings
                + _check_lanes(plan, built, heads, routes, entities, known_recipes))
    for block in built:
        planned_rate = block.planned_crafts_per_second
        if planned_rate is not None and planned_rate > block.crafts_per_second + 1e-9:
            findings.append(inspection.Finding(
                inspection.Severity.SUSPECT, "block-short",
                f"block {block.index} ({block.recipe}) makes at most {block.crafts_per_second:.4g} crafts/s; "
                f"the request needs {planned_rate:.4g}",
                "Add machines or rows, or a faster machine; the shortfall comes out of everything downstream.",
                tuple(block.at),
            ))
    networks = _power_networks(entities)
    if networks > 1:
        findings.append(
            inspection.Finding(
                inspection.Severity.PROBLEM,
                "power-split",
                f"the poles form {networks} separate networks",
                "Only one of them can be connected to power without more poles.",
            )
        )
    return BuildResult(label, blueprint, built, findings, networks, list(dict.fromkeys(collected)), routes)


def load(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PlanError(f"{path} is not valid JSON: {exc}") from exc
