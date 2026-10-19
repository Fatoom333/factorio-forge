"""Measuring what a build really makes: the spec going into the game, the answer coming back.

A checked blueprint says what the machines *could* do. To see what it does, the
companion mod builds it on an empty surface of its own, feeds the sources the
plan declares, empties every output belt end, runs for a while and writes
``measure.json`` (``/forge-measure``, or ``tools/headless-run.ps1 -Measure``
in the companion for a game without a window). This module writes the request
for that run from a plan, and reads the answer back as a short comparison with
what was ordered.

The spec is one JSON object::

    {"blueprint": "<string>", "surface": "vulcanus",
     "sources": [{"at": [x, y], "direction": 4, "items": [left, right], "rate": 2.0},
                 {"at": [x, y], "fluid": "water", "rate": 50}],
     "outputs": [{"at": [x, y], "item": "advanced-circuit"}],
     "targets": [{"item": "advanced-circuit", "rate": 1.0}],
     "warmup_s": 60, "measure_s": 120}

``at`` is a tile in the blueprint's own coordinates, the ones the plan and the
report use. ``items`` is ``[left lane, right lane]`` (one item stands for both)
and ``rate`` is items per second over both lanes, as in the plan's ``sources``;
no rate means as much as the belt takes. Optional fields: ``id`` and
``direction`` on an output (the way a belt the run must add flows), ``speed``
(game speed during the run), ``temperature`` on a fluid source.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

# A target counts as met from this share of what was ordered.
TOLERANCE = 0.95
# A machine counts as starved, or blocked, when it spent at least this share
# of the window waiting for an ingredient, or on a full output.
STARVED_SHARE = 0.25

DEFAULT_WARMUP_S = 60
DEFAULT_MEASURE_S = 120
DEFAULT_SPEED = 10


class MeasureError(Exception):
    """The plan cannot be turned into a spec, or the answer cannot be read."""


# --------------------------------------------------------------------------
# the request
# --------------------------------------------------------------------------


def _tile(value, what: str) -> list[int]:
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) for v in value)):
        raise MeasureError(f"{what} is [x, y] in whole tiles, not {value!r}")
    return [int(value[0]), int(value[1])]


def _lanes(lanes) -> list[str]:
    left, right = lanes
    return [left] if left == right else [left, right]


def sources_from(plan_sources: list, notes: list[str]) -> list[dict]:
    """Spec sources from the plan's resolved ones: (label, Feed, direction) triples."""
    out = []
    for label, feed, direction in plan_sources:
        if feed.lanes is None or not all(feed.lanes):
            notes.append(f"{label}: no items declared, so nothing is fed there")
            continue
        entry: dict = {"id": label, "at": [feed.tile[0], feed.tile[1]]}
        if direction is not None:
            entry["direction"] = direction
        entry["items"] = _lanes(feed.lanes)
        if feed.rate is not None:
            entry["rate"] = feed.rate
        else:
            notes.append(f"{label}: no rate declared, so it is fed as fast as the belt takes")
        out.append(entry)
    return out


def route_sources_from(routes: list, notes: list[str]) -> list[dict]:
    """Spec sources for the connections that start at a point.

    A connection whose ``from`` is ``{"at", "direction", "items", "rate"}`` is a
    supply that starts there, as a plan ``sources`` entry is; it is not listed
    in ``sources`` (that may not name the same tile), so it is read from the
    route. A branch that starts at a splitter inside the plan is not a supply.
    """
    out = []
    for r in routes:
        spec = getattr(r, "spec", None)
        if not r.ok or spec is None or r.kind != "belt" or spec.start.port is not None:
            continue
        junction = getattr(r, "junction", None)
        if junction is not None and junction.kind == "splitter":
            continue
        label = f"connection {r.id} start"
        items = list(spec.start.items or ())
        if not items:
            notes.append(f"{label}: no items declared, so nothing is fed there")
            continue
        if len(items) == 1:
            items = items * 2
        if len(items) != 2 or not all(items) or any(i == "-" or "+" in i for i in items):
            notes.append(f"{label}: its lanes ({', '.join(map(str, items))}) are not one item each, so nothing is fed "
                         "there; write it with --output-end/--fluid or as a plan source")
            continue
        entry: dict = {"id": label, "at": [spec.start.x, spec.start.y], "direction": spec.start.direction,
                       "items": _lanes(items)}
        if spec.start.rate is not None:
            entry["rate"] = float(spec.start.rate)
        else:
            notes.append(f"{label}: no rate declared, so it is fed as fast as the belt takes")
        out.append(entry)
    return out


