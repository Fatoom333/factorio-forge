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
      "blocks": [
        {"type": "rows", "recipe": "battery", "machine": "chemical-plant",
         "rows": 4, "per_row": 12,              (or "rows": [10, 12, 12, 10])
         "belt": "fast-transport-belt", "inserter": "fast-inserter",
         "long_inserter": "long-handed-inserter", "pole": "medium-electric-pole",
         "stack": "mirror", "align": "start", "at": [0, 0], "rotate": 0}
      ],
      "entities": [{"name": "fast-transport-belt", "position": [-1, 3], "direction": 4}],
      "connections": [
        {"id": "out", "from": {"block": 0, "port": 3, "items": ["battery"]},
         "to": {"at": [60, 2], "direction": 4}, "via": [[50, 2]]}
      ],
      "routing": {"margin": 3, "reserve": [[44, -4, 46, 0]]}
    }

A connection joins an out port (or a point) to an in port (or a point) with
belts or pipes; `route.py` finds the path, after the blocks and hand-placed
entities and in plan order, so each route is an obstacle for the next. Ports
are named by their index in the block's report. Belt and pipe prototypes not
named come from the blocks at either end.
"""

from __future__ import annotations

import json
import math
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path

from draftsman.data import entities as entity_data

from . import fluids, inspection, layout, route, rows
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

_CONNECTION_FIELDS = {"id", "kind", "from", "to", "belt", "underground", "pipe", "pipe_to_ground",
                      "via", "turn_cost", "hop_cost"}
_PORT_FIELDS = {"block", "port", "items"}
_POINT_FIELDS = {"at", "direction", "items", "rate"}
_ROUTING_FIELDS = {"margin", "max_nodes", "reserve"}


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
        point = route.Endpoint(port["x"], port["y"], port["direction"], tuple(port["items"]),
                               float(port["rate"]), (b, j), pipe)
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


def _route_spec(conn: dict, index: int, built: list[BuiltBlock]) -> route.RouteSpec:
    """One plan connection, resolved against the built blocks into what the router needs."""
    ident = conn.get("id") if isinstance(conn, dict) and isinstance(conn.get("id"), str) else f"c{index}"
    try:
        return _resolve_connection(conn, ident, built)
    except (PlanError, route.RouteError, LayoutError) as exc:
        raise PlanError(f"connection {index} ({ident}): {exc}") from exc


def _resolve_connection(conn, ident: str, built: list[BuiltBlock]) -> route.RouteSpec:
    if not isinstance(conn, dict):
        raise PlanError("a connection is an object with 'from' and 'to'")
    unknown = set(conn) - _CONNECTION_FIELDS
    if unknown:
        raise PlanError(f"unknown field(s) {', '.join(sorted(unknown))}")
    if "from" not in conn or "to" not in conn:
        raise PlanError("a connection needs 'from' and 'to'")
    start, start_kind, start_protos = _endpoint(conn["from"], "from", built)
    goal, goal_kind, goal_protos = _endpoint(conn["to"], "to", built)

    kinds = {k for k in (start_kind, goal_kind) if k}
    if len(kinds) > 1:
        raise PlanError("one end is a belt port and the other a pipe port")
    stated = conn.get("kind")
    if stated is not None and stated not in ("belt", "pipe"):
        raise PlanError(f"kind is 'belt' or 'pipe', not {stated!r}")
    if kinds and stated and stated not in kinds:
        raise PlanError(f"kind {stated!r} does not match the {next(iter(kinds))} port")
    kind = stated or (next(iter(kinds)) if kinds else None)
    if kind is None:
        raise PlanError("neither end is a port, so 'kind' must say 'belt' or 'pipe'")

    wrong = ({"pipe", "pipe_to_ground"} if kind == "belt" else {"belt", "underground"}) & set(conn)
    if wrong:
        raise PlanError(f"a {kind} connection does not take {', '.join(sorted(wrong))}")

    def normalise(point: route.Endpoint) -> route.Endpoint:
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

    start, goal = normalise(start), normalise(goal)
    if kind == "pipe" and start.items and goal.items and start.items[0] != goal.items[0]:
        raise PlanError(f"it would join {start.items[0]} to {goal.items[0]}")

    via = tuple(_tile(v, "a via tile") for v in (conn.get("via") or []))
    costs = {}
    for key in ("turn_cost", "hop_cost"):
        if key in conn:
            value = conn[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise PlanError(f"{key} must be a number of at least 0")
            costs[key] = float(value)

    protos = [p for p in (start_protos, goal_protos) if p]
    if kind == "belt":
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


def _routing_options(plan: dict) -> tuple[int, int, set]:
    options = plan.get("routing") or {}
    if not isinstance(options, dict):
        raise PlanError("routing: must be an object")
    unknown = set(options) - _ROUTING_FIELDS
    if unknown:
        raise PlanError(f"routing: unknown field(s) {', '.join(sorted(unknown))}")
    margin = options.get("margin", route.DEFAULT_MARGIN)
    max_nodes = options.get("max_nodes", route.DEFAULT_MAX_NODES)
    if not _is_int(margin) or margin < 0:
        raise PlanError("routing: margin must be a whole number of at least 0")
    if not _is_int(max_nodes) or max_nodes < 1:
        raise PlanError("routing: max_nodes must be a whole number of at least 1")
    reserved: set[tuple[int, int]] = set()
    for rect in options.get("reserve") or []:
        if not (isinstance(rect, (list, tuple)) and len(rect) == 4 and all(_is_int(v) for v in rect)):
            raise PlanError(f"routing: reserve takes [x0, y0, x1, y1] rectangles, not {rect!r}")
        x0, y0, x1, y1 = rect
        reserved.update(
            (x, y) for x in range(min(x0, x1), max(x0, x1) + 1) for y in range(min(y0, y1), max(y0, y1) + 1)
        )
    return margin, max_nodes, reserved


def _route_all(plan: dict, built: list[BuiltBlock], blueprint) -> tuple[list[route.RouteResult], list]:
    """Resolve every connection first, then route them in plan order, placing what succeeds."""
    connections = plan.get("connections") or []
    if not isinstance(connections, list):
        raise PlanError("connections must be a list")
    specs = [_route_spec(conn, index, built) for index, conn in enumerate(connections)]
    used: dict[tuple[int, int], int] = {}
    for index, spec in enumerate(specs):
        for point in (spec.start, spec.goal):
            if point.port is None:
                continue
            if point.port in used:
                raise PlanError(
                    f"connection {index} ({spec.id}): block {point.port[0]} port {point.port[1]} is already "
                    f"used by connection {used[point.port]}; merging and splitting are out of scope"
                )
            used[point.port] = index
    margin, max_nodes, reserved = _routing_options(plan)
    if not specs:
        return [], []

    entities = list(blueprint.entities)
    endpoints = [t for s in specs for t in (s.start_tile, s.last_tile, s.goal_tile, *s.via)]
    grid = route.grid_of(entities, route.search_area(entities, endpoints, margin), reserved)
    results, found = [], []
    for spec in specs:
        result = route.route(spec, grid, max_nodes)
        if result.ok:
            for piece in result.pieces:
                kwargs = {"tile_position": (piece.x, piece.y)}
                if piece.io_type:
                    kwargs["io_type"] = piece.io_type
                entity = blueprint.entities.append(piece.name, **kwargs)
                # Whether an entity turns at all is the entity class's to say.
                if getattr(entity, "rotatable", False):
                    entity.direction = piece.direction
            grid.add(result.pieces)
        results.append(result)
        found.extend(route.findings(spec, result))
    return results, found


def build(plan: dict) -> BuildResult:
    from draftsman.blueprintable import Blueprint

    label = plan.get("label") or "factorio-forge layout"
    blueprint = Blueprint()
    blueprint.label = label
    built: list[BuiltBlock] = []
    collected: list[str] = []

    placements: list[rows.Placement] = []
    for index, block in enumerate(plan.get("blocks") or []):
        kind = block.get("type", "rows")
        if kind != "rows":
            raise PlanError(f"block {index}: unknown type {kind!r}; only 'rows' exists so far")
        known = set(rows.RowBlockSpec.__dataclass_fields__) | {"type", "per_row", "at", "rotate"}
        unknown = set(block) - known
        if unknown:
            raise PlanError(f"block {index}: unknown field(s) {', '.join(sorted(unknown))}")
        fields = {k: v for k, v in block.items() if k in rows.RowBlockSpec.__dataclass_fields__}
        fields["rows"] = _rows_of(block)
        try:
            spec = rows.RowBlockSpec(**fields)
            expanded = rows.build_block(spec)
            at = tuple(block.get("at") or (0, 0))
            placed, ports, width, height = rows.transform(expanded, at, int(block.get("rotate", 0)))
        except LayoutError as exc:
            raise PlanError(f"block {index} ({block.get('recipe')}): {exc}") from exc
        placements.extend(placed)
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

    report = inspection.inspect(blueprint)
    findings = list(report.findings) + fluids.check(list(blueprint.entities)) + route_findings
    networks = _power_networks(list(blueprint.entities))
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
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise PlanError(f"{path} is not valid JSON: {exc}") from exc
