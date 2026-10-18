"""Look at a blueprint and say what looks wrong, without changing anything.

A player's own blueprints are the best source of their style — the grid they
build on, the belt tier they use, how long their trains are. They are not a
source of correctness. A real blueprint can have a belt someone rotated by
accident, a wire never run, a filter set on an inserter that ignores filters.
Learning style from such a blueprint is right; learning its mistakes is not.

So this module separates three things that want opposite treatment:

    style        a choice — copy it
    a defect     a mistake — never copy it, say so
    inefficiency neither — it may be entirely deliberate

and it only ever reports. Nothing here edits a blueprint. The line between a
mistake and an intentional oddity is usually not ours to draw, and a tool that
quietly "fixes" a layout is worse than one that points and asks.

Findings come in three strengths, because crying wolf is the way to make
someone stop reading: `PROBLEM` is almost certainly wrong, `SUSPECT` is worth a
look and has honest reasons to be deliberate, `NOTE` is context.

## Edges

Most blueprints are fragments meant to join onto something else. A belt running
off the edge is normal, and so is a machine with no power in a piece that
carries none. Checks that would otherwise fire on every second blueprint are
therefore edge-aware: anything reaching past the boundary is presumed to meet
whatever is out there.
"""

from __future__ import annotations

import math

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Iterator

import draftsman.data.entities as entity_data
from draftsman.data import recipes as recipe_data

# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------


class Severity(str, Enum):
    PROBLEM = "problem"
    SUSPECT = "suspect"
    NOTE = "note"


@dataclass(frozen=True)
class Finding:
    """One thing worth telling the player about."""

    severity: Severity
    code: str
    summary: str
    detail: str = ""
    position: tuple[int, int] | None = None
    entities: tuple[str, ...] = ()

    def __str__(self) -> str:
        where = f" at {self.position[0]}, {self.position[1]}" if self.position else ""
        return f"[{self.severity.value}] {self.summary}{where}"


@dataclass
class Report:
    """Everything one inspection found."""

    findings: list[Finding] = field(default_factory=list)
    entities_checked: int = 0
    # Which game data the checks read. The same blueprint legitimately gives
    # different answers under different mod sets, so a report that does not say
    # which one it used is not reproducible.
    dataset: str = ""

    def of(self, severity: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity is severity]

    @property
    def problems(self) -> list[Finding]:
        return self.of(Severity.PROBLEM)

    @property
    def suspects(self) -> list[Finding]:
        return self.of(Severity.SUSPECT)

    @property
    def notes(self) -> list[Finding]:
        return self.of(Severity.NOTE)

    @property
    def clean(self) -> bool:
        return not self.problems and not self.suspects

    def summary(self) -> str:
        against = f" (against {self.dataset})" if self.dataset else ""
        if self.clean:
            return f"{self.entities_checked} entities, nothing to report{against}"
        bits = []
        if self.problems:
            bits.append(f"{len(self.problems)} problem(s)")
        if self.suspects:
            bits.append(f"{len(self.suspects)} to look at")
        if self.notes:
            bits.append(f"{len(self.notes)} note(s)")
        return f"{self.entities_checked} entities: " + ", ".join(bits) + against


# --------------------------------------------------------------------------
# what the checks work from
# --------------------------------------------------------------------------

# 2.0 counts direction in sixteenths of a turn from north.
STEP: dict[int, tuple[int, int]] = {
    0: (0, -1),
    4: (1, 0),
    8: (0, 1),
    12: (-1, 0),
}


class Layout:
    """A blueprint arranged so questions about neighbours are cheap."""

    def __init__(self, blueprint) -> None:
        self.blueprint = blueprint
        self.entities = list(blueprint.entities)
        self.occupied: dict[tuple[int, int], list[int]] = {}
        for index, entity in enumerate(self.entities):
            for tile in self.tiles_of(entity):
                self.occupied.setdefault(tile, []).append(index)

        xs = [t[0] for t in self.occupied] or [0]
        ys = [t[1] for t in self.occupied] or [0]
        self.left, self.right = min(xs), max(xs)
        self.top, self.bottom = min(ys), max(ys)

        self.wired: set[int] = set()
        for wire in getattr(blueprint, "wires", None) or []:
            for part in (wire[0], wire[2]):
                resolved = _resolve(part)
                if resolved is not None:
                    self.wired.add(id(resolved))

    @staticmethod
    def tiles_of(entity) -> Iterator[tuple[int, int]]:
        x, y = int(entity.tile_position.x), int(entity.tile_position.y)
        for dx in range(entity.tile_width):
            for dy in range(entity.tile_height):
                yield (x + dx, y + dy)

    def at(self, x: float, y: float) -> list:
        """Entities occupying a tile, given a position that may be a centre."""
        import math

        key = (math.floor(x), math.floor(y))
        return [self.entities[i] for i in self.occupied.get(key, ())]

    def outside(self, x: float, y: float) -> bool:
        """Whether a tile falls beyond the blueprint, where anything may be."""
        import math

        tx, ty = math.floor(x), math.floor(y)
        return not (self.left <= tx <= self.right and self.top <= ty <= self.bottom)

    def has_wire(self, entity) -> bool:
        return id(entity) in self.wired

    @staticmethod
    def tile_of(entity) -> tuple[int, int]:
        return int(entity.tile_position.x), int(entity.tile_position.y)