def belt_travel_s(routes: list) -> float:
    """Seconds an item takes along the longest chain of routes.

    Each belt route counts its pieces at the belt's speed (underground hops as
    belts). A route that starts at a port leaves its block; one that starts at a
    point is a supply and leaves a node of its own; one that ends at a port
    enters that block, otherwise it ends the chain. Routes whose belt is not in
    the active data count nothing.
    """
    from draftsman.data import entities as entity_data

    edges: dict = {}
    for r in routes:
        spec = getattr(r, "spec", None)
        if not r.ok or spec is None or r.kind != "belt":
            continue
        speed = (entity_data.raw.get(spec.surface) or {}).get("speed")
        if not speed:
            continue
        seconds = len(getattr(r, "pieces", None) or ()) / (float(speed) * 60)
        begin = spec.start.port[0] if spec.start.port is not None else ("supply", r.id)
        end = spec.goal.port[0] if spec.goal.port is not None else ("end", r.id)
        edges.setdefault(begin, []).append((end, seconds))
    best: dict = {}

    def longest(node, path: frozenset) -> float:
        if node in best:
            return best[node]
        top = max((s + longest(nxt, path | {node}) for nxt, s in edges.get(node, []) if nxt not in path and nxt != node),
                  default=0.0)
        best[node] = top  # the routes between blocks form a DAG; `path` only guards a cycle
        return top

    return max((longest(n, frozenset()) for n in edges), default=0.0)


def outputs_from(blocks: list, routes: list) -> list[dict]:
    """Every belt end where the build hands something on, one entry per item.

    An out belt port nobody routes from, and a route that really ends at a free
    point, are the ends of the build. A route that merges onto a belt (it ends
    in the side of that belt, ``goal.sideload``) is not: the belt carries on, and
    an output there would empty it. Pipes are not measured: the mod empties belts.
    """
    routed_from = set()
    for r in routes:
        spec = getattr(r, "spec", None)
        if r.ok and spec is not None and spec.start.port is not None:
            routed_from.add(tuple(spec.start.port))

    seen: set[tuple] = set()
    out: list[dict] = []

    def add(at, direction, items, label):
        items = [i for i in dict.fromkeys(items) if i and i != "-"]  # "-" is a lane carrying nothing
        for item in items or [None]:
            marker = (tuple(at), item)
            if marker in seen:
                continue
            seen.add(marker)
            entry: dict = {"id": label, "at": list(at)}
            if direction is not None:
                entry["direction"] = direction
            if item:
                entry["item"] = item
            out.append(entry)

    for block in blocks:
        for port in block.ports:
            if port["kind"] != "belt" or port["io"] != "out":
                continue
            if (block.index, port["index"]) in routed_from:
                continue
            add((port["x"], port["y"]), port.get("direction"), list(port.get("items") or []),
                f"block {block.index} port {port['index']}")
    for r in routes:
        spec = getattr(r, "spec", None)
        if (not r.ok or spec is None or r.kind != "belt" or spec.goal.port is not None
                or getattr(spec.goal, "sideload", False)):
            continue
        items = list(r.delivered or r.lanes or [])
        add((spec.goal.x, spec.goal.y), spec.goal.direction, items, f"route {r.id}")
    return out


