"""What to build and what to mend, from a diagnosis of a surveyed base.

``diagnose`` finds the leaves of each chain that only building fixes: an item
nobody makes here, makers that work all the time and are too few, drills that
work all the time and are too few, a field with no drills. This module turns
each into an order exactly as ``review`` and ``chain`` take it (targets,
boundary, surface) and computes its bill of materials, so the answer is
"8 more furnaces, here is the bill" rather than "iron is short". With drafts
on, ``chain`` is run on each order and the plan and blueprint written into
``<out>/<item>/``.

What the base already makes beyond what it uses (an item whose flow made more
than it used) is taken as arriving from the base: it goes into the order's
``boundary``, so the order is not expanded back to ore for it. It is only the
surplus, and the report says how much, next to what the order draws on it. The
other shortfalls are in each order's boundary too: they are built by their own
orders, and a bill that made them again would count the same machines twice. So
that those orders really cover what the others draw, they are sized together:
whatever one order's bill takes from another order's item is added to that
order's rate and its bill is made again, until the sizes settle.

The order pins the recipe and the machine the base itself uses for the item
(``recipe_choices`` / ``machine_choices``), so the bill speaks of the machines
the base has, not of whatever ``bom`` would pick by default. Whether the base's
machine can be built from what the player has unlocked is the review's to say.

Whatever needs no building -- no recipe set, no fuel, a disabled machine,
power short by some megawatts, an output with nowhere to go, drills whose
ore is not carried off -- is listed apart as "fix", each with unit numbers
and positions from the snapshot.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .diagnose import Cause, Diagnosis, Shortfall
from .survey import Survey

# Leaves that mean "mend it", not "build more", and what to say about each.
FIX_KINDS = frozenset({"no_power", "no_fuel", "disabled", "output_full_end", "drills_blocked", "no_recipe", "power"})
DRILL_KINDS = frozenset({"too_few_drills", "no_drills", "no_field"})
MAX_ROUNDS = 6  # how often the orders are sized again for what they draw on each other
SURPLUS_SHARE = 0.02  # made must exceed used by this share of made to count as surplus


@dataclass
class BuildItem:
    item: str
    short_per_s: float
    kind: str
    text: str
    more_machines: int | None = None
    machine: str | None = None
    extra_per_s: float = 0.0  # what the other orders' bills draw from this item, added to the rate asked
    arrives: dict[str, float] = field(default_factory=dict)
    alongside: list[str] = field(default_factory=list)
    request: dict | None = None
    bill: dict | None = None
    problems: list[str] = field(default_factory=list)
    draft: dict | None = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Fix:
    kind: str
    text: str
    units: list[int] = field(default_factory=list)
    positions: list[list[float]] = field(default_factory=list)
    numbers: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class BaseReport:
    diagnosis: Diagnosis
    build: list[BuildItem] = field(default_factory=list)
    fix: list[Fix] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = self.diagnosis.to_dict()
        data["build"] = [b.to_dict() for b in self.build]
        data["fix"] = [f.to_dict() for f in self.fix]
        return data

    def text(self) -> str:
        d = self.diagnosis
        lines = [d.verdict, ""]
        if d.causes:
            lines.append("what holds the base back (biggest loss first):")
            for number, cause in enumerate(d.causes, 1):
                lines.append(f" {number}. {cause.text}")
                lines += _tree_lines(cause.children, 1)
            lines.append("")
        if self.build:
            lines.append("build:")
            for b in self.build:
                lines.append(f"  - {b.text}")
                for problem in b.problems:
                    lines.append(f"      problem: {problem}")
                if b.draft:
                    lines.append("      draft: " + (b.draft.get("error") or b.draft.get("plan", "")))
            lines.append("")
        if self.fix:
            lines.append("fix (no building needed):")
            for f in self.fix:
                lines.append(f"  - {f.text}")
            lines.append("")
        if d.good:
            lines.append("works well:")
            for g in d.good:
                made = ", ".join(f"{k} {v:.3g}/s" for k, v in g["made_per_s"].items())
                lines.append(f"  - {g['recipe']}: {g['machines']} x {g['machine']} working {g['working']:.0%}"
                             + (f", making {made}" if made else ""))
            lines.append("")
        lines.append("limits of this reading:")
        lines += [f"  - {limit}" for limit in d.limits]
        return "\n".join(lines)


def _tree_lines(children: list[Cause], depth: int) -> list[str]:
    out: list[str] = []
    for child in children:
        out.append("    " * depth + "-> " + child.text)
        out += _tree_lines(child.children, depth + 1)
    return out


# --------------------------------------------------------------------------
# orders
# --------------------------------------------------------------------------


def surplus(survey: Survey, exclude: frozenset[str] | set[str] = frozenset()) -> dict[str, float]:
    """Items the base makes beyond what it uses, per second: what already arrives from the base."""
    out: dict[str, float] = {}
    for table in (survey.items, survey.fluids):
        for name, flow in table.items():
            extra = flow.made - flow.used
            if name not in exclude and flow.made > 0 and extra > max(1e-6, SURPLUS_SHARE * flow.made):
                out[name] = extra
    return dict(sorted(out.items()))


def _category_of(recipe: str) -> str | None:
    """The crafting category of a recipe in the active data, None when it is not there."""
    try:
        from draftsman.data import recipes

        from .categories import primary_category

        entry = recipes.raw.get(recipe)
        return None if entry is None else primary_category(entry)
    except Exception:  # noqa: BLE001 - no profile: the order just does not pin the machine
        return None


def make_request(item: str, rate: float, survey: Survey, boundary: list[str], *,
                 recipe: str | None = None, machine: str | None = None, category: str | None = None) -> dict:
    """The order as `review` and `chain` take it; with `recipe` and `machine`, the ones the base uses."""
    out: dict = {
        "said": f"build out {item} for the surveyed base",
        "targets": [{"item": item, "per_second": rate}],
        "boundary": sorted({b for b in boundary if b != item}),
        "surface": survey.surface,
    }
    if recipe:
        out["recipe_choices"] = {item: recipe}
        if machine and category:
            out["machine_choices"] = {category: machine}
    return out


def _known_name(name: str) -> bool:
    from . import request

    return request._kind_of(name) is not None


def _bill(request_dict: dict):
    """The bill for an order, the review it came with, and what was wrong; the bill is None when not ready."""
    from . import bom, request

    try:
        spec = request.parse(request_dict)
        reviewed = request.review(spec)
    except (request.RequestError, bom.BillOfMaterialsError) as exc:
        return None, None, [str(exc)]
    if not reviewed.ready:
        return None, reviewed, list(reviewed.problems or ["no bill of materials"])
    bill = reviewed.bill
    return {
        "lines": [{"recipe": line.recipe, "machine": line.machine, "machines": line.machines,
                   "outputs": line.outputs, "inputs": line.inputs, "power_w": line.power}
                  for line in bill.lines],
        "from_outside": dict(bill.raw_materials),
        "power_w": bill.total_power,
    }, reviewed, []


def _line(b: BuildItem) -> str:
    base = f"{b.item}: {b.short_per_s:.3g}/s short"
    if b.extra_per_s > 0:
        base += (f", {b.extra_per_s:.3g}/s more for the other orders that draw on it "
                 f"({b.short_per_s + b.extra_per_s:.3g}/s in all)")
    if b.more_machines is not None and b.machine:
        base += f" -- about {b.more_machines} more {b.machine}"
    if b.bill:
        counts = ", ".join(f"{ln['machines']} x {ln['machine']} on {ln['recipe']}" for ln in b.bill["lines"])
        if counts:
            base += f"; the bill for it: {counts}"
        drawn = []
        for name, need in b.bill["from_outside"].items():
            note = ""
            if name in b.arrives:
                note = f" (the base's surplus: {b.arrives[name]:.3g}/s)"
            elif name in b.alongside:
                note = " (made by the other order, whose size counts this draw)"
            drawn.append(f"{name} {need:.3g}/s{note}")
        if drawn:
            base += "; draws " + ", ".join(drawn)
    elif b.arrives:
        base += "; the base's surplus it can draw on: " + ", ".join(f"{k} {v:.3g}/s" for k, v in b.arrives.items())
    return base


def build_orders(diagnosis: Diagnosis, survey: Survey, *, bill: bool = True,
                 known: Callable[[str], bool] | None = None,
                 compute: Callable[[dict], tuple[dict | None, object, list[str]]] | None = None,
                 category_of: Callable[[str], str | None] | None = None,
                 ) -> list[tuple[BuildItem, object]]:
    """One order per shortfall; with `bill`, its bill of materials too. Returns (order, review) pairs.

    The orders are sized together: what an order's bill draws from another order's item is added to that
    order's rate, and the bills are made again until the sizes stop moving.
    """
    known = known or _known_name
    compute = compute or _bill
    category_of = category_of or _category_of
    entries: list[tuple[BuildItem, Shortfall, dict[str, float]]] = []
    for s in diagnosis.shortfalls:
        item = BuildItem(s.item, s.short_per_s, s.kind, "", more_machines=s.machines_needed, machine=s.maker_machine)
        extra: dict[str, float] = {}
        if s.kind in DRILL_KINDS:
            item.text = _drill_text(s)
        else:
            extra = {k: v for k, v in surplus(survey, {s.item}).items() if known(k)}
            # What the other orders make is theirs: this order takes it as arriving, not as work to repeat.
            item.alongside = [o.item for o in diagnosis.shortfalls
                              if o.item != s.item and o.kind not in DRILL_KINDS and known(o.item)]
            item.arrives = extra
        entries.append((item, s, extra))
    chain_orders = [(i, s, extra) for i, s, extra in entries if s.kind not in DRILL_KINDS]
    reviews: dict[str, object] = {}

    def run(more: dict[str, float]) -> None:
        for item, s, extra in chain_orders:
            item.extra_per_s = more.get(item.item, 0.0)
            category = category_of(s.maker_recipe) if s.maker_recipe else None
            item.request = make_request(
                s.item, s.short_per_s + item.extra_per_s, survey, list(extra) + item.alongside,
                recipe=s.maker_recipe, machine=s.maker_machine, category=category)
            if bill:
                item.bill, reviews[item.item], item.problems = compute(item.request)
                if item.bill:
                    for name, need in item.bill["from_outside"].items():
                        if name in extra and need > extra[name] * 1.001:
                            item.problems.append(
                                f"it needs {need:.3g}/s of {name} from the base, whose surplus is {extra[name]:.3g}/s: "
                                "raise that supply too")

    more: dict[str, float] = {}
    run(more)
    if bill:
        settled = False
        for _ in range(MAX_ROUNDS):
            draw = {item.item: sum(other.bill["from_outside"].get(item.item, 0.0)
                                   for other, _s, _e in chain_orders if other is not item and other.bill)
                    for item, _s, _e in chain_orders}
            if all(abs(draw[k] - more.get(k, 0.0)) <= 1e-6 + 1e-3 * draw[k] for k in draw):
                settled = True
                break
            more = draw
            run(more)
        if not settled:
            for item, _s, _e in chain_orders:
                item.problems.append("the orders draw on each other and their sizes had not settled: "
                                     "read the rates as a floor")
    for item, s, _extra in entries:
        if s.kind not in DRILL_KINDS:
            item.text = _line(item)
            if s.made_elsewhere > 0:
                item.text += (f"; {s.made_elsewhere:.3g}/s of it is already made outside the surveyed machines, "
                              "so bringing more of that is the other way")
    return [(item, reviews.get(item.item)) for item, _s, _e in entries]


def _drill_text(s: Shortfall) -> str:
    if s.kind == "too_few_drills":
        more = f"about {s.machines_needed} more {s.maker_machine or 'drills'}" if s.machines_needed else "more drills"
        return f"{s.item}: {s.short_per_s:.3g}/s short -- {more} on the same field (or a new field)"
    if s.kind == "no_drills":
        return f"{s.item}: {s.short_per_s:.3g}/s short -- put drills on the field the snapshot found, which has none"
    if s.patch:
        p = s.patch
        amount = ("at least " if p.get("truncated") else "") + f"{p.get('amount', 0):.3g}"
        where = (f"nearest patch at ({p['position'][0]:.0f}, {p['position'][1]:.0f}), "
                 f"{p.get('distance', 0):.0f} tiles from the base, {amount} in it")
        enemies = {k: v for k, v in (p.get("enemies") or {}).items() if v > 0}
        if enemies:
            counted = ", ".join(f"{v} {k}" for k, v in sorted(enemies.items()))
            return f"{s.item}: {s.short_per_s:.3g}/s short -- {where}; {counted} near it: clear them, then mine it"
        return f"{s.item}: {s.short_per_s:.3g}/s short -- {where}: put drills on it and bring it to the base"
    return f"{s.item}: {s.short_per_s:.3g}/s short -- no field of it in the snapshot: find one and connect it"


# --------------------------------------------------------------------------
# drafts
# --------------------------------------------------------------------------


def _folder_name(item: str, taken: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", item).strip("-.") or "item"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    taken.add(name)
    return name


def declare_fluid_sources(plan: dict, stages: list, boundary=()) -> int:
    """Declare as plan sources the fluids that come into the chain from outside; returns how many.

    The chain declares its belt feeds itself; a pipe in port that takes a fluid no other stage makes (the
    boundary's fluids count as made outside) is left to the player there. Here each such port becomes a source
    with the rate the stage draws through it, so a measuring run feeds it. A fluid the chain makes itself is never
    declared, even when its pipe was left out: that port stays unfed and the measurement shows the shortage.
    """
    from . import chain

    made = chain._producers(stages, set(boundary))
    reached = set()
    for conn in plan.get("connections") or []:
        for end in ([conn.get("to")] if isinstance(conn.get("to"), dict) else conn.get("to") or []):
            if isinstance(end, dict):
                reached.add((end.get("block"), end.get("port")))
    sources = plan.setdefault("sources", [])
    added = 0
    for stage in stages:
        for port in stage.ports:
            if port.io != "in" or port.kind != "pipe" or (stage.index, port.index) in reached:
                continue
            for fluid in sorted(port.items):
                if made.get(fluid) not in (None, stage):
                    continue
                rate = stage.line.inputs.get(fluid, 0.0) * chain._share(stage, port, fluid, "pipe")
                sources.append({"id": f"{fluid}-in-{stage.index}-{port.index}", "block": stage.index,
                                "port": port.index, "fluid": fluid, "rate": math.ceil(rate * 1000) / 1000})
                added += 1
    return added


def write_draft(reviewed, folder: Path) -> dict:
    """Run `chain` on a reviewed order and write plan.json and the blueprint into `folder`."""
    from . import chain

    try:
        drafted, attempts = chain.build_with_retries(reviewed.spec, reviewed=reviewed)
        declare_fluid_sources(drafted.plan, drafted.stages, reviewed.spec.boundary)
        folder.mkdir(parents=True, exist_ok=True)
        plan_path = folder / "plan.json"
        plan_path.write_text(json.dumps(drafted.plan, indent=1, ensure_ascii=False), encoding="utf-8")
        out = {"plan": str(plan_path), "blocks": len(drafted.plan["blocks"]),
               "issues": list(drafted.issues), "blueprint": None, "problems": None}
        best = chain.best_attempt(attempts)
        if best is not None:
            slug = re.sub(r"[^A-Za-z0-9._-]+", "-", best.result.label).strip("-.") or "layout"
            bp = folder / f"{slug}.txt"
            bp.write_text(best.result.blueprint.to_string(), encoding="utf-8")
            (folder / f"{slug}.report.json").write_text(
                json.dumps(best.result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
            out["blueprint"] = str(bp)
            out["problems"] = len(best.problems)
        return out
    except Exception as exc:  # noqa: BLE001 - one failed draft must not lose the report
        return {"error": f"no draft: {type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------
# what to mend
# --------------------------------------------------------------------------


def fixes(diagnosis: Diagnosis, survey: Survey) -> list[Fix]:
    where = {m.unit: [m.position[0], m.position[1]] for m in survey.machines}
    where.update({m.unit: [m.position[0], m.position[1]] for m in survey.miners})
    out: list[Fix] = []
    seen: set[tuple] = set()
    nodes = list(diagnosis.standalone)
    for root in diagnosis.causes:
        nodes += [c for c in root.walk() if c.kind in FIX_KINDS and c.kind != "power"]
    for c in nodes:
        key = (c.kind, tuple(c.units), c.text)
        if key in seen:
            continue
        seen.add(key)
        out.append(Fix(c.kind, c.text, list(c.units), [where[u] for u in c.units if u in where], dict(c.numbers)))
    return out


# --------------------------------------------------------------------------
# the whole report
# --------------------------------------------------------------------------


def report(diagnosis: Diagnosis, survey: Survey, *, out_dir: Path | None = None, drafts: bool = False,
           bill: bool = True, known: Callable[[str], bool] | None = None, compute=None) -> BaseReport:
    """The diagnosis with what to build (orders, bills, optional drafts under `out_dir`) and what to fix."""
    result = BaseReport(diagnosis)
    taken: set[str] = set()
    for item, reviewed in build_orders(diagnosis, survey, bill=bill, known=known, compute=compute):
        if drafts and out_dir is not None and reviewed is not None and item.bill and item.bill["lines"]:
            item.draft = write_draft(reviewed, Path(out_dir) / _folder_name(item.item, taken))
        result.build.append(item)
    result.fix = fixes(diagnosis, survey)
    return result
