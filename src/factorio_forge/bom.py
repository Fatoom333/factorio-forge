"""Bill of materials: how many machines, how much flow, how much power.

Takes a target rate for one or more items or fluids and works backwards
through the active profile's own recipes to raw materials, the way a player
would: how many assemblers make enough green circuits, how many of those feed
from copper cable, how much power the whole thing draws.

## Why this is a linear program and not a recursive walk

A recipe can have more than one result from a single craft -- oil processing
turns one input into three outputs at fixed ratios -- and a recipe can consume
what it produces, as Kovarex enrichment does. "How many machines for light
oil", asked independently of petroleum gas, is the wrong question when both
numbers come out of the same process running at one rate.

Walking the tree item by item cannot answer that, and neither can solving each
loop as a system of equations, which was tried first and thrown away: exact
balance (`production == demand`) forces every recipe a chain touches to run at
some non-zero rate, and in a densely cross-linked chain -- Krastorio 2's
chemistry, where coal filtration makes coal out of the products of coal --
the only arithmetic answer is for several of them to run backwards.

What works is stating the whole chain as one linear program:

    minimise   machine-seconds spent
    such that  net production of every item >= what is wanted of it
    and        every crafting rate >= 0

Inequalities are what make it behave: a recipe nobody needs settles at zero
instead of being forced to run, and a byproduct nobody wants is allowed to go
to waste. This is the shape Helmod reaches with its own simplex solver (see
its `math/SolverMatrixSimplex.lua`); `_simplex` here is a two-phase tableau
solver written for it.

## Choosing recipes is the solver's job, not a heuristic's

Where several recipes make the same item, all of them are handed to the solver
as candidates and the cheapest combination wins. Picking one up front by a
local rule does not work: Krastorio 2 can make imersite powder by crushing a
finished inserter, which beats every honest recipe on output-per-second and is
obvious nonsense as a way to *manufacture* powder. Priced in machine-seconds,
including everything needed to build the inserter first, the solver rejects it
without anyone having to name it.

Every choice that mattered -- a recipe used where alternatives existed, a
machine picked because it was fastest -- is reported in
`BillOfMaterials.ambiguities` with its alternatives, so a caller can pin it
through `Request.recipe_choices` / `machine_choices` instead of accepting it.

Module and beacon effects are taken as already-decided bonus fractions
(`Effects`), keyed by crafting category -- this module does the arithmetic for
a given loadout, it does not choose one. Productivity changes how much of a
recipe's ingredients a given output actually needs, so it is folded into the
program's coefficients; speed and consumption only affect machine count and
power once rates are known, so they are applied afterwards.

A fluid's name is what balances in the linear program; its temperature, where
a recipe states one, rides along as reported metadata rather than a second
axis the program balances separately -- every real recipe checked so far (see
CONTEXT.md) states a temperature on a result at most, never a range on an
ingredient, and never two different temperatures for the same name at once.
`Target.temperature` lets a caller pin which one is meant when it matters
(Space Age gives some planets fluids that share a name across temperatures
that are not interchangeable); `BillOfMaterials.ambiguities` warns instead of
guessing if that ever stops holding.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace

from draftsman.data import entities as entity_data
from draftsman.data import items as item_data
from draftsman.data import recipes as recipe_data
from draftsman.data import resources as resource_data

from .environment import Environment

SPEED_FLOOR = -0.8
CONSUMPTION_FLOOR = -0.8


class BillOfMaterialsError(Exception):
    """The request could not be resolved against the active profile's data."""


# --------------------------------------------------------------------------
# input
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """A rate the finished bill of materials must produce, at minimum.

    `temperature` pins which physical state of a fluid counts: Space Age
    gives some fluids the same name at genuinely different temperatures
    (`steam` leaving a foundry's acid-neutralisation at 500 degrees is not
    interchangeable with plain 100-degree steam), and which one a target
    means is not decidable from the item name alone. Left `None`, any
    recipe that makes the item counts, whatever temperature its result
    states or leaves unstated -- the previous, only behaviour.
    """

    item: str
    rate: float  # per second
    temperature: float | None = None


@dataclass(frozen=True)
class Effects:
    """Module/beacon bonus fractions already decided for one crafting category.

    0.5 means +50%; -0.2 means -20%. Speed and consumption are clamped to the
    game's own floor of -80% when applied -- a machine always runs, and always
    draws some power.
    """

    speed: float = 0.0
    productivity: float = 0.0
    consumption: float = 0.0


