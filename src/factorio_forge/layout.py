"""The numbers a layout decision is made with.

This module answers "how much can one row of these machines do, and what
stops it" -- the arithmetic behind choosing a row length, a belt tier, an
inserter. It decides nothing. Choosing the layout is the job of whoever uses
the tool (Claude, through the skill); `rows.py` turns a chosen layout into
exact entities and `plan.py` checks the result. What this module owes that
decision is numbers that are right, with the reason attached.

## A row is fed by lanes, not by belts

One production row in the grammar measured from a real base (see CONTEXT.md)
looks like this:

    belt           input
    inserters      take from the belt, put into the machine
    MACHINE ROW
    inserters      take from the machine, put onto the belt
    belt           output, often shared with the mirrored row below

Three facts about inserters decide what such a row can carry, and the first
version of this module got two of them wrong:

- **An inserter only ever drops onto the far lane of a belt.** A row's output
  therefore fills one lane, never a whole belt, whether or not the belt is
  shared. Sizing the output against a whole belt doubled the row.
- **Two ingredients on one belt get a lane each.** Checking each of them
  against the whole belt, as if it had the belt to itself, doubled the row
  again for every two-ingredient recipe (battery: 120 machines instead of 60).
- **An inserter picks from both lanes**, so a single ingredient does get the
  whole belt, and a belt shared by two rows is split between them.

How many belts a side can use depends on the inserters chosen -- an ordinary
one reaches the belt beside it, a long-handed one the belt beyond -- and mods
change both reach and direction, so nothing here assumes them: reach is read
from `pickup_position`, and `rows.py` searches the rotations.

## Inserters are a limit of their own

`inserter_rate` is 60 x rotation speed x stack size: one full swing per item
stack, which is the chest-to-chest ceiling. Taking from a moving belt is
slower, and how much slower depends on belt speed; the wiki measures it, the
prototype does not say. The number is used as an upper bound and reported as
one, so an inserter that looks barely sufficient here should be read as
insufficient.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from draftsman.data import entities as entity_data
from draftsman.data import recipes as recipe_data

# Engine constants, not prototype data: a belt lane holds four items per tile,
# a belt has two lanes, a second has sixty ticks. `speed` in the prototype is
# tiles per tick.
_ITEM_SPACING = 0.25
_TICKS_PER_SECOND = 60
_LANES = 2

# A plan names at most two inserters for input: one per input belt.
_INPUT_SERVERS = 2


class LayoutError(Exception):
    """A layout that cannot be built, with the reason in words."""


@dataclass(frozen=True)
class Flow:
    """One item or fluid moving into or out of machines, per second."""

    item: str
    rate: float
    direction: str  # "in" or "out"


# --------------------------------------------------------------------------
# prototypes
# --------------------------------------------------------------------------


def _entity(name: str) -> dict:
    entry = entity_data.raw.get(name)
    if entry is None:
        raise LayoutError(f"{name!r} is not an entity in the active data")
    return entry


def _recipe(name: str) -> dict:
    entry = recipe_data.raw.get(name)
    if entry is None:
        raise LayoutError(f"{name!r} is not a recipe in the active data")
    return entry


def belt_throughput(belt: str) -> float:
    """Items per second a belt of this prototype carries, both lanes."""
    entry = entity_data.raw.get(belt)
    if entry is None or "speed" not in entry:
        raise LayoutError(f"{belt!r} is not a transport belt in the active data")
    return entry["speed"] * _TICKS_PER_SECOND / _ITEM_SPACING * _LANES


def lane_throughput(belt: str) -> float:
    """Items per second one lane of this belt carries."""
    return belt_throughput(belt) / _LANES


def machine_size(machine: str, direction: int = 0) -> tuple[int, int]:
    """Width and height in tiles, from the collision box, turned by direction.

    A collision box is deliberately smaller than the tiles the entity occupies
    -- an electric furnace fills 3 tiles but boxes at 2.4, an oil refinery
    fills 5 and boxes at 4.6 -- so the size is the box rounded *up*. Rounding
    to nearest silently loses a tile on every 3-wide machine.
    """
    entry = _entity(machine)
    box = entry.get("collision_box") or entry.get("selection_box")
    if not box:
        raise LayoutError(f"{machine!r} has no collision box to measure")
    (left, top), (right, bottom) = box
    width = max(1, math.ceil(right - left))
    height = max(1, math.ceil(bottom - top))
    if direction % 8 == 4:
        return height, width
    return width, height


def machine_footprint(machine: str) -> int:
    """Width in tiles: the spacing of machines standing flush in a row."""
    return machine_size(machine)[0]


def inserter_reach(inserter: str) -> int:
    """How many tiles from itself an inserter picks up, whichever way it faces."""
    entry = _entity(inserter)
    pickup = entry.get("pickup_position")
    if entry.get("type") != "inserter" or not pickup:
        raise LayoutError(f"{inserter!r} is not an inserter in the active data")
    return max(1, round(math.hypot(*pickup)))


def inserter_hand_size(inserter: str, bonuses) -> int:
    """Items an inserter carries per swing, for a force with these bonuses.

    One, plus what the prototype has built in (`stack_size_bonus`), plus the
    force's capacity bonus -- the bulk one for a prototype marked `bulk`, the
    ordinary one otherwise. The force's number already includes bonuses that
    do not come from capacity research (the bulk inserter technology adds one),
    which is why it is read from the game rather than summed from technologies.
    """
    entry = _entity(inserter)
    if entry.get("type") != "inserter":
        raise LayoutError(f"{inserter!r} is not an inserter in the active data")
    key = "bulk_inserter_capacity_bonus" if entry.get("bulk") else "inserter_stack_size_bonus"
    return 1 + int(entry.get("stack_size_bonus") or 0) + int(bonuses.get(key, 0))


def inserter_rate(inserter: str, stack_size: int = 1) -> float:
    """Items per second at best: one swing per stack, chest to chest.

    From a moving belt it is lower; see the module notes. `stack_size` is what
    research has made of the inserter's hand, which the prototype cannot know.
    """
    entry = _entity(inserter)
    speed = entry.get("rotation_speed")
    if entry.get("type") != "inserter" or not speed:
        raise LayoutError(f"{inserter!r} is not an inserter in the active data")
    return _TICKS_PER_SECOND * float(speed) * max(1, stack_size)


# --------------------------------------------------------------------------
# flows
# --------------------------------------------------------------------------


def crafts_per_second(recipe: str, machine: str, speed_bonus: float = 0.0) -> float:
    entry = _recipe(recipe)
    machine_entry = _entity(machine)
    if "crafting_speed" not in machine_entry:
        raise LayoutError(f"{machine!r} cannot craft anything in the active data")
    speed = float(machine_entry["crafting_speed"]) * (1 + max(speed_bonus, -0.8))
    return speed / float(entry.get("energy_required", 0.5) or 0.5)


def _amount(part: dict) -> float:
    if "amount" in part:
        amount = float(part["amount"])
    else:
        amount = (float(part.get("amount_min", 0)) + float(part.get("amount_max", 0))) / 2
    return amount * float(part.get("probability", 1))


def _flows(recipe: str, machine: str, count: int, speed_bonus: float, fluid: bool) -> list[Flow]:
    entry = _recipe(recipe)
    crafts = crafts_per_second(recipe, machine, speed_bonus) * count
    flows = [
        Flow(i["name"], crafts * _amount(i), "in")
        for i in entry.get("ingredients", [])
        if (i.get("type") == "fluid") == fluid
    ]
    flows += [
        Flow(r["name"], crafts * _amount(r), "out")
        for r in entry.get("results", [])
        if (r.get("type") == "fluid") == fluid
    ]
    return flows


def solid_flows(recipe: str, machine: str, count: int, speed_bonus: float = 0.0) -> list[Flow]:
    """Items per second into and out of `count` machines running `recipe`."""
    return _flows(recipe, machine, count, speed_bonus, fluid=False)


def fluid_flows(recipe: str, machine: str, count: int, speed_bonus: float = 0.0) -> list[Flow]:
    """Fluid units per second into and out of `count` machines.

    Reported, not used to size a row: a pipe's capacity does not shrink with
    the number of machines on it the way a lane's supply does.
    """
    return _flows(recipe, machine, count, speed_bonus, fluid=True)


# --------------------------------------------------------------------------
# lanes
# --------------------------------------------------------------------------


def assign_input_lanes(rates: dict[str, float], belts: int) -> list[list[str]]:
    """Which ingredient rides which lane of which input belt.

    Each belt is a pair of lanes. Every ingredient gets at least one lane; the
    spare lanes go, one at a time, to whichever ingredient is then shortest of
    supply for its rate. An ingredient holding both lanes of a belt has that
    belt to itself. The result is packable by construction: two belts have
    four lanes, so at most one ingredient can end up split across belts, and
    that one simply appears on both.
    """
    items = list(rates)
    lanes = belts * _LANES
    if len(items) > lanes:
        raise LayoutError(
            f"{len(items)} solid ingredients need {len(items)} lanes; "
            f"{belts} input belt(s) have {lanes}"
        )
    held = {item: 1 for item in items}
    for _ in range(lanes - len(items)):
        neediest = max(items, key=lambda i: rates[i] / held[i])
        held[neediest] += 1

    # Items holding two lanes first, so they get a belt to themselves.
    order = sorted(items, key=lambda i: (-held[i], items.index(i)))
    flat = [item for item in order for _ in range(held[item])]
    return [flat[b * _LANES:(b + 1) * _LANES] for b in range(belts)]


def input_belts_needed(recipe: str) -> int:
    """The fewest input belts that give every solid ingredient a lane."""
    solids = [i for i in _recipe(recipe).get("ingredients", []) if i.get("type") != "fluid"]
    return 0 if not solids else math.ceil(len(solids) / _LANES)


# --------------------------------------------------------------------------
# row capacity
# --------------------------------------------------------------------------


@dataclass
class RowCapacity:
    """How many machines one row keeps supplied, and everything that says so.

    `per_row` is 0 when nothing solid moves at all -- a fluid-only row, which
    no belt limits. `limit` names the binding constraint in words, the way it
    should be explained to a player.
    """

    recipe: str
    machine: str
    belt: str
    per_row: int
    limit: str
    input_lanes: list[list[str]] = field(default_factory=list)
    # Inserters one machine needs, by the belt they serve: "input-1" takes
    # from the belt beside the machine, "input-2" from the belt beyond it.
    inserters: dict[str, int] = field(default_factory=dict)
    per_machine: list[Flow] = field(default_factory=list)
    fluids_per_machine: list[Flow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def row_capacity(
    recipe: str,
    machine: str,
    belt: str,
    *,
    inserter: str | None = None,
    long_inserter: str | None = None,
    input_belts: int | None = None,
    rows_per_input_belt: int = 1,
    stack_size: int | dict[str, int] = 1,
    speed_bonus: float = 0.0,
) -> RowCapacity:
    """The most machines one row can keep fed and emptied, and what binds it.

    `rows_per_input_belt` is 2 when mirrored rows share their input belts
    between them, which halves each row's share. Output needs no such
    parameter: every row fills exactly one lane of its output belt, shared or
    not. `stack_size` is one hand size for every inserter, or a hand size per
    inserter name (see `inserter_hand_size`).
    """

    def hand(name: str) -> int:
        return stack_size.get(name, 1) if isinstance(stack_size, dict) else stack_size

    per_machine = solid_flows(recipe, machine, 1, speed_bonus)
    fluids = fluid_flows(recipe, machine, 1, speed_bonus)
    inputs = {f.item: f.rate for f in per_machine if f.direction == "in"}
    outputs = [f for f in per_machine if f.direction == "out"]

    if not per_machine:
        return RowCapacity(
            recipe, machine, belt, 0,
            "nothing solid moves, so no belt limits the row",
            per_machine=per_machine, fluids_per_machine=fluids,
        )

    belts = input_belts if input_belts is not None else input_belts_needed(recipe)
    servers = _INPUT_SERVERS if inserter is None else 1 + (long_inserter is not None)
    if inputs and not 1 <= belts <= servers:
        raise LayoutError(
            f"{recipe!r} has {len(inputs)} solid ingredients needing {belts} input belt(s); "
            f"the chosen inserters serve {servers} ({servers * _LANES} lanes)"
            + ("" if long_inserter is not None else "; a long_inserter would serve a second belt")
        )
    lanes = assign_input_lanes(inputs, belts) if inputs else []
    lane = lane_throughput(belt)
    share = max(1, rows_per_input_belt)

    # (machines this constraint allows, why)
    bounds: list[tuple[int, str]] = []
    for item, rate in inputs.items():
        held = sum(belt_lanes.count(item) for belt_lanes in lanes)
        supply = held * lane / share
        reason = (
            f"{item}: {rate:.3g}/s per machine against {held} lane(s) of {belt} "
            f"({supply:.3g}/s{' after sharing with the mirrored row' if share > 1 else ''})"
        )
        bounds.append((int(supply // rate) if rate > 0 else math.inf, reason))

    out_rate = sum(f.rate for f in outputs)
    if out_rate > 0:
        names = ", ".join(f.item for f in outputs)
        bounds.append(
            (
                int(lane // out_rate),
                f"{names}: {out_rate:.3g}/s per machine onto the one lane an inserter "
                f"drops on ({lane:.3g}/s)",
            )
        )

    per_row, limit = min(bounds, key=lambda b: b[0])
    if per_row == math.inf:  # every solid flow is zero: nothing to limit
        per_row, limit = 0, "no solid flow has a non-zero rate"
    notes: list[str] = []
    if len(outputs) > 1:
        notes.append(
            f"{len(outputs)} products share one output lane; they need filtering "
            "downstream or the belt will jam on whichever is not taken"
        )

    need: dict[str, int] = {}
    if inputs or outputs:
        if inserter is None:
            notes.append("no inserter chosen, so inserter throughput was not checked")
        else:
            reach_rate = {1: inserter_rate(inserter, hand(inserter))}
            if long_inserter is not None:
                reach_rate[2] = inserter_rate(long_inserter, hand(long_inserter))
            for index, belt_lanes in enumerate(lanes, start=1):
                # The inserter on this belt carries every ingredient on it. An
                # ingredient split over two belts is counted on both, which
                # errs toward more inserters rather than fewer.
                rate = sum(inputs[i] for i in set(belt_lanes))
                capacity = reach_rate.get(index)
                if capacity is None:
                    notes.append(f"input belt {index} needs a long-handed inserter; none chosen")
                    continue
                need[f"input-{index}"] = max(1, math.ceil(rate / capacity))
            if outputs:
                need["output"] = max(1, math.ceil(out_rate / reach_rate[1]))
            if any(n > 1 for n in need.values()):
                notes.append(
                    "one machine needs more than one inserter on a belt: "
                    + ", ".join(f"{k} x{n}" for k, n in need.items() if n > 1)
                    + f" (at most {reach_rate[1]:.3g}/s each, chest to chest)"
                )

    if min(b[0] for b in bounds) < 1:
        raise LayoutError(
            f"one {machine} running {recipe} already outruns its belt -- {limit}. "
            "A faster belt, or a slower machine, is needed"
        )

    return RowCapacity(
        recipe, machine, belt, per_row, limit,
        input_lanes=lanes, inserters=need,
        per_machine=per_machine, fluids_per_machine=fluids, notes=notes,
    )
