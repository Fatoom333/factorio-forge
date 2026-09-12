"""Expand a chosen row layout into exact entities.

The decision -- which recipe, how many rows of how many machines, which belt,
mirrored or repeated, which way round -- comes from outside (from Claude,
through a plan; see `plan.py`). This module does the part a model should not
do tile by tile: work out, from the prototypes of the active profile, where
every machine, inserter, belt, pipe and pole goes so that the result is
exactly buildable, and describe the finished block by its ports.

## Nothing about the entities is assumed

Mods change everything this depends on: machine sizes, where fluid boxes
connect, how far and in which direction an inserter reaches (some mods turn
the pickup ninety degrees), pole supply areas, underground reach. So every
one of those is read from the prototype and the geometry is searched, not
assumed:

- an inserter's direction is whichever of the four rotations makes its own
  `pickup_position` land on a belt line and its `insert_position` land inside
  the machine (or the reverse, for output); the belt then goes exactly as far
  out as that pickup reaches;
- a machine's direction is whichever rotation puts a connection of every
  fluid box the recipe fills on a long side of the row, and stacks tightest;
- poles are placed by their own supply area and wire reach.

A prototype whose geometry does not fit this form of row is refused with the
reason, never built wrong.

## The cross-section

A row is a band of machines standing flush, with *lines* parallel to it on
either side:

    outer   track      a pipe carrying one fluid along the row
            partner    pipe-to-ground surfacing from under the belts
            belts      each as far out as the inserter serving it reaches
    inner   arm line   inserters, pipe-to-ground dives, poles
            MACHINES

How many lines a side needs follows from the recipe and the prototypes, so
the distance between rows is an output of the geometry. A side with no belts
and exactly one fluid lays its pipe straight along the arm line, which lets
mirrored rows share it -- the lubricant block in the measured base, pitch 4 on
a 3-tile plant.

## Stacking

`mirror` turns every second row round so neighbours face each other and share
what can be shared: output belts (each row fills its own lane), input belts
(each row gets half), a pipe laid in the arm line, the outermost track.
`repeat` stacks rows the same way up. Two pipe lines of different fluids never
touch: a blank line goes between them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from draftsman.data import entities as entity_data
from draftsman.data import recipes as recipe_data

from . import fluids as fluid_rules
from . import layout
from .layout import LayoutError

EAST = 4


# --------------------------------------------------------------------------
# what goes in, what comes out
# --------------------------------------------------------------------------


@dataclass
class RowBlockSpec:
    recipe: str
    machine: str
    rows: list[int]  # machines in each row, top to bottom
    belt: str | None = None
    inserter: str | None = None  # serves the nearer input belt, and the output
    long_inserter: str | None = None  # serves a second input belt, further out
    pole: str | None = None
    pipe: str | None = None
    pipe_to_ground: str | None = None
    stack: str = "mirror"  # or "repeat"
    align: str = "start"  # or "center": shorter rows centred, for non-rectangular plots
    input_belts: int | None = None
    speed_bonus: float = 0.0
    stack_size: int = 1


@dataclass(frozen=True)
class Placement:
    name: str
    x: int  # top-left tile
    y: int
    direction: int = 0
    recipe: str | None = None
    width: int = 1
    height: int = 1


@dataclass(frozen=True)
class Port:
    """Where something enters or leaves the block, for connecting it up.

    For a belt, `items` is the lane contents as (left lane, right lane) seen
    in the direction of travel, "" for a lane nothing uses. For a pipe it is
    the one fluid.
    """

    kind: str  # "belt" or "pipe"
    io: str  # "in" or "out"
    items: tuple[str, ...]
    x: int
    y: int
    direction: int  # the way it flows
    rate: float  # per second: demand at an input, supply at an output


@dataclass
class RowReport:
    index: int
    machines: int
    capacity: int  # 0 = no belt limits it
    shares_input: bool
    limit: str


@dataclass
class Block:
    spec: RowBlockSpec
    placements: list[Placement]
    ports: list[Port]
    width: int
    height: int
    rows: list[RowReport]
    capacity: layout.RowCapacity
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# prototype choices
# --------------------------------------------------------------------------


def _require(kind: str, name: str | None, role: str) -> str:
    if name is None:
        options = sorted(n for n, d in entity_data.raw.items() if d.get("type") == kind)
        raise LayoutError(f"choose a {role} ({kind}); the active data has: {', '.join(options)}")
    entry = entity_data.raw.get(name)
    if entry is None or entry.get("type") != kind:
        raise LayoutError(f"{name!r} is not a {kind} in the active data")
    return name


def _default_pipe(kind: str) -> str:
    """The plainest pipe the data has: default connection category, shortest name."""
    found = []
    for name, data in entity_data.raw.items():
        if data.get("type") != kind:
            continue
        connections = (data.get("fluid_box") or {}).get("pipe_connections") or []
        categories = set()
        for conn in connections:
            category = conn.get("connection_category") or ["default"]
            categories.update([category] if isinstance(category, str) else category)
        if "default" in categories:
            found.append(name)
    if not found:
        raise LayoutError(f"the active data has no {kind} with the default connection category")
    return min(found, key=lambda n: (len(n), n))


def _ptg_reach(name: str) -> int:
    for connection in (entity_data.raw[name].get("fluid_box") or {}).get("pipe_connections") or []:
        if connection.get("max_underground_distance"):
            return int(connection["max_underground_distance"])
    raise LayoutError(f"{name!r} states no underground reach")


def _is_electric(name: str) -> bool:
    source = (entity_data.raw.get(name) or {}).get("energy_source")
    return isinstance(source, dict) and source.get("type") == "electric"


# --------------------------------------------------------------------------
# inserter geometry, from the prototype
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Arm:
    """How one inserter prototype sits in an arm line.

    Worked out for an arm line with the machine *below* it for input, and
    *above* it for output; a turned row uses the same arm rotated half a turn.
    `distance` is how many lines out the belt it serves lies.
    """

    name: str
    role: str  # "input" or "output"
    direction: int
    distance: int
    pick_dx: int
    drop_dx: int
    far_lane: bool  # output only: drops beyond the belt's centre line


def _tile_offset(vector: tuple[float, float]) -> tuple[int, int]:
    return math.floor(0.5 + vector[0] + 1e-9), math.floor(0.5 + vector[1] + 1e-9)


def fit_arm(name: str, role: str, machine_height: int) -> Arm:
    data = entity_data.raw.get(name) or {}
    pickup, drop = data.get("pickup_position"), data.get("insert_position")
    if data.get("type") != "inserter" or pickup is None or drop is None:
        raise LayoutError(f"{name!r} is not an inserter with pickup and insert positions")
    fits = []
    for direction in (0, 4, 8, 12):
        p = fluid_rules.rotate(tuple(pickup), direction)
        q = fluid_rules.rotate(tuple(drop), direction)
        (px, py), (qx, qy) = _tile_offset(p), _tile_offset(q)
        if role == "input":  # machine below: take from above, put below
            if py <= -1 and 1 <= qy <= machine_height:
                fits.append(Arm(name, role, direction, -py, px, qx, False))
        else:  # machine above: take from above, put below
            if -machine_height <= py <= -1 and qy >= 1:
                within = (0.5 + q[1]) - qy  # how far into the belt tile it drops
                fits.append(Arm(name, role, direction, qy, px, qx, within > 0.5))
    if not fits:
        verb = "take from a belt and put into" if role == "input" else "take from and put onto a belt beside"
        raise LayoutError(
            f"{name} picks up at {pickup} and drops at {drop}; no rotation of it can {verb} "
            f"a {machine_height}-tall machine across a row"
        )
    return min(fits, key=lambda a: (abs(a.pick_dx) + abs(a.drop_dx), a.distance, a.direction))


# --------------------------------------------------------------------------
# one side of a row
# --------------------------------------------------------------------------


@dataclass
class Side:
    """Everything along one long side of a row."""

    role: str  # "input" or "output"
    belts: dict[int, list[str]]  # lines out from the arm line -> lane contents
    tracks: list[str]  # fluids on partner+track pairs, inner first
    direct: str | None  # a fluid laid straight along the arm line
    entries: dict[int, str]  # column within the machine -> fluid dived from here
    arms: list[tuple[Arm, int]]  # geometry, count per machine
    keep_line: bool  # an arm line is needed even with nothing on it

    @property
    def reach(self) -> int:
        return max(self.belts, default=0)

    @property
    def depth(self) -> int:
        if self.direct is not None:
            return 1
        lines = self.reach + 2 * len(self.tracks)
        if lines or self.arms or self.entries or self.keep_line:
            return 1 + lines
        return 0

    @property
    def shareable(self) -> bool:
        """Whether a mirrored neighbour's arms reach the very same lines."""
        if self.direct is not None:
            return True
        if not self.belts or self.tracks:
            return False
        return {self.reach + 1 - d for d in self.belts} == set(self.belts)

    def free_columns(self, width: int) -> int:
        if self.direct is not None or self.depth == 0:
            return 0
        return width - len(self.entries) - sum(n for _, n in self.arms)