@dataclass(frozen=True)
class Request:
    targets: tuple[Target, ...]
    # Items/fluids treated as arriving from outside -- a city block's train
    # input, say -- and never expanded to their own recipe.
    boundary: frozenset[str] = frozenset()
    recipe_choices: dict[str, str] = field(default_factory=dict)
    machine_choices: dict[str, str] = field(default_factory=dict)
    # Keyed by crafting category; "*" is the fallback for a category with no
    # entry of its own.
    effects: dict[str, Effects] = field(default_factory=dict)
    environment: Environment | None = None

    def effects_for(self, category: str) -> Effects:
        return self.effects.get(category, self.effects.get("*", Effects()))


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------


@dataclass
class MachineLine:
    recipe: str
    machine: str
    category: str
    rate: float  # crafts per second, across every machine on the line
    machines: int
    power: float  # watts, total across every machine on the line
    inputs: dict[str, float] = field(default_factory=dict)  # item -> per second
    outputs: dict[str, float] = field(default_factory=dict)  # item -> per second
    # Only the outputs whose recipe entry actually states a temperature --
    # most items and most fluids have none. Informational: the rate above
    # already trusts every producer of the same name as one fungible pool
    # (see `Ambiguity(kind="temperature")` in `BillOfMaterials.ambiguities`
    # for when that pool actually mixes more than one temperature).
    output_temperatures: dict[str, float] = field(default_factory=dict)


@dataclass
class Ambiguity:
    """A place something was picked rather than being told.

    `kind` is "recipe" when several recipes could make an item and the solver
    settled it on cost, or "machine" when several machines could run a recipe
    and the fastest was taken. Only choices that ended up in the result are
    reported; alternatives the solver priced and left at zero are not.
    """

    subject: str
    kind: str
    candidates: tuple[str, ...]
    detail: str


@dataclass
class BillOfMaterials:
    lines: list[MachineLine] = field(default_factory=list)
    raw_materials: dict[str, float] = field(default_factory=dict)  # item -> per second
    ambiguities: list[Ambiguity] = field(default_factory=list)
    total_power: float = 0.0


# --------------------------------------------------------------------------
# recipe/item data helpers
# --------------------------------------------------------------------------


def _expected_amount(entry: dict) -> float:
    """A result's average yield per craft, folding in `probability`.

    `amount` is exact when present; otherwise `amount_min`/`amount_max` bracket
    a uniform range and the midpoint is used.
    """
    if "amount" in entry:
        amount = float(entry["amount"])
    else:
        low = float(entry.get("amount_min", 0))
        high = float(entry.get("amount_max", low))
        amount = (low + high) / 2
    return amount * float(entry.get("probability", 1))


def _result_entry(recipe: dict, item: str) -> dict | None:
    return next((r for r in recipe.get("results", []) if r.get("name") == item), None)


def _ingredient_entry(recipe: dict, item: str) -> dict | None:
    return next((i for i in recipe.get("ingredients", []) if i.get("name") == item), None)


def _result_temperature(recipe: dict, item: str) -> float | None:
    """The exact temperature `recipe` states for its `item` result, if any.

    Only an exact `temperature` is read here, not a `minimum_temperature`/
    `maximum_temperature` range: no ingredient in any profile inspected so
    far states a temperature range at all (that mechanic lives on a
    building's fluidbox, not a crafting recipe -- see CONTEXT.md), so there
    is nothing real to test a range-matching rule against yet.
    """
    entry = _result_entry(recipe, item)
    return entry.get("temperature") if entry else None


def _productivity_multiplier(recipe: dict, entry: dict, productivity: float) -> float:
    """How much of a result's amount actually benefits from a productivity bonus.

    A recipe that consumes and produces the same item -- Kovarex enrichment is
    the vanilla example -- marks the portion that merely offsets its own
    ingredient as `ignored_by_productivity`, so productivity modules cannot
    turn a self-sustaining loop into an unbounded multiplier. `catalyst_amount`
    excludes a result's amount from the bonus for a related reason: it marks
    the portion handed back to replace what a catalytic ingredient elsewhere
    in the same recipe consumed, not a net product, so a bigger productivity
    bonus should not multiply it either -- Helmod's `Product:getBonusAmount`
    excludes it the same way. Only the amount above both is scaled.
    """
    if not recipe.get("allow_productivity"):
        return 1.0
    amount = _expected_amount(entry)
    if amount <= 0:
        return 1.0
    excluded = float(entry.get("ignored_by_productivity", 0)) + float(entry.get("catalyst_amount", 0))
    scaled = excluded + (amount - excluded) * (1 + max(productivity, 0.0))
    return scaled / amount