def _resolve(part):
    """An Association dereferences to the entity; anything else passes through."""
    try:
        return part()
    except TypeError:
        return part
    except Exception:
        return None


def _raw(entity) -> dict:
    """The prototype data of an entity, or of a prototype given by name."""
    return entity_data.raw.get(entity if isinstance(entity, str) else entity.name, {})


def underground_reach(entity) -> int | None:
    """How far this entity's underground run may span, or None if unknown.

    Read from the prototype every time, because mods change it wholesale and
    a constant here would be wrong by multiples. Krastorio 2 alone gives the
    express underground twenty tiles where vanilla gives nine, and adds
    undergrounds reaching thirty and forty.

    Belts state it plainly; pipes bury it in their fluid box, which is a
    different place for the same idea. Takes an entity or a prototype name.
    """
    raw = _raw(entity)
    plain = raw.get("max_distance")
    if plain is not None:
        return int(plain)

    fluid_box = raw.get("fluid_box") or {}
    for connection in fluid_box.get("pipe_connections") or []:
        distance = connection.get("max_underground_distance")
        if distance is not None:
            return int(distance)

    # Not knowing is a fact, not a licence to assume a number.
    return None


def underground_direction(entity) -> int | None:
    """Which way this entity's underground run leaves it, or None if unknown.

    Not simply the way the entity faces. A pipe to ground shows its open end
    above ground and buries the run behind it: the prototype states the two
    connections separately, the visible one at the entity's own direction and
    the underground one at the opposite. Reading it rather than assuming the
    opposite matters because a mod is free to place it anywhere -- the number
    lives in the data, so that is where it is taken from.

    Belts say nothing of the kind. Their run follows the way they face, turned
    around for the receiving end, which the caller knows from `io_type`.
    """
    raw = _raw(entity)
    fluid_box = raw.get("fluid_box") or {}
    for connection in fluid_box.get("pipe_connections") or []:
        if connection.get("connection_type") == "underground":
            offset = connection.get("direction")
            if offset is not None:
                return int(offset)
    return None


def is_known(entity) -> bool:
    """Whether the loaded game data has anything to say about this entity."""
    return bool(entity_data.raw.get(entity.name))


def active_dataset() -> str:
    """Which profile's data the checks are reading, if it was recorded."""
    try:
        from .profile import Profile

        name = Profile.active_profile_name()
    except Exception:
        name = None
    return name or "the bundled data (no profile activated)"


def _needs_electricity(entity) -> bool:
    source = _raw(entity).get("energy_source")
    kind = source.get("type") if isinstance(source, dict) else source
    return kind == "electric"


def _condition_is_set(condition) -> bool:
    return condition is not None and getattr(condition, "first_signal", None) is not None


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

Check = Callable[[Layout], Iterable[Finding]]
CHECKS: list[Check] = []


def check(function: Check) -> Check:
    CHECKS.append(function)
    return function


@check
def prototypes_the_data_does_not_know(layout: Layout) -> Iterator[Finding]:
    """Entities absent from the loaded game data.

    Almost always the wrong profile: checking a Krastorio blueprint against
    vanilla data, say. Every other check reads sizes, reach and behaviour from
    that data, so if it is the wrong set then the rest of this report is not to
    be trusted. Said first, and loudly, for that reason.
    """
    unknown = sorted({e.name for e in layout.entities if not is_known(e)})
    if not unknown:
        return
    shown = ", ".join(unknown[:5]) + (f" and {len(unknown) - 5} more" if len(unknown) > 5 else "")
    yield Finding(
        Severity.PROBLEM,
        "unknown-prototypes",
        f"{len(unknown)} entity type(s) are missing from the game data in use",
        f"Not found: {shown}. The data being read is {active_dataset()}. "
        "Everything else in this report depends on that data, so activate the "
        "profile this blueprint belongs to and check again.",
        None,
        tuple(unknown[:5]),
    )


