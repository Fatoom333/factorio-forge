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
      "entities": [{"name": "fast-transport-belt", "position": [-1, 3], "direction": 4}]
    }
"""

from __future__ import annotations

import json
import math
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path

from draftsman.data import entities as entity_data

from . import fluids, inspection, rows
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


@dataclass
class BuildResult:
    label: str
    blueprint: object
    blocks: list[BuiltBlock]
    findings: list[inspection.Finding]
    power_networks: int
    warnings: list[str] = field(default_factory=list)

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
                ports=[asdict(p) for p in ports],
                crafts_per_second=crafts_per_second(spec.recipe, spec.machine, spec.speed_bonus) * sum(spec.rows),
                notes=expanded.notes,
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
        blueprint.generate_power_connections()
    collected.extend(str(w.message) for w in caught)

    report = inspection.inspect(blueprint)
    findings = list(report.findings) + fluids.check(list(blueprint.entities))
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
    return BuildResult(label, blueprint, built, findings, networks, list(dict.fromkeys(collected)))


def load(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PlanError(f"{path} is not valid JSON: {exc}") from exc