@dataclass
class RowType:
    direction: int  # machine direction for a row with its input side on top
    width: int
    height: int
    top: Side  # input side
    bottom: Side  # output side
    misplaced: int = 0  # fluids piped on the side opposite their role


def _machine_connections(machine: str, recipe: str, direction: int) -> tuple[int, int, list]:
    """(width, height, [(box index, column, "top"/"bottom"/None)]) for a direction."""
    width, height = layout.machine_size(machine, direction)
    cx, cy = width / 2, height / 2
    found = []
    for index, box in enumerate(fluid_rules.active_boxes(machine, recipe)):
        for raw in box.get("pipe_connections") or []:
            if raw.get("position") is None or raw.get("direction") is None:
                continue
            if raw.get("connection_type") == "underground":
                continue
            ox, oy = fluid_rules.rotate(tuple(raw["position"]), direction)
            col, row = math.floor(cx + ox), math.floor(cy + oy)
            facing = (int(raw["direction"]) + direction) % 16
            side = None
            if facing == 0 and row == 0:
                side = "top"
            elif facing == 8 and row == height - 1:
                side = "bottom"
            found.append((index, col, side))
    return width, height, found


def _arm_columns(width: int, side: Side, turned: bool, span: tuple[int, int] | None = None, x: int = 0):
    """Columns for the pipe dives and every arm of one machine, or an error."""
    entries = {((width - 1 - c) if turned else c): fluid for c, fluid in side.entries.items()}
    free = [c for c in range(width) if c not in entries]
    placed: list[tuple[int, Arm]] = []
    for arm, count in side.arms:
        into_dx = arm.drop_dx if arm.role == "input" else arm.pick_dx
        belt_dx = arm.pick_dx if arm.role == "input" else arm.drop_dx
        if turned:
            into_dx, belt_dx = -into_dx, -belt_dx
        for _ in range(count):
            valid = [c for c in free if 0 <= c + into_dx < width]
            if span is not None:
                valid = [c for c in valid if span[0] <= x + c + belt_dx < span[1]]
            if not valid:
                raise LayoutError(
                    f"no arm-line tile of a {width}-wide machine lets {arm.name} reach both the "
                    f"machine and its belt ({len(entries)} pipe dive(s) and "
                    + ", ".join(f"{n} x {a.name}" for a, n in side.arms)
                    + " per machine). A faster inserter, a bigger stack or fewer machines per belt frees tiles"
                )
            middle = (width - 1) / 2
            c = min(valid, key=lambda v: (abs(v - middle), v))
            free.remove(c)
            placed.append((c, arm))
    return entries, placed, free