def targets_from(plan_targets) -> list[dict]:
    """The plan's `targets` ({"item", "per_second" | "per_minute"}) as spec targets."""
    out = []
    for entry in plan_targets or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("item"), str):
            raise MeasureError(f"a target is {{\"item\": name, \"per_second\": n}}, not {entry!r}")
        if "per_second" in entry:
            rate = entry["per_second"]
        elif "per_minute" in entry:
            rate = entry["per_minute"] / 60
        else:
            raise MeasureError(f"target {entry['item']!r} has no per_second or per_minute")
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate < 0:
            raise MeasureError(f"target {entry['item']!r}: the rate must be a number of at least 0")
        out.append({"item": entry["item"], "rate": float(rate)})
    return out


def build_spec(blueprint: str, *, sources: list[dict], outputs: list[dict], targets: list[dict],
               surface: str | None = None, warmup_s: float = DEFAULT_WARMUP_S,
               measure_s: float = DEFAULT_MEASURE_S, speed: float | None = DEFAULT_SPEED,
               belt_travel_s: float | None = None) -> dict:
    """The spec object; checks what the game would otherwise only fail on."""
    if not isinstance(blueprint, str) or not blueprint:
        raise MeasureError("the blueprint string is empty")
    if warmup_s < 0 or measure_s <= 0:
        raise MeasureError("warm-up is at least 0 seconds and the measured stretch more than 0")
    if not outputs and not targets:
        raise MeasureError("nothing to measure: the build has no output belt end and no target")
    spec: dict = {"blueprint": blueprint}
    if surface:
        spec["surface"] = surface
    spec["sources"] = sources
    spec["outputs"] = outputs
    spec["targets"] = targets
    spec["warmup_s"] = warmup_s
    spec["measure_s"] = measure_s
    if speed:
        spec["speed"] = speed
    if belt_travel_s:
        # Not read by the game: `measure result` uses it to judge the warm-up.
        spec["belt_travel_s"] = round(belt_travel_s, 1)
    return spec


def parse_fluid(text: str) -> dict:
    """`x,y,fluid,rate` from the command line as a fluid source."""
    parts = text.split(",")
    if len(parts) not in (3, 4):
        raise MeasureError(f"a fluid source is x,y,fluid[,rate], not {text!r}")
    try:
        entry: dict = {"at": [int(parts[0]), int(parts[1])], "fluid": parts[2].strip()}
        if len(parts) == 4:
            entry["rate"] = float(parts[3])
    except ValueError as exc:
        raise MeasureError(f"a fluid source is x,y,fluid[,rate], not {text!r}") from exc
    return entry


def parse_output(text: str) -> dict:
    """`x,y[,item[,direction]]` from the command line as an output belt end."""
    parts = text.split(",")
    if not 2 <= len(parts) <= 4:
        raise MeasureError(f"an output is x,y[,item[,direction]], not {text!r}")
    try:
        entry: dict = {"at": [int(parts[0]), int(parts[1])]}
        if len(parts) >= 3 and parts[2].strip():
            entry["item"] = parts[2].strip()
        if len(parts) == 4:
            entry["direction"] = int(parts[3])
    except ValueError as exc:
        raise MeasureError(f"an output is x,y[,item[,direction]], not {text!r}") from exc
    return entry


def parse_target(text: str) -> dict:
    """`item=rate` (items per second) from the command line as a target."""
    item, sep, rate = text.partition("=")
    if not sep or not item.strip():
        raise MeasureError(f"a target is item=rate, not {text!r}")
    try:
        return {"item": item.strip(), "rate": float(rate)}
    except ValueError as exc:
        raise MeasureError(f"a target is item=rate, not {text!r}") from exc


# --------------------------------------------------------------------------
# the answer
# --------------------------------------------------------------------------


@dataclass
class TargetLine:
    item: str
    ordered: float
    delivered: float | None  # counted at the output belt ends; None when none was declared
    produced: float  # what the machines made, from the production statistics

    @property
    def measured(self) -> float:
        return self.delivered if self.delivered is not None else self.produced

    @property
    def met(self) -> bool:
        return self.measured >= self.ordered * TOLERANCE - 1e-9

    @property
    def share(self) -> float | None:
        return self.measured / self.ordered if self.ordered > 0 else None