# Subgroups the engine itself gives every fluid's automatic barrel-fill and
# barrel-empty recipe, regardless of which fluid or mod -- confirmed against
# several fluids, not just one. `empty-*-barrel` is the one that matters here:
# it lists the fluid as a result, which would otherwise make a fluid that is
# actually pumped or mined, like crude oil, look crafted. Both are excluded
# from auto-selection together, since neither is really a production source --
# see `_build_result_index`.
_BARREL_SUBGROUPS = frozenset({"fill-barrel", "empty-barrel"})


def _build_result_index() -> dict[str, list[str]]:
    """Item/fluid -> recipes that make it, for auto-selecting a producer.

    Two kinds of recipe are excluded, both real repackaging steps that the
    game's own data marks as such rather than a production source:

    - `allow_as_intermediate: false`. Packaging mods set this on both halves
      of a stack/unstack pair, which would otherwise make a mined resource
      like coal look crafted: the unstacking recipe lists coal as a result,
      even though coal has no recipe of its own at all.
    - A barrel-fill/barrel-empty `subgroup` (see `_BARREL_SUBGROUPS`) -- the
      same problem for any fluid, vanilla or modded: unbarrelling technically
      "produces" the fluid it releases.

    An explicit override in `Request.recipe_choices` is validated against a
    recipe's own results directly (see `_candidate_recipes`), not this index, so
    a caller who genuinely wants such a recipe is never blocked by it.
    """
    index: dict[str, list[str]] = defaultdict(list)
    for name, recipe in recipe_data.raw.items():
        if recipe.get("allow_as_intermediate", True) is False:
            continue
        if recipe.get("subgroup") in _BARREL_SUBGROUPS:
            continue
        for result in recipe.get("results", []):
            index[result["name"]].append(name)
    return index


_ENERGY_UNITS = {"": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}
_ENERGY_PATTERN = re.compile(r"^([\d.]+)\s*([kKMGT]?)W$")


def _watts(value) -> float:
    """Power as a plain number of watts, from either a number or "483.4MW".

    Not delegated to draftsman's own `parse_energy`: in this fork it raises on
    a decimal value like "483.4MW" (`int()` on the digit string) and returns
    joules-per-tick rather than watts even when it works, neither of which is
    what a wattage sum wants.
    """
    if isinstance(value, (int, float)):
        return float(value)
    match = _ENERGY_PATTERN.match(str(value).strip())
    if not match:
        raise BillOfMaterialsError(f"cannot parse energy value {value!r}")
    number, suffix = match.groups()
    return float(number) * _ENERGY_UNITS[suffix]


# --------------------------------------------------------------------------
# machine selection -- never silent, always reported
# --------------------------------------------------------------------------


def _choose_machine(recipe_name: str, request: Request) -> tuple[str, Ambiguity | None]:
    recipe = recipe_data.raw[recipe_name]
    category = recipe.get("category", "crafting")

    override = request.machine_choices.get(category)
    if override is not None:
        return override, None

    ingredient_count = len(recipe.get("ingredients", []))
    candidates = sorted(
        name
        for name, data in entity_data.raw.items()
        if category in (data.get("crafting_categories") or ())
        and data.get("crafting_speed")
        and (data.get("ingredient_count") is None or data["ingredient_count"] >= ingredient_count)
    )
    if not candidates:
        raise BillOfMaterialsError(
            f"no machine in the active data can craft category {category!r} (needed for {recipe_name!r})"
        )

    chosen = max(candidates, key=lambda name: entity_data.raw[name]["crafting_speed"])
    if len(candidates) == 1:
        return chosen, None
    return chosen, Ambiguity(
        subject=category,
        kind="machine",
        candidates=tuple(candidates),
        detail=f"picked {chosen!r} (fastest); override via machine_choices",
    )