@check
def undergrounds_without_a_pair(layout: Layout) -> Iterator[Finding]:
    """An underground belt or pipe that never surfaces carries nothing."""
    for entity in layout.entities:
        kind = getattr(entity, "type", "")
        if kind not in ("underground-belt", "pipe-to-ground"):
            continue
        direction = int(getattr(entity, "direction", 0) or 0)
        step = STEP.get(direction)
        if step is None:
            continue

        reach = underground_reach(entity)
        if reach is None:
            # Without the prototype we cannot say how far it should reach, and
            # guessing would produce confident nonsense. The missing data is
            # reported separately by the unknown-prototype check.
            continue
        offset = underground_direction(entity)
        if offset is not None:
            # A pipe's run leaves it the way its underground connection points,
            # which is its own direction turned by whatever the prototype says
            # -- the opposite way, in every pipe to ground seen so far.
            step = STEP.get((direction + offset) % 16)
            if step is None:
                continue
        elif getattr(entity, "io_type", None) == "output":
            # A belt input travels the way it faces; an output receives from
            # behind it.
            step = (-step[0], -step[1])

        x, y = layout.tile_of(entity)
        found = False
        left_the_blueprint = False
        for distance in range(1, reach + 1):
            tx, ty = x + step[0] * distance, y + step[1] * distance
            if layout.outside(tx, ty):
                left_the_blueprint = True
                break
            # `is not entity` matters: an underground wider than one tile
            # covers the first tiles the search walks through, and matching on
            # name alone let it find itself and call that a pair. Everything
            # vanilla is one tile across, so this only shows up under a mod --
            # the ducts in Fluid Must Flow are two.
            if any(
                other is not entity and other.name == entity.name
                for other in layout.at(tx, ty)
            ):
                found = True
                break

        if found or left_the_blueprint:
            continue

        if kind == "pipe-to-ground":
            # Not a defect on its own. A pipe to ground does not connect to
            # above-ground pipes on its buried side, so one placed alone caps a
            # run: nothing built further along can join it. Asking where the
            # run would continue to is the wrong question, since it is not
            # meant to continue. Said, because a genuinely forgotten end looks
            # the same; not accused, because this one usually is not.
            yield Finding(
                Severity.NOTE,
                "underground-unpaired",
                f"{entity.name} has no matching end within {reach} tiles",
                "Alone it caps the run rather than carrying anything, which is "
                "often exactly what was wanted.",
                (x, y),
                (entity.name,),
            )
            continue

        yield Finding(
            Severity.SUSPECT,
            "underground-unpaired",
            f"{entity.name} has no matching end within {reach} tiles",
            "Items entering it stop there. The other end may lie outside this "
            "blueprint.",
            (x, y),
            (entity.name,),
        )


def underground_partners(entities: list) -> dict[int, int]:
    """Each underground belt entrance's exit, by index into `entities`.

    An entrance walks the way it faces, up to its reach, and pairs with the
    first underground of its own name it meets -- if that one is an exit
    carrying the same way. Anything else of its name there (an entrance, an
    exit facing elsewhere) leaves it unpaired. The router and the lane
    tracker both pair through here, so they agree on every tunnel.
    """
    by_tile: dict[tuple[int, int], list[int]] = {}
    for index, entity in enumerate(entities):
        for tile in Layout.tiles_of(entity):
            by_tile.setdefault(tile, []).append(index)
    partners: dict[int, int] = {}
    for index, entity in enumerate(entities):
        if getattr(entity, "type", "") != "underground-belt" or getattr(entity, "io_type", None) != "input":
            continue
        direction = int(getattr(entity, "direction", 0) or 0)
        step = STEP.get(direction)
        if step is None:
            continue
        x, y = Layout.tile_of(entity)
        for k in range(1, (underground_reach(entity) or 0) + 1):
            other = next(
                (i for i in by_tile.get((x + step[0] * k, y + step[1] * k), ())
                 if i != index and entities[i].name == entity.name),
                None,
            )
            if other is None:
                continue
            found = entities[other]
            if getattr(found, "io_type", None) == "output" and int(getattr(found, "direction", 0) or 0) == direction:
                partners[index] = other
            break
    return partners