@dataclass
class MachineGroup:
    name: str
    recipe: str | None
    count: int
    shares: dict[str, float]
    missing: dict[str, float]
    other: dict[str, int] = field(default_factory=dict)

    @property
    def starved(self) -> bool:
        return self.shares.get("waiting_input", 0) >= STARVED_SHARE

    @property
    def blocked(self) -> bool:
        return self.shares.get("output_full", 0) >= STARVED_SHARE

    @property
    def unpowered(self) -> bool:
        return self.shares.get("no_power", 0) > 0.05


@dataclass
class Comparison:
    ok: bool
    error: str | None
    placed: int
    expected: int
    window: dict
    targets: list[TargetLine]
    groups: list[MachineGroup]
    sources: list[dict]
    produced: dict[str, float]
    consumed: dict[str, float]
    delivered: dict[str, float]
    problems: list[str]
    refused: dict[str, int]
    surface: dict
    belt_travel_s: float = 0.0  # from the spec: belt travel along the longest chain of routes

    @property
    def all_met(self) -> bool:
        return self.ok and all(t.met for t in self.targets)


def load_result(path: Path) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
        data = json.loads(text)
    except (OSError, ValueError) as exc:
        raise MeasureError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict) or "ok" not in data:
        raise MeasureError(f"{path} is not a measure.json (no 'ok')")
    return data


def _table(value) -> dict:
    # An empty Lua table is written as [], not {}.
    return value if isinstance(value, dict) else {}


def _rows(value) -> list:
    return value if isinstance(value, list) else list(_table(value).values())


def compare(result: dict, targets: list[dict] | None = None, belt_travel_s: float = 0.0) -> Comparison:
    """Ordered against measured, from the parsed ``measure.json``.

    ``targets`` are the ordered rates ({"item", "rate"}); without them the
    result's own spec is unknown and only the machines and flows are reported.
    """
    produced = {k: float(v) for k, v in _table(result.get("produced")).items()}
    consumed = {k: float(v) for k, v in _table(result.get("consumed")).items()}
    outputs = _rows(result.get("outputs"))
    delivered: dict[str, float] = {}
    for out in outputs:
        for item, rate in _table(out.get("delivered")).items():
            delivered[item] = delivered.get(item, 0.0) + float(rate)

    lines = []
    for target in targets or []:
        item = target["item"]
        lines.append(TargetLine(item, float(target["rate"]),
                                delivered.get(item, 0.0) if outputs else None,
                                produced.get(item, 0.0)))

    groups: dict[tuple, dict] = {}
    for machine in _rows(result.get("machines")):
        key = (machine.get("name"), machine.get("recipe"))
        g = groups.setdefault(key, {"count": 0, "shares": {}, "missing": {}, "other": {}})
        g["count"] += 1
        for name, share in _table(machine.get("shares")).items():
            g["shares"][name] = g["shares"].get(name, 0.0) + float(share)
        for name, share in _table(machine.get("missing")).items():
            g["missing"][name] = g["missing"].get(name, 0.0) + float(share)
        for name, n in _table(machine.get("other")).items():
            g["other"][name] = g["other"].get(name, 0) + int(n)
    machine_groups = []
    for (name, recipe), g in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        n = g["count"]
        machine_groups.append(MachineGroup(
            name or "?", recipe, n,
            {k: v / n for k, v in g["shares"].items()},
            {k: v / n for k, v in g["missing"].items()}, g["other"]))

    return Comparison(
        ok=bool(result.get("ok")), error=result.get("error"),
        placed=int(result.get("placed") or 0), expected=int(result.get("expected") or 0),
        window=_table(result.get("window")), targets=lines, groups=machine_groups,
        sources=_rows(result.get("sources")), produced=produced, consumed=consumed,
        delivered=delivered, problems=[str(p) for p in _rows(result.get("problems"))],
        refused={k: int(v) for k, v in _table(result.get("refused")).items()},
        surface=_table(result.get("surface")), belt_travel_s=float(belt_travel_s or 0.0))


