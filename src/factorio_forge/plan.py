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
        {"type": "rows", "id": "battery", "recipe": "battery", "machine": "chemical-plant",
         "rows": 4, "per_row": 12,              (or "rows": [10, 12, 12, 10])
         "belt": "fast-transport-belt", "inserter": "fast-inserter",
         "long_inserter": "long-handed-inserter", "pole": "medium-electric-pole",
         "stack": "mirror", "align": "start", "at": [0, 0], "rotate": 0,
         "planned": 1.5}                        (optional: crafts/s it is meant to run at;
                                                 "id": optional unique name, usable as {"block": "battery"})
      ],
      "entities": [{"name": "fast-transport-belt", "position": [-1, 3], "direction": 4}],
      "inputs": [{"at": [-1, 3], "items": ["iron-plate"]}],
      "sources": [{"id": "iron", "at": [-1, 3], "direction": 4, "items": ["iron-plate"], "rate": 7.5},
                  {"block": 1, "port": 0, "items": ["copper-plate", "iron-plate"], "rate": 3},
                  {"block": 2, "port": 0, "fluid": "water", "rate": 50}],  (a pipe in port: units/s; measuring only)
      "targets": [{"item": "battery", "per_second": 2}],   (optional: what is ordered; sizes the ports)
      "recipe_choices": {"battery": "battery"},            (optional, with targets: as in a request;
                                                            also machine_choices, tiers, effects, surface, boundary)
      "connections": [
        {"id": "out", "from": {"block": 0, "port": 3, "items": ["battery"]},
         "to": [{"at": [60, 2], "direction": 4}, {"at": [60, 12], "direction": 4, "via": [[50, 8]]}]},
        {"id": "more", "from": {"at": [30, -6], "direction": 8, "items": ["battery"]},
         "onto": {"route": "out/1", "lane": "left"}}
      ],
      "routing": {"margin": 3, "reserve": [[44, -4, 46, 0]], "candidates": 12},
      "power": {"link": true, "pole": "medium-electric-pole"}   (optional: join the pole networks)
    }

A block may instead be a library block: {"library": "<name>", "at": [x, y], "rotate": 0,
"mirror": false, "recipe": "<optional swap>", "id": "..."}, a block kept in the active
profile's `library/` and pasted whole (see `insert.py`). Its ports work like a rows block's.

A connection joins an out port (or a point) to an in port (or a point) with
belts or pipes; `route.py` finds the path, after the blocks and hand-placed
entities and in plan order, so each route is an obstacle for the next. Ports
are named by their index in the block's report. Belt and pipe prototypes not
named come from the blocks at either end.

A belt connection may also split (`to` is a list: one splitter per extra
destination, sub-routes `<id>/0`, `<id>/1`, ...), merge (`onto` instead of
`to`: side-loaded onto a lane of an earlier route, the route into an in port,
or a belt at a tile) or join two sources a lane each (`from` is a list of
two). A pipe connection branches the same way (`to` is a list: the first
destination gets the trunk, each other in port joins the pipes laid so far) and
merges (`onto` a route of the same fluid); a pipe never joins another fluid. `inputs` names what enters a hand-placed belt head. `sources` does the
same and more: a source is named (`id`), sits at a belt head (`at` and the
`direction` it flows) or at a block's in port (`block`, `port`), and says the
`items` per lane and the `rate` the player feeds there. After routing,
`lanes.py` traces every belt and the report says what arrives at each in port;
what does not match what the port wants is a finding (`docs/checking.md`).
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path

from draftsman.data import entities as entity_data

from . import fluids, inspection, lanes, layout, power, route, rows
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
    # Every pole network with what it powers, the gaps between separate ones,
    # and the poles a `power.link` added (see power.analyse).
    power: dict = field(default_factory=dict)

    @property
    def problems(self) -> list[inspection.Finding]:
        return [f for f in self.findings if f.severity is inspection.Severity.PROBLEM]

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "entities": len(self.blueprint.entities),
            "power_networks": self.power_networks,
            "power": self.power,
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


_ID_OK = re.compile(r"^\S+$")


def block_ids(plan: dict) -> dict[str, int]:
    """The plan's block ids, each with the index of its block; malformed or repeated ones are an error."""
    ids: dict[str, int] = {}
    blocks = plan.get("blocks") or []
    if not isinstance(blocks, list):
        raise PlanError("blocks must be a list")
    for index, block in enumerate(blocks):
        if not isinstance(block, dict) or "id" not in block:
            continue
        name = block["id"]
        if not isinstance(name, str) or not _ID_OK.match(name) or name.isdigit():
            raise PlanError(f"block {index}: id must be a text without spaces that is not a number, not {name!r}")
        if name in ids:
            raise PlanError(f"block {index}: id {name!r} is already the id of block {ids[name]}")
        ids[name] = index
    return ids