def _row_type(spec: RowBlockSpec, direction: int, capacity: layout.RowCapacity, ptg_reach: int) -> RowType:
    recipe = recipe_data.raw[spec.recipe]
    width, height, connections = _machine_connections(spec.machine, spec.recipe, direction)
    try:
        assigned = fluid_rules.assign_fluid_boxes(spec.recipe, spec.machine)
    except fluid_rules.FluidAssignmentError as exc:
        raise LayoutError(str(exc)) from exc

    boxes_of = fluid_rules.active_boxes(spec.machine, spec.recipe)
    # A fluid is keyed with its role: coal liquefaction takes heavy oil in and
    # gives heavy oil out, and those are two different pipes.
    wanted = [("input", p["name"]) for p in recipe.get("ingredients", []) if p.get("type") == "fluid"]
    wanted += [("output", p["name"]) for p in recipe.get("results", []) if p.get("type") == "fluid"]

    entries: dict[str, dict[int, tuple[str, str]]] = {"top": {}, "bottom": {}}
    tracks: dict[str, list[tuple[str, str]]] = {"top": [], "bottom": []}
    misplaced = 0
    for key in wanted:
        role, fluid = key
        boxes = {i for i, f in assigned.items() if f == fluid and boxes_of[i].get("production_type") == role}
        usable = {s: sorted(c for i, c, side in connections if i in boxes and side == s) for s in ("top", "bottom")}
        natural = "top" if role == "input" else "bottom"
        other = "bottom" if natural == "top" else "top"
        side = natural if usable[natural] else other if usable[other] else None
        if side is None:
            raise LayoutError(
                f"{spec.machine} facing {direction}: no fluid box for {fluid} connects across "
                "the row; its connections point into the neighbouring machine"
            )
        misplaced += side != natural
        # One connection per fluid is enough, even where the recipe spreads
        # the fluid over several boxes -- the arm-line tiles are worth more
        # as inserters.
        free = [c for c in usable[side] if c not in entries[side]]
        if not free:
            raise LayoutError(f"{spec.machine}: two fluids connect at the same tile")
        entries[side][free[0]] = key
        tracks[side].append(key)

    # Boxes the recipe leaves empty must not touch a pipe.
    idle = {s: {c for i, c, side in connections if side == s and i not in assigned} for s in ("top", "bottom")}

    solids_out = [p["name"] for p in recipe.get("results", []) if p.get("type") != "fluid"]
    need = capacity.inserters
    sides = {}
    for side, role in (("top", "input"), ("bottom", "output")):
        belts: dict[int, list[str]] = {}
        arms: list[tuple[Arm, int]] = []
        if role == "input" and capacity.input_lanes:
            servers = [spec.inserter, spec.long_inserter][: len(capacity.input_lanes)]
            for k, (server, lanes) in enumerate(zip(servers, capacity.input_lanes), start=1):
                if server is None:
                    raise LayoutError(
                        f"{spec.recipe} needs input belt {k}, served by an inserter reaching past "
                        "the first belt; choose a long_inserter"
                    )
                arm = fit_arm(server, "input", height)
                if arm.distance in belts:
                    raise LayoutError(
                        f"{spec.inserter} and {spec.long_inserter} both reach {arm.distance} tile(s) "
                        "out, so they cannot serve two different belts"
                    )
                belts[arm.distance] = list(lanes)
                arms.append((arm, need.get(f"input-{k}", 1)))
        elif role == "output" and solids_out:
            arm = fit_arm(spec.inserter, "output", height)
            belts[arm.distance] = list(solids_out)
            arms.append((arm, need.get("output", 1)))

        side_tracks = tracks[side]
        direct = side_tracks[0] if not belts and len(side_tracks) == 1 and not idle[side] else None
        s = Side(
            role=role,
            belts=belts,
            tracks=[] if direct else list(side_tracks),
            direct=direct,
            entries={} if direct else dict(entries[side]),
            arms=arms,
            keep_line=bool(idle[side] or entries[side]),
        )
        if s.tracks:
            deepest = s.reach + 2 * len(s.tracks) - 1  # arm line to the outermost partner
            if deepest > ptg_reach:
                raise LayoutError(
                    f"{spec.pipe_to_ground} reaches {ptg_reach} tiles; the outermost fluid on the "
                    f"{role} side surfaces {deepest} lines out"
                )
        if direct is None:
            for turned in (False, True):
                _arm_columns(width, s, turned)
        sides[side] = s
    return RowType(direction, width, height, sides["top"], sides["bottom"], misplaced)