def craft_chain_s(groups: list[MachineGroup], item: str) -> float | None:
    """Seconds of crafting along the longest chain of recipes that ends in ``item``.

    A stage takes its recipe's time over its machine's crafting speed (modules
    and beacons are not known here); a stage's ingredients that another
    measured group makes come first, and the slowest of them counts. None when
    no group makes the item or the data lacks the recipe or machine.
    """
    from draftsman.data import entities as entity_data
    from draftsman.data import recipes as recipe_data

    stages: dict[str, tuple[float, list[str]]] = {}  # product -> (seconds, ingredient items)
    for g in groups:
        entry = recipe_data.raw.get(g.recipe) if g.recipe else None
        speed = (entity_data.raw.get(g.name) or {}).get("crafting_speed")
        if not entry or not speed:
            continue
        seconds = float(entry.get("energy_required", 0.5) or 0.5) / float(speed)
        ingredients = [i.get("name") for i in entry.get("ingredients") or [] if isinstance(i, dict)]
        for product in entry.get("results") or []:
            if isinstance(product, dict) and product.get("name"):
                stages.setdefault(product["name"], (seconds, ingredients))

    def depth(name: str, seen: frozenset) -> float:
        seconds, ingredients = stages[name]
        return seconds + max((depth(i, seen | {name}) for i in ingredients if i in stages and i not in seen),
                             default=0.0)

    return depth(item, frozenset()) if item in stages else None


def _arrives(c: Comparison, item: str) -> bool:
    """Whether the window saw ``item`` made by a measured group or fed by a source."""
    if c.produced.get(item, 0.0) > 0:
        return True
    for source in c.sources:
        for lane in _rows(source.get("lanes")):
            if lane.get("item") == item and (lane.get("fed_rate") or 0) > 0:
                return True
    return False


def _item_ingredients(recipe: str) -> set[str]:
    from draftsman.data import recipes as recipe_data

    entry = recipe_data.raw.get(recipe) or {}
    return {i["name"] for i in entry.get("ingredients") or []
            if isinstance(i, dict) and i.get("name") and i.get("type", "item") == "item"}


def unsupplied(c: Comparison, item: str) -> list[tuple[str, bool]]:
    """Ingredients of the machines making ``item`` that they lacked and nothing delivered.

    Each is (ingredient, made by a measured group). Lacked: missing at least
    ``STARVED_SHARE`` of the window. Fluids are not judged.
    """
    made = {p for g in c.groups if g.recipe for p in _products(g.recipe)}
    out: dict[str, bool] = {}
    for g in c.groups:
        if not g.recipe or not _makes(g.recipe, item):
            continue
        items = _item_ingredients(g.recipe)
        for name, share in g.missing.items():
            if share >= STARVED_SHARE and name in items and not _arrives(c, name):
                out[name] = name in made
    return sorted(out.items())