# --------------------------------------------------------------------------
# gathering the candidate recipes
# --------------------------------------------------------------------------


# Fluids drawn straight from a tile (an offshore pump) rather than mined from
# a resource entity -- `draftsman.data.resources` cannot see these no matter
# what, because there is no resource prototype backing them at all, and
# Factorio's data has no generic "this fluid is pumped" marker to read
# instead. Helmod hits the identical wall from inside a running game with
# full access to every prototype, and resorts to the same hardcoded name
# (`ModelCompute.computeResources`, see CONTEXT.md) -- this is not a gap our
# own data access could close, it is what the data itself is missing.
# Krastorio 2 also has real recipes that make water (`kr-water`, atmospheric
# condensation, ...), which would otherwise send the chain hunting for
# chemistry to avoid ever pumping it for free; water is a leaf regardless.
_PUMPED_FLUIDS = frozenset({"water"})


def _mined_items() -> frozenset[str]:
    """Every item/fluid a resource entity's `minable` block actually yields.

    This is the game's own answer to "is this raw", read from
    `draftsman.data.resources` (`data.raw["resource"]`, not extracted by
    draftsman before this): a resource entity's `minable` names exactly what
    mining it produces, either as a single `result` or, for multi-output
    mining (Space Age asteroid chunks) or a resource that needs an input
    fluid (uranium ore), as a list under `results`. This matters because
    "nothing crafts it" is not enough on its own: Krastorio 2 has a coal
    filtration recipe, so coal *can* be crafted, and following that as a way
    to get coal sends the chain hunting for the chemistry that filtration
    itself needs, which needs coal. A mined resource is a leaf whatever else
    happens to produce it.

    The `subgroup == "raw-resource"` check this replaced is kept alongside
    it, not instead of it: a profile extracted before `resources.pkl` existed
    has nothing here until it is re-extracted, and the two signals should
    agree wherever both are present anyway.
    """
    mined: set[str] = set(_PUMPED_FLUIDS)
    for resource in resource_data.raw.values():
        minable = resource.get("minable") or {}
        if "result" in minable:
            mined.add(minable["result"])
        for result in minable.get("results", []) or []:
            name = result.get("name")
            if name:
                mined.add(name)
    for name, entry in item_data.raw.items():
        if entry.get("subgroup") == "raw-resource":
            mined.add(name)
    return frozenset(mined)


@dataclass
class _Chain:
    recipes: list[str]  # every candidate the solver may use
    machine: dict[str, str]  # recipe -> chosen machine
    produced: set[str]  # items something in `recipes` can make
    target_demand: dict[str, float]
    ambiguities: list[Ambiguity]


def _candidate_recipes(item: str, result_index: dict[str, list[str]], request: Request) -> list[str]:
    """Every recipe the solver is allowed to consider for one item.

    An explicit choice in `Request.recipe_choices` narrows this to exactly
    one. Otherwise every recipe that makes the item and is currently unlocked
    stays in, and which of them to actually use is left to the solver rather
    than decided here -- see `_solve_rates` on why a local "best" guess is
    the wrong call.
    """
    producers = result_index.get(item) or []
    override = request.recipe_choices.get(item)
    if override is not None:
        recipe = recipe_data.raw.get(override)
        if recipe is None or not any(r.get("name") == item for r in recipe.get("results", [])):
            raise BillOfMaterialsError(f"{override!r} does not produce {item!r}")
        return [override]

    if request.environment is not None:
        unlocked = [r for r in producers if r in request.environment.recipes_enabled]
        if producers and not unlocked:
            raise BillOfMaterialsError(
                f"{item!r} needs one of {sorted(producers)}, none currently unlocked"
            )
        return unlocked
    return producers