def inserter_reach(entity) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Where an inserter takes from and where it puts, in world coordinates.

    A blueprint may carry the two positions itself, as offsets from the
    inserter, and when it does they are the answer: they are what the game
    wrote down, and mods that let an inserter reach sideways or diagonally --
    Bob's Inserters, Change Inserter Drop Lane -- express themselves entirely
    through them. They are already oriented, so they are added as they stand.

    Checked against a real base of 298 such inserters: taken as written the
    pickups land on belts, underground belts and chests, and on nothing
    absurd; rotated by the inserter's direction, thirty-six of them take from
    another inserter. The computed positions disagree with both, which is why
    they are the last resort rather than the first.
    """
    position = entity.position
    stored = entity.to_dict()
    pickup, drop = stored.get("pickup_position"), stored.get("drop_position")
    if pickup is not None and drop is not None:
        return (
            (position.x + pickup[0], position.y + pickup[1]),
            (position.x + drop[0], position.y + drop[1]),
        )

    computed_pickup = getattr(entity, "pickup_position", None)
    computed_drop = getattr(entity, "drop_position", None)
    if computed_pickup is None or computed_drop is None:
        return None
    return (
        (computed_pickup.x, computed_pickup.y),
        (computed_drop.x, computed_drop.y),
    )


@check
def inserters_reaching_nothing(layout: Layout) -> Iterator[Finding]:
    """An inserter with empty tiles on both sides does nothing at all."""
    for entity in layout.entities:
        if getattr(entity, "type", "") != "inserter":
            continue
        reach = inserter_reach(entity)
        if reach is None:
            continue
        pickup, drop = reach

        # Reading the positions rather than reasoning from `direction`, which
        # names the side the inserter takes from -- the opposite of the obvious
        # guess, and not even fixed once mods are involved.
        pickup_empty = not layout.at(*pickup) and not layout.outside(*pickup)
        drop_empty = not layout.at(*drop) and not layout.outside(*drop)

        x, y = layout.tile_of(entity)
        if pickup_empty and drop_empty:
            yield Finding(
                Severity.PROBLEM,
                "inserter-idle",
                f"{entity.name} has nothing to take from and nothing to feed",
                "Both the tile it reaches into and the tile it drops onto are empty.",
                (x, y),
                (entity.name,),
            )
            continue

        taking = layout.at(*pickup)
        giving = layout.at(*drop)
        if taking and giving and taking[0] is giving[0]:
            yield Finding(
                Severity.PROBLEM,
                "inserter-loop",
                f"{entity.name} takes from and drops into the same {taking[0].name}",
                "It will move items in a circle.",
                (x, y),
                (entity.name, taking[0].name),
            )


def _solid_items(parts) -> frozenset[str]:
    return frozenset(p["name"] for p in parts or [] if p.get("type") != "fluid" and p.get("name"))


def backwards_inserters(entities: list, lane_map, complete: bool = False) -> list[Finding]:
    """Inserters that work in the wrong direction, judged from what stands on each side.

    In a blueprint an inserter's direction points at the side it takes FROM, so
    a row built by eye is easily mirrored: the input inserter then takes the
    product out of the machine and puts it on the belt that feeds it, and the
    output inserter takes from an empty belt and puts into the machine. An
    inserter between two things looks fine to `inserters_reaching_nothing`
    either way round, so the sides are asked what they carry.

    `lane_map` is a `lanes.trace` of `entities`. Two kinds of verdict:

    - Without `complete`, only what holds whatever else is true about the
      supply: an item taken from a belt that no ingredient of the receiving
      machine is, two machines handed something neither uses, and (as a
      suspect) a machine that inserters only take from or only bring to.
      A product put on a belt upstream of the machine's own input is fine:
      one belt may carry both ways. A belt that
      carries nothing is no verdict here, since the supply may come from a
      source that is not part of the picture.
    - With `complete` (the trace was given every declared source), a machine
      that nothing brings an ingredient to, or an inserter taking from a belt
      that nothing is put on, is a verdict too.

    Where the answer depends on something not in the picture -- a belt of
    unknown content, a machine with no recipe, a machine at the edge of the
    blueprint -- nothing is said, or a suspect at most.
    """
    from .lanes import _filter_of

    index_at: dict[tuple[int, int], list[int]] = {}
    for index, entity in enumerate(entities):
        for tile in Layout.tiles_of(entity):
            index_at.setdefault(tile, []).append(index)
    if not index_at:
        return []
    lo_x, hi_x = min(t[0] for t in index_at), max(t[0] for t in index_at)
    lo_y, hi_y = min(t[1] for t in index_at), max(t[1] for t in index_at)
    tiles_cache: dict[int, list] = {}

    def tiles_of(index: int) -> list:
        if index not in tiles_cache:
            tiles_cache[index] = list(Layout.tiles_of(entities[index]))
        return tiles_cache[index]

    def beyond(tile) -> bool:
        return not (lo_x <= tile[0] <= hi_x and lo_y <= tile[1] <= hi_y)

    def near_the_edge(index: int) -> bool:
        """Whether an inserter standing beside this machine could lie outside the blueprint."""
        tiles = tiles_of(index)
        left, right = min(t[0] for t in tiles) - 1, max(t[0] for t in tiles) + 1
        top, bottom = min(t[1] for t in tiles) - 1, max(t[1] for t in tiles) + 1
        return any(beyond((x, y)) for x in range(left, right + 1) for y in (top, bottom)) or any(
            beyond((x, y)) for y in range(top, bottom + 1) for x in (left, right)
        )

    def machine_at(tile):
        return next((i for i in index_at.get(tile, ()) if i in lane_map.machines), None)

    def ingredients(machine: int) -> frozenset[str]:
        return _solid_items(recipe_data.raw[lane_map.machines[machine][0]].get("ingredients"))

    def products(machine: int) -> frozenset[str]:
        return _solid_items(recipe_data.raw[lane_map.machines[machine][0]].get("results"))

    def burns(machine: int) -> bool:
        source = (entity_data.raw.get(entities[machine].name) or {}).get("energy_source") or {}
        return source.get("type") == "burner"

    def on_belt(tile):
        """(items, unknown) over both lanes of a belt tile, or None where there is no belt."""
        both = lane_map.at(tile)
        if both is None:
            return None
        return both[0].items | both[1].items, bool(both[0].unknown or both[1].unknown)

    def passing(filt, items):
        return frozenset(i for i in items if filt is None or filt.passes(i))

    def has_loader(machine: int) -> bool:
        tiles = tiles_of(machine)
        around = {(x + dx, y + dy) for x, y in tiles for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
        return any(
            getattr(entities[i], "type", "") in ("loader", "loader-1x1")
            for tile in around for i in index_at.get(tile, ())
        )

    reverse_edges: dict = {}
    for source_key, targets in lane_map.edges.items():
        for tile, lane, _ in targets:
            reverse_edges.setdefault((tile, lane), []).append(source_key)

    def line_of(tile) -> set:
        """Every belt tile joined to `tile` by items moving either way."""
        todo = [(tile, 0), (tile, 1)]
        seen = set(todo)
        while todo:
            key = todo.pop()
            for nxt in [k for k in reverse_edges.get(key, ())] + [(t, ln) for t, ln, _ in lane_map.edges.get(key, ())]:
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        return {key[0] for key in seen}

    def settled(tile) -> bool:
        """Whether every belt head behind `tile` is one the plan named.

        What a belt carries is only known as far as where it starts. A head
        nobody declared may be fed from outside the picture, and then both
        "it carries nothing" and "it carries only this" are guesses.
        """
        todo = [(tile, 0), (tile, 1)]
        seen = set(todo)
        while todo:
            key = todo.pop()
            behind = reverse_edges.get(key, ())
            if not behind and key[0] not in lane_map.feed_rates:
                return False
            for before in behind:
                if before not in seen:
                    seen.add(before)
                    todo.append(before)
        return True

    def real_feed(machine: int) -> bool:
        """Whether some inserter could bring this machine an ingredient."""
        needed = ingredients(machine)
        for tile, filt in lane_map.fed_by.get(machine, ()):
            if tile is None:
                return True
            seen = on_belt(tile)
            if seen is None or seen[1] or not settled(tile) or passing(filt, seen[0]) & needed:
                return True
        return False

    moves = []
    for entity in entities:
        if getattr(entity, "type", "") != "inserter":
            continue
        reach = inserter_reach(entity)
        if reach is None:
            continue
        pick = (math.floor(reach[0][0]), math.floor(reach[0][1]))
        put = (math.floor(reach[1][0]), math.floor(reach[1][1]))
        source, sink = machine_at(pick), machine_at(put)
        if source is not None and source == sink:
            continue  # a loop; `inserters_reaching_nothing` says so
        moves.append((entity, pick, put, source, sink))
    emptied = {source for _, _, _, source, _ in moves if source is not None}

    found: list[Finding] = []

    def report(entity, severity, summary, detail):
        found.append(Finding(severity, "inserter-backwards", summary, detail,
                             Layout.tile_of(entity), (entity.name,)))

    stuck: dict[int, list] = {}  # blind: machine -> inserters that only bring things to it
    starved: dict[int, list] = {}  # complete: machine -> [(entity, already reported)]
    for entity, pick, put, source, sink in moves:
        filt = _filter_of(entity)

        if source is not None:
            made = passing(filt, products(source))
            machine = lane_map.machines[source]
            if put in lane_map.nodes and made:
                feeding = {tile for tile, _ in lane_map.fed_by.get(source, ()) if tile is not None}
                supply = (on_belt(put) or (frozenset(), False))[0] & ingredients(source)
                if complete and supply and not line_of(put) & feeding:
                    starved.setdefault(source, []).append((entity, True))
                    report(
                        entity, Severity.PROBLEM,
                        f"{entity.name} puts {', '.join(sorted(made))} onto a belt that carries "
                        f"{', '.join(sorted(supply))}, which the {machine[1]} it takes from needs, "
                        "and takes nothing from that belt",
                        "That belt is the machine's supply, and the product is going onto it: the inserter "
                        "is turned the wrong way round (its direction names the side it takes FROM). "
                        "The machine's own input inserters point at the belt and away from the machine.",
                    )
                else:
                    starved.setdefault(source, []).append((entity, False))
            if (not complete and sink is not None and not burns(sink) and made
                    and not made & ingredients(sink)):
                receiver = lane_map.machines[sink]
                reverse = products(sink) & ingredients(source)
                report(
                    entity, Severity.PROBLEM,
                    f"{entity.name} moves {', '.join(sorted(made))} from {machine[0]} into a "
                    f"{receiver[1]} making {receiver[0]}, which does not use it"
                    + ("; the other way round would work" if reverse else ""),
                    "The direction names the side the inserter takes from, so it may be mirrored."
                    if reverse else "Neither machine uses what this inserter carries.",
                )
        elif sink is not None and pick in lane_map.nodes and not burns(sink):
            seen = on_belt(pick)
            receiver = lane_map.machines[sink]
            if not complete:
                carried = passing(filt, seen[0]) if seen is not None else frozenset()
                if seen is not None and not seen[1] and seen[0] and not carried & ingredients(sink):
                    report(
                        entity, Severity.PROBLEM,
                        f"{entity.name} takes {', '.join(sorted(seen[0]))} from a belt into a "
                        f"{receiver[1]} making {receiver[0]}, which does not use it",
                        "The direction names the side the inserter takes from; if it is meant to carry "
                        "the product away, it is turned the wrong way round.",
                    )
                elif (products(sink) and sink not in emptied and not near_the_edge(sink)
                        and not has_loader(sink) and not carried & ingredients(sink)):
                    stuck.setdefault(sink, []).append(entity)
            elif seen is not None and not seen[1] and not seen[0] and settled(pick):
                report(
                    entity, Severity.PROBLEM,
                    f"{entity.name} feeds the {receiver[1]} making {receiver[0]} from a belt that carries nothing",
                    "Nothing declared puts anything on the belt it takes from. If that belt is where the "
                    "machine's product is meant to go, the inserter is turned the wrong way round.",
                )

    for sink, feeders in stuck.items():
        receiver = lane_map.machines[sink]
        at = "; ".join("{}, {}".format(*Layout.tile_of(e)) for e in feeders)
        report(
            feeders[0], Severity.SUSPECT,
            f"nothing takes the product out of the {receiver[1]} making {receiver[0]}; "
            f"the inserter(s) at {at} only bring things to it from belts",
            "The direction names the side an inserter takes from. If one of them is meant to carry the "
            "product away onto its belt, it is turned the wrong way round.",
        )

    if not complete:
        for source, takers in starved.items():
            if (not ingredients(source) or lane_map.fed_by.get(source) or has_loader(source)
                    or near_the_edge(source)):
                continue
            at = "; ".join("{}, {}".format(*Layout.tile_of(e)) for e, _ in takers)
            machine = lane_map.machines[source]
            report(
                takers[0][0], Severity.SUSPECT,
                f"nothing brings the {machine[1]} making {machine[0]} an ingredient; "
                f"the inserter(s) at {at} only take from it",
                "The direction names the side an inserter takes from. If one of them is meant as the "
                "machine's input, it is turned the wrong way round.",
            )
    else:
        for source, takers in starved.items():
            machine = lane_map.machines[source]
            if (not ingredients(source) or real_feed(source) or has_loader(source)
                    or any(flag for _, flag in takers)):
                continue  # a supply belt being written to is already said above
            edge = near_the_edge(source)
            at = "; ".join("{}, {}".format(*Layout.tile_of(e)) for e, _ in takers)
            report(
                takers[0][0], Severity.SUSPECT if edge else Severity.PROBLEM,
                f"no inserter brings the {machine[1]} making {machine[0]} an ingredient; "
                f"the inserter(s) at {at} only take from it",
                "Every inserter at the machine either takes from it or takes from a belt that carries "
                "nothing it uses. If one of them is meant as the machine's input, it is turned the wrong way "
                "round (its direction names the side it takes FROM)."
                + (" The machine is near the edge of the blueprint, so the supply may be outside it." if edge else ""),
            )
    return found


@check
def inserters_the_wrong_way_round(layout: Layout) -> Iterator[Finding]:
    """An inserter that takes a machine's product back to its input, or feeds a machine what it does not use."""
    from . import lanes

    if not any(getattr(e, "type", "") == "inserter" for e in layout.entities):
        return
    yield from backwards_inserters(layout.entities, lanes.trace(layout.entities))