# --------------------------------------------------------------------------
# lines: the vertical composition
# --------------------------------------------------------------------------


@dataclass
class Line:
    kind: str  # "arm", "belt", "partner", "track", "direct", "machines", "blank"
    rows: list[tuple[int, str]]  # (row index, side) served by this line
    fluid: str | None = None
    items: tuple[str, ...] = ()
    role: str = ""


def _side_lines(row: int, side_name: str, side: Side) -> list[Line]:
    """A side's lines, inner first."""
    if side.depth == 0:
        return []
    owner = [(row, side_name)]
    if side.direct is not None:
        return [Line("direct", list(owner), fluid=side.direct, role=side.role)]
    lines = [Line("arm", list(owner), role=side.role)]
    for distance in range(1, side.reach + 1):
        if distance in side.belts:
            lines.append(Line("belt", list(owner), items=tuple(side.belts[distance]), role=side.role))
        else:
            lines.append(Line("blank", list(owner)))
    for fluid in side.tracks:
        lines.append(Line("partner", list(owner), fluid=fluid, role=side.role))
        lines.append(Line("track", list(owner), fluid=fluid, role=side.role))
    return lines


def _compose(row_type: RowType, count: int, stack: str) -> tuple[list[Line], list[int], list[bool]]:
    """All lines top to bottom, the machine line of each row, and which rows are turned."""
    lines: list[Line] = []
    band_lines: list[int] = []
    flipped: list[bool] = []
    for row in range(count):
        turned = stack == "mirror" and row % 2 == 1
        top_side, bottom_side = (row_type.bottom, row_type.top) if turned else (row_type.top, row_type.bottom)
        top = list(reversed(_side_lines(row, "top", top_side)))
        bottom = _side_lines(row, "bottom", bottom_side)

        if lines and top and stack == "mirror" and top_side.shareable:
            # In mirror stacking the previous row's bottom side is this same
            # kind of side, the other way up, so its outer lines are ours too.
            if top_side.direct is not None:
                lines[-1].rows.append((row, "top"))
                top = []
            else:
                for offset in range(top_side.reach):
                    lines[-1 - offset].rows.append((row, "top"))
                top = [ln for ln in top if ln.kind == "arm"]
        elif lines and top:
            last, first = lines[-1], top[0]
            if stack == "mirror" and last.kind == first.kind == "track" and last.fluid == first.fluid:
                # Mirrored neighbours with the same outermost fluid share the
                # pipe; each row's partner line still surfaces into it.
                last.rows.append((row, "top"))
                top = top[1:]
            elif last.kind in ("track", "direct") and first.kind in ("track", "direct") and last.fluid[1] != first.fluid[1]:
                lines.append(Line("blank", []))
        lines.extend(top)
        band_lines.append(len(lines))
        lines.append(Line("machines", [(row, "band")]))
        lines.extend(bottom)
        flipped.append(turned)
    return lines, band_lines, flipped