def _gather(
    request: Request, result_index: dict[str, list[str]], mined: frozenset[str]
) -> _Chain:
    """Collect every recipe reachable from the targets, keeping all the options.

    Unlike a walk that commits to one producer per item, this keeps every
    unlocked candidate and recurses into all of their ingredients, so the
    solver gets to see alternative routes and price them against each other.
    An item stops the walk when it is an explicit boundary supply, when it is
    mined, or when nothing makes it.
    """
    recipes: list[str] = []
    seen_recipes: set[str] = set()
    machine: dict[str, str] = {}
    produced: set[str] = set()
    target_demand: dict[str, float] = defaultdict(float)
    ambiguities: list[Ambiguity] = []
    reported_machines: set[str] = set()

    for t in request.targets:
        target_demand[t.item] += t.rate

    target_temperature = {t.item: t.temperature for t in request.targets if t.temperature is not None}

    queue = [t.item for t in request.targets]
    queued: set[str] = set(queue)
    while queue:
        item = queue.pop()
        if item in request.boundary or item in mined:
            continue

        candidates = _candidate_recipes(item, result_index, request)
        wanted_temperature = target_temperature.get(item)
        if wanted_temperature is not None and candidates:
            matching = [
                r for r in candidates if _result_temperature(recipe_data.raw[r], item) == wanted_temperature
            ]
            if not matching:
                raise BillOfMaterialsError(
                    f"{item!r} was asked for at {wanted_temperature} degrees; "
                    f"{sorted(candidates)} produce it, but none of them at that temperature"
                )
            candidates = matching
        if not candidates:
            continue  # raw material, or nothing unlocked makes it
        produced.add(item)

        if len(candidates) > 1:
            ambiguities.append(
                Ambiguity(
                    subject=item,
                    kind="recipe",
                    candidates=tuple(sorted(candidates)),
                    detail=(
                        f"{len(candidates)} recipes make {item!r}; the solver picks "
                        "whichever costs least, pin one with recipe_choices"
                    ),
                )
            )

        for recipe in candidates:
            if recipe in seen_recipes:
                continue
            seen_recipes.add(recipe)
            recipes.append(recipe)

            chosen_machine, machine_ambiguity = _choose_machine(recipe, request)
            machine[recipe] = chosen_machine
            if machine_ambiguity and chosen_machine not in reported_machines:
                reported_machines.add(chosen_machine)
                ambiguities.append(machine_ambiguity)

            for ingredient in recipe_data.raw[recipe].get("ingredients", []):
                name = ingredient["name"]
                if name not in queued:
                    queued.add(name)
                    queue.append(name)

    return _Chain(sorted(recipes), machine, produced, dict(target_demand), ambiguities)


# --------------------------------------------------------------------------
# the linear program
# --------------------------------------------------------------------------


def _signed_amount(recipe: dict, item: str, productivity: float) -> float:
    """Net units of `item` one craft of `recipe` contributes: positive to make
    it, negative to consume it. Both apply at once to a recipe that consumes
    what it also produces."""
    net = 0.0
    result = _result_entry(recipe, item)
    if result:
        net += _expected_amount(result) * _productivity_multiplier(recipe, result, productivity)
    ingredient = _ingredient_entry(recipe, item)
    if ingredient:
        net -= float(ingredient["amount"])
    return net