@check
def settings_that_do_nothing(layout: Layout) -> Iterator[Finding]:
    """Configuration that is set but switched off, or has no wire to act on.

    This is the careless-mistake family: everything looks configured, and none
    of it runs.
    """
    for entity in layout.entities:
        x, y = layout.tile_of(entity)

        filters = getattr(entity, "filters", None) or []
        if filters and getattr(entity, "use_filters", None) is False:
            yield Finding(
                Severity.PROBLEM,
                "filters-ignored",
                f"{entity.name} has filters set but filtering is switched off",
                "The filters are stored and never applied.",
                (x, y),
                (entity.name,),
            )

        for attribute, what in (
            ("circuit_condition", "a circuit condition"),
            ("logistic_condition", "a logistic condition"),
        ):
            if _condition_is_set(getattr(entity, attribute, None)) and not layout.has_wire(entity):
                yield Finding(
                    Severity.PROBLEM,
                    "condition-without-wire",
                    f"{entity.name} has {what} but no wire reaches it",
                    "The condition can never be satisfied, so the entity stays disabled.",
                    (x, y),
                    (entity.name,),
                )


def combinator_types() -> set[str]:
    """Which prototype types are combinators, according to the loaded data.

    Listing the four that exist today would be right until the day it is not:
    2.1 revises circuit logic, and a version or a mod that introduces another
    combinator would be skipped in silence rather than checked. The engine
    names them consistently, so the data itself can be asked.
    """
    import draftsman.data.entities as data

    return {kind for kind in getattr(data, "of_type", {}) if kind.endswith("-combinator")}