def warmup_hint(c: Comparison) -> str | None:
    """A line about why a short target is short: a missing supply, or a run that was too short.

    Per short target. (1) A machine of the last stage lacked an ingredient that
    neither a measured group made nor a source fed in the whole window: that is
    a missing source or a starved stage upstream, and a longer warm-up will not
    change it. (2) Otherwise, when the target is above 0 (or its machines waited
    for input a quarter of the window or more) and every ingredient arrived, the
    line may simply still be filling: advise a warm-up at least twice as long,
    and a figure is trusted when two windows agree. The craft time along the
    longest chain of recipes (recipe time over crafting speed; one craft per
    stage, no buffers, no modules) plus the belt travel the spec recorded is
    given as a floor of the fill time, not as a test: the line fills slower than
    that. None of this says the plan is at fault.
    """
    short = [t for t in c.targets if not t.met]
    if not short:
        return None
    warmup = c.window.get("warmup_s")
    lines: list[str] = []
    filling: list[str] = []
    for t in short:
        lacking = unsupplied(c, t.item)
        if lacking:
            parts = []
            for name, made in lacking:
                parts.append(f"{name} was not made in the window (the machines that make it were starved: look there first)"
                             if made else f"{name} is never supplied (no machine makes it and no source feeds it): "
                                          "a missing source")
            lines.append(f"{t.item}: " + "; ".join(parts) + ". A longer warm-up will not help")
            continue
        last = [g for g in c.groups if g.recipe and _makes(g.recipe, t.item)]
        waited = max((g.shares.get("waiting_input", 0.0) for g in last), default=0.0)
        if max(t.measured, t.produced) <= 0 and waited < STARVED_SHARE:
            continue
        text = f"{t.item}: {_rate(t.measured)} of {_rate(t.ordered)} ordered, every ingredient arrived"
        if waited >= STARVED_SHARE:
            text += f", its machines waited for input {_percent(waited)} of the window"
        chain = craft_chain_s(c.groups, t.item)
        if chain is not None:
            text += (f"; filling takes at least ~{chain + c.belt_travel_s:.0f} s (crafting {chain:.0f} s along the "
                     f"longest chain + belt travel {c.belt_travel_s:.0f} s, one craft per stage), in practice more")
        filling.append(text)
    if filling:
        now = f" (it was {warmup:g} s)" if isinstance(warmup, (int, float)) else ""
        lines.append("the run may have been too short to fill the line: " + "; ".join(filling) + ". "
                     f"That alone is not a fault of the plan: run again with a warm-up at least twice as long{now} "
                     "and trust a figure when two windows agree")
    return "\n".join("note: " + line for line in lines) if lines else None


def _products(recipe: str) -> set[str]:
    from draftsman.data import recipes as recipe_data

    entry = recipe_data.raw.get(recipe) or {}
    return {p["name"] for p in entry.get("results") or [] if isinstance(p, dict) and p.get("name")}


def _makes(recipe: str, item: str) -> bool:
    from draftsman.data import recipes as recipe_data

    entry = recipe_data.raw.get(recipe) or {}
    return any(isinstance(p, dict) and p.get("name") == item for p in entry.get("results") or [])


def _rate(value: float) -> str:
    return f"{value:.3g}/s"


def _percent(value: float) -> str:
    return f"{value * 100:.0f}%"


