"""Step 1: what the player asked for, pinned down before anything is designed.

The player's words go to Claude, and reading them is Claude's job. This module
is what that reading is checked against: a request is a small JSON file in the
shape every later step takes, and `review` says what in it is unknown or
locked, what was assumed and from where, runs the bill of materials so the
numbers are in the conversation early, and lists what only the player can
answer.

    {
      "said": "нужен блок на 2 батареи в секунду, пластины и кислота приходят поездом",
      "targets": [{"item": "battery", "per_second": 2}],
      "boundary": ["iron-plate", "copper-plate", "sulfuric-acid"],
      "tiers": {"belt": "fast-transport-belt"},
      "machine_choices": {"chemistry": "chemical-plant"},
      "recipe_choices": {},
      "effects": {"*": {"speed": 0, "productivity": 0, "consumption": 0}},
      "plot": {"width": 120, "height": 40},
      "style": ["mirrored rows, like the copper block"]
    }

Only `targets` is required. A rate is `per_second` or `per_minute`. What is
not given is filled from the player's own game where the companion mod's
export allows -- the fastest belt and inserters they have unlocked, the
machines they can build -- and every such fill-in is listed, so it can be
confirmed or overridden rather than discovered in the blueprint.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from draftsman.data import entities as entity_data
from draftsman.data import fluids as fluid_data
from draftsman.data import items as item_data

from . import bom, environment, layout, names

ROLES = ("belt", "inserter", "long_inserter", "pole")


class RequestError(Exception):
    """The request file is malformed."""


@dataclass
class Spec:
    targets: list[tuple[str, float]]  # item or fluid, per second
    said: str = ""
    boundary: list[str] = field(default_factory=list)
    tiers: dict[str, str] = field(default_factory=dict)
    machine_choices: dict[str, str] = field(default_factory=dict)
    recipe_choices: dict[str, str] = field(default_factory=dict)
    effects: dict[str, bom.Effects] = field(default_factory=dict)
    plot: dict | None = None
    style: list[str] = field(default_factory=list)


@dataclass
class Option:
    name: str
    unlocked: bool | None
    detail: str
    rank: float


@dataclass
class Review:
    spec: Spec
    environment_note: str
    problems: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    tiers: dict[str, str] = field(default_factory=dict)
    bill: bom.BillOfMaterials | None = None
    bonuses: dict[str, float] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return not self.problems and self.bill is not None

    def to_dict(self) -> dict:
        bill = None
        if self.bill is not None:
            bill = {
                "lines": [
                    {
                        "recipe": line.recipe, "machine": line.machine, "machines": line.machines,
                        "crafts_per_second": line.rate, "inputs": line.inputs, "outputs": line.outputs,
                        "power_watts": line.power,
                    }
                    for line in self.bill.lines
                ],
                "from_outside": self.bill.raw_materials,
                "total_power_watts": self.bill.total_power,
                "choices_made": [a.__dict__ for a in self.bill.ambiguities],
            }
        return {
            "ready": self.ready,
            "environment": self.environment_note,
            "problems": self.problems,
            "questions": self.questions,
            "assumptions": self.assumptions,
            "tiers": self.tiers,
            "bonuses": self.bonuses,
            "bill": bill,
        }


# --------------------------------------------------------------------------
# reading the file
# --------------------------------------------------------------------------


def parse(data: dict) -> Spec:
    if not isinstance(data, dict):
        raise RequestError("a request is a JSON object")
    known = {"said", "targets", "boundary", "tiers", "machine_choices", "recipe_choices", "effects", "plot", "style"}
    unknown = set(data) - known
    if unknown:
        raise RequestError(f"unknown field(s): {', '.join(sorted(unknown))}")
    targets = []
    for entry in data.get("targets") or []:
        try:
            item = entry["item"]
            if "per_second" in entry:
                rate = float(entry["per_second"])
            elif "per_minute" in entry:
                rate = float(entry["per_minute"]) / 60
            else:
                raise KeyError("per_second")
        except (KeyError, TypeError, ValueError) as exc:
            raise RequestError(f"a target needs 'item' and 'per_second' or 'per_minute': {entry!r}") from exc
        targets.append((str(item), rate))
    if not targets:
        raise RequestError("a request needs at least one target")
    tiers = dict(data.get("tiers") or {})
    if set(tiers) - set(ROLES):
        raise RequestError(f"tiers are {', '.join(ROLES)}")
    effects = {}
    for category, values in (data.get("effects") or {}).items():
        try:
            effects[category] = bom.Effects(**values)
        except TypeError as exc:
            raise RequestError(f"effects for {category!r}: {exc}") from exc
    return Spec(
        targets=targets,
        said=str(data.get("said") or ""),
        boundary=[str(b) for b in data.get("boundary") or []],
        tiers=tiers,
        machine_choices=dict(data.get("machine_choices") or {}),
        recipe_choices=dict(data.get("recipe_choices") or {}),
        effects=effects,
        plot=data.get("plot"),
        style=[str(s) for s in data.get("style") or []],
    )


def load(path: Path) -> Spec:
    try:
        return parse(json.loads(Path(path).read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise RequestError(f"{path} is not valid JSON: {exc}") from exc


# --------------------------------------------------------------------------
# what the player has
# --------------------------------------------------------------------------


def _electric(data: dict) -> bool:
    source = data.get("energy_source")
    return isinstance(source, dict) and source.get("type") == "electric"


def options(role: str, found: environment.Environment | None) -> list[Option]:
    """Every prototype that can fill a role, best first, marked unlocked or not."""
    bonuses = found.bonuses if found is not None else None
    picked: list[Option] = []
    for name, data in sorted(entity_data.raw.items()):
        kind = data.get("type")
        try:
            if role == "belt" and kind == "transport-belt" and data.get("speed"):
                rate = layout.belt_throughput(name)
                detail, rank = f"{rate:g}/s ({rate / 2:g}/s a lane)", rate
            elif role in ("inserter", "long_inserter") and kind == "inserter" and _electric(data):
                reach = layout.inserter_reach(name)
                if reach != (1 if role == "inserter" else 2):
                    continue
                hand = layout.inserter_hand_size(name, bonuses) if bonuses is not None else 1
                rate = layout.inserter_rate(name, hand)
                detail = f"hand {hand}, up to {rate:.3g} items/s chest to chest"
                rank = rate
            elif role == "pole" and kind == "electric-pole":
                w, h = layout.machine_size(name)
                supply = float(data.get("supply_area_distance") or 0)
                wire = float(data.get("maximum_wire_distance") or 0)
                detail = f"{w}x{h}, supply {supply:g}, wire {wire:g}"
                # For rows, a pole that fits an arm line and covers most wins.
                rank = supply + wire / 100 - (10 if (w, h) != (1, 1) else 0)
            else:
                continue
        except layout.LayoutError:
            continue
        unlocked = found.can_build(name) if found is not None else None
        picked.append(Option(name, unlocked, detail, rank))
    picked.sort(key=lambda o: (o.unlocked is False, -o.rank, o.name))
    return picked


# --------------------------------------------------------------------------
# the review
# --------------------------------------------------------------------------


def _kind_of(name: str) -> str | None:
    if name in item_data.raw:
        return "item"
    if name in fluid_data.raw:
        return "fluid"
    return None


def _suggest(name: str, found) -> str:
    candidates = names.find(name.replace("-", " "), kinds=("item", "fluid"), limit=3, environment=found)
    if not candidates:
        return ""
    shown = ", ".join(
        f"{c.name} ({c.titles.get('ru') or c.titles.get('en') or ''})" for c in candidates
    )
    return f"; did you mean {shown}? (`factorio-forge find` searches the player's words)"


def review(spec: Spec, found: environment.Environment | None = None, note: str | None = None) -> Review:
    if found is None and note is None:
        found, note = environment.for_active_profile()
    result = Review(spec, note or "")
    raw = bom._mined_items() | bom._PUMPED_FLUIDS

    if found is not None and found.bonuses is not None:
        result.bonuses = dict(found.bonuses.force)
    elif found is not None:
        result.assumptions.append("the export predates bonuses (companion mod 0.7.0): inserter hands are taken as 1")
    else:
        result.assumptions.append(
            f"no export of the player's game ({note}): nothing below is checked against what is unlocked"
        )

    for item, rate in spec.targets:
        if _kind_of(item) is None:
            result.problems.append(f"target {item!r} is not an item or fluid in the active data{_suggest(item, found)}")
        elif rate <= 0:
            result.problems.append(f"target {item!r} needs a positive rate")
        elif found is not None and not names.is_unlocked(_kind_of(item), item, found, raw):
            result.problems.append(f"target {item!r} is not unlocked in the player's game")
    for item in spec.boundary:
        if _kind_of(item) is None:
            result.problems.append(f"boundary {item!r} is not an item or fluid{_suggest(item, found)}")

    for role in ROLES:
        chosen = spec.tiers.get(role)
        available = options(role, found)
        if chosen is not None:
            match = next((o for o in available if o.name == chosen), None)
            if match is None:
                result.problems.append(f"{role} {chosen!r} is not a prototype that can serve as {role}")
            elif match.unlocked is False:
                result.problems.append(f"{role} {chosen!r} is not unlocked in the player's game")
            result.tiers[role] = chosen
            continue
        usable = [o for o in available if o.unlocked is not False]
        if not usable:
            continue
        if found is None:
            result.questions.append(
                f"Which {role.replace('_', ' ')} does the player have? Without an export every "
                f"one looks available: {', '.join(o.name for o in available[:6])}"
            )
            continue
        best = usable[0]
        result.tiers[role] = best.name
        result.assumptions.append(f"{role}: {best.name} ({best.detail}), the best the player has unlocked")

    for category, machine in spec.machine_choices.items():
        if machine not in entity_data.raw:
            result.problems.append(f"machine {machine!r} for {category!r} is not in the active data")
        elif found is not None and not found.can_build(machine):
            result.problems.append(f"machine {machine!r} for {category!r} is not unlocked in the player's game")

    if not result.problems:
        request = bom.Request(
            targets=tuple(bom.Target(item, rate) for item, rate in spec.targets),
            boundary=frozenset(spec.boundary),
            recipe_choices=spec.recipe_choices,
            machine_choices=spec.machine_choices,
            effects=spec.effects,
            environment=found,
        )
        try:
            result.bill = bom.compute(request)
        except bom.BillOfMaterialsError as exc:
            result.problems.append(f"bill of materials: {exc}")

    if result.bill is not None:
        for ambiguity in result.bill.ambiguities:
            result.assumptions.append(
                f"{ambiguity.kind} for {ambiguity.subject}: {ambiguity.detail} "
                f"(candidates: {', '.join(ambiguity.candidates)})"
            )
        if found is not None and found.bonuses is not None:
            researched = {
                line.recipe: found.bonuses.recipe_productivity[line.recipe]
                for line in result.bill.lines
                if line.recipe in found.bonuses.recipe_productivity
            }
            if researched:
                result.assumptions.append(
                    "researched recipe productivity applied: "
                    + ", ".join(f"{r} +{v:.0%}" for r, v in researched.items())
                )
        if not spec.boundary:
            intermediate = sorted(
                {i for line in result.bill.lines for i in line.inputs if i not in raw and i not in dict(spec.targets)}
            )
            result.questions.append(
                "Nothing is marked as arriving from outside, so the bill goes all the way back to "
                f"{', '.join(sorted(result.bill.raw_materials)) or 'nothing'}. Which of these does the "
                f"player bring in instead (by train, from a bus)? {', '.join(intermediate[:12])}"
            )
        big = [line for line in result.bill.lines if line.machines > 0]
        if big:
            lines = ", ".join(f"{line.machines} x {line.machine} on {line.recipe}" for line in big)
            result.assumptions.append(f"machines: {lines}")

    if spec.plot is None:
        result.questions.append(
            "How much room is there, and what shape (width x height, or a city block of the "
            "player's grid)? Row lengths depend on it."
        )
    if not spec.style:
        result.questions.append(
            "Anything about how it should look -- like one of the player's existing blocks? "
            "`factorio-forge show-style` has what was measured."
        )
    return result