def _holds_signals(entity) -> bool:
    sections = getattr(entity, "sections", None) or []
    return any(getattr(section, "filters", None) for section in sections)


@check
def combinators_without_wires(layout: Layout) -> Iterator[Finding]:
    """A combinator no wire reaches computes into the void.

    Except for one idiom that looks exactly like the mistake and is not. A
    blueprint meant to be parameterised needs a free variable to exist
    somewhere before it can be turned into a parameter, and the way to make one
    is a constant combinator holding a value that appears nowhere else, wired
    to nothing. Calling that a problem would be calling a deliberate placeholder
    a defect, so a constant combinator that holds signals is noted rather than
    complained about; one holding nothing is reported by its own check.
    """
    kinds = combinator_types()
    for entity in layout.entities:
        kind = getattr(entity, "type", "")
        if kind not in kinds:
            continue
        if layout.has_wire(entity):
            continue
        x, y = layout.tile_of(entity)

        if kind == "constant-combinator" and _holds_signals(entity):
            yield Finding(
                Severity.NOTE,
                "combinator-unwired",
                f"{entity.name} holds signals and has no wires",
                "Nothing reads it. That is how a free variable is made for a "
                "parameterised blueprint, so it may well be deliberate.",
                (x, y),
                (entity.name,),
            )
            continue

        yield Finding(
            Severity.PROBLEM,
            "combinator-unwired",
            f"{entity.name} has no wires attached",
            "Nothing can reach its output, and nothing feeds its input.",
            (x, y),
            (entity.name,),
        )