def _simplex(cost: list[float], matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Minimise `cost` . x subject to `matrix` . x >= `rhs` and x >= 0.

    Two-phase simplex on a dense tableau: phase one drives artificial
    variables to zero to find any feasible point at all -- or proves there is
    none -- and phase two minimises the real objective from there.

    Inequalities rather than equalities are the point. Requiring a production
    chain to balance *exactly* forces recipes nobody needs to run at some
    non-zero rate, and in a densely cross-linked chain (Krastorio 2's
    chemistry, for one) the only arithmetic solution to that is for some of
    them to run backwards. With `>=`, an unneeded recipe sits at zero and a
    surplus byproduct is allowed to go to waste, which is what a real factory
    does.
    """
    constraint_count = len(matrix)
    variable_count = len(cost)
    if constraint_count == 0:
        return [0.0] * variable_count

    surplus_at = variable_count
    artificial_at = variable_count + constraint_count
    width = variable_count + 2 * constraint_count

    tableau: list[list[float]] = []
    for i in range(constraint_count):
        row = [0.0] * (width + 1)
        row[:variable_count] = matrix[i]
        row[surplus_at + i] = -1.0
        row[artificial_at + i] = 1.0
        row[width] = rhs[i]
        if row[width] < 0:  # the right-hand side has to stay non-negative
            row = [-value for value in row]
            row[artificial_at + i] = 1.0
            tableau.append(row)
            continue
        tableau.append(row)
    basis = [artificial_at + i for i in range(constraint_count)]

    def run(costs: list[float], forbidden: set[int]) -> None:
        objective = [0.0] * (width + 1)
        for j in range(width + 1):
            value = costs[j] if j < width else 0.0
            for i, basic in enumerate(basis):
                value -= costs[basic] * tableau[i][j]
            objective[j] = value

        for _ in range(5000):
            entering = -1
            best = -1e-9
            for j in range(width):
                if j not in forbidden and objective[j] < best:
                    best = objective[j]
                    entering = j
            if entering < 0:
                return

            leaving = -1
            best_ratio = None
            for i in range(constraint_count):
                if tableau[i][entering] > 1e-9:
                    ratio = tableau[i][width] / tableau[i][entering]
                    if best_ratio is None or ratio < best_ratio - 1e-12:
                        best_ratio = ratio
                        leaving = i
            if leaving < 0:
                raise BillOfMaterialsError(
                    "the production chain has no bounded solution: a recipe could run "
                    "arbitrarily fast without the demand ever being met"
                )

            pivot = tableau[leaving][entering]
            tableau[leaving] = [value / pivot for value in tableau[leaving]]
            for i in range(constraint_count):
                if i != leaving and tableau[i][entering]:
                    factor = tableau[i][entering]
                    tableau[i] = [
                        value - factor * pivot_value
                        for value, pivot_value in zip(tableau[i], tableau[leaving])
                    ]
            if objective[entering]:
                factor = objective[entering]
                for j in range(width + 1):
                    objective[j] -= factor * tableau[leaving][j]
            basis[leaving] = entering
        raise BillOfMaterialsError("the production chain did not converge to a solution")

    phase_one_cost = [0.0] * width
    for i in range(constraint_count):
        phase_one_cost[artificial_at + i] = 1.0
    run(phase_one_cost, forbidden=set())

    leftover = sum(
        tableau[i][width] for i, basic in enumerate(basis) if basic >= artificial_at
    )
    if leftover > 1e-6:
        raise BillOfMaterialsError(
            "no set of crafting rates can meet this demand: something asked for "
            "cannot be produced in the amounts wanted from what is available"
        )

    phase_two_cost = [0.0] * width
    phase_two_cost[:variable_count] = cost
    run(phase_two_cost, forbidden=set(range(artificial_at, width)))

    solution = [0.0] * variable_count
    for i, basic in enumerate(basis):
        if basic < variable_count:
            solution[basic] = max(tableau[i][width], 0.0)
    return solution


def _machine_seconds(recipe: str, chain: _Chain, request: Request) -> float:
    """How long one craft occupies one machine, after speed effects.

    This is the solver's cost. It is what makes an absurd route lose on its
    own merits rather than by being filtered out by name: crushing a finished
    inserter back into powder is a perfectly real recipe, but paying for the
    whole inserter first costs far more machine-seconds than making the powder
    directly, so a solver minimising this never picks it.
    """
    entry = recipe_data.raw[recipe]
    machine = entity_data.raw[chain.machine[recipe]]
    effects = request.effects_for(entry.get("category", "crafting"))
    speed = machine["crafting_speed"] * (1 + max(effects.speed, SPEED_FLOOR))
    return float(entry.get("energy_required", 0.5) or 0.5) / speed


def _solve_rates(chain: _Chain, request: Request) -> dict[str, float]:
    """Crafts per second for every candidate recipe; unused ones come back zero.

    One variable per recipe, one `>=` constraint per item the chain can make,
    and a cost of machine-seconds per craft, so the cheapest way to meet the
    demand wins and everything unnecessary settles at zero.
    """
    if not chain.recipes:
        return {}
    index = {recipe: i for i, recipe in enumerate(chain.recipes)}

    productivity = {
        recipe: request.effects_for(
            recipe_data.raw[recipe].get("category", "crafting")
        ).productivity
        for recipe in chain.recipes
    }

    constrained = sorted(chain.produced)
    matrix = [
        [
            _signed_amount(recipe_data.raw[recipe], item, productivity[recipe])
            for recipe in chain.recipes
        ]
        for item in constrained
    ]
    rhs = [chain.target_demand.get(item, 0.0) for item in constrained]
    cost = [_machine_seconds(recipe, chain, request) for recipe in chain.recipes]

    rates = _simplex(cost, matrix, rhs)
    return {recipe: rates[index[recipe]] for recipe in chain.recipes}


# --------------------------------------------------------------------------
# top level
# --------------------------------------------------------------------------


def compute(request: Request) -> BillOfMaterials:
    if not request.targets:
        raise BillOfMaterialsError("no targets to produce")

    result_index = _build_result_index()
    mined = _mined_items()
    chain = _gather(request, result_index, mined)
    rates = _solve_rates(chain, request)

    bom = BillOfMaterials()
    drawn: dict[str, float] = defaultdict(float)
    used: set[str] = set()

    for recipe, rate in sorted(rates.items()):
        if rate <= 1e-9:
            continue
        used.add(recipe)
        entry = recipe_data.raw[recipe]
        category = entry.get("category", "crafting")
        machine_name = chain.machine[recipe]
        machine = entity_data.raw[machine_name]
        effects = request.effects_for(category)

        speed = machine["crafting_speed"] * (1 + max(effects.speed, SPEED_FLOOR))
        crafts_per_second = speed / float(entry.get("energy_required", 0.5) or 0.5)
        machines = math.ceil(rate / crafts_per_second - 1e-9)

        base_power = _watts(machine.get("energy_usage", 0))
        power = base_power * (1 + max(effects.consumption, CONSUMPTION_FLOOR)) * machines

        inputs = {i["name"]: rate * float(i["amount"]) for i in entry.get("ingredients", [])}
        outputs = {
            r["name"]: rate
            * _expected_amount(r)
            * _productivity_multiplier(entry, r, effects.productivity)
            for r in entry.get("results", [])
        }
        output_temperatures = {
            r["name"]: r["temperature"] for r in entry.get("results", []) if "temperature" in r
        }
        for name, amount in inputs.items():
            if name not in chain.produced:
                drawn[name] += amount

        bom.lines.append(
            MachineLine(
                recipe=recipe,
                machine=machine_name,
                category=category,
                rate=rate,
                machines=machines,
                power=power,
                inputs=inputs,
                outputs=outputs,
                output_temperatures=output_temperatures,
            )
        )
        bom.total_power += power

    # A target nothing in the chain makes comes straight from outside: an
    # explicit boundary supply, or a raw material like ore or crude oil.
    for target in request.targets:
        if target.item not in chain.produced:
            drawn[target.item] += target.rate

    # Only report a choice that actually mattered. Gathering deliberately
    # pulls in every alternative route so the solver can price them, which
    # means most of what it considered never runs -- reporting those would
    # bury the handful of picks a reader can actually act on.
    used_categories = {recipe_data.raw[r].get("category", "crafting") for r in used}
    reported: list[Ambiguity] = []
    for ambiguity in chain.ambiguities:
        if ambiguity.kind == "recipe":
            picked = sorted(set(ambiguity.candidates) & used)
            if not picked:
                continue
            reported.append(
                replace(
                    ambiguity,
                    detail=(
                        f"used {', '.join(picked)} out of "
                        f"{len(ambiguity.candidates)} recipes that make "
                        f"{ambiguity.subject!r}; pin one with recipe_choices"
                    ),
                )
            )
        elif ambiguity.subject in used_categories:
            reported.append(ambiguity)

    # Every producer of the same name is treated as one fungible pool of
    # rate, temperature or not -- correct for total mass balance, but a
    # caller building an actual factory needs to know when that pool is not
    # actually one physical fluid. Pin a temperature on the target instead
    # of guessing which producer was meant when this fires.
    temperatures: dict[str, set[float]] = defaultdict(set)
    producers: dict[str, set[str]] = defaultdict(set)
    for recipe in used:
        for r in recipe_data.raw[recipe].get("results", []):
            temperature = r.get("temperature")
            if temperature is not None:
                temperatures[r["name"]].add(temperature)
                producers[r["name"]].add(recipe)
    for item, degrees in sorted(temperatures.items()):
        if len(degrees) > 1:
            reported.append(
                Ambiguity(
                    subject=item,
                    kind="temperature",
                    candidates=tuple(sorted(producers[item])),
                    detail=(
                        f"{', '.join(sorted(producers[item]))} produce {item!r} at different "
                        f"temperatures ({sorted(degrees)}); the rate above pools them as one "
                        "fungible amount -- pin a temperature on the target if the physical "
                        "difference matters"
                    ),
                )
            )

    bom.ambiguities = reported
    bom.raw_materials = dict(sorted(drawn.items()))
    return bom