# --------------------------------------------------------------------------
# power
# --------------------------------------------------------------------------


def _pole_numbers(pole: str) -> tuple[int, int, int, float]:
    data = entity_data.raw[pole]
    pw, ph = layout.machine_size(pole)
    return pw, ph, int(data.get("supply_area_distance") or 0), float(data.get("maximum_wire_distance") or 0)


def _pole_interval(pole: str, width: int) -> int:
    """Machines between gap columns, so poles standing in them reach and cover.

    Consecutive gap poles are `k * width + pole width` apart, which the wire
    must span, and every machine between two of them needs a tile inside one
    supply area. 0 means no interval works.
    """
    pw, _, span, wire = _pole_numbers(pole)
    best = 0
    for k in range(1, 256):
        if k * width + pw > wire:
            break
        if all(min(pw - pw // 2 + j * width, (k - 1 - j) * width + pw // 2 + 1) <= span for j in range(k)):
            best = k
    return best


def _place_poles(pole: str, electric: list, candidates: set) -> tuple[list[tuple[int, int]], int, list[str]]:
    """Poles on candidate tiles covering every electric entity, bridged into one network.

    Returns the poles' top-left tiles, how many separate networks remain, and
    notes.
    """
    pw, ph, span, wire = _pole_numbers(pole)
    notes: list[str] = []

    def fits(tile):
        return all((tile[0] + dx, tile[1] + dy) in candidates for dx in range(pw) for dy in range(ph))

    def centre(tile):
        return tile[0] + pw / 2, tile[1] + ph / 2

    def covers(tile, box):
        cx, cy = tile[0] + pw // 2, tile[1] + ph // 2
        x0, y0, x1, y1 = box
        return x0 - span <= cx <= x1 + span and y0 - span <= cy <= y1 + span

    spots = sorted(t for t in candidates if fits(t))
    poles: list[tuple[int, int]] = []
    used: set[tuple[int, int]] = set()

    def take(tile):
        poles.append(tile)
        used.update((tile[0] + dx, tile[1] + dy) for dx in range(pw) for dy in range(ph))

    def open_spot(tile):
        return not any((tile[0] + dx, tile[1] + dy) in used for dx in range(pw) for dy in range(ph))

    uncovered = sorted(electric)
    while uncovered:
        target = uncovered[0]
        options = [t for t in spots if covers(t, target) and open_spot(t)]
        if not options:
            raise LayoutError(
                f"no free tile can power the entity at {target[:2]} with {pole}; a pole with a "
                "larger supply area, or another inserter arrangement, would leave room"
            )
        window = [b for b in uncovered if b[0] <= target[2] + 2 * span + pw]

        def score(tile):
            linked = not poles or any(math.dist(centre(tile), centre(p)) <= wire for p in poles)
            return (linked, sum(1 for b in window if covers(tile, b)), tile[0])

        chosen = max(options, key=score)
        take(chosen)
        uncovered = [b for b in uncovered if not covers(chosen, b)]

    def membership():
        parent = list(range(len(poles)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(len(poles)):
            for j in range(i + 1, len(poles)):
                if math.dist(centre(poles[i]), centre(poles[j])) <= wire:
                    parent[find(i)] = find(j)
        return [find(i) for i in range(len(poles))]

    # Grow the first network toward the nearest other one, a pole at a time.
    pieces = 1
    for _ in range(len(spots) + 1):
        groups = membership()
        pieces = len(set(groups)) if poles else 1
        if pieces <= 1:
            break
        inside = [centre(p) for p, g in zip(poles, groups) if g == groups[0]]
        outside = [centre(p) for p, g in zip(poles, groups) if g != groups[0]]
        gap = min(math.dist(a, b) for a in inside for b in outside)
        best = None
        for tile in spots:
            if not open_spot(tile):
                continue
            c = centre(tile)
            if not any(math.dist(c, p) <= wire for p in inside):
                continue
            ahead = min(math.dist(c, b) for b in outside)
            if ahead < gap and (best is None or ahead < best[0]):
                best = (ahead, tile)
        if best is None:
            break
        take(best[1])
    if pieces > 1:
        notes.append(
            f"the pole network is in {pieces} pieces; no free tile bridges them with {pole}'s "
            f"{wire:g}-tile wire -- a pole with a longer maximum_wire_distance would"
        )
    return poles, pieces, notes


# --------------------------------------------------------------------------
# the block
# --------------------------------------------------------------------------


def build_block(spec: RowBlockSpec) -> Block:
    if not spec.rows or any(n < 1 for n in spec.rows):
        raise LayoutError("every row needs at least one machine")
    if spec.recipe not in recipe_data.raw:
        raise LayoutError(f"{spec.recipe!r} is not a recipe in the active data")
    machine_entry = entity_data.raw.get(spec.machine)
    if machine_entry is None or "crafting_speed" not in machine_entry:
        raise LayoutError(f"{spec.machine!r} is not a crafting machine in the active data")
    category = recipe_data.raw[spec.recipe].get("category", "crafting")
    if category not in (machine_entry.get("crafting_categories") or ()):
        raise LayoutError(f"{spec.machine} does not craft {category!r} recipes like {spec.recipe}")
    if spec.stack not in ("mirror", "repeat") or spec.align not in ("start", "center"):
        raise LayoutError("stack is 'mirror' or 'repeat'; align is 'start' or 'center'")

    solids = layout.solid_flows(spec.recipe, spec.machine, 1, spec.speed_bonus)
    if solids:
        _require("transport-belt", spec.belt, "belt")
        _require("inserter", spec.inserter, "inserter")
        if spec.long_inserter is not None:
            _require("inserter", spec.long_inserter, "long-handed inserter")
    _require("electric-pole", spec.pole, "pole")
    spec.pipe = spec.pipe or _default_pipe("pipe")
    spec.pipe_to_ground = spec.pipe_to_ground or _default_pipe("pipe-to-ground")
    ptg_reach = _ptg_reach(spec.pipe_to_ground)

    def capacity_for(share: int) -> layout.RowCapacity:
        if not solids:
            return layout.row_capacity(spec.recipe, spec.machine, spec.belt or "", speed_bonus=spec.speed_bonus)
        return layout.row_capacity(
            spec.recipe, spec.machine, spec.belt,
            inserter=spec.inserter, long_inserter=spec.long_inserter,
            input_belts=spec.input_belts, rows_per_input_belt=share,
            stack_size=spec.stack_size, speed_bonus=spec.speed_bonus,
        )

    alone = capacity_for(1)
    shared = capacity_for(2) if alone.input_lanes else alone
    # Arms on a pair of shared input belts each take from the other row's
    # nearer belt too, so size every input arm for the busier belt.
    sizing_needs = dict(alone.inserters)
    if spec.stack == "mirror" and len(alone.input_lanes) > 1:
        busiest = max(n for k, n in sizing_needs.items() if k.startswith("input"))
        for k in list(sizing_needs):
            if k.startswith("input"):
                sizing_needs[k] = busiest
    sizing = layout.RowCapacity(**{**alone.__dict__, "inserters": sizing_needs})

    attempts: list[tuple[int, int, int, RowType]] = []
    reasons: list[str] = []
    for preference, direction in enumerate((0, 8, 4, 12)):
        try:
            candidate = _row_type(spec, direction, sizing, ptg_reach)
        except LayoutError as exc:
            reasons.append(str(exc))
            continue
        lines, _, _ = _compose(candidate, 3, spec.stack)
        tall = sum(candidate.height if ln.kind == "machines" else 1 for ln in lines)
        attempts.append((tall, candidate.misplaced, preference, candidate))
    if not attempts:
        raise LayoutError("; ".join(dict.fromkeys(reasons)))
    row_type = min(attempts, key=lambda a: a[:3])[3]

    # Poles on free tiles first. Gap columns between machines cost width, so
    # they are the fallback for rows that leave no room to power or join up
    # any other way.
    intervals = [0]
    gap = _pole_interval(spec.pole, row_type.width)
    if gap:
        intervals.append(gap)
    tried: list[tuple[int, int, Block]] = []
    failure: LayoutError | None = None
    for order, interval in enumerate(intervals):
        try:
            block, pieces = _emit(spec, row_type, interval, alone, shared)
        except LayoutError as exc:
            failure = exc
            continue
        tried.append((pieces, order, block))
        if pieces <= 1:
            break
    if not tried:
        raise failure or LayoutError(f"{spec.pole} cannot power this row")
    return min(tried, key=lambda t: t[:2])[2]


def _emit(spec: RowBlockSpec, row_type: RowType, interval: int, alone, shared) -> tuple[Block, int]:
    """Place everything for one choice of gap interval (0 = no gap columns)."""
    w, h = row_type.width, row_type.height
    pw, ph = layout.machine_size(spec.pole)
    machine_entry = entity_data.raw[spec.machine]
    lines, band_lines, flipped = _compose(row_type, len(spec.rows), spec.stack)
    y_of: list[int] = []
    y = 0
    for line in lines:
        y_of.append(y)
        y += h if line.kind == "machines" else 1
    height = y

    def row_width(n: int) -> int:
        # Gap columns: before the first machine, after every `interval`
        # machines, after the last.
        return n * w if not interval else n * w + ((n - 1) // interval + 2) * pw

    widest = max(row_width(n) for n in spec.rows)
    starts = [0 if spec.align == "start" else (widest - row_width(n)) // 2 for n in spec.rows]

    def machine_x(row: int, i: int) -> int:
        return starts[row] + i * w + ((i // interval + 1) * pw if interval else 0)

    placements: list[Placement] = []

    def put(name, x, y, direction=0, recipe=None, width=1, height=1):
        placements.append(Placement(name, x, y, direction, recipe, width, height))

    is_assembler = machine_entry.get("type") == "assembling-machine"
    electric: list[tuple[int, int, int, int]] = []
    candidates: set[tuple[int, int]] = set()
    notes: list[str] = list(alone.notes)

    for row, n in enumerate(spec.rows):
        yb = y_of[band_lines[row]]
        direction = (row_type.direction + (8 if flipped[row] else 0)) % 16
        for i in range(n):
            x = machine_x(row, i)
            put(spec.machine, x, yb, direction, spec.recipe if is_assembler else None, w, h)
            if _is_electric(spec.machine):
                electric.append((x, yb, x + w - 1, yb + h - 1))

    def span_of(line: Line) -> tuple[int, int]:
        if not line.rows:
            return 0, widest
        rows = {r for r, _ in line.rows}
        return (
            min(starts[r] for r in rows),
            max(starts[r] + row_width(spec.rows[r]) for r in rows),
        )

    def side_of(row: int, side_name: str) -> Side:
        if flipped[row]:
            return row_type.bottom if side_name == "top" else row_type.top
        return row_type.top if side_name == "top" else row_type.bottom

    solid_rate = {f.item: f.rate for f in alone.per_machine}
    fluid_rate = {("input" if f.direction == "in" else "output", f.item): f.rate for f in alone.fluids_per_machine}
    ports: list[Port] = []

    for index, line in enumerate(lines):
        yl = y_of[index]
        if line.kind in ("machines", "blank", "arm", "partner") or not line.rows:
            continue
        x0, x1 = span_of(line)
        served = sum(spec.rows[r] for r, _ in line.rows)
        if line.kind == "belt":
            for x in range(x0, x1):
                put(spec.belt, x, yl, EAST)
            rate = sum(solid_rate.get(i, 0) for i in set(line.items)) * served
            if line.role == "input":
                lanes = tuple(line.items) if len(line.items) == 2 else (line.items[0], line.items[0])
                ports.append(Port("belt", "in", lanes, x0, yl, EAST, rate))
            else:
                # Travelling east, the north lane is the left one.
                left = right = ""
                for r, side_name in line.rows:
                    arm = side_of(r, side_name).arms[0][0]
                    if arm.far_lane == (side_name == "bottom"):
                        right = "+".join(line.items)
                    else:
                        left = "+".join(line.items)
                ports.append(Port("belt", "out", (left, right), x1 - 1, yl, EAST, rate))
        else:  # track or direct
            for x in range(x0, x1):
                put(spec.pipe, x, yl)
            role, fluid = line.fluid
            io = "in" if role == "input" else "out"
            rate = fluid_rate.get(line.fluid, 0) * served
            ports.append(Port("pipe", io, (fluid,), x0 if io == "in" else x1 - 1, yl, EAST, rate))

    for index, line in enumerate(lines):
        if line.kind not in ("arm", "partner"):
            continue
        yl = y_of[index]
        for row, side_name in line.rows:
            turned = flipped[row]
            side = side_of(row, side_name)
            above_machine = side_name == "top"
            span = (starts[row], starts[row] + row_width(spec.rows[row]))
            for i in range(spec.rows[row]):
                x = machine_x(row, i)
                entries, arms, spare = _arm_columns(w, side, turned, span, x)
                if line.kind == "partner":
                    for c, key in entries.items():
                        if key == line.fluid:
                            put(spec.pipe_to_ground, x + c, yl, 0 if above_machine else 8)
                    continue
                for c in entries:
                    put(spec.pipe_to_ground, x + c, yl, 8 if above_machine else 0)
                for c, arm in arms:
                    put(arm.name, x + c, yl, (arm.direction + (8 if turned else 0)) % 16)
                    if _is_electric(arm.name):
                        electric.append((x + c, yl, x + c, yl))
                candidates.update((x + c, yl) for c in spare)

    # Poles may also stand on free tiles of partner and blank lines, which is
    # what bridges rows kept apart by fluid tracks, and in gap columns.
    for index, line in enumerate(lines):
        if line.kind in ("partner", "blank"):
            x0, x1 = span_of(line)
            candidates.update((x, y_of[index]) for x in range(x0, x1))
    if interval:
        for row, n in enumerate(spec.rows):
            gaps = [starts[row]] + [machine_x(row, i) - pw for i in range(interval, n, interval)]
            gaps.append(machine_x(row, n - 1) + w)
            for gx in gaps:
                candidates.update((gx + dx, yy) for dx in range(pw) for yy in range(height))
    candidates -= {(p.x + dx, p.y + dy) for p in placements for dx in range(p.width) for dy in range(p.height)}

    spots, pieces, pole_notes = _place_poles(spec.pole, electric, candidates)
    for x, yp in spots:
        put(spec.pole, x, yp, width=pw, height=ph)
    notes.extend(pole_notes)
    if interval:
        notes.append(f"poles needed gap columns: one after every {interval} machine(s)")

    reports = []
    for row, n in enumerate(spec.rows):
        shares = any(
            ln.kind == "belt" and ln.role == "input" and len({r for r, _ in ln.rows}) > 1
            and any(r == row for r, _ in ln.rows)
            for ln in lines
        )
        cap = shared if shares else alone
        reports.append(RowReport(row, n, cap.per_row, shares, cap.limit))
        if cap.per_row and n > cap.per_row:
            hint = ""
            lanes = cap.input_lanes
            if len(lanes) == 1 and len(set(lanes[0])) == 2 and spec.long_inserter and spec.input_belts is None:
                hint = "; input_belts=2 would give each ingredient more lanes"
            notes.append(
                f"row {row + 1} has {n} machines where its belts keep {cap.per_row} running ({cap.limit}){hint}"
            )

    return Block(spec, placements, ports, widest, height, reports, alone, notes), pieces


# --------------------------------------------------------------------------
# moving a finished block
# --------------------------------------------------------------------------


def transform(block: Block, at: tuple[int, int], rotate: int) -> tuple[list[Placement], list[Port], int, int]:
    """Turn the block clockwise by `rotate` degrees and put its corner at `at`."""
    if rotate % 90:
        raise LayoutError("rotate is 0, 90, 180 or 270")
    turns = (rotate // 90) % 4

    def rect(x, y, width, height):
        for _ in range(turns):
            x, y, width, height = -(y + height), x, height, width
        return x, y, width, height

    placed = []
    for p in block.placements:
        x, y, width, height = rect(p.x, p.y, p.width, p.height)
        placed.append(Placement(p.name, x, y, (p.direction + 4 * turns) % 16, p.recipe, width, height))
    ports = []
    for port in block.ports:
        x, y, _, _ = rect(port.x, port.y, 1, 1)
        ports.append(Port(port.kind, port.io, port.items, x, y, (port.direction + 4 * turns) % 16, port.rate))

    dx = at[0] - min((p.x for p in placed), default=0)
    dy = at[1] - min((p.y for p in placed), default=0)
    placed = [Placement(p.name, p.x + dx, p.y + dy, p.direction, p.recipe, p.width, p.height) for p in placed]
    ports = [Port(q.kind, q.io, q.items, q.x + dx, q.y + dy, q.direction, q.rate) for q in ports]
    width, height = (block.width, block.height) if turns % 2 == 0 else (block.height, block.width)
    return placed, ports, width, height
