"""A snapshot of a running base: what stands where, what it is doing, what flows.

The companion mod writes ``survey.json`` (``/forge-survey``, or
``tools/headless-run.ps1 -Survey`` in the companion for a game without a
window). It watches the base for a while, so each machine's state is a share of
that window rather than one tick's luck. This module only reads the file; the
review of the base is built on what it returns.

The file is one JSON object::

    {"exported_by": "factorio-forge-companion", "kind": "survey",
     "game_version": "2.0.77", "tick": 8116700, "force": "player",
     "surface": "vulcanus", "area": [[x1, y1], [x2, y2]] or null,
     "window_s": 60,
     "machines": [{"unit": 101, "name": "assembling-machine-2",
                   "position": [x, y], "direction": 0, "recipe": "...",
                   "crafting_speed": 0.75,
                   "states": {"working": 0.6, "no_ingredients": 0.4},
                   "missing": {"copper-cable": 0.4},
                   "crafts_per_s": 0.9}],
     "miners": [{"unit": 7, "name": "electric-mining-drill",
                 "position": [x, y], "resource": "iron-ore", "field": 3,
                 "states": {"working": 1.0}, "amount_left": 120000}],
     "fields": [{"id": 3, "resource": "iron-ore", "amount": 2400000,
                 "tiles": 410, "drills": 12, "center": [x, y]}],
     "flows": {"items": {"iron-plate": {"made": 12.5, "used": 11.0}},
               "fluids": {"water": {"made": 300.0, "used": 250.0}}},
     "power": [{"network": 1, "made_w": 4.1e7, "used_w": 4.0e7,
                "satisfaction": 1.0, "machines": 120}],
     "blueprint": "<blueprint string of the area>",
     "blueprint_origin": [x, y]}

Positions are world tiles (entity centres, as the game gives them).
``states`` maps a state to the share of the window the machine spent in it;
the shares add up to about 1. The states are the game's entity statuses in
snake case: ``working``, ``no_ingredients``, ``output_full``, ``no_power``,
``low_power``, ``no_fuel``, ``no_recipe``, ``disabled``, ``no_minable_resources``,
``waiting_for_space_in_destination``, ...; anything else is kept as given.
``missing`` is, for the waiting time, which ingredient was short, as a share of
the window. ``crafts_per_s`` is what the machine finished over the window.
``flows`` are per second over the window, from the force's production
statistics on that surface. ``blueprint`` holds every entity of the area;
``blueprint_origin`` is the world tile of the blueprint's (0, 0), so a world
position minus the origin is a blueprint position.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

WORKING = "working"


class SurveyError(Exception):
    """The file is not a survey forge can read."""


@dataclass
class Machine:
    unit: int
    name: str
    position: tuple[float, float]
    direction: int = 0
    recipe: str | None = None
    crafting_speed: float = 1.0
    states: dict[str, float] = field(default_factory=dict)
    missing: dict[str, float] = field(default_factory=dict)
    crafts_per_s: float = 0.0

    @property
    def working_share(self) -> float:
        return self.states.get(WORKING, 0.0)


@dataclass
class Miner:
    unit: int
    name: str
    position: tuple[float, float]
    resource: str | None = None
    field: int | None = None
    states: dict[str, float] = field(default_factory=dict)
    amount_left: float | None = None

    @property
    def working_share(self) -> float:
        return self.states.get(WORKING, 0.0)


@dataclass
class ResourceField:
    id: int
    resource: str
    amount: float
    tiles: int
    drills: int
    center: tuple[float, float]


@dataclass
class Flow:
    made: float = 0.0
    used: float = 0.0


@dataclass
class PowerNetwork:
    network: int
    made_w: float
    used_w: float
    satisfaction: float
    machines: int


@dataclass
class Survey:
    surface: str
    tick: int
    window_s: float
    machines: list[Machine]
    miners: list[Miner]
    fields: list[ResourceField]
    items: dict[str, Flow]
    fluids: dict[str, Flow]
    power: list[PowerNetwork]
    area: tuple[tuple[float, float], tuple[float, float]] | None = None
    blueprint: str | None = None
    blueprint_origin: tuple[float, float] = (0.0, 0.0)
    game_version: str | None = None
    force: str = "player"

    def flow(self, name: str) -> Flow:
        """Made and used per second of an item or fluid; zero when it does not flow."""
        return self.items.get(name) or self.fluids.get(name) or Flow()


def _pair(value, what: str) -> tuple[float, float]:
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        raise SurveyError(f"{what} is [x, y], not {value!r}")
    return (float(value[0]), float(value[1]))


def _shares(value, what: str) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SurveyError(f"{what} maps names to shares, not {value!r}")
    return {str(k): float(v) for k, v in value.items()}


def _flows(value, what: str) -> dict[str, Flow]:
    if not isinstance(value, dict):
        raise SurveyError(f"{what} maps names to {{made, used}}, not {value!r}")
    return {str(k): Flow(float(v.get("made", 0.0)), float(v.get("used", 0.0)))
            for k, v in value.items()}


def from_dict(data: dict) -> Survey:
    """Read a survey that is already parsed; raises SurveyError on a bad shape."""
    if not isinstance(data, dict) or data.get("kind") != "survey":
        raise SurveyError("not a survey: the object has no \"kind\": \"survey\"")
    try:
        machines = [Machine(
            unit=int(m["unit"]), name=str(m["name"]),
            position=_pair(m["position"], "machine position"),
            direction=int(m.get("direction", 0)), recipe=m.get("recipe"),
            crafting_speed=float(m.get("crafting_speed", 1.0)),
            states=_shares(m.get("states"), "machine states"),
            missing=_shares(m.get("missing"), "machine missing"),
            crafts_per_s=float(m.get("crafts_per_s", 0.0)),
        ) for m in data.get("machines", [])]
        miners = [Miner(
            unit=int(m["unit"]), name=str(m["name"]),
            position=_pair(m["position"], "miner position"),
            resource=m.get("resource"), field=m.get("field"),
            states=_shares(m.get("states"), "miner states"),
            amount_left=m.get("amount_left"),
        ) for m in data.get("miners", [])]
        fields = [ResourceField(
            id=int(f["id"]), resource=str(f["resource"]), amount=float(f["amount"]),
            tiles=int(f.get("tiles", 0)), drills=int(f.get("drills", 0)),
            center=_pair(f["center"], "field center"),
        ) for f in data.get("fields", [])]
        flows = data.get("flows") or {}
        power = [PowerNetwork(
            network=int(p["network"]), made_w=float(p.get("made_w", 0.0)),
            used_w=float(p.get("used_w", 0.0)),
            satisfaction=float(p.get("satisfaction", 1.0)),
            machines=int(p.get("machines", 0)),
        ) for p in data.get("power", [])]
        area = data.get("area")
        return Survey(
            surface=str(data["surface"]), tick=int(data.get("tick", 0)),
            window_s=float(data.get("window_s", 0.0)),
            machines=machines, miners=miners, fields=fields,
            items=_flows(flows.get("items", {}), "flows.items"),
            fluids=_flows(flows.get("fluids", {}), "flows.fluids"),
            power=power,
            area=(_pair(area[0], "area corner"), _pair(area[1], "area corner"))
            if area else None,
            blueprint=data.get("blueprint"),
            blueprint_origin=_pair(data.get("blueprint_origin", [0, 0]), "blueprint_origin"),
            game_version=data.get("game_version"),
            force=str(data.get("force", "player")),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise SurveyError(f"survey has a bad entry: {error}") from error


def load(path: str | Path) -> Survey:
    """Read ``survey.json``; a missing or broken file is one SurveyError line."""
    path = Path(path)
    try:
        with path.open(encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise SurveyError(f"no survey at {path}") from None
    except json.JSONDecodeError as error:
        raise SurveyError(f"{path} is not JSON: {error}") from None
    return from_dict(data)