@check
def entities_without_power(layout: Layout) -> Iterator[Finding]:
    """An electric machine outside every pole's supply area never runs."""
    poles = [e for e in layout.entities if getattr(e, "type", "") == "electric-pole"]
    if not poles:
        # A fragment carrying no power at all is a normal thing to blueprint.
        return

    covered: set[tuple[int, int]] = set()
    for pole in poles:
        reach = _raw(pole).get("supply_area_distance") or 0
        px = pole.tile_position.x + pole.tile_width / 2
        py = pole.tile_position.y + pole.tile_height / 2
        # Every tile the supply square touches; floor, not int(): int() rounds
        # toward zero and shifted the square by a tile at negative coordinates.
        for tx in range(math.floor(px - reach), math.ceil(px + reach)):
            for ty in range(math.floor(py - reach), math.ceil(py + reach)):
                covered.add((tx, ty))

    for entity in layout.entities:
        if not _needs_electricity(entity):
            continue
        tiles = list(layout.tiles_of(entity))
        if any(tile in covered for tile in tiles):
            continue
        x, y = layout.tile_of(entity)
        yield Finding(
            Severity.SUSPECT,
            "unpowered",
            f"{entity.name} is not covered by any pole in this blueprint",
            "It may be fed by a pole outside the blueprint, but check.",
            (x, y),
            (entity.name,),
        )


@check
def isolated_poles(layout: Layout) -> Iterator[Finding]:
    """A pole out of wire reach of every other pole powers only itself."""
    poles = [e for e in layout.entities if getattr(e, "type", "") == "electric-pole"]
    if len(poles) < 2:
        return
    for pole in poles:
        reach = _raw(pole).get("maximum_wire_distance") or 0
        px, py = pole.position.x, pole.position.y
        connected = any(
            other is not pole
            and (other.position.x - px) ** 2 + (other.position.y - py) ** 2 <= reach**2
            for other in poles
        )
        if connected:
            continue
        x, y = layout.tile_of(pole)
        yield Finding(
            Severity.SUSPECT,
            "pole-isolated",
            f"{pole.name} is out of wire reach of every other pole",
            f"Its reach is {reach} tiles; the network here is broken in two.",
            (x, y),
            (pole.name,),
        )


@check
def machines_without_a_recipe(layout: Layout) -> Iterator[Finding]:
    """Deliberate in a parameterised blueprint, an oversight otherwise."""
    parameterised = bool(getattr(layout.blueprint, "parameters", None))
    for entity in layout.entities:
        if getattr(entity, "type", "") != "assembling-machine":
            continue
        if getattr(entity, "recipe", None):
            continue
        x, y = layout.tile_of(entity)
        yield Finding(
            Severity.NOTE if parameterised else Severity.SUSPECT,
            "machine-no-recipe",
            f"{entity.name} has no recipe set",
            "Expected in a parameterised blueprint; otherwise it will sit idle."
            if parameterised
            else "It will do nothing until a recipe is chosen.",
            (x, y),
            (entity.name,),
        )


@check
def empty_constant_combinators(layout: Layout) -> Iterator[Finding]:
    for entity in layout.entities:
        if getattr(entity, "type", "") != "constant-combinator":
            continue
        if _holds_signals(entity):
            continue
        x, y = layout.tile_of(entity)
        yield Finding(
            Severity.SUSPECT,
            "constant-empty",
            f"{entity.name} holds no signals",
            "It outputs nothing. Often a placeholder that was never filled in.",
            (x, y),
            (entity.name,),
        )