def format_comparison(c: Comparison) -> str:
    """The short text the `measure result` command prints."""
    if not c.ok:
        return f"measuring failed: {c.error or 'no reason given'}"
    out: list[str] = []
    window = c.window
    out.append(f"measured {window.get('measure_s', '?')} s after {window.get('warmup_s', '?')} s of warm-up; "
               f"{c.placed} of {c.expected} entities stood"
               + (f" (refused: {', '.join(f'{n} x{k}' for n, k in sorted(c.refused.items()))})" if c.refused else ""))
    if c.targets:
        out.append("targets (ordered -> measured):")
        for t in c.targets:
            where = "at the output belts" if t.delivered is not None else "made by the machines"
            share = f" ({_percent(t.share)})" if t.share is not None else ""
            verdict = "ok" if t.met else "SHORT"
            extra = f", machines made {_rate(t.produced)}" if t.delivered is not None else ""
            out.append(f"  {t.item}: ordered {_rate(t.ordered)}, measured {_rate(t.measured)} {where}{share}{extra} [{verdict}]")
    elif c.delivered:
        out.append("delivered at the output belts: " + ", ".join(f"{k} {_rate(v)}" for k, v in sorted(c.delivered.items())))

    for source in c.sources:
        per_item: dict[str, list[float]] = {}
        for lane in _rows(source.get("lanes")):
            declared, fed = lane.get("declared_rate"), lane.get("fed_rate")
            if declared and fed is not None:
                total = per_item.setdefault(lane.get("item"), [0.0, 0.0])
                total[0] += declared
                total[1] += fed
        for item, (declared, fed) in per_item.items():
            if fed < declared * TOLERANCE:
                out.append(f"supply held back: {item} at {source.get('at')} was fed {_rate(fed)} of "
                           f"{_rate(declared)} declared (the belt could not take more: something downstream is full or slow)")
        if source.get("problem"):
            out.append(f"source at {source.get('at')}: {source['problem']}")

    starved = [g for g in c.groups if g.starved]
    blocked = [g for g in c.groups if g.blocked]
    unpowered = [g for g in c.groups if g.unpowered]
    if c.groups:
        out.append("machines:")
        for g in c.groups:
            parts = [f"{k.replace('_', ' ')} {_percent(g.shares[k])}"
                     for k in ("working", "waiting_input", "output_full", "no_power", "no_fuel", "other")
                     if g.shares.get(k, 0) >= 0.005]
            recipe = f" ({g.recipe})" if g.recipe else ""
            out.append(f"  {g.count} x {g.name}{recipe}: {', '.join(parts) or 'no samples'}")
    for g in starved:
        why = sorted(g.missing.items(), key=lambda kv: -kv[1])
        reason = ", ".join(f"{name} missing {_percent(share)} of the time" for name, share in why[:3]) or "ingredient not named"
        out.append(f"starved: {g.count} x {g.name} ({g.recipe or 'no recipe'}) waited for input {_percent(g.shares['waiting_input'])}: {reason}")
    for g in blocked:
        out.append(f"blocked: {g.count} x {g.name} ({g.recipe or 'no recipe'}) stood with a full output "
                   f"{_percent(g.shares['output_full'])} of the time (nothing takes the product away fast enough)")
    for g in unpowered:
        out.append(f"no power: {g.count} x {g.name} ({g.recipe or 'no recipe'}) {_percent(g.shares['no_power'])} of the time")
    for g in c.groups:
        if g.recipe is None and g.other.get("no_recipe"):
            out.append(f"no recipe: {g.count} x {g.name} stood without one (the blueprint set none, or the recipe is not "
                       "allowed on this surface or is not known to the game)")
    for problem in c.problems:
        out.append(f"note: {problem}")
    hint = warmup_hint(c)
    if hint:
        out.append(hint)
    if c.targets:
        out.append("result: " + ("every target met" if all(t.met for t in c.targets)
                                 else f"{sum(not t.met for t in c.targets)} of {len(c.targets)} target(s) short"))
    return "\n".join(out)


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------


