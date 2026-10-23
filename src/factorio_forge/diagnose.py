"""Why a base makes less than it could: from the snapshot, down the chain to the first cause.

The input is a ``Survey`` (see ``survey.py``): every machine's state over a
window, what was missing while it waited, the drills and their fields, the
flows and the power networks. The answer is a ``Diagnosis``:

* ``good`` -- recipe groups whose machines work at least 90% of the window;
* ``causes`` -- the groups that make less than their machines could, ranked by
  the machine time they explain (see ``IDLE_BASIS``), each a tree. A machine that waits for an ingredient points at
  whoever makes it; that group is looked at the same way, and so on until the
  first cause: nobody here makes it, its makers are too few (they work all the
  time yet the users starve), the drills hold their ore (the belt backs up),
  the field is running out, there is no field, there is not enough power, no
  fuel, a disabled machine, a product with nowhere to go;
* ``shortfalls`` -- the leaves that only building more can fix, summed per item,
  which is what ``buildout`` turns into orders for ``chain``.

Machines that make buildings are not demand: their waiting and their full output
raise no shortfall and add nothing to what their ingredients are wanted at. Science
packs that sit at a full output are judged by what the labs do with them, when the
survey has the labs.

Lost output of a machine is (1 - share of the window it worked) times its full
rate: the recipe's products times crafting speed over the recipe's time, with
the built-in productivity of the machine counted as ``bom`` counts it. A number
is either read from the snapshot or computed from the recipes and the speeds in
it; what is not known is said so, not filled in. What the snapshot cannot see
(belts, trains, robots, hand-delivered items, modules, beacons) is listed in
``limits``.

Every recipe group and every short item is expanded once: a second visit to
the same group or item is a one-line reference to where it was shown, so the
report stays as long as the base has groups, not as long as it has paths. What
is short of an item is summed over everyone who waits for it and the makers'
headroom is taken off once, at the item.

Recipe data comes in through ``RecipeBook`` so the logic can be tested without
a game profile; ``RecipeBook.from_active_data`` reads the active profile.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from . import bom
from .survey import WORKING, Machine, Miner, Survey

EPS = 1e-9
GOOD_SHARE = 0.9  # a group working this share of the window or more is "good"
MIN_STATE_SHARE = 0.05  # a state below this part of the group's loss is not followed
NEAR_EMPTY_HOURS = 24.0  # a field with less than this much left at the current rate is running out
LAB_FED = 0.5  # labs working at least this share of the window are fed
LAB_STARVED = 0.1  # labs missing packs this share of the window are short of packs
PACK_BACKED_UP = 0.3  # a pack whose makers wait with a full output this much of the time is not the short one
IDLE_BASIS = ("machine time idle for a shortage (not for a full output), in machines, credited to the faults "
              "it waits for: each group counts once, in the chain that shows it, divided among what it waited "
              "for; groups that only hold a full output add nothing")
OVER_SHARE = 0.02  # made above possible by more than this share: the possible rate is only a lower bound
MAX_DEPTH = 90  # a guard only: the path and the once-only expansion already end every walk

INGREDIENT_STATES = frozenset({
    "no_ingredients", "item_ingredient_shortage", "fluid_ingredient_shortage",
    "waiting_for_source_items", "missing_required_fluid", "no_input_fluid", "low_input_fluid",
    "missing_science_packs",
})
OUTPUT_FULL_STATES = frozenset({
    "output_full", "full_output", "waiting_for_space_in_destination", "waiting_for_space",
})
POWER_STATES = frozenset({"no_power", "low_power", "recharging_after_power_outage"})
DISABLED_STATES = frozenset({"disabled", "disabled_by_control_behavior", "disabled_by_script"})
DEPLETED_STATES = frozenset({"no_minable_resources"})

KNOWN_STATES = (INGREDIENT_STATES | OUTPUT_FULL_STATES | POWER_STATES | DISABLED_STATES | DEPLETED_STATES
                | {"no_fuel", "no_recipe"})

# Leaves that only building (more machines, drills, a new source) can fix.
SHORT_KINDS = frozenset({"too_few", "nobody_makes", "too_few_drills", "no_drills", "no_field"})


# --------------------------------------------------------------------------
# recipes
# --------------------------------------------------------------------------


@dataclass
class RecipeBook:
    """The recipe data the diagnosis reads, so it does not depend on one profile.

    ``recipes`` maps a name to a recipe prototype (``ingredients``, ``results``,
    ``energy_required``). ``built_in`` is a machine's built-in productivity,
    ``resource_items`` the items a resource entity yields, ``is_mined`` whether an item is mined
    at all, ``is_building`` whether an item is a building (it places an entity) and ``lab_inputs``
    the items labs consume. ``index`` maps an
    item to the recipes that may make it (default: every recipe naming it).
    """

    recipes: dict[str, dict]
    built_in: Callable[[str], float] = lambda machine: 0.0
    resource_items: Callable[[str], list[str]] = lambda resource: [resource]
    is_mined: Callable[[str], bool] = lambda item: False
    index: dict[str, list[str]] | None = None
    is_building: Callable[[str], bool] = lambda item: False
    lab_inputs: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.index is None:
            index: dict[str, list[str]] = defaultdict(list)
            for name, recipe in self.recipes.items():
                for result in recipe.get("results", []):
                    index[result["name"]].append(name)
            self.index = dict(index)

    @classmethod
    def from_active_data(cls) -> RecipeBook:
        from draftsman.data import entities as entity_data
        from draftsman.data import items as item_data
        from draftsman.data import recipes as recipe_data
        from draftsman.data import resources as resource_data

        def yields(resource: str) -> list[str]:
            minable = (resource_data.raw.get(resource) or {}).get("minable") or {}
            names = [r["name"] for r in minable.get("results", []) if "name" in r]
            if not names and minable.get("result"):
                names = [minable["result"]]
            return names or [resource]

        def places(item: str) -> bool:
            return bool((item_data.raw.get(item) or {}).get("place_result"))

        labs = frozenset(name for proto in entity_data.raw.values() if proto.get("type") == "lab"
                         for name in proto.get("inputs", []))
        mined = bom._mined_items()
        return cls(dict(recipe_data.raw), bom.built_in_productivity, yields, lambda item: item in mined,
                   bom._build_result_index(), places, labs)

    def makers_of(self, item: str) -> list[str]:
        return list(self.index.get(item, [])) if self.index else []


def _energy(recipe: dict) -> float:
    return float(recipe.get("energy_required", 0.5) or 0.5)


# --------------------------------------------------------------------------
# the answer
# --------------------------------------------------------------------------


@dataclass
class Cause:
    """One step of a chain: what is wrong here, with numbers, and what lies under it."""

    kind: str
    text: str
    item: str | None = None
    recipe: str | None = None
    lost_per_s: float = 0.0
    short_per_s: float = 0.0
    share: float = 0.0
    units: list[int] = field(default_factory=list)
    numbers: dict[str, object] = field(default_factory=dict)
    children: list[Cause] = field(default_factory=list)

    def walk(self) -> Iterable[Cause]:
        yield self
        for child in self.children:
            yield from child.walk()

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "text": self.text, "item": self.item, "recipe": self.recipe,
            "lost_per_s": self.lost_per_s, "short_per_s": self.short_per_s, "share": self.share,
            "units": self.units, "numbers": self.numbers,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class Shortfall:
    """An item the base is short of that only building fixes, summed over everyone who starves."""

    item: str
    short_per_s: float
    kind: str
    consumers: dict[str, float] = field(default_factory=dict)
    maker_recipe: str | None = None
    maker_machine: str | None = None
    per_machine: float | None = None
    machines_needed: int | None = None
    made_elsewhere: float = 0.0
    note: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Diagnosis:
    surface: str
    window_s: float
    machines: int
    causes: list[Cause] = field(default_factory=list)
    good: list[dict] = field(default_factory=list)
    shortfalls: list[Shortfall] = field(default_factory=list)
    standalone: list[Cause] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)
    targets: dict[str, float] = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        if not self.machines:
            return "no machines in the snapshot: nothing to judge"
        gaps = [c for c in self.causes if c.kind == "target_gap"]
        parts = [f"{len(self.good)} recipe group(s) work well"]
        if self.causes:
            top = self.causes[0]
            parts.append(f"{len(self.causes)} problem(s), the biggest: {top.text}")
        else:
            parts.append("no group makes noticeably less than it could")
        if gaps:
            parts.append(f"{len(gaps)} target(s) short")
        return "; ".join(parts)

    def to_dict(self) -> dict:
        return {
            "surface": self.surface, "window_s": self.window_s, "machines": self.machines,
            "verdict": self.verdict, "targets": self.targets,
            "causes": [c.to_dict() for c in self.causes],
            "good": self.good,
            "shortfalls": [s.to_dict() for s in self.shortfalls],
            "standalone": [c.to_dict() for c in self.standalone],
            "limits": self.limits,
        }


# --------------------------------------------------------------------------
# machines grouped by recipe
# --------------------------------------------------------------------------


def _n(value: float) -> str:
    return f"{value:.3g}"


def _units_text(units: list[int], most: int = 6) -> str:
    if not units:
        return ""
    shown = ", ".join(str(u) for u in units[:most])
    return f"units {shown}" + (f" (+{len(units) - most} more)" if len(units) > most else "")


@dataclass
class _Group:
    recipe: str
    entry: dict
    machines: list[Machine]
    main: str
    full: dict[str, float] = field(default_factory=dict)  # product -> per second at full work
    made: dict[str, float] = field(default_factory=dict)  # product -> per second, finished in the window
    lost: dict[str, float] = field(default_factory=dict)  # product -> per second lost to idle time
    ingredient_full: dict[str, float] = field(default_factory=dict)
    working: float = 0.0
    state_lost: dict[str, float] = field(default_factory=dict)  # state -> lost per second of the main product
    state_units: dict[str, list[int]] = field(default_factory=dict)
    state_share: dict[str, float] = field(default_factory=dict)
    missing_short: dict[str, float] = field(default_factory=dict)  # ingredient -> per second it could not take
    missing_lost: dict[str, float] = field(default_factory=dict)
    missing_units: dict[str, list[int]] = field(default_factory=dict)
    missing_share: dict[str, float] = field(default_factory=dict)
    idle_machines: float = 0.0  # machines' worth of time idle for a shortage, not for a full output
    blocked_machines: float = 0.0  # machines' worth of time with a full output
    building: bool = False  # the machines make buildings: not demand

    @property
    def units(self) -> list[int]:
        return [m.unit for m in self.machines]

    @property
    def machine_name(self) -> str:
        names = defaultdict(int)
        for m in self.machines:
            names[m.name] += 1
        return max(names, key=lambda n: (names[n], n))

    @property
    def is_good(self) -> bool:
        return self.working >= GOOD_SHARE - EPS

    def per_machine(self, item: str) -> float:
        return self.full.get(item, 0.0) / len(self.machines)


def _group(recipe: str, entry: dict, machines: list[Machine], book: RecipeBook) -> _Group:
    results = entry.get("results", [])
    main = results[0]["name"] if results else recipe
    g = _Group(recipe, entry, machines, main)
    total_weight = 0.0
    working_sum = 0.0
    for m in machines:
        crafts_full = (m.crafting_speed or 0.0) / _energy(entry)
        productivity = book.built_in(m.name)
        weight = m.crafting_speed if m.crafting_speed > 0 else 1.0
        if m.states:
            working = min(max(m.working_share, 0.0), 1.0)
        else:
            working = min(m.crafts_per_s / crafts_full, 1.0) if crafts_full > 0 else 0.0
        total_weight += weight
        working_sum += weight * working
        full_output = min(sum(m.states.get(st, 0.0) for st in OUTPUT_FULL_STATES), 1.0 - working)
        g.blocked_machines += max(0.0, full_output)
        g.idle_machines += max(0.0, 1.0 - working - full_output)
        full_main = 0.0
        for result in results:
            amount = bom._expected_amount(result) * bom._productivity_multiplier(entry, result, productivity)
            full = amount * crafts_full
            name = result["name"]
            g.full[name] = g.full.get(name, 0.0) + full
            g.made[name] = g.made.get(name, 0.0) + m.crafts_per_s * amount
            g.lost[name] = g.lost.get(name, 0.0) + max(0.0, 1.0 - working) * full
            if name == main:
                full_main = full
        for ingredient in entry.get("ingredients", []):
            name = ingredient["name"]
            g.ingredient_full[name] = g.ingredient_full.get(name, 0.0) + float(ingredient.get("amount", 1)) * crafts_full
        for state, share in m.states.items():
            if state == WORKING or share <= 0:
                continue
            g.state_lost[state] = g.state_lost.get(state, 0.0) + share * full_main
            g.state_share[state] = g.state_share.get(state, 0.0) + share * weight
            if share >= MIN_STATE_SHARE:
                g.state_units.setdefault(state, []).append(m.unit)
        for item, share in m.missing.items():
            if share <= 0:
                continue
            per_craft = next((float(i.get("amount", 1)) for i in entry.get("ingredients", []) if i["name"] == item), 1.0)
            g.missing_short[item] = g.missing_short.get(item, 0.0) + share * per_craft * crafts_full
            g.missing_lost[item] = g.missing_lost.get(item, 0.0) + share * full_main
            g.missing_share[item] = g.missing_share.get(item, 0.0) + share * weight
            if share >= MIN_STATE_SHARE:
                g.missing_units.setdefault(item, []).append(m.unit)
    g.working = working_sum / total_weight if total_weight else 0.0
    for state in g.state_share:
        g.state_share[state] /= total_weight or 1.0
    for item in g.missing_share:
        g.missing_share[item] /= total_weight or 1.0
    return g


# --------------------------------------------------------------------------
# the walk down the chain
# --------------------------------------------------------------------------


class _Walk:
    def __init__(self, survey: Survey, book: RecipeBook) -> None:
        self.survey = survey
        self.book = book
        self.groups: dict[str, _Group] = {}
        self.skipped: dict[str, int] = {}
        by_recipe: dict[str, list[Machine]] = defaultdict(list)
        self.recipeless: list[Machine] = []
        for m in survey.machines:
            if not m.recipe:
                self.recipeless.append(m)
            elif book.recipes.get(m.recipe) is None:
                self.skipped[m.recipe] = self.skipped.get(m.recipe, 0) + 1
            else:
                by_recipe[m.recipe].append(m)
        for recipe, machines in by_recipe.items():
            self.groups[recipe] = _group(recipe, book.recipes[recipe], machines, book)
        for g in self.groups.values():
            g.building = bool(book.is_building(g.main))
        # A building that a machine outside the mall takes as an ingredient (a belt into a science pack) is
        # made for a use: its makers are ordinary producers, not part of the mall.
        changed = True
        while changed:
            changed = False
            for g in self.groups.values():
                if g.building and any(h is not g and not h.building and set(g.full) & set(h.ingredient_full)
                                      for h in self.groups.values()):
                    g.building = False
                    changed = True
        self._made_by: dict[str, list[_Group]] = defaultdict(list)
        for g in self.groups.values():
            for item in g.full:
                self._made_by[item].append(g)
        # who waits for whom, kept across passes: it says which groups are explained by which
        self.edges: dict[str, set[str]] = defaultdict(set)
        # who takes whose output: a full output is the maker's symptom, not what explains the user's idle time
        self.out_edges: dict[str, set[str]] = defaultdict(set)
        self.science_seen = False
        self.reset()

    def reset(self) -> None:
        """Forget what was expanded, so the next pass builds the trees afresh (the edges stay)."""
        self.expanded: set[str] = set()
        self.traced: set[str] = set()
        self.demand: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.slots: dict[str, tuple[list[Cause], list[Cause]]] = {}

    # -- helpers ---------------------------------------------------------

    def makers(self, item: str) -> list[_Group]:
        return list(self._made_by.get(item, []))

    def users(self, products: Iterable[str], besides: str) -> list[_Group]:
        wanted = set(products)
        return [g for g in self.groups.values()
                if g.recipe != besides and not g.building and wanted & set(g.ingredient_full)]

    def building_users(self, products: Iterable[str], besides: str) -> list[_Group]:
        wanted = set(products)
        return [g for g in self.groups.values()
                if g.recipe != besides and g.building and wanted & set(g.ingredient_full)]

    def drills_of(self, item: str) -> list[Miner]:
        return [m for m in self.survey.miners
                if m.resource and item in self.book.resource_items(m.resource)]

    def fields_of(self, item: str):
        return [f for f in self.survey.fields if item in self.book.resource_items(f.resource)]

    def depends(self, recipe: str, other: str) -> bool:
        """Whether `recipe`'s ingredients lead, through the groups of the snapshot, back to `other`."""
        seen = {recipe}
        stack = [recipe]
        while stack:
            group = self.groups.get(stack.pop())
            if group is None:
                continue
            for item in group.ingredient_full:
                for maker in self._made_by.get(item, []):
                    if maker.recipe == other:
                        return True
                    if maker.recipe not in seen:
                        seen.add(maker.recipe)
                        stack.append(maker.recipe)
        return False

    def ref_node(self, maker: str, consumer: str) -> Cause:
        """A group met again on the way down: a loop only if the maker itself needs what the consumer makes."""
        if self.depends(maker, consumer):
            return Cause("cycle", f"{maker}: the recipes feed each other here; not followed again", recipe=maker)
        return Cause("seen_above", f"{maker}: already shown above", recipe=maker)

    # -- nodes -----------------------------------------------------------

    def group_node(self, g: _Group, path: frozenset[str], depth: int) -> Cause:
        if g.recipe in self.expanded:
            return Cause("seen_above", f"{g.recipe}: already shown above", recipe=g.recipe)
        self.expanded.add(g.recipe)
        lost = g.lost.get(g.main, 0.0)
        could = g.full.get(g.main, 0.0)
        made = g.made.get(g.main, 0.0)
        over = could > EPS and made > could * (1.0 + OVER_SHARE)
        possible = (f"above the {_n(could)}/s counted as possible, which is a lower bound: modules and "
                    "researched productivity are not counted" if over else f"of {_n(could)}/s possible")
        node = Cause(
            "low_output",
            f"{g.recipe}: {_n(made)}/s {g.main} made, {possible} "
            f"({len(g.machines)} x {g.machine_name}, working {g.working:.0%} of the time)",
            item=g.main, recipe=g.recipe, lost_per_s=lost, share=1.0 - g.working, units=g.units,
            numbers={"made_per_s": made, "possible_per_s": could, "machines": len(g.machines),
                     "machine": g.machine_name, "working": g.working,
                     "possible_is_lower_bound": over,
                     "idle_machines": g.idle_machines, "blocked_machines": g.blocked_machines,
                     "idle": dict(sorted(g.state_share.items(), key=lambda kv: -kv[1]))},
        )
        if depth >= MAX_DEPTH:
            return node
        inner = path | {g.recipe}
        handled: set[str] = set()
        waited = False
        for state, state_lost in sorted(g.state_lost.items(), key=lambda kv: -kv[1]):
            if lost <= EPS or state_lost < MIN_STATE_SHARE * lost or state in handled:
                continue
            if state in INGREDIENT_STATES or (g.missing_short and state not in KNOWN_STATES):
                # a state the list does not know, but the snapshot says what was short: follow that
                if not waited:
                    waited = True
                    node.children += self.waiting_nodes(g, inner, depth + 1)
            elif state in OUTPUT_FULL_STATES:
                node.children.append(self.output_node(g, state, inner, depth + 1))
            elif state in POWER_STATES:
                handled |= POWER_STATES
                units = sorted({u for s in POWER_STATES for u in g.state_units.get(s, [])})
                share = sum(g.state_share.get(s, 0.0) for s in POWER_STATES)
                node.children.append(self.power_node(units, share, state_lost + sum(
                    g.state_lost.get(s, 0.0) for s in POWER_STATES if s != state)))
            elif state == "no_fuel":
                node.children.append(Cause(
                    "no_fuel", f"{g.recipe}: out of fuel {g.state_share[state]:.0%} of the time "
                    f"({_units_text(g.state_units.get(state, []))})",
                    item=g.main, recipe=g.recipe, share=g.state_share[state], units=g.state_units.get(state, []),
                    numbers={"lost_per_s": state_lost}))
            elif state in DISABLED_STATES:
                node.children.append(Cause(
                    "disabled", f"{g.recipe}: switched off by a circuit or script {g.state_share[state]:.0%} of the time "
                    f"({_units_text(g.state_units.get(state, []))})",
                    item=g.main, recipe=g.recipe, share=g.state_share[state], units=g.state_units.get(state, []),
                    numbers={"state": state, "lost_per_s": state_lost}))
            else:
                node.children.append(Cause(
                    "other", f"{g.recipe}: state '{state}' {g.state_share[state]:.0%} of the time; "
                    "the snapshot does not say more", item=g.main, recipe=g.recipe,
                    share=g.state_share[state], units=g.state_units.get(state, []),
                    numbers={"state": state, "lost_per_s": state_lost}))
        if (not waited and lost > EPS and g.missing_lost
                and sum(g.missing_lost.values()) >= MIN_STATE_SHARE * lost):
            node.children += self.waiting_nodes(g, inner, depth + 1)
        return node

    def waiting_nodes(self, g: _Group, path: frozenset[str], depth: int) -> list[Cause]:
        lost_total = g.lost.get(g.main, 0.0)
        nodes: list[Cause] = []
        for item, short in sorted(g.missing_short.items(), key=lambda kv: -g.missing_lost[kv[0]]):
            lost = g.missing_lost[item]
            if lost < MIN_STATE_SHARE * lost_total:
                continue
            node = Cause(
                "waiting",
                f"{g.recipe} waits for {item}: short {_n(short)}/s, {g.missing_share[item]:.0%} of the time "
                f"({_n(lost)}/s of {g.main} lost)",
                item=item, recipe=g.recipe, lost_per_s=lost, short_per_s=short, share=g.missing_share[item],
                units=g.missing_units.get(item, []), numbers={"consumer": g.recipe},
            )
            node.children = self.trace_item(item, short, g.recipe, path, depth + 1)
            nodes.append(node)
        if not nodes:
            unknown = g.state_lost.keys() & INGREDIENT_STATES
            share = sum(g.state_share.get(s, 0.0) for s in unknown)
            nodes.append(Cause(
                "waiting", f"{g.recipe} waits for ingredients {share:.0%} of the time; the snapshot does not say which",
                recipe=g.recipe, item=g.main, share=share, lost_per_s=sum(g.state_lost[s] for s in unknown),
                units=sorted({u for s in unknown for u in g.state_units.get(s, [])}),
            ))
        return nodes

    def output_node(self, g: _Group, state: str, path: frozenset[str], depth: int) -> Cause:
        share = g.state_share.get(state, 0.0)
        lost = g.state_lost.get(state, 0.0)
        units = g.state_units.get(state, [])
        users = self.users(g.full, g.recipe)
        outside = sum(self.survey.flow(p).used for p in g.full)
        numbers = {"lost_per_s": lost, "used_outside_per_s": outside}
        if not users:
            packs = sorted(set(g.full) & set(self.book.lab_inputs))
            if packs:
                return self.science_node(g, packs, share, lost, units, numbers)
            mall = self.building_users(g.full, g.recipe)
            if mall:
                return Cause(
                    "backed_up",
                    f"{g.recipe}: the output is full {share:.0%} of the time; the only machines that use "
                    f"{', '.join(sorted(g.full))} make buildings ({', '.join(sorted(u.recipe for u in mall))}) "
                    f"and wait when what they stock is full: normal, not a shortage ({_units_text(units)})",
                    item=g.main, recipe=g.recipe, lost_per_s=lost, share=share, units=units,
                    numbers={**numbers, "benign": True})
            where = (f"; {_n(outside)}/s of it is used somewhere outside the snapshot, "
                     "so the way out is full or blocked" if outside > EPS else "")
            return Cause(
                "output_full_end",
                f"{g.recipe}: the output is full {share:.0%} of the time and no machine here uses "
                f"{', '.join(sorted(g.full))}: nowhere for it to go{where} ({_units_text(units)})",
                item=g.main, recipe=g.recipe, lost_per_s=lost, share=share, units=units, numbers=numbers)
        low = [u for u in users if not u.is_good]
        node = Cause(
            "output_full",
            f"{g.recipe}: the output is full {share:.0%} of the time ({_units_text(units)}); "
            f"its users {', '.join(sorted(u.recipe for u in users))} do not take it all",
            item=g.main, recipe=g.recipe, lost_per_s=lost, share=share, units=units, numbers=numbers)
        for user in low:
            self.out_edges[g.recipe].add(user.recipe)
            if user.recipe in path:
                node.children.append(self.ref_node(g.recipe, user.recipe))
            elif depth < MAX_DEPTH:
                node.children.append(self.group_node(user, path, depth + 1))
        if not low:
            node.kind = "backed_up"
            node.text = (f"{g.recipe}: the output is full {share:.0%} of the time because its users "
                         f"({', '.join(sorted(u.recipe for u in users))}) already work "
                         f"{min(u.working for u in users):.0%} or more and take all they can: "
                         f"it is made faster than it is used ({_units_text(units)})")
        return node

    def lab_stats(self) -> dict:
        labs = self.survey.labs
        n = len(labs)
        states: dict[str, float] = defaultdict(float)
        for lab in labs:
            for state, share in lab.states.items():
                states[state] += share / n
        return {"labs": n, "working": states.get(WORKING, 0.0),
                "missing": states.get("missing_science_packs", 0.0),
                "no_research": states.get("no_research_in_progress", 0.0),
                "states": dict(sorted(states.items(), key=lambda kv: -kv[1]))}

    def science_node(self, g: _Group, packs: list[str], share: float, lost: float, units: list[int],
                     numbers: dict) -> Cause:
        """Science packs at a full output: whether that is a fault depends on what the labs do with them."""
        self.science_seen = True
        head = (f"{g.recipe}: the output is full {share:.0%} of the time ({_units_text(units)}); "
                f"labs use {', '.join(packs)}")
        common = dict(item=g.main, recipe=g.recipe, lost_per_s=lost, share=share, units=units)
        if not self.survey.labs:
            return Cause("labs_unknown",
                         head + ", and the survey has no labs (an older survey file): whether research takes "
                         "the packs is not known, so this is not counted as a cause",
                         numbers={**numbers, "labs_surveyed": False, "benign": True}, **common)
        stats = self.lab_stats()
        numbers = {**numbers, "labs_surveyed": True, "lab_working": stats["working"],
                   "lab_missing_packs": stats["missing"], "lab_no_research": stats["no_research"],
                   "benign": True}
        if stats["no_research"] >= LAB_FED:
            return Cause("no_research",
                         head + f"; no research is in progress (the {stats['labs']} labs have none "
                         f"{stats['no_research']:.0%} of the time), so the packs pile up: queue a research",
                         numbers={**numbers, "benign": False}, **common)
        if stats["missing"] >= LAB_STARVED:
            return Cause("backed_up",
                         head + f"; the labs wait for packs {stats['missing']:.0%} of the time, but not for "
                         "this one, its makers hold a full output: what the labs lack is shown under the labs",
                         numbers=numbers, **common)
        if stats["working"] >= LAB_FED:
            return Cause("backed_up",
                         head + f"; the {stats['labs']} labs work {stats['working']:.0%} of the time and are "
                         "fed: research takes what it needs, so a full output here is normal",
                         numbers=numbers, **common)
        idle = ", ".join(f"{k} {v:.0%}" for k, v in list(stats["states"].items())[:4])
        return Cause("backed_up",
                     head + f"; the labs work only {stats['working']:.0%} of the time ({idle}), not for lack "
                     "of packs: a full output here is not a shortage",
                     numbers=numbers, **common)

    def labs_nodes(self) -> list[Cause]:
        """Labs short of packs: the packs not held back at their makers are the real shortfall."""
        labs = self.survey.labs
        if not labs:
            return []
        stats = self.lab_stats()
        if stats["missing"] < LAB_STARVED or stats["no_research"] >= LAB_FED:
            return []
        working = stats["working"]
        backed_up: list[str] = []
        candidates: list[tuple[str, float]] = []
        for item in sorted(self.book.lab_inputs):
            makers = self.makers(item)
            flow = self.survey.flow(item)
            if not makers and flow.made <= EPS and flow.used <= EPS:
                continue  # a pack this base does not deal with
            count = sum(len(g.machines) for g in makers)
            if makers and sum(g.blocked_machines for g in makers) / count >= PACK_BACKED_UP:
                backed_up.append(item)
                continue
            wanted = max(flow.used, flow.made)
            # Labs would take more by about the share of time they wait, but never more than twice what they take now:
            # with labs that hardly work at all the ratio says nothing.
            candidates.append((item, wanted * min(stats["missing"] / max(working, 0.05), 1.0)))
        sure = len(candidates) == 1
        node = Cause(
            "waiting",
            f"labs wait for science packs {stats['missing']:.0%} of the time ({stats['labs']} labs, working "
            f"{working:.0%})" + ("" if candidates else
                                 ": every pack the base makes is held at a full output by its makers, so "
                                 "the way from the makers to the labs (belts, not in the snapshot) is the limit"),
            share=stats["missing"], units=[lab.unit for lab in labs],
            numbers={"consumer": "labs", "labs": stats["labs"], "working": working,
                     "missing_packs": stats["missing"], "backed_up_packs": backed_up,
                     "candidates": [c for c, _ in candidates]})
        for item, short in candidates:
            if not sure:
                # The survey does not say which pack is missing: with several candidates none is ordered.
                node.children.append(Cause(
                    "waiting",
                    f"labs may wait for {item} (its makers are not held back by a full output; the survey does not "
                    "say which pack the labs miss, so nothing is counted as short)",
                    item=item, numbers={"consumer": "labs", "guess": True}))
                continue
            child = Cause("waiting",
                          f"labs may wait for {item} (the only pack whose makers are not held back by a full output; "
                          f"an estimate): about {_n(short)}/s more would feed them"
                          if short > EPS else
                          f"labs may wait for {item} (its makers are not held back by a full output)",
                          item=item, short_per_s=short, numbers={"consumer": "labs", "guess": True})
            child.children = self.trace_item(item, short, "labs", frozenset(), 2)
            node.children.append(child)
        return [node]

    def power_node(self, units: list[int], share: float, lost: float) -> Cause:
        short = [p for p in self.survey.power if p.satisfaction < 0.999 or _power_gap(p) > 0]
        if not short:
            return Cause(
                "no_power",
                f"machines report no power {share:.0%} of the time, yet no network in the snapshot is short: "
                f"they are probably not connected to a pole ({_units_text(units)})",
                share=share, units=units, numbers={"lost_per_s": lost})
        text, numbers = _power_text(short)
        numbers["lost_per_s"] = lost
        if len(short) > 1:
            text += "; which network these machines are on is not in the snapshot"
        where = f"; {_units_text(units)}" if units else ""
        return Cause("power", f"{text} ({share:.0%} of the machine time{where})",
                     share=share, units=units, numbers=numbers)

    # -- item: who makes it, and why not enough ----------------------------

    def trace_item(self, item: str, short: float, consumer: str, path: frozenset[str], depth: int) -> list[Cause]:
        """What lies under an item some consumer waits for.

        An item is expanded once; later waiters only add to its demand, which `finalize` settles.
        """
        self.demand[item][consumer] += short
        makers = self.makers(item)
        for g in makers:
            if not g.is_good and not g.building:
                self.edges[consumer].add(g.recipe)
        if item in self.traced:
            return [Cause("seen_above", f"{item}: what is short of it is shown above", item=item)]
        if depth >= MAX_DEPTH:
            return []
        self.traced.add(item)
        children: list[Cause] = []
        for g in makers:
            if g.is_good or g.building:
                continue
            if g.recipe in path:
                children.append(self.ref_node(g.recipe, consumer))
            else:
                children.append(self.group_node(g, path, depth + 1))
        leaves = self.leaf_nodes(item, {consumer: short})
        children += leaves
        self.slots[item] = (children, leaves)
        return children

    def finalize(self) -> None:
        """Redo each item's leaf with the demand of everyone who waits for it.

        The makers' headroom is then taken off once, at the item, not once per consumer.
        """
        for item, (children, old) in list(self.slots.items()):
            new = self.leaf_nodes(item, dict(self.demand[item]))
            gone = {id(n) for n in old}
            children[:] = [c for c in children if id(c) not in gone] + new
            self.slots[item] = (children, new)

    def leaf_nodes(self, item: str, consumers: dict[str, float]) -> list[Cause]:
        short = sum(consumers.values())
        first = next(iter(consumers))
        makers = self.makers(item)
        drills = self.drills_of(item)
        leaves: list[Cause] = []
        if drills:
            leaves = self.drill_nodes(item, short, first, drills)
        elif not makers:
            leaves = self.source_nodes(item, short, first)
        else:
            headroom = sum(g.lost.get(item, 0.0) for g in makers)
            # What the makers could add if they ran all the time is not enough: more are needed.
            remaining = short - headroom
            if remaining > max(1e-6, 0.02 * short):
                best = max(makers, key=lambda g: (g.made.get(item, 0.0), g.recipe))
                per = best.per_machine(item)
                more = math.ceil(remaining / per - 1e-9) if per > EPS else None
                count = sum(len(g.machines) for g in makers)
                working = sum(g.working * len(g.machines) for g in makers) / count
                why = ("they work all the time yet the users starve" if headroom <= EPS
                       else f"even running all the time they would add only {_n(headroom)}/s")
                wanted = ""
                if len(consumers) > 1:
                    who = ", ".join(f"{c} {_n(v)}/s" for c, v in sorted(consumers.items()))
                    wanted = f", {_n(short)}/s wanted by {len(consumers)} users ({who})"
                leaves.append(Cause(
                    "too_few",
                    f"{item}: {count} maker(s) ({best.recipe}) work {working:.0%} of the time; {why}: "
                    f"short {_n(remaining)}/s{wanted}, about {more if more is not None else '?'} more "
                    f"{best.machine_name} ({_n(per)}/s each)",
                    item=item, recipe=best.recipe, short_per_s=remaining, share=working,
                    units=[u for g in makers for u in g.units],
                    numbers={"consumer": first, "maker_recipe": best.recipe, "maker_machine": best.machine_name,
                             "per_machine_per_s": per, "machines_needed": more, "makers": count,
                             "headroom_per_s": headroom, "wanted_per_s": short}))
        for leaf in leaves:
            leaf.numbers["consumers"] = dict(consumers)
        return leaves

    def nobody_nodes(self, item: str, short: float, consumer: str) -> list[Cause]:
        elsewhere = self.survey.flow(item).made
        recipes = [r for r in self.book.makers_of(item)][:6]
        if elsewhere > EPS:
            text = (f"{item}: no machine in the snapshot makes it, yet {_n(elsewhere)}/s is made on the surface: "
                    "it comes from outside the surveyed machines (another area, a train, by hand) "
                    f"and {_n(short)}/s more is wanted")
        else:
            text = (f"{item}: nobody makes it and none is made on the surface; "
                    f"{_n(short)}/s is wanted" + (f"; recipes that make it: {', '.join(recipes)}" if recipes else ""))
        return [Cause("nobody_makes", text, item=item, short_per_s=short,
                      numbers={"consumer": consumer, "made_elsewhere_per_s": elsewhere, "recipes": recipes})]

    def drill_nodes(self, item: str, short: float, consumer: str, drills: list[Miner]) -> list[Cause]:
        n = len(drills)
        # Production flows are for the whole surface; with a surveyed area the drills outside it are in them
        # too, so a rate per drill or per field cannot be read from them.
        rate_known = self.survey.whole_base
        mined = self.survey.flow(item).made
        working_sum = sum(m.working_share for m in drills)
        per_drill = mined / working_sum if rate_known and working_sum > EPS and mined > EPS else None
        out: list[Cause] = []

        def idle(states: frozenset[str]) -> tuple[float, list[int]]:
            total = sum(sum(m.states.get(s, 0.0) for s in states) for m in drills) / n
            units = [m.unit for m in drills if sum(m.states.get(s, 0.0) for s in states) >= MIN_STATE_SHARE]
            return total, units

        blocked, blocked_units = idle(OUTPUT_FULL_STATES)
        if blocked >= 0.1:
            out.append(Cause(
                "drills_blocked",
                f"{item}: {len(blocked_units)} of {n} drills sit with a full output {blocked:.0%} of the time: "
                "it is mined but not carried away, so the limit is the belt or whatever takes it "
                f"(not in the snapshot) ({_units_text(blocked_units)})",
                item=item, short_per_s=short, share=blocked, units=blocked_units,
                numbers={"consumer": consumer, "drills": n}))
        by_field: dict[int | None, list[Miner]] = defaultdict(list)
        for m in drills:
            by_field[m.field].append(m)
        for fid, members in sorted(by_field.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
            found = next((f for f in self.survey.fields if f.id == fid), None)
            if found is None:
                continue
            rate = (mined * sum(m.working_share for m in members) / working_sum) \
                if rate_known and working_sum > EPS else None
            hours = found.amount / rate / 3600.0 if rate is not None and rate > EPS else None
            empty_here = sum(m.states.get(s, 0.0) for m in members for s in DEPLETED_STATES) / len(members)
            # A field cut by the mod's search bound is bigger than it reads: its hours are a floor, not an alarm.
            if empty_here >= 0.1 or (hours is not None and hours < NEAR_EMPTY_HOURS and not found.truncated):
                unused = [{"field": f.id, "amount": f.amount, "center": list(f.center)}
                          for f in self.fields_of(item) if f.drills == 0 and f.id != fid]
                if hours is not None:
                    left = (f"at least {hours:.2g} hours left at {_n(rate)}/s (the field reaches past what was searched)"
                            if found.truncated else f"about {hours:.2g} hours left at {_n(rate)}/s")
                elif not rate_known:
                    left = "how fast it is mined is not known for the surveyed area alone"
                else:
                    left = "nothing mined from it now"
                out.append(Cause(
                    "field_depleting",
                    f"{item}: field {fid} has {_n(found.amount)} left, {left}; "
                    f"{len(members)} drill(s) there"
                    + (f", {empty_here:.0%} of the time without ore" if empty_here >= 0.1 else "")
                    + (f"; {len(unused)} other field(s) of it have no drills" if unused else ""),
                    item=item, short_per_s=short, share=empty_here, units=[m.unit for m in members],
                    numbers={"consumer": consumer, "field": fid, "amount": found.amount, "rate_per_s": rate,
                             "hours_left": hours, "unused_fields": unused, "center": list(found.center)}))
        power = idle(POWER_STATES)
        if power[0] >= 0.1:
            out.append(self.power_node(power[1], power[0], 0.0))
        if not blocked >= 0.1 and sum(m.working_share for m in drills) / n >= GOOD_SHARE - EPS:
            more = math.ceil(short / per_drill - 1e-9) if per_drill else None
            unknown = ("; how fast a drill mines is not known for the surveyed area alone" if not rate_known else
                       "; the rate per drill is not known (nothing mined in the window)")
            out.append(Cause(
                "too_few_drills",
                f"{item}: {n} drill(s) work {working_sum / n:.0%} of the time yet {consumer} starves: "
                f"short {_n(short)}/s"
                + (f", about {more} more drills ({_n(per_drill)}/s each)" if more is not None else unknown),
                item=item, short_per_s=short, share=working_sum / n, units=[m.unit for m in drills],
                numbers={"consumer": consumer, "drills": n, "per_drill_per_s": per_drill, "machines_needed": more,
                         "machine": drills[0].name}))
        if not out:
            share = 1.0 - working_sum / n
            out.append(Cause(
                "other", f"{item}: drills work only {working_sum / n:.0%} of the time and the snapshot does not say why",
                item=item, short_per_s=short, share=share, units=[m.unit for m in drills],
                numbers={"consumer": consumer, "states": _mean_states(drills)}))
        return out

    # -- nothing makes it here -------------------------------------------------

    def source_nodes(self, item: str, short: float, consumer: str) -> list[Cause]:
        """No machine and no drill produces the item: an unworked field, no field at all, or nobody."""
        free = [f for f in self.fields_of(item) if f.drills == 0]
        if free:
            first = free[0]
            danger = _enemy_count(first.enemies)
            note = (f"field {first.id} has no drills" + (
                f"; {_enemy_text(first.enemies)}" if danger else ""))
            return [Cause(
                "no_drills",
                f"{item}: field {first.id} ({_n(first.amount)} left, at {first.center[0]:.0f}, "
                f"{first.center[1]:.0f}) has no drills; {_n(short)}/s is wanted"
                + (f"; {_enemy_text(first.enemies)}" if danger else ""),
                item=item, short_per_s=short,
                numbers={"consumer": consumer, "note": note, "enemies": first.enemies,
                         "clear_enemies_first": danger > 0,
                         "fields": [{"field": f.id, "amount": f.amount, "center": list(f.center),
                                     "enemies": f.enemies} for f in free]})]
        patches = sorted((u for u in self.survey.unmined if item in self.book.resource_items(u.resource)),
                         key=lambda u: (u.distance, u.resource))
        if not self.fields_of(item) and (patches or self.book.is_mined(item)):
            if patches:
                u = patches[0]
                amount = ("at least " if u.truncated else "") + _n(u.amount)
                danger = _enemy_count(u.enemies)
                where = (f"the nearest patch of {u.resource} is at ({u.position[0]:.0f}, {u.position[1]:.0f}), "
                         f"{u.distance:.0f} tiles from the base, {amount} in it; {_enemy_text(u.enemies)}")
                return [Cause(
                    "no_field",
                    f"{item}: it is mined, but the base has no drill and no field of it; {where}; "
                    f"{_n(short)}/s is wanted",
                    item=item, short_per_s=short,
                    numbers={"consumer": consumer, "note": f"no drill and no field; {where}",
                             "clear_enemies_first": danger > 0, "enemies": u.enemies,
                             "patch": {"resource": u.resource, "position": list(u.position),
                                       "distance": u.distance, "amount": u.amount, "truncated": u.truncated}})]
            looked = ("it is in no patch the survey searched (within its search radius)" if self.survey.unmined
                      else "the survey lists no unmined patches (none within its search radius, or an older "
                           "survey file that did not look)")
            return [Cause(
                "no_field",
                f"{item}: it is mined, but the snapshot has no field of it and no drill on one; {looked}; "
                f"{_n(short)}/s is wanted",
                item=item, short_per_s=short,
                numbers={"consumer": consumer, "note": f"no field of it in the snapshot; {looked}",
                         "patch": None})]
        return self.nobody_nodes(item, short, consumer)


def _enemy_count(enemies: dict[str, int] | None) -> int:
    return sum(v for v in (enemies or {}).values() if v > 0)


def _enemy_text(enemies: dict[str, int] | None) -> str:
    """Enemies near a patch or a field in words: unknown, none, or counted (clear them first)."""
    if enemies is None:
        return "whether enemies are near it was not looked at"
    counted = [f"{v} {k}" for k, v in sorted(enemies.items()) if v > 0]
    if not counted:
        return "no enemies found near it"
    return f"{', '.join(counted)} near it: clear the enemies first"


def _mean_states(drills: list[Miner]) -> dict[str, float]:
    total: dict[str, float] = defaultdict(float)
    for m in drills:
        for state, share in m.states.items():
            total[state] += share / len(drills)
    return dict(sorted(total.items(), key=lambda kv: -kv[1]))


def _power_gap(p) -> float:
    """Watts a network lacks, known only when its machines want more than it makes.

    ``satisfaction`` is not a watt ratio (the survey does not define it as one), so it never
    turns into a figure here.
    """
    return p.used_w - p.made_w if p.used_w > p.made_w * 1.001 else 0.0


def _power_text(networks) -> tuple[str, dict]:
    """The sentence and numbers for networks that are short; watts only where the snapshot gives them."""
    gaps = [p for p in networks if _power_gap(p) > 0]
    total = sum(_power_gap(p) for p in gaps)
    entries = [{"network": p.network, "made_w": p.made_w, "used_w": p.used_w, "satisfaction": p.satisfaction,
                "short_w": _power_gap(p) or None} for p in networks]
    parts = [f"network {p.network} makes {p.made_w / 1e6:.3g} MW, machines want {p.used_w / 1e6:.3g} MW"
             for p in gaps]
    rest = [p for p in networks if _power_gap(p) <= 0]
    if gaps:
        text = f"power is short by {total / 1e6:.3g} MW: " + "; ".join(parts)
        if rest:
            text += ("; network(s) " + ", ".join(str(p.network) for p in rest)
                     + " show low satisfaction without a watt figure")
    else:
        text = ("machine time is lost to low or no power; network(s) "
                + ", ".join(f"{p.network} (satisfaction {p.satisfaction:.0%})" for p in networks)
                + ": the shortfall in watts is not in the snapshot")
    return text, {"short_w": total if gaps else None, "networks": entries}


# --------------------------------------------------------------------------
# the whole diagnosis
# --------------------------------------------------------------------------


def _targets(targets: Mapping[str, float] | Iterable[tuple[str, float]] | None) -> dict[str, float]:
    if not targets:
        return {}
    pairs = targets.items() if isinstance(targets, Mapping) else targets
    return {str(item): float(rate) for item, rate in pairs}


def _made_of(walk: _Walk, item: str) -> float:
    if item in walk.survey.items or item in walk.survey.fluids:
        return walk.survey.flow(item).made
    return sum(g.made.get(item, 0.0) for g in walk.groups.values())


def _reach(edges: Mapping[str, set[str]], start: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    stack = list(start)
    while stack:
        for nxt in edges.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def diagnose(survey: Survey, targets: Mapping[str, float] | Iterable[tuple[str, float]] | None = None,
             book: RecipeBook | None = None) -> Diagnosis:
    """Why the surveyed base makes less than it could, and, with targets, less than asked."""
    book = book or RecipeBook.from_active_data()
    wanted = _targets(targets)
    walk = _Walk(survey, book)
    result = Diagnosis(surface=survey.surface, window_s=survey.window_s, machines=len(survey.machines), targets=wanted)

    for g in sorted(walk.groups.values(), key=lambda g: g.recipe):
        if g.is_good and not g.building:
            result.good.append({
                "recipe": g.recipe, "machines": len(g.machines), "machine": g.machine_name,
                "working": g.working, "made_per_s": {k: v for k, v in g.made.items() if v > EPS},
                "units": g.units})

    weak = {r: g for r, g in walk.groups.items()
            if not g.is_good and not g.building and g.lost.get(g.main, 0.0) > EPS}
    gaps: list[tuple[str, float, float, float]] = []
    for item, rate in wanted.items():
        made = _made_of(walk, item)
        if rate - made > max(EPS, 0.01 * rate):
            gaps.append((item, rate, made, rate - made))
    gaps.sort(key=lambda t: (-t[3], t[0]))

    def target_nodes() -> list[Cause]:
        nodes = []
        for item, rate, made, gap in gaps:
            node = Cause(
                "target_gap", f"target {item}: {_n(rate)}/s wanted, {_n(made)}/s made, short {_n(gap)}/s",
                item=item, lost_per_s=gap, short_per_s=gap,
                numbers={"target_per_s": rate, "made_per_s": made, "consumer": f"target:{item}"})
            node.children = walk.trace_item(item, gap, f"target:{item}", frozenset(), 1)
            nodes.append(node)
        return nodes

    # First pass: only to learn which group waits on which, so each is explained by the one above it.
    target_nodes()
    walk.labs_nodes()
    for recipe in sorted(weak):
        walk.group_node(weak[recipe], frozenset(), 0)
    shown = _merge(walk.edges, walk.out_edges)
    below = {r: _reach(shown, [r]) - {r} for r in weak}
    # A full output does not explain the idle time of those who take it: only waiting does.
    below_waiting = {r: _reach(walk.edges, [r]) - {r} for r in weak}
    covered = _reach(shown, [f"target:{item}" for item, *_ in gaps] + ["labs"])

    candidates = []
    for recipe in weak:
        explained = any(recipe in below_waiting[other] and other not in below_waiting[recipe]
                        for other in weak if other != recipe)
        if not explained and recipe not in covered:
            candidates.append(recipe)
    candidates.sort(key=lambda r: (-weak[r].idle_machines, -weak[r].lost.get(weak[r].main, 0.0), r))
    chosen: list[str] = []
    for recipe in candidates:
        if any(recipe in below[o] and o in below[recipe] for o in chosen):
            continue  # one half of a loop is enough
        chosen.append(recipe)

    # Second pass: the trees as they are shown, each group and item expanded once.
    walk.reset()
    roots = target_nodes()
    others = walk.labs_nodes()
    others += [walk.group_node(weak[r], frozenset(), 0) for r in chosen]
    walk.finalize()
    # A group already shown inside an earlier chain is not a second entry.
    others = [c for c in others if c.kind != "seen_above"]
    # What is left with a full output that nobody has to empty (labs fed, a mall that stands) is no cause.
    benign: set[str] = set()
    found: dict[int, bool | None] = {}
    while True:
        before = len(benign)
        for node in roots + others:
            _settle(node, benign, found)
        if len(benign) == before:
            break
    for node in roots + others:
        _cut(node, found)
    others = [c for c in others if found.get(id(c)) is not True]
    present = {n.recipe for root in roots + others for n in root.walk() if n.kind not in ("seen_above", "cycle")}
    for node in roots + others:
        _drop_pointers(node, present)
    _impact(walk, roots, others)
    roots.sort(key=lambda c: (-c.numbers["impact_idle_machines"],
                              -c.lost_per_s / (c.numbers.get("target_per_s") or 1.0), c.item or ""))
    others.sort(key=lambda c: (not c.numbers["has_shortage"], -c.numbers["impact_idle_machines"],
                               c.recipe or c.item or c.text))
    result.causes = roots + others
    _lab_fact(walk, result)

    result.shortfalls = _shortfalls(walk)
    result.standalone = _standalone(walk)
    result.limits = _limits(walk, result)
    return result


# What a leaf of a chain can be that is a fault to act on (the rest of a chain is groups waiting and full outputs).
FAULT_KINDS = SHORT_KINDS | {"drills_blocked", "field_depleting", "power", "no_power", "no_fuel", "disabled", "other"}


def _merge(*graphs: Mapping[str, set[str]]) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = defaultdict(set)
    for graph in graphs:
        for key, values in graph.items():
            merged[key] |= values
    return merged


def _settle(node: Cause, benign: set[str], found: dict[int, bool | None]) -> bool | None:
    """Whether nothing here needs fixing, bottom up (a pure reading: `_cut` removes what it finds).

    True: no cause (a full output that science, a mall or the labs take care of, and every user of it
    likewise). False: a cause. None: only a pointer to where something was shown. A pointer to a group that
    is itself no cause is no cause either (`benign` holds those groups).
    """
    if node.kind in ("seen_above", "cycle"):
        found[id(node)] = True if node.recipe in benign else None
        return found[id(node)]
    verdicts = [_settle(c, benign, found) for c in node.children]
    if node.kind in ("low_output", "output_full") and verdicts:
        result: bool | None = False if any(v is False for v in verdicts) else any(v is True for v in verdicts)
    else:
        result = bool(node.numbers.get("benign")) and not node.children
    if result and node.kind == "low_output" and node.recipe:
        benign.add(node.recipe)
    found[id(node)] = result
    return result


def _cut(node: Cause, found: Mapping[int, bool | None]) -> None:
    node.children = [c for c in node.children if found.get(id(c)) is not True]
    for child in node.children:
        _cut(child, found)


def _drop_pointers(node: Cause, present: set) -> None:
    """A pointer "shown above" to a group that was cut out as no cause would point at nothing."""
    node.children = [c for c in node.children
                     if not (c.kind in ("seen_above", "cycle") and c.recipe and c.recipe not in present)]
    for child in node.children:
        _drop_pointers(child, present)


def _has_fault(node: Cause) -> bool:
    return any(n.kind in FAULT_KINDS for n in node.walk())


def _credit(node: Cause, groups: Mapping[str, _Group], elsewhere: dict[str, float]) -> float:
    """Idle machines (see ``IDLE_BASIS``) credited inside a node, each group once, at the faults it waits for.

    A group's idle time for a shortage is divided among what it waited for, in step with the output lost
    to each; the part for a thing that has no fault under it here is credited to the chain that shows that
    thing (`elsewhere`, by item), or to nothing when the snapshot does not say what it waited for.
    """
    total = sum(_credit(child, groups, elsewhere) for child in node.children)
    g = groups.get(node.recipe or "") if node.kind == "low_output" else None
    if g is not None and not g.building and g.idle_machines > EPS:
        parts = []
        for child in node.children:
            if child.kind == "waiting":
                parts.append((child, child.lost_per_s))
            elif child.kind in ("power", "no_power", "no_fuel", "disabled", "other"):
                parts.append((child, float(child.numbers.get("lost_per_s", 0.0))))
        weight = sum(max(lost, 0.0) for _c, lost in parts)
        if weight > EPS:
            for child, lost in parts:
                part = g.idle_machines * max(lost, 0.0) / weight
                if child.kind != "waiting" or _has_fault(child):
                    total += part
                    continue
                pointer = next((c.item for c in child.children if c.kind == "seen_above" and c.item), None)
                if pointer:
                    elsewhere[pointer] = elsewhere.get(pointer, 0.0) + part
    node.numbers["explains_idle_machines"] = total
    return total


def _impact(walk: _Walk, roots: list[Cause], others: list[Cause]) -> None:
    """Put the machine-time impact of every cause in its numbers and in its text."""
    elsewhere: dict[str, float] = {}
    top = roots + others
    for node in top:
        node.numbers["impact_idle_machines"] = _credit(node, walk.groups, elsewhere)
    owner: dict[int, Cause] = {id(n): root for root in top for n in root.walk()}
    for item, amount in elsewhere.items():
        children = walk.slots.get(item, ([], []))[0]
        root = next((owner[id(c)] for c in children if id(c) in owner), None)
        if root is not None:
            root.numbers["impact_idle_machines"] += amount
    for node in top:
        total = node.numbers["impact_idle_machines"]
        node.numbers["impact_basis"] = IDLE_BASIS
        node.numbers["has_shortage"] = _has_fault(node)
        if total > EPS:
            node.text += f"; explains {_n(total)} machine(s) of idle time"
        elif node.kind == "low_output" and not _has_fault(node):
            node.text += ("; no fault of its own found: what it waits for is made enough here or explained in the "
                          "chains above" if any(n.kind == "waiting" for n in node.children)
                          else "; no shortage under it, only a full output")


def _lab_fact(walk: _Walk, result: Diagnosis) -> None:
    """Labs that are fed while a pack's output is full are good news, said as such."""
    if not walk.science_seen or not walk.survey.labs:
        return
    stats = walk.lab_stats()
    if stats["working"] >= LAB_FED and stats["missing"] < LAB_STARVED:
        labs = walk.survey.labs
        result.good.append({
            "recipe": "research", "machines": len(labs), "machine": labs[0].name, "working": stats["working"],
            "made_per_s": {}, "units": [lab.unit for lab in labs],
            "note": "labs are fed: science packs held at a full output are what research does not need yet"})


def _shortfalls(walk: _Walk) -> list[Shortfall]:
    """One shortfall per item, from the item's own leaf: demand summed over its consumers, headroom taken off once."""
    found: list[Shortfall] = []
    for item, (_children, leaves) in walk.slots.items():
        for node in leaves:
            if node.kind not in SHORT_KINDS:
                continue
            entry = Shortfall(item, node.short_per_s, node.kind,
                              consumers=dict(walk.demand[item]))
            if node.kind in ("too_few", "too_few_drills"):
                entry.maker_recipe = node.numbers.get("maker_recipe")
                entry.maker_machine = node.numbers.get("maker_machine") or node.numbers.get("machine")
                per = node.numbers.get("per_machine_per_s") or node.numbers.get("per_drill_per_s")
                entry.per_machine = float(per) if per else None
                if entry.per_machine:
                    entry.machines_needed = math.ceil(entry.short_per_s / entry.per_machine - 1e-9)
            elif node.kind == "nobody_makes":
                entry.made_elsewhere = float(node.numbers.get("made_elsewhere_per_s", 0.0))
            elif node.kind == "no_drills":
                entry.note = node.numbers.get("note") or "a field is known but nothing mines it"
            elif node.kind == "no_field":
                entry.note = node.numbers.get("note") or "no field of it in the snapshot"
            found.append(entry)
            break
    return found


def _standalone(walk: _Walk) -> list[Cause]:
    out: list[Cause] = []
    if walk.recipeless:
        units = [m.unit for m in walk.recipeless]
        out.append(Cause("no_recipe", f"{len(units)} machine(s) have no recipe set ({_units_text(units)})",
                         units=units, numbers={"machines": len(units)}))
    short = [p for p in walk.survey.power if p.satisfaction < 0.999 or _power_gap(p) > 0]
    if short:
        text, numbers = _power_text(short)
        out.append(Cause("power", text, numbers=numbers))
    return out


def _limits(walk: _Walk, result: Diagnosis) -> list[str]:
    limits = [
        "Belts, trains, robots and items brought by hand are not in the snapshot: a machine that waits "
        "for something its makers make plenty of may be waiting for the belt, not for the maker.",
        "Modules, beacons and researched productivity are not in the snapshot: full rates count only "
        "a machine's crafting speed and its built-in productivity.",
        f"States are shares of one window of {_n(walk.survey.window_s)} s; a long cycle (oil, rocket) may be half seen.",
        "Power is known in watts only where a network's machines want more than it makes; otherwise the "
        "snapshot gives a share of machine time, not a shortfall.",
        "What is short of an item is summed over the machines that waited for it in the window, and what its "
        "makers could still add is taken off once; a machine count is therefore a first estimate, not a design.",
    ]
    if not walk.survey.whole_base:
        (x1, y1), (x2, y2) = walk.survey.area
        limits.append(f"Only the area ({x1:g}, {y1:g}) to ({x2:g}, {y2:g}) was surveyed: makers and users "
                      "outside it count as nobody, and the production flows are for the whole surface, so "
                      "no mining rate or hours left is given for fields.")
    mall = sorted((g for g in walk.groups.values() if g.building), key=lambda g: g.recipe)
    if mall:
        count = sum(len(g.machines) for g in mall)
        limits.append(f"{count} machine(s) make buildings ({', '.join(g.recipe for g in mall[:8])}"
                      + (f", +{len(mall) - 8} more recipes" if len(mall) > 8 else "")
                      + "): idle or a full output is normal for them, so they raise no shortfall and add nothing "
                        "to what their ingredients are wanted at.")
    if not walk.survey.labs and any(set(g.full) & set(walk.book.lab_inputs) for g in walk.groups.values()):
        limits.append("Labs were not surveyed (an older survey file): whether research takes the science packs "
                      "is not known, so packs held at a full output are not counted as a fault.")
    elif walk.survey.labs and walk.survey.research is None:
        stats = walk.lab_stats()
        limits.append(f"No research was in progress at the end of the window (the labs worked {stats['working']:.0%} "
                      "of it): packs pile up until one is queued.")
    danger = [f for f in walk.survey.fields if _enemy_count(f.enemies)]
    if danger:
        limits.append("Enemies near fields: " + "; ".join(
            f"{f.resource} field {f.id} at ({f.center[0]:.0f}, {f.center[1]:.0f}): {_enemy_text(f.enemies)}"
            for f in danger[:8]) + (f"; +{len(danger) - 8} more" if len(danger) > 8 else "") + ".")
    if any(c.kind == "nobody_makes" and c.numbers.get("made_elsewhere_per_s", 0) > EPS
           for root in result.causes for c in root.walk()):
        limits.append("An item made on the surface but by no machine in the snapshot is taken to arrive from "
                      "outside; how it travels is not seen.")
    if walk.skipped:
        limits.append("Recipes not in the game data are skipped: "
                      + ", ".join(f"{r} ({n} machine(s))" for r, n in sorted(walk.skipped.items())) + ".")
    return limits