def _by_index(node, ids: dict[str, int], where: str):
    """A copy of a connection or source with every {"block": <id>} turned into the block's index."""
    if isinstance(node, list):
        return [_by_index(item, ids, where) for item in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key == "block" and isinstance(value, str):
            if value not in ids:
                known = ", ".join(sorted(ids)) or "none"
                raise PlanError(f"{where}: block {value!r} is not a block id (the ids are: {known})")
            value = ids[value]
        out[key] = _by_index(value, ids, where) if isinstance(value, (dict, list)) else value
    return out


def resolve_block_ids(plan: dict) -> dict:
    """The plan with blocks named by id in `connections` and `sources` named by index, as the rest of the code reads them."""
    ids = block_ids(plan)
    if not any(isinstance(item, dict) and isinstance(item.get("block"), str)
               for field_ in ("connections", "sources") for item in _flat(plan.get(field_))):
        return plan
    out = dict(plan)
    for field_, label in (("connections", "connection"), ("sources", "source")):
        value = plan.get(field_)
        if isinstance(value, list):
            out[field_] = [_by_index(item, ids, f"{label} {i}") for i, item in enumerate(value)]
    return out


def _flat(value):
    """Every dict inside a nested plan value, itself included."""
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _flat(item)
    elif isinstance(value, list):
        for item in value:
            yield from _flat(item)


def _rows_of(block: dict) -> list[int]:
    value = block.get("rows")
    if isinstance(value, list):
        return [int(n) for n in value]
    if isinstance(value, int) and "per_row" in block:
        return [int(block["per_row"])] * value
    raise PlanError("a rows block needs 'rows' as a list of machine counts, or 'rows' and 'per_row'")


# --------------------------------------------------------------------------
# power between blocks
# --------------------------------------------------------------------------


def _power_options(plan: dict) -> tuple[bool, str | None]:
    """`power`: {"link": true, "pole": name} -- whether to add poles joining the networks, and which."""
    options = plan.get("power")
    if options is None:
        return False, None
    if not isinstance(options, dict):
        raise PlanError("power: must be an object like {\"link\": true, \"pole\": \"medium-electric-pole\"}")
    unknown = set(options) - _POWER_FIELDS
    if unknown:
        raise PlanError(f"power: unknown field(s) {', '.join(sorted(unknown))}")
    link = options.get("link", False)
    if not isinstance(link, bool):
        raise PlanError("power: link is true or false")
    pole = options.get("pole")
    if pole is not None:
        if not isinstance(pole, str):
            raise PlanError("power: pole is a prototype name")
        try:
            power.check_pole(pole)
        except power.PowerError as exc:
            raise PlanError(str(exc)) from exc
    return link, pole


def _link_poles(plan: dict, built: list[BuiltBlock], blueprint, pole: str | None) -> power.Link | None:
    """Add the poles `power.link` asks for, on tiles nothing else wants."""
    entities = list(blueprint.entities)
    name = pole or power.default_pole(power.poles_of(entities))
    if name is None:
        raise PlanError("power: link needs a pole to add; the build has none, so name one with \"pole\"")
    margin, _, reserved, _ = _routing_options(plan)
    keep = set(reserved)
    # The tile in front of each port stays open for the belt or pipe that will join it.
    for block in built:
        for port in block.ports:
            dx, dy = inspection.STEP[port["direction"]]
            sign = 1 if port["io"] == "out" else -1
            keep.add((port["x"] + sign * dx, port["y"] + sign * dy))
    # So does the line through each outside point (a source, or the end of a connection that
    # leaves or enters the build at a tile): the tile itself and the ones before and after it.
    points = list(plan.get("sources") or [])
    for conn in plan.get("connections") or []:
        if isinstance(conn, dict):
            points += [conn.get("from"), conn.get("to")]
    for point in points:
        at = point.get("at") if isinstance(point, dict) else None
        if not (isinstance(at, (list, tuple)) and len(at) == 2 and all(_is_int(v) for v in at)):
            continue
        x, y = int(at[0]), int(at[1])
        keep.add((x, y))
        direction = point.get("direction")
        if _is_int(direction) and direction in inspection.STEP:
            dx, dy = inspection.STEP[direction]
            keep.update({(x + dx, y + dy), (x - dx, y - dy)})
    tiles = [t for e in entities for t in inspection.Layout.tiles_of(e)]
    if not tiles:
        return power.Link(name)
    around = max(margin, power.LINK_MARGIN)
    area = (min(t[0] for t in tiles) - around, min(t[1] for t in tiles) - around,
            max(t[0] for t in tiles) + around, max(t[1] for t in tiles) + around)
    grid = route.grid_of(entities, area, keep)
    try:
        return power.link(blueprint, grid, name)
    except power.PowerError as exc:
        raise PlanError(str(exc)) from exc


def _power_findings(report: dict, link: power.Link | None, wanted: bool) -> list[inspection.Finding]:
    """`power-split` when the poles are still in several networks, with where they fall short."""
    networks = report["networks"]
    if len(networks) < 2:
        return []

    def said(g: dict) -> str:
        a, b = g["from"], g["to"]
        short = f", {g['short_by']:g} too far" if g["short_by"] else ", within reach"
        return (f"networks {g['networks'][0]} and {g['networks'][1]}: poles at ({a['x']:g}, {a['y']:g}) and "
                f"({b['x']:g}, {b['y']:g}) are {g['distance']:g} tiles apart, wire reach {g['reach']:g}{short}")

    detail = "; ".join(said(g) for g in report["gaps"])
    if not wanted:
        detail += ". Add \"power\": {\"link\": true} to the plan to have poles added between them."
    elif link is not None and link.failures:
        detail += ". " + " ".join(link.failures)
    return [inspection.Finding(
        inspection.Severity.PROBLEM, "power-split", f"the poles form {len(networks)} separate networks", detail,
    )]


def _link_failures(link: power.Link, report: dict) -> None:
    """Networks still apart with no failure recorded: the poles are in reach but the wiring left them apart."""
    if len(report["networks"]) > 1 and not link.failures:
        link.failures.append(
            "The poles stand within wire reach all the way, yet the blueprint's wiring left them apart "
            "(a pole holds at most five wires); move a pole or add one by hand."
        )


# --------------------------------------------------------------------------
# connections
# --------------------------------------------------------------------------

_CONNECTION_FIELDS = {"id", "kind", "from", "to", "onto", "split", "splitter", "belt", "underground", "pipe",
                      "pipe_to_ground", "via", "turn_cost", "hop_cost"}
_PORT_FIELDS = {"block", "port", "items"}
_POINT_FIELDS = {"at", "direction", "items", "rate"}
_ONTO_FIELDS = {"route", "block", "port", "at", "lane"}
_ROUTING_FIELDS = {"margin", "max_nodes", "reserve", "candidates"}
_POWER_FIELDS = {"link", "pole"}

Spec = route.RouteSpec | route.SplitSpec | route.MergeSpec | route.JoinSpec | route.TrunkSpec | route.TapSpec


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


def _pipe_prototypes(conn: dict, protos: list[dict], points: tuple[route.Endpoint, ...]) -> tuple[str, str | None]:
    """The pipe and pipe-to-ground a connection routes with: named, else from the blocks at its ends."""
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
    for point in points:
        if point.pipe_name and not _categories(point.pipe_name) & _categories(surface):
            raise PlanError(f"{surface} shares no connection category with the port's {point.pipe_name}")
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
        if "/" in ident:
            raise PlanError(f"connection {index}: id {ident!r} has a '/'; those name a split's or join's branches")
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
        for name in dict.fromkeys([ident, *subs]):
            if name in survey.index_of or name in survey.resolved:
                raise PlanError(f"connection {index}: id {ident!r} is already used by connection "
                                f"{survey.index_of[survey.resolved[name]]}")
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
        surface, underground = _pipe_prototypes(conn, protos, (start, goal))

    return route.RouteSpec(ident, kind, start, goal, surface, underground, via, **costs)


_LANE_JOIN_IS_FOR_BELTS = "a lane join is for belts; a second pipe source of the same fluid joins with 'onto'"


def _resolve_trunk(conn: dict, ident: str, source, goals, kinds, protos, vias) -> route.TrunkSpec:
    """One pipe source to several destinations of one fluid (`to` is a list)."""
    for field_ in ("split", "splitter"):
        if field_ in conn:
            raise PlanError(f"'{field_}' belongs to a belt split; pipes branch on their own")
    kind = _kind_of(conn, kinds)
    source = _normalise(source, kind)
    goals = [_normalise(g, kind) for g in goals]
    fluid = next((p.items[0] for p in [source, *goals] if p.items), None)
    for p in [source, *goals]:
        if p.items and p.items[0] != fluid:
            raise PlanError(f"it would join {fluid} to {p.items[0]}")
    for k, g in enumerate(goals):
        if k and g.port is None:
            raise PlanError("a branch ends at a block's in port: only the first destination of a pipe may be a point")
    surface, underground = _pipe_prototypes(conn, protos, (source, *goals))
    return route.TrunkSpec(ident, "pipe", source, tuple(goals), tuple(vias), surface, underground, **_costs(conn))


def _resolve_tap(conn: dict, ident: str, index: int, survey: "_Routes", source, source_protos,
                 onto: dict) -> route.TapSpec:
    """A pipe source that joins a pipe route laid earlier (`onto` names the route, or the in port it ends at)."""
    for field_ in ("split", "splitter"):
        if field_ in conn:
            raise PlanError(f"'{field_}' belongs to a belt split; pipes branch on their own")
    if "lane" in onto or "at" in onto:
        raise PlanError("a pipe joins a route, not a tile or a lane: 'onto' is {\"route\": id} or an in port")
    named = [k for k in ("route",) if k in onto] + (["port"] if "block" in onto or "port" in onto else [])
    if len(named) != 1:
        raise PlanError("'onto' names exactly one of a route or an in port")
    if "route" in onto:
        name = onto["route"]
        if not isinstance(name, str) or name not in survey.resolved:
            raise PlanError(f"'onto' names route {name!r}, which no connection makes")
        onto_route = survey.resolved[name]
    else:
        b, j = onto.get("block"), onto.get("port")
        if (b, j) not in survey.ending_at:
            raise PlanError(f"no connection ends at block {b!r} port {j!r}, so there is no route to join")
        onto_route = survey.ending_at[(b, j)]
    at = survey.index_of[onto_route]
    if at == index:
        raise PlanError("a connection cannot join itself")
    if at > index:
        raise PlanError(f"connection {index} joins route {onto_route}, which is routed later; "
                        f"list it after {onto_route}")
    kind = _kind_of({**conn, "kind": "pipe"}, {"pipe"})
    source = _normalise(source, kind)
    surface, underground = _pipe_prototypes(conn, [source_protos] if source_protos else [], (source,))
    return route.TapSpec(ident, "pipe", source, onto_route, surface, underground, _vias(conn.get("via")),
                         **_costs(conn))


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
        return _resolve_trunk(conn, ident, source, goals, kinds, protos, vias)
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


def _resolve_merge(conn: dict, ident: str, index: int, built: list[BuiltBlock], survey: _Routes) -> Spec:
    source, source_kind, source_protos = _endpoint(conn["from"], "from", built)
    onto = conn["onto"]
    if not isinstance(onto, dict):
        raise PlanError("'onto' is {\"route\": id}, {\"block\": b, \"port\": j} or {\"at\": [x, y]}, with 'lane'")
    unknown = set(onto) - _ONTO_FIELDS
    if unknown:
        raise PlanError(f"'onto' has unknown field(s) {', '.join(sorted(unknown))}")
    if source_kind == "pipe" or conn.get("kind") == "pipe":
        return _resolve_tap(conn, ident, index, survey, source, source_protos, onto)
    for field_ in ("split", "splitter"):
        if field_ in conn:
            raise PlanError(f"'{field_}' belongs to a split, whose 'to' is a list")
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
        raise PlanError(_LANE_JOIN_IS_FOR_BELTS)
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
    if isinstance(spec, route.TrunkSpec):
        return [(spec.source, "from")] + [(g, "to") for g in spec.goals]
    if isinstance(spec, (route.MergeSpec, route.TapSpec)):
        return [(spec.source, "from")]
    return [(s, "from") for s in spec.sources] + [(spec.goal, "to")]


def _tiles_of(spec: Spec) -> list[tuple[int, int]]:
    """Tiles the search area must cover for this connection."""
    if isinstance(spec, route.RouteSpec):
        return [spec.start_tile, spec.last_tile, spec.goal_tile, *spec.via]
    points = []
    if isinstance(spec, route.TrunkSpec):
        points = [spec.source, *spec.goals]
        extra = [t for vias in spec.vias for t in vias]
    elif isinstance(spec, route.TapSpec):
        points = [spec.source]
        extra = list(spec.via)
    elif isinstance(spec, route.SplitSpec):
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


def _route_all(plan: dict, built: list[BuiltBlock], blueprint, cache=None) -> tuple[list[route.RouteResult], list]:
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
    if cache is not None:
        grid = cache.watched(grid)
    results: list[route.RouteResult] = []
    found: list = []
    by_id: dict[str, route.RouteResult] = {}
    for index, spec in enumerate(specs):
        target = None
        if isinstance(spec, route.TapSpec):
            target = by_id.get(spec.onto_route)
        elif isinstance(spec, route.MergeSpec):
            target = by_id.get(spec.onto_route) if spec.onto_route else next(
                (r for r in results if r.ok and any((p.x, p.y) == spec.onto_tile for p in r.pieces)), None)

        def run(spec=spec, target=target) -> list[route.RouteResult]:
            if isinstance(spec, route.RouteSpec):
                done = [route.route(spec, grid, max_nodes)]
                done[0].connection = spec.id
                if done[0].ok:
                    grid.add(done[0].pieces, route.pipe_fluid(spec))
                return done
            if isinstance(spec, route.TrunkSpec):
                return route.route_trunk(spec, grid, max_nodes, candidates)
            if isinstance(spec, route.TapSpec):
                if not (target and target.ok and target.kind == "pipe"):
                    point = (spec.source.x, spec.source.y)
                    stand_in = route.RouteSpec(spec.id, "pipe", spec.source,
                                               route.Endpoint(*point, spec.source.direction),
                                               spec.surface, spec.underground)
                    return [route._failed(spec.id, stand_in, spec.id,
                                          f"route {spec.onto_route}, which it joins, was not built as a pipe", point)]
                return [route.route_tap(spec, target, grid, max_nodes, candidates)]
            if isinstance(spec, route.SplitSpec):
                return route.route_split(spec, grid, max_nodes, candidates)
            if isinstance(spec, route.MergeSpec):
                if spec.onto_route and not (target and target.ok):
                    point = (spec.source.x, spec.source.y)
                    stand_in = route.RouteSpec(spec.id, "belt", spec.source, route.Endpoint(*point, spec.source.direction),
                                               spec.surface, spec.underground)
                    return [route._failed(spec.id, stand_in, spec.id,
                                          f"route {spec.onto_route}, which it merges onto, was not built", point)]
                return [route.route_merge(spec, target, grid, max_nodes, candidates)]
            return route.route_join(spec, grid, max_nodes, candidates)

        try:
            done = run() if cache is None else cache.stage(spec, (max_nodes, candidates), grid, target, run)
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


_SOURCE_FIELDS = {"id", "at", "direction", "block", "port", "items", "rate", "fluid"}


def _sources(plan: dict, built: list[BuiltBlock]) -> list[tuple[str, lanes.Feed, int | None]]:
    """The plan's outside supply: (name, feed, direction the belt must flow or None), validated like connections are."""
    raw = plan.get("sources") or []
    if not isinstance(raw, list):
        raise PlanError("sources is a list of {\"at\": [x, y], \"direction\": d, \"items\": [...], \"rate\": n} "
                        "or {\"block\": b, \"port\": p, \"items\": [...], \"rate\": n}")
    out: list[tuple[str, lanes.Feed, int | None]] = []
    names: set[str] = set()
    tiles: dict[tuple[int, int], str] = {}
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise PlanError(f"source {index}: expected an object, not {entry!r}")
        unknown = set(entry) - _SOURCE_FIELDS
        if unknown:
            raise PlanError(f"source {index}: unknown field(s) {', '.join(sorted(unknown))}")
        name = entry.get("id", f"source {index}")
        if not isinstance(name, str) or not name:
            raise PlanError(f"source {index}: id is a non-empty text")
        if name in names:
            raise PlanError(f"source {index}: id {name!r} is used twice")
        names.add(name)
        label = f"source {name!r}" if "id" in entry else name
        if "fluid" in entry:
            continue  # a pipe supply: _fluid_sources reads it
        direction = entry.get("direction")
        if direction is not None and (not _is_int(direction) or direction not in inspection.STEP):
            raise PlanError(f"{label}: direction must be 0, 4, 8 or 12, not {direction!r}")
        if "block" in entry or "port" in entry:
            if "at" in entry:
                raise PlanError(f"{label}: a source is at a tile ('at') or at a block's in port, not both")
            b, j = entry.get("block"), entry.get("port")
            if not (_is_int(b) and 0 <= b < len(built)):
                raise PlanError(f"{label}: block {b!r} does not exist; the plan has {len(built)} block(s)")
            ports = built[b].ports
            if not (_is_int(j) and 0 <= j < len(ports)):
                raise PlanError(f"{label}: block {b} has {len(ports)} port(s), not a port {j!r}")
            port = ports[j]
            if port["kind"] != "belt" or port["io"] != "in":
                raise PlanError(f"{label}: block {b} port {j} is a {port['io']!r} {port['kind']} port; "
                                "a source feeds an 'in' belt port")
            if direction is not None and direction != port["direction"]:
                raise PlanError(f"{label}: block {b} port {j} flows {port['direction']}, not {direction}")
            tile, direction = (port["x"], port["y"]), port["direction"]
        else:
            if direction is None:
                raise PlanError(f"{label}: a source at a tile needs the 'direction' its belt flows")
            tile = _tile(entry.get("at"), f"{label} at")
        items = entry.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 2 or not all(isinstance(i, str) for i in items):
            raise PlanError(f"{label}: items are [left, right], or one item for both lanes")
        rate = entry.get("rate")
        if rate is not None and (isinstance(rate, bool) or not isinstance(rate, (int, float))
                             or not math.isfinite(rate) or rate < 0):
            raise PlanError(f"{label}: rate is items per second, a number of at least 0")
        if tile in tiles:
            raise PlanError(f"{label}: {list(tile)} is already the source of {tiles[tile]}")
        tiles[tile] = label
        out.append((label, lanes.Feed(tile, (items[0], items[-1]), None if rate is None else float(rate)),
                    direction))
    return out


def _fluid_sources(plan: dict, built: list[BuiltBlock]) -> list[dict]:
    """The plan's fluid supply: ``{"id", "at": [x, y], "fluid", "rate"}`` at the tile of a block's in pipe port.

    A source with a ``fluid`` names the block and port it feeds, like a belt source does; there is no belt
    to lay, the supply is the pipe the port ends in. ``rate`` is units per second.
    """
    raw = plan.get("sources") or []
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict) or "fluid" not in entry:
            continue
        name = entry.get("id", f"source {index}")
        label = f"source {name!r}"
        fluid = entry.get("fluid")
        if not isinstance(fluid, str) or not fluid:
            raise PlanError(f"{label}: fluid is the name of a fluid")
        extra = set(entry) - {"id", "block", "port", "fluid", "rate"}
        if extra:
            raise PlanError(f"{label}: a fluid source has only block, port, fluid and rate, not {', '.join(sorted(extra))}")
        b, j = entry.get("block"), entry.get("port")
        if not (_is_int(b) and 0 <= b < len(built)):
            raise PlanError(f"{label}: block {b!r} does not exist; the plan has {len(built)} block(s)")
        ports = built[b].ports
        if not (_is_int(j) and 0 <= j < len(ports)):
            raise PlanError(f"{label}: block {b} has {len(ports)} port(s), not a port {j!r}")
        port = ports[j]
        if port["kind"] != "pipe" or port["io"] != "in":
            raise PlanError(f"{label}: block {b} port {j} is a {port['io']!r} {port['kind']} port; "
                            "a fluid source feeds an 'in' pipe port")
        rate = entry.get("rate")
        if rate is not None and (isinstance(rate, bool) or not isinstance(rate, (int, float))
                                 or not math.isfinite(rate) or rate < 0):
            raise PlanError(f"{label}: rate is units per second, a number of at least 0")
        item: dict = {"id": name, "at": [port["x"], port["y"]], "fluid": fluid}
        if rate is not None:
            item["rate"] = float(rate)
        out.append(item)
    return out