def _cmd_spec(args) -> int:
    """A plan in; the measuring spec for the game out."""
    import sys

    from . import plan

    try:
        data = plan.load(Path(args.plan))
        planned, warned = None, []
        if data.get("request"):
            planned, warned = plan.planned_from_request(data, Path(args.plan))
        result = plan.build(data, planned)
        notes: list[str] = []
        sources = sources_from(plan._sources(data, result.blocks), notes)
        sources += route_sources_from(result.routes, notes)
        sources += [parse_fluid(text) for text in args.fluid or []]
        outputs = outputs_from(result.blocks, result.routes) + [parse_output(t) for t in args.output_ends or []]
        targets = [parse_target(t) for t in args.target or []] or targets_from(data.get("targets"))
        spec = build_spec(result.blueprint.to_string(), sources=sources, outputs=outputs, targets=targets,
                          surface=args.surface, warmup_s=args.warmup, measure_s=args.seconds,
                          speed=args.speed, belt_travel_s=belt_travel_s(result.routes))
    except (plan.PlanError, MeasureError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    from . import report

    errors = [f for f in result.findings if report.level(f) == report.ERROR]
    if errors:
        print(f"warning: the plan has {len(errors)} error(s); the run will measure it as it is", file=sys.stderr)
    out = Path(args.output) if args.output else Path(args.plan).resolve().with_suffix(".measure-spec.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    print(f"{result.label}: {len(spec['sources'])} source(s), {len(spec['outputs'])} output end(s), "
          f"{len(spec['targets'])} target(s); {spec['warmup_s']:g} s warm-up + {spec['measure_s']:g} s measured")
    for note in notes + warned:
        print(f"note: {note}")
    print(f"spec: {out.resolve()}")
    print("run it: tools/headless-run.ps1 -Save <save> -Out <folder> -Measure <spec> (companion mod), "
          "then: factorio-forge measure result <folder>/measure.json --spec <spec>")
    return 0


def _cmd_result(args) -> int:
    """measure.json in; ordered against measured out."""
    import sys

    try:
        result = load_result(Path(args.result))
        targets, travel = None, 0.0
        if args.spec:
            try:
                spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
                targets, travel = spec.get("targets"), spec.get("belt_travel_s") or 0.0
            except (OSError, ValueError) as exc:
                raise MeasureError(f"cannot read the spec {args.spec}: {exc}") from exc
        if args.target:
            targets = [parse_target(t) for t in args.target]
        comparison = compare(result, targets, travel)
    except MeasureError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(format_comparison(comparison))
    if not getattr(args, "no_library", False):
        _offer_to_library(args, comparison)
    return 0 if comparison.all_met else 1


def _offer_to_library(args, comparison: Comparison) -> None:
    """When every target was met, keep the plan's blocks in the profile's library (see library.save_measured)."""
    import sys

    from . import library, plan

    if not comparison.all_met or not comparison.targets:
        return
    plan_path = Path(args.plan) if getattr(args, "plan", None) else None
    if plan_path is None and args.spec:
        spec = Path(args.spec)
        stem = spec.name[: -len(".measure-spec.json")] if spec.name.endswith(".measure-spec.json") else None
        guess = spec.with_name(stem + ".json") if stem else None
        plan_path = guess if guess is not None and guess.is_file() else None
    if plan_path is None:
        if not args.spec:
            return  # a run with only --target names no plan; nothing to say
        print("library: no plan known (pass --plan), so no block was offered; --no-library turns this off", file=sys.stderr)
        return
    try:
        report = library.save_measured(plan.load(plan_path), comparison, found_from=f"measure run of {plan_path.name}")
    except (library.LibraryError, plan.PlanError) as exc:
        print(f"library: nothing saved ({exc})", file=sys.stderr)
        return
    for line in report.lines():
        print(line)


def register(sub) -> None:
    """Add the `measure` command to the CLI's subparsers."""
    measure = sub.add_parser("measure", help="measure what a built plan really makes, in the game")
    kinds = measure.add_subparsers(dest="what", required=True)

    spec = kinds.add_parser("spec", help="write the measuring spec for a plan")
    spec.add_argument("plan", help="a plan JSON file (see factorio_forge/plan.py)")
    spec.add_argument("-o", "--output", help="where to write the spec (default: beside the plan)")
    spec.add_argument("--surface", help="the planet the plan is for (its surface properties are used)")
    spec.add_argument("--warmup", type=float, default=DEFAULT_WARMUP_S, help="seconds before measuring (default 60)")
    spec.add_argument("--seconds", type=float, default=DEFAULT_MEASURE_S, help="seconds measured (default 120)")
    spec.add_argument("--speed", type=float, default=DEFAULT_SPEED, help="game speed during the run (default 10)")
    spec.add_argument("--output-end", dest="output_ends", action="append", metavar="X,Y[,ITEM[,DIR]]",
                      help="one more output belt end, for a plan that does not route its product anywhere")
    spec.add_argument("--fluid", action="append", metavar="X,Y,FLUID[,RATE]", help="a fluid source (an infinity pipe at the tile)")
    spec.add_argument("--target", action="append", metavar="ITEM=RATE", help="a target in items per second (default: the plan's targets)")
    spec.set_defaults(func=_cmd_spec)

    result = kinds.add_parser("result", help="compare a measure.json with what was ordered")
    result.add_argument("result", help="the measure.json the run wrote")
    result.add_argument("--spec", help="the spec that was run, for its targets")
    result.add_argument("--target", action="append", metavar="ITEM=RATE", help="a target in items per second (instead of the spec's)")
    result.add_argument("--plan", help="the plan that was run, for the library (default: the spec's name without .measure-spec)")
    result.add_argument("--no-library", action="store_true",
                        help="do not save the plan's blocks to the profile's library when every target is met")
    result.set_defaults(func=_cmd_result)