@check
def belts_facing_each_other(layout: Layout) -> Iterator[Finding]:
    """Two belts pointing into one another jam where they meet."""
    seen: set[tuple[int, int, int, int]] = set()
    for entity in layout.entities:
        if getattr(entity, "type", "") != "transport-belt":
            continue
        step = STEP.get(int(getattr(entity, "direction", 0) or 0))
        if step is None:
            continue
        x, y = layout.tile_of(entity)
        ahead = (x + step[0], y + step[1])
        for other in layout.at(*ahead):
            if getattr(other, "type", "") != "transport-belt":
                continue
            other_step = STEP.get(int(getattr(other, "direction", 0) or 0))
            if other_step != (-step[0], -step[1]):
                continue
            key = tuple(sorted([(x, y), ahead]))
            flat = (key[0][0], key[0][1], key[1][0], key[1][1])
            if flat in seen:
                continue
            seen.add(flat)
            yield Finding(
                Severity.PROBLEM,
                "belts-head-on",
                "two belts face each other and will jam",
                "Items arrive from both sides onto the same tile.",
                (x, y),
                (entity.name, other.name),
            )


def _collides(first, second) -> bool:
    """Whether the game would refuse to have both of these where they are.

    Two conditions, both the game's own. Their collision masks must share a
    layer -- a rail and a rail signal are allowed to sit close precisely
    because their masks differ -- and their real collision boxes must actually
    intersect. Sharing a tile is neither necessary nor sufficient: a rail
    signal is a fifth of a tile across and a diagonal rail is a slanted shape
    inside a square of four, so tile arithmetic reported dozens of collisions
    the game had already accepted on the map.
    """
    first_mask = getattr(first, "collision_mask", None) or set()
    second_mask = getattr(second, "collision_mask", None) or set()
    if isinstance(first_mask, dict):
        first_mask = set(first_mask.get("layers") or ())
    if isinstance(second_mask, dict):
        second_mask = set(second_mask.get("layers") or ())
    if not (first_mask & second_mask):
        return False

    # Rails are the one family this cannot judge. Their real shapes are
    # curved and diagonal, the geometry available here approximates each with
    # a rectangle, and at a junction those rectangles overlap while the rails
    # themselves do not. Every rail pair flagged on a blueprint taken straight
    # off a working map was one the game had already accepted, so the honest
    # answer is that we do not know rather than a confident wrong one.
    if "rail" in getattr(first, "type", "") and "rail" in getattr(second, "type", ""):
        return False

    try:
        return first.get_world_collision_set().overlaps(second.get_world_collision_set())
    except Exception:
        # Geometry we cannot obtain is not evidence of a collision. The
        # prototype being unknown is reported by its own check.
        return False


@check
def overlapping_entities(layout: Layout) -> Iterator[Finding]:
    """Two things the game would not let stand together."""
    reported: set[tuple[str, str]] = set()
    seen_pairs: set[tuple[int, int]] = set()
    for tile, indices in layout.occupied.items():
        if len(indices) < 2:
            continue
        # Sharing a tile only makes a pair worth examining; the answer comes
        # from the geometry below.
        for position, first in enumerate(indices):
            for second in indices[position + 1:]:
                pair = (first, second) if first < second else (second, first)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                one, other = layout.entities[pair[0]], layout.entities[pair[1]]
                if not _collides(one, other):
                    continue
                names = tuple(sorted((one.name, other.name)))
                if names in reported:
                    continue
                reported.add(names)
                yield Finding(
                    Severity.PROBLEM,
                    "overlap",
                    f"{' and '.join(names)} cannot both stand there",
                    "Their collision boxes intersect on a layer they share, "
                    "so the game will refuse to place one of them.",
                    tile,
                    names,
                )


@check
def rail_signals_without_rail(layout: Layout) -> Iterator[Finding]:
    """A signal not beside a rail governs nothing."""
    for entity in layout.entities:
        if getattr(entity, "type", "") not in ("rail-signal", "rail-chain-signal"):
            continue
        x, y = layout.tile_of(entity)
        neighbours = ((1, 0), (-1, 0), (0, 1), (0, -1), (0, 0))
        near_rail = any(
            "rail" in getattr(other, "type", "")
            for dx, dy in neighbours
            for other in layout.at(x + dx, y + dy)
        )
        if near_rail:
            continue

        # At the boundary the rail may genuinely be in the neighbouring
        # blueprint, so this is softened rather than silenced. Silencing it
        # would make the check almost never fire: signals sit at the edges of
        # rail blueprints by their nature.
        at_edge = any(layout.outside(x + dx, y + dy) for dx, dy in neighbours[:4])
        yield Finding(
            Severity.SUSPECT if at_edge else Severity.PROBLEM,
            "signal-without-rail",
            f"{entity.name} is not beside a rail",
            "The track it governs would have to be in the neighbouring blueprint."
            if at_edge
            else "A signal must sit against the track it governs.",
            (x, y),
            (entity.name,),
        )


# --------------------------------------------------------------------------


def inspect(blueprint) -> Report:
    """Run every check over a blueprint and collect what they say."""
    layout = Layout(blueprint)
    report = Report(entities_checked=len(layout.entities), dataset=active_dataset())
    for run in CHECKS:
        report.findings.extend(run(layout))
    order = {Severity.PROBLEM: 0, Severity.SUSPECT: 1, Severity.NOTE: 2}
    report.findings.sort(key=lambda f: (order[f.severity], f.code, f.position or (0, 0)))
    return report