def _check_lanes(plan: dict, built: list[BuiltBlock], heads: list[tuple[int, int]], routes: list[route.RouteResult],
                 entities: list, known_recipes: dict[int, str], loose: set[int]) -> list[inspection.Finding]:
    """Trace every belt lane of the build, fill in what the report says arrives, and check it."""
    inputs = _inputs(plan)
    sources = _sources(plan, built)
    declared_tiles = {f.tile for f in inputs} | {f.tile for _, f, _ in sources}
    if len(declared_tiles) < len(inputs) + len(sources):
        clash = next(t for t in declared_tiles if sum(f.tile == t for f in inputs)
                     + sum(f.tile == t for _, f, _ in sources) > 1)
        raise PlanError(f"{list(clash)} is named by both 'inputs' and 'sources', or twice; name a belt head once")
    feeds = list(inputs) + [f for _, f, _ in sources]
    point_starts = {r.spec.start_tile: r.id for r in routes
                    if r.ok and r.spec is not None and r.spec.start.port is None
                    and not (r.junction is not None and r.junction.kind == "splitter")}
    for label, feed, _ in [(f"input at {list(f.tile)}", f, None) for f in inputs] + sources:
        if feed.tile in point_starts:
            raise PlanError(f"{label} is where connection {point_starts[feed.tile]} starts; "
                            "give the items and rate on the connection's 'from'")
    for r in routes:
        if not r.ok or r.spec is None or r.spec.start.port is not None:
            continue
        if r.junction is not None and r.junction.kind == "splitter":
            continue  # a branch starts at its splitter, inside the plan
        items = r.spec.start.items
        feeds.append(lanes.Feed(r.spec.start_tile, (items[0], items[1]) if len(items) == 2 else None,
                               r.spec.start.rate))
    # Nothing enters a block's output line from behind: what it carries, its inserters put there.
    feeds.extend(lanes.Feed(tile, ("", "")) for tile in heads)
    in_ports = [(p["x"], p["y"]) for b in built for p in b.ports if p["kind"] == "belt" and p["io"] == "in"]
    lane_map = lanes.trace(entities, feeds, in_ports, known_recipes, loose)

    named = [(f"input at {list(f.tile)}", f, None, "an input") for f in inputs]
    named += [(f"{label} at {list(f.tile)}", f, d, "a source") for label, f, d in sources]
    ending = {r.spec.goal.port: r for r in routes if r.ok and r.spec and r.spec.goal.port is not None}
    for label, feed, _, _ in named:
        for block in built:
            for port in block.ports:
                if (port["x"], port["y"]) == feed.tile and (block.index, port["index"]) in ending:
                    raise PlanError(f"{label} is where connection {ending[(block.index, port['index'])].id} "
                                    "ends; a source feeds a port directly, a connection brings it")

    for label, feed, direction, noun in named:
        if feed.tile not in lane_map.nodes:
            raise PlanError(f"{label}: no belt stands there")
        if direction is not None and lane_map.nodes[feed.tile].direction != direction:
            raise PlanError(f"{label}: the belt there flows {lane_map.nodes[feed.tile].direction}, "
                            f"the source says {direction}")
        if lane_map.behind.get(feed.tile):
            source = lane_map.behind[feed.tile][0]
            name = entities[lane_map.nodes[source].entity].name
            raise PlanError(f"{label} is fed from behind by {name} at {list(source)}; "
                            f"{noun} names what enters a belt head")
        if lane_map.sides.get(feed.tile):
            source = min(lane_map.sides[feed.tile].values())
            name = entities[lane_map.nodes[source].entity].name
            raise PlanError(f"{label} is fed from its side by {name} at {list(source)}; "
                            f"{noun} names what enters a belt head")

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
            ordered_rate = port.get("planned_rate")
            expectations.append(lanes.Expectation(
                (port["x"], port["y"]), (items[0], items[-1]), False, f"block {block.index} port {port['index']}",
                r is not None, r.id if r else None, predicted(r),
                port["rate"] if ordered_rate is None else ordered_rate, ordered_rate is not None,
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
    return lanes.check(lane_map, expectations, declared=bool(routes or sources))


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
    return _planned_from_spec(spec)


# What a plan may say next to its `targets`, read as in a request file: without
# `recipe_choices` a target made by several recipes is planned with whichever
# the bill picks first, and the blocks of the recipe actually built get no share.
ORDER_FIELDS = ("boundary", "tiers", "machine_choices", "recipe_choices", "effects", "surface")


def planned_from_targets(plan: dict) -> tuple[dict[str, float], list[str]]:
    """The same from the plan's own `targets` (what is ordered), for a plan with no request file."""
    from . import request

    raw = plan.get("targets")
    if not raw:
        return {}, []
    if not isinstance(raw, list) or not all(isinstance(t, dict) for t in raw):
        raise PlanError("targets is a list of {\"item\": name, \"per_second\": n} (or \"per_minute\")")
    for key in ORDER_FIELDS:
        value = plan.get(key)
        if not value:
            continue
        if key == "boundary":
            ok, shape = isinstance(value, list) and all(isinstance(x, str) for x in value), "a list of names"
        elif key == "surface":
            ok, shape = isinstance(value, str), "a name"
        else:
            ok, shape = isinstance(value, dict), "an object"
        if not ok:
            raise PlanError(f"{key} must be {shape}, not {value!r}")
    try:
        spec = request.parse({"targets": raw, **{k: plan[k] for k in ORDER_FIELDS if plan.get(k)}})
    except request.RequestError as exc:
        raise PlanError(f"targets: {exc}") from exc
    return _planned_from_spec(spec)


def _planned_from_spec(spec) -> tuple[dict[str, float], list[str]]:
    from . import request

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
    remaining = dict(planned or {})
    for block in built:
        if block.index not in wanted:
            capacity[block.recipe] = capacity.get(block.recipe, 0.0) + block.crafts_per_second
        elif block.recipe in remaining:
            # A block's own 'planned' is its share; the rest of the recipe goes to the others.
            remaining[block.recipe] = max(0.0, remaining[block.recipe] - wanted[block.index])
    for block in built:
        rate = wanted.get(block.index)
        if rate is None and planned:
            if block.recipe in planned:
                share = capacity.get(block.recipe) or 0.0
                rate = remaining[block.recipe] * (block.crafts_per_second / share if share else 0.0)
            else:
                block.notes.append("not in the request's bill of materials")
        block.planned_crafts_per_second = rate
        # Per item, in proportion to the recipe: a route that brings one of a
        # port's two ingredients is sized for that one.
        # a library block may not say its recipe; a rows block always does
        per_craft = ({(f.direction, f.item): f.rate for f in layout.solid_flows(block.recipe, block.machine, 1)}
                     if block.recipe and block.machine else {})
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


_LIBRARY_FIELDS = {"type", "library", "at", "rotate", "mirror", "recipe", "planned", "id"}
_BLOCK_FIELDS = set(rows.RowBlockSpec.__dataclass_fields__) | {"type", "per_row", "at", "rotate", "planned", "id"}


def _expand(block: dict):
    """What one block's own definition expands to: its spec, rows, placed entities, ports and size."""
    fields = {k: v for k, v in block.items() if k in rows.RowBlockSpec.__dataclass_fields__}
    fields["rows"] = _rows_of(block)
    spec = rows.RowBlockSpec(**fields)
    expanded = rows.build_block(spec)
    at = tuple(block.get("at") or (0, 0))
    placed, ports, width, height = rows.transform(expanded, at, int(block.get("rotate", 0)))
    return spec, expanded, placed, ports, width, height


def _expansion_key(block: dict) -> str:
    """The text a block's expansion is cached under: everything of its own definition that shapes it."""
    return json.dumps({k: v for k, v in block.items() if k not in ("id", "planned")}, sort_keys=True,
                      ensure_ascii=False, default=repr)


def build(plan: dict, planned: dict[str, float] | None = None, cache=None) -> BuildResult:
    """Build a plan. `planned` (recipe -> crafts/s, from `planned_from_request`) sizes routes for what is wanted.

    `cache` (an `edit.BuildCache`) lets a rebuild reuse the blocks and routes whose inputs did not change;
    the result is the same as without it.
    """
    from draftsman.blueprintable import Blueprint

    from . import insert

    plan = resolve_block_ids(plan)

    label = plan.get("label") or "factorio-forge layout"
    blueprint = Blueprint()
    blueprint.label = label
    built: list[BuiltBlock] = []
    collected: list[str] = []
    if plan.get("targets") and plan.get("request"):
        raise PlanError("a plan names its order once: 'request' or 'targets', not both")
    stray = [k for k in ORDER_FIELDS if plan.get(k)]
    if stray and not plan.get("targets"):
        raise PlanError(f"{', '.join(stray)} goes with 'targets'; with 'request', put it in the request file")
    if planned is None and plan.get("targets"):
        planned, said = planned_from_targets(plan)
        collected.extend(said)
    wanted: dict[int, float] = {}
    heads: list[tuple[int, int]] = []
    known_recipes: dict[int, str] = {}  # entity index -> recipe, for block machines that store none

    power_link, power_pole = _power_options(plan)
    placements: list[rows.Placement] = []
    owner: list[int] = []  # the block each placed entity belongs to
    prebuilt: dict[int, object] = {}  # placement index -> a ready entity (library blocks bring their own)
    for index, block in enumerate(plan.get("blocks") or []):
        kind = block.get("type") or ("library" if "library" in block else "rows")
        if kind not in ("rows", "library"):
            raise PlanError(f"block {index}: unknown type {kind!r}; 'rows' and 'library' exist")
        if kind == "library":
            unknown = set(block) - _LIBRARY_FIELDS
            if unknown or "library" not in block:
                raise PlanError(f"block {index}: a library block takes library, at, rotate, mirror, recipe, planned, id"
                                + (f"; not {', '.join(sorted(unknown))}" if unknown else ""))
            if "planned" in block:
                value = block["planned"]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                    raise PlanError(f"block {index}: planned is crafts per second, a number of at least 0")
                wanted[index] = float(value)
            pasted = insert.paste_block(block, index)
            for k, obj in enumerate(pasted.objects):
                prebuilt[len(placements) + k] = obj
            placements.extend(pasted.placed)
            owner.extend([index] * len(pasted.placed))
            heads.extend(_out_line_heads(pasted.placed, pasted.ports, pasted.prototypes.get("belt")))
            try:
                per_second = (crafts_per_second(pasted.recipe, pasted.machine) * pasted.machines
                              if pasted.recipe and pasted.machine else 0.0)
            except LayoutError:
                per_second = 0.0
            built.append(BuiltBlock(
                index=index, recipe=pasted.recipe, machine=pasted.machine, at=tuple(block.get("at") or (0, 0)),
                width=pasted.width, height=pasted.height, machines=pasted.machines, rows=[],
                ports=[{**asdict(p), "index": j} for j, p in enumerate(pasted.ports)],
                crafts_per_second=per_second, notes=list(pasted.notes), prototypes=dict(pasted.prototypes),
            ))
            continue
        unknown = set(block) - _BLOCK_FIELDS
        if unknown:
            raise PlanError(f"block {index}: unknown field(s) {', '.join(sorted(unknown))}")
        if "planned" in block:
            value = block["planned"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise PlanError(f"block {index}: planned is crafts per second, a number of at least 0")
            wanted[index] = float(value)
        try:
            if cache is None:
                expansion = _expand(block)
            else:
                expansion = cache.block(_expansion_key(block), lambda: _expand(block))
            spec, expanded, placed, ports, width, height = expansion
            at = tuple(block.get("at") or (0, 0))
        except LayoutError as exc:
            raise PlanError(f"block {index} ({block.get('recipe')}): {exc}") from exc
        known_recipes.update(
            (len(placements) + k, spec.recipe) for k, p in enumerate(placed) if p.name == spec.machine and not p.recipe
        )
        placements.extend(placed)
        owner.extend([index] * len(placed))
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
                notes=list(expanded.notes),
                prototypes={"belt": spec.belt, "pipe": spec.pipe, "pipe_to_ground": spec.pipe_to_ground},
            )
        )
    _plan_rates(built, wanted, planned)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for i, p in enumerate(placements):
            if i in prebuilt:
                blueprint.entities.append(prebuilt[i])
                continue
            entity = blueprint.entities.append(p.name, tile_position=(p.x, p.y))
            # Whether an entity turns at all is the entity class's to say.
            if getattr(entity, "rotatable", False):
                entity.direction = p.direction
            if p.recipe:
                entity.recipe = p.recipe
        # The plan's own entities come right after the blocks', before any route piece.
        loose = set(range(len(placements), len(placements) + len(plan.get("entities") or [])))
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
        routes, route_findings = _route_all(plan, built, blueprint, cache)
        linked = _link_poles(plan, built, blueprint, power_pole) if power_link else None
        blueprint.generate_power_connections()
    collected.extend(str(w.message) for w in caught)

    entities = list(blueprint.entities)
    report = inspection.inspect(blueprint)
    lane_findings = _check_lanes(plan, built, heads, routes, entities, known_recipes, loose)
    # the lane check knows the declared supply; its verdict on an inserter replaces the blind one
    judged = {f.position for f in lane_findings if f.code == "inserter-backwards"}
    findings = ([f for f in report.findings if not (f.code == "inserter-backwards" and f.position in judged)]
                + fluids.check(entities) + route_findings + lane_findings)
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
    report = power.analyse(blueprint, owner, routes, linked)
    if linked is not None:
        _link_failures(linked, report)
        report["added"] = [{"name": p.name, "x": p.x, "y": p.y} for p in linked.added]
        report["link"] = {"pole": linked.pole, "failures": linked.failures}
    findings.extend(_power_findings(report, linked, power_link))
    networks = len(report["networks"])
    return BuildResult(label, blueprint, built, findings, networks, list(dict.fromkeys(collected)), routes, report)


def load(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise PlanError(f"{path} is not valid JSON: {exc}") from exc
