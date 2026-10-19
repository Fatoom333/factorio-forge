"""A whole production chain as a draft plan: one command from an order to every stage and belt.

`chain` runs the bill of materials the request step already has, makes one
block per recipe on it, lays the blocks out in the order the goods flow (the
stages that take only raw input on the left, the ordered product on the right,
each stage between the stages that use it), joins every producing port to the
ports that take its product, and writes a plan `plan.build` accepts as it is.

What it decides: how many rows a stage needs, where each block stands, which
product goes to which port. What it leaves to the router: every tile of every
belt and pipe. The plan it writes is a draft for whoever designs the layout to
read and correct, not a finished design; `Draft.issues` lists what it could
not do.

Shared stages. The bill counts an intermediate once for all its users, so the
plan has one block for it. Its output port goes to every user's input port as
one connection with a list of destinations; the router puts a splitter in for
each branch.

Ports that carry two goods. One good is routed to the port and the other is
merged onto its belt (`onto`): a raw plate arrives as a belt head beside the
port, a product comes from its own out port. When the routed good's belt
already fills both lanes, the two are joined instead (a lane each).

Rows. A stage gets as few rows as keep its machines fed; a stage of several
rows is mirrored, and its input ports repeat (one per row pair).

Mending. The first build is kept if it is clean. For a block at the end of a
failed connection, at most two other placements are tried, each once: more
room around it, and its feeders on the other sides. A connection the plan
check refuses is left out and listed, so the rest of the chain still builds.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from pathlib import Path

from draftsman.data import recipes as recipe_data

from . import bom, insert, layout, request, rows
from .categories import primary_category

# Preferred tiers when the player's game says nothing about what is unlocked.
_DEFAULT_TIERS = {
    "belt": "fast-transport-belt",
    "inserter": "fast-inserter",
    "long_inserter": "long-handed-inserter",
    "pole": "medium-electric-pole",
}

# Free tiles between stacked blocks, and between columns before the routes are counted.
_ROW_GAP = 8
_COLUMN_GAP = 16
_PER_LINK_GAP = 3
_HEAD_DISTANCE = 6
_FEEDER_SPREAD = 15


class ChainError(Exception):
    """The order cannot be made into a chain."""


@dataclass
class Port:
    index: int
    kind: str  # belt or pipe
    io: str  # in or out
    items: frozenset[str]
    lanes: tuple[str, ...]
    x: int  # relative to the block's corner
    y: int
    direction: int
    rate: float


@dataclass
class Stage:
    index: int
    line: bom.MachineLine
    block: dict  # the plan's block, without "at"
    width: int
    height: int
    ports: list[Port]
    notes: list[str] = field(default_factory=list)
    column: int = 0
    x: int = 0
    y: int = 0
    # What the library did for this stage: the block used, or why a block of its shape was passed over.
    reuse: list[str] = field(default_factory=list)

    @property
    def recipe(self) -> str:
        return self.line.recipe

    def ports_where(self, io: str, item: str, kind: str | None = None) -> list[Port]:
        return [p for p in self.ports if p.io == io and item in p.items and (kind is None or p.kind == kind)]


@dataclass
class Tweaks:
    """Alternative placements for blocks that went wrong, by block index.

    `roomy`: the block moves right and its feeders stand twice as far apart;
    `flipped`: its feeders swap sides (the one for the top port goes below);
    both also start the raw belts that join its ports further out.
    """

    roomy: set[int] = field(default_factory=set)
    flipped: set[int] = field(default_factory=set)

    def far(self, index: int) -> bool:
        return index in self.roomy or index in self.flipped


@dataclass
class Draft:
    plan: dict
    stages: list[Stage]
    issues: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    links: list["Link"] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# the order
# --------------------------------------------------------------------------


def parse_targets(words: list[str]) -> list[dict]:
    """`item=rate` words: items per second, or `item=rate/m` for per minute."""
    targets = []
    for word in words:
        item, sep, value = word.partition("=")
        if not sep or not item or not value:
            raise ChainError(f"{word!r} is not item=rate (for example advanced-circuit=1, or =60/m for per minute)")
        per_minute = value.endswith("/m")
        try:
            rate = float(value[:-2] if per_minute else value)
        except ValueError as exc:
            raise ChainError(f"{word!r}: {value!r} is not a number") from exc
        targets.append({"item": item, ("per_minute" if per_minute else "per_second"): rate})
    return targets


def _tiers(spec: request.Spec, reviewed: request.Review) -> tuple[dict[str, str], list[str]]:
    """The belt, inserters and pole to build with, and where each came from."""
    tiers, said = dict(reviewed.tiers), []
    for role in request.ROLES:
        if role in tiers:
            continue
        available = [o.name for o in request.options(role, None)]
        pick = _DEFAULT_TIERS[role] if _DEFAULT_TIERS[role] in available else (available[0] if available else None)
        if pick is None:
            raise ChainError(f"the active data has no {role.replace('_', ' ')}")
        tiers[role] = pick
        said.append(f"{role}: {pick}, assumed (nothing says what the player has unlocked)")
    return tiers, said


# --------------------------------------------------------------------------
# stages
# --------------------------------------------------------------------------


def _speed_bonus(spec: request.Spec, recipe: str) -> float:
    category = primary_category(recipe_data.raw[recipe])
    effects = spec.effects.get(category) or spec.effects.get("*")
    return effects.speed if effects else 0.0


def _row_counts(machines: int, rows_wanted: int) -> list[int]:
    count = max(1, min(machines, rows_wanted))
    base, extra = divmod(machines, count)
    return [base + (1 if i < extra else 0) for i in range(count)]


def _library_stage(index: int, line: bom.MachineLine, tiers: dict[str, str]) -> tuple[Stage | None, list[str]]:
    """A stage from a library block of the line's shape, with the recipe swapped in when the shape allows."""
    try:
        choice, said = insert.choose(line.recipe, line.machine, tiers, max(1, line.machines))
        if choice is None:
            return None, said
        pasted = insert.paste(choice.entry, (0, 0), choice.rotate, choice.mirror,
                              line.recipe if choice.swapped else None, choice.name)
    except (insert.PlanError, OSError) as exc:
        return None, [f"library: {exc}"]
    block = {"library": choice.name, "rotate": choice.rotate}
    if choice.mirror:
        block["mirror"] = True
    if choice.swapped:
        block["recipe"] = line.recipe
    stage = Stage(index, line, block, pasted.width, pasted.height, [], list(pasted.notes))
    for j, p in enumerate(pasted.ports):
        items = frozenset(part for lane in p.items for part in lane.split("+") if part)
        stage.ports.append(Port(j, p.kind, p.io, items, tuple(p.items), p.x, p.y, p.direction, p.rate))
    how = f", recipe swapped ({choice.entry['shape']['recipe']} -> {line.recipe})" if choice.swapped else ""
    stage.reuse.append(f"stage {index}: library block {choice.name}{how}")
    stage.reuse.extend(f"stage {index}: {why}" for why in said)
    return stage, []


def make_stage(index: int, line: bom.MachineLine, spec: request.Spec, tiers: dict[str, str]) -> Stage:
    """One block for a line of the bill: a library block of its shape, or as few rows as keep every machine fed."""
    bonus = _speed_bonus(spec, line.recipe)
    passed: list[str] = []
    if not bonus:
        reused, passed = _library_stage(index, line, tiers)
        if reused is not None:
            return reused
    stage = _rows_stage(index, line, bonus, tiers)
    stage.reuse.extend(f"stage {index}: {why}" for why in passed)
    return stage


def _rows_stage(index: int, line: bom.MachineLine, bonus: float, tiers: dict[str, str]) -> Stage:
    template = {
        "type": "rows", "recipe": line.recipe, "machine": line.machine,
        "belt": tiers["belt"], "inserter": tiers["inserter"], "long_inserter": tiers["long_inserter"],
        "pole": tiers["pole"], "stack": "mirror", "align": "start", "rotate": 0,
    }
    if bonus:
        template["speed_bonus"] = bonus
    machines = max(1, line.machines)
    fields = rows.RowBlockSpec.__dataclass_fields__
    for wanted in range(1, machines + 1):
        counts = _row_counts(machines, wanted)
        try:
            built = rows.build_block(rows.RowBlockSpec(**{k: v for k, v in template.items() if k in fields}, rows=counts))
        except layout.LayoutError as exc:
            raise ChainError(f"{line.recipe} on {line.machine}: {exc}") from exc
        short = [r for r in built.rows if r.capacity and r.machines > r.capacity]
        if not short:
            break
    _, ports, width, height = rows.transform(built, (0, 0), 0)
    notes = list(built.notes)
    if short:
        notes.append("a row holds more machines than its belt keeps fed: " + "; ".join(r.limit for r in short))
    block_json = {**template, "rows": counts}
    stage = Stage(index, line, block_json, width, height, [], notes)
    for j, p in enumerate(ports):
        items = frozenset(part for lane in p.items for part in lane.split("+") if part)
        stage.ports.append(Port(j, p.kind, p.io, items, tuple(p.items), p.x, p.y, p.direction, p.rate))
    return stage


# --------------------------------------------------------------------------
# what feeds what
# --------------------------------------------------------------------------


@dataclass
class Link:
    """One product from a stage's out port to the in ports that take it.

    `mode` is how it joins the port: "to" (routed there, several ports make a split), "onto" (side-loaded
    onto the belt another good already rides) or "join" (one lane of a join of two goods).
    """

    item: str
    producer: Stage
    out: Port
    ends: list[tuple[Stage, Port]] = field(default_factory=list)
    mode: str = "to"

    @property
    def free_lane(self) -> bool:
        return "" in self.out.lanes


@dataclass
class Feed:
    """A good that arrives from outside at one in port: how, and how much."""

    stage: Stage
    port: Port
    item: str
    rate: float
    how: str  # "source" (the port itself), "merge" (onto the belt of another good) or "join"
    partner: Link | None = None  # for a join: the link it is joined with

    @property
    def lane(self) -> str:
        lanes = self.port.lanes
        return "right" if lanes and lanes[-1] == self.item and lanes[0] != self.item else "left"


@dataclass
class Join:
    consumer: Stage
    port: Port
    first: Link
    second: "Link | Feed"


def _producers(stages: list[Stage], boundary: set[str]) -> dict[str, Stage]:
    made: dict[str, Stage] = {}
    for stage in stages:
        for item, rate in stage.line.outputs.items():
            if item in boundary:
                continue
            best = made.get(item)
            if best is None or rate > best.line.outputs[item]:
                made[item] = stage
    return made


def _share(stage: Stage, port: Port, item: str) -> float:
    """What part of the stage's demand for `item` this port carries (mirrored rows split it)."""
    same = stage.ports_where("in", item, "belt")
    total = sum(p.rate for p in same)
    return port.rate / total if total else 1.0 / max(1, len(same))


def wire(stages: list[Stage], boundary: set[str], issues: list[str]) -> tuple[list[Link], list[Feed], list[Join]]:
    """Every product's route from out port to in ports, every raw good's feed, every two-good join."""
    made = _producers(stages, boundary)
    # how many in ports want each good: the good wanted at more ports is the one routed to a port, the other merges
    users: dict[str, int] = {}
    for st in stages:
        for p in st.ports:
            if p.io == "in" and p.kind == "belt":
                for g in p.items:
                    users[g] = users.get(g, 0) + 1
    links: dict[tuple[int, str, int], Link] = {}
    roles: dict[tuple[int, str, int], list[tuple[Stage, Port, str]]] = {}
    feeds: list[Feed] = []
    joins: list[Join] = []

    def link_for(producer: Stage, item: str, kind: str) -> Link | None:
        outs = producer.ports_where("out", item, kind)
        if not outs:
            issues.append(f"stage {producer.index} ({producer.recipe}) makes {item} but has no out port that carries it")
            return None
        key = (producer.index, item, outs[0].index)
        return links.setdefault(key, Link(item, producer, outs[0]))

    def key_of(link: Link) -> tuple[int, str, int]:
        return (link.producer.index, link.item, link.out.index)

    def role(link: Link, consumer: Stage, port: Port, mode: str) -> None:
        roles.setdefault(key_of(link), []).append((consumer, port, mode))

    for consumer in stages:
        for port in consumer.ports:
            if port.io != "in":
                continue
            goods = sorted(port.items, key=lambda i: (port.lanes.index(i) if i in port.lanes else 9, i))
            if port.kind == "pipe":
                for g in goods:
                    producer = made.get(g)
                    if producer is None or producer is consumer:
                        issues.append(f"stage {consumer.index} ({consumer.recipe}) port {port.index} takes {g} from "
                                      "outside: bring a pipe there by hand")
                        continue
                    link = link_for(producer, g, "pipe")
                    if link is not None:
                        link.ends.append((consumer, port))
                        role(link, consumer, port, "to")
                continue
            makers = sorted(((g, made[g]) for g in goods if made.get(g) not in (None, consumer)),
                            key=lambda gs: (-users.get(gs[0], 0), goods.index(gs[0])))
            raws = [g for g in goods if g not in dict(makers)]
            if not makers:
                for g in raws[:2]:
                    rate = consumer.line.inputs.get(g, 0.0) * _share(consumer, port, g)
                    feeds.append(Feed(consumer, port, g, rate, "source"))
                continue
            first = link_for(makers[0][1], makers[0][0], "belt")
            if first is None:
                continue
            others: list[tuple[str, Stage | None]] = [(g, p) for g, p in makers[1:]] + [(g, None) for g in raws]
            if len(others) > 1:
                issues.append(
                    f"stage {consumer.index} ({consumer.recipe}) port {port.index} takes {len(goods)} goods on one "
                    f"belt; {', '.join(g for g, _ in others[1:])} is left for you to bring in")
                others = others[:1]
            first.ends.append((consumer, port))
            if not others:
                role(first, consumer, port, "to")
                continue
            good, producer = others[0]
            second: Link | Feed | None
            if producer is not None:
                second = link_for(producer, good, "belt")
                if second is None:
                    role(first, consumer, port, "to")
                    continue
                second.ends.append((consumer, port))
            else:
                rate = consumer.line.inputs.get(good, 0.0) * _share(consumer, port, good)
                second = Feed(consumer, port, good, rate, "merge")
                feeds.append(second)
            if first.free_lane:
                role(first, consumer, port, "to")
                if isinstance(second, Link):
                    role(second, consumer, port, "onto")
            else:
                role(first, consumer, port, "join")
                if isinstance(second, Link):
                    role(second, consumer, port, "join")
                else:
                    second.how, second.partner = "join", first
                joins.append(Join(consumer, port, first, second))

    # a link has one mode: where the roles disagree, the first stands and the others are left to the player
    for key, link in links.items():
        wanted = roles.get(key, [])
        link.ends = []
        if not wanted:
            continue
        # routing to the port (and splitting to several) beats a merge or a join, which take one end each
        keep = "to" if any(m == "to" for _, _, m in wanted) else wanted[0][2]
        kept = [(c, p) for c, p, m in wanted if m == keep]
        if keep != "to":
            kept = kept[:1]
        link.mode, link.ends = keep, kept
        for c, p, m in wanted:
            if (c, p) in kept:
                continue
            issues.append(
                f"{link.item} from stage {link.producer.index} also has to reach stage {c.index} port {p.index}, "
                "but one out port starts one connection: route it by hand")
            for j in list(joins):
                if (j.consumer, j.port) == (c, p):
                    joins.remove(j)
                    if isinstance(j.second, Feed) and j.second in feeds:
                        feeds.remove(j.second)
    return [l for l in links.values() if l.ends], feeds, joins


# --------------------------------------------------------------------------
# where the blocks stand
# --------------------------------------------------------------------------


def _consumers(stages: list[Stage], links: list[Link]) -> dict[int, list[Stage]]:
    users: dict[int, list[Stage]] = {s.index: [] for s in stages}
    for link in links:
        for consumer, _ in link.ends:
            if consumer not in users[link.producer.index]:
                users[link.producer.index].append(consumer)
    return users


def assign_columns(stages: list[Stage], links: list[Link]) -> int:
    """Column 0 holds the stages furthest from the product; each stage stands as late as it can."""
    users = _consumers(stages, links)
    depth: dict[int, int] = {}

    def to_sink(stage: Stage, trail: tuple[int, ...] = ()) -> int:
        if stage.index in depth:
            return depth[stage.index]
        if stage.index in trail:  # a loop in the bill: leave it where it is
            return 0
        below = [to_sink(u, trail + (stage.index,)) for u in users[stage.index]]
        depth[stage.index] = 1 + max(below) if below else 0
        return depth[stage.index]

    widest = max(to_sink(s) for s in stages)
    for stage in stages:
        stage.column = widest - depth[stage.index]
    return widest


def _feeder_offsets(stages: list[Stage], links: list[Link], spread: int, tweaks: Tweaks) -> dict[tuple[int, int], int]:
    """How far above or below its port a producer stands: feeders of one block are spread in port order.

    The feeder of the topmost port stands highest, so its belt comes in from above and never crosses
    the belt of a feeder for the port under it.
    """
    feeders: dict[int, dict[int, int]] = {}
    for link in links:
        for consumer, port in link.ends:
            if consumer is link.producer:
                continue
            seen = feeders.setdefault(consumer.index, {})
            seen[link.producer.index] = min(port.y, seen.get(link.producer.index, port.y))
    offsets = {}
    for consumer, seen in feeders.items():
        ranked = sorted(seen, key=lambda p: (seen[p], p))
        step = spread * (2 if consumer in tweaks.roomy else 1) * (-1 if consumer in tweaks.flipped else 1)
        for rank, producer in enumerate(ranked):
            offsets[(producer, consumer)] = round((rank - (len(ranked) - 1) / 2) * step) if len(ranked) > 1 else 0
    return offsets


def _free_slot(wanted: int, height: int, taken: list[tuple[int, int]], gap: int) -> int:
    """The row nearest `wanted` where a block `height` tall clears every taken (top, height) by `gap`."""
    def clear(y: int) -> bool:
        return all(y + height + gap <= top or top + h + gap <= y for top, h in taken)

    candidates = [wanted] + [t - height - gap for t, _ in taken] + [t + h + gap for t, h in taken]
    return min((y for y in candidates if clear(y)), key=lambda y: (abs(y - wanted), y))


def lay_out(stages: list[Stage], links: list[Link], tweaks: Tweaks | None = None,
            spread: int = _FEEDER_SPREAD) -> None:
    """Corners for every block: columns left to right by flow, rows lined up with the ports they feed."""
    tweaks = tweaks or Tweaks()
    widest = assign_columns(stages, links)
    offsets = _feeder_offsets(stages, links, spread, tweaks)
    crossing = [0] * (widest + 1)
    for link in links:
        for consumer, _ in link.ends:
            for col in range(link.producer.column, consumer.column):
                crossing[col] += 1
    columns: list[list[Stage]] = [[s for s in stages if s.column == c] for c in range(widest + 1)]
    x = 0
    for c, members in enumerate(columns):
        wide = max((s.width for s in members), default=0)
        for s in members:
            s.x = x + (wide - s.width) + (_COLUMN_GAP // 2 if s.index in tweaks.roomy else 0)
        x += wide + _COLUMN_GAP + _PER_LINK_GAP * crossing[c]

    placed: set[int] = set()
    for c in range(widest, -1, -1):
        wanted: dict[int, float] = {}
        for s in columns[c]:
            ys = []
            for link in links:
                if link.producer is not s:
                    continue
                for consumer, port in link.ends:
                    if consumer.index in placed:
                        ys.append(consumer.y + port.y + offsets.get((s.index, consumer.index), 0) - link.out.y)
            wanted[s.index] = sum(ys) / len(ys) if ys else float(s.index * 40)
        # the stage that moves the most goods is lined up first; the others go to the nearest free row
        order = sorted(columns[c], key=lambda s: (-sum(s.line.outputs.values()), wanted[s.index], s.index))
        taken: list[tuple[int, int]] = []
        for s in order:
            gap = _ROW_GAP + (6 if s.index in tweaks.roomy else 0)
            s.y = _free_slot(round(wanted[s.index]), s.height, taken, gap)
            taken.append((s.y, s.height))
        placed.update(s.index for s in columns[c])
    top = min(s.y for s in stages)
    for s in stages:
        s.y -= top


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------


def _up(rate: float) -> float:
    """A rate rounded up to six places: a declared supply must not fall short of the demand by rounding."""
    return math.ceil(rate * 1e6 - 1e-6) / 1e6


def _absolute(stage: Stage, port: Port) -> tuple[int, int]:
    return stage.x + port.x, stage.y + port.y


def _head(feed: Feed, number: int, far: bool) -> dict:
    """Where the belt that brings a raw good to a port begins: beside the port, on the side of its lane."""
    x, y = _absolute(feed.stage, feed.port)
    away = _HEAD_DISTANCE + (4 if far else 0)
    if feed.lane == "right":
        return {"at": [x - 3 - 2 * number, y + away], "direction": 0}
    return {"at": [x - 3 - 2 * number, y - away], "direction": 8}


def _ref(link: Link) -> dict:
    return {"block": link.producer.index, "port": link.out.index, "items": [link.item]}


def _reach(link: Link) -> int:
    return max((abs(c.column - link.producer.column) for c, _ in link.ends), default=0)


def to_plan(stages: list[Stage], links: list[Link], feeds: list[Feed], joins: list[Join], spec: request.Spec,
            tiers: dict[str, str], label: str, tweaks: Tweaks | None = None) -> dict:
    tweaks = tweaks or Tweaks()
    plan: dict = {
        "label": label,
        "targets": [{"item": i, "per_second": r} for i, r in spec.targets],
        "blocks": [{**s.block, "at": [s.x, s.y]} for s in stages],
        "connections": [],
        "sources": [],
        "routing": {"margin": 8},
        "power": {"link": True, "pole": tiers["pole"]},
    }
    for key in ("recipe_choices", "machine_choices", "boundary", "surface"):
        value = getattr(spec, key, None)
        if value:
            plan[key] = value
    plan["tiers"] = tiers
    if spec.effects:
        plan["effects"] = {k: {"speed": v.speed, "productivity": v.productivity, "consumption": v.consumption}
                           for k, v in spec.effects.items()}

    connections = plan["connections"]
    # nearer users first: short links get their straight tiles before the long ones go round
    for link in sorted((l for l in links if l.mode == "to"), key=lambda l: (_reach(l), l.producer.index, l.item)):
        ends = sorted(link.ends, key=lambda e: (e[0].column, abs(_absolute(*e)[1] - _absolute(link.producer, link.out)[1])))
        conn: dict = {"id": f"{link.item}-{link.producer.index}", "from": _ref(link)}
        if link.out.kind == "pipe":
            conn["kind"] = "pipe"
            ends = ends[:1]  # pipes do not split; the rest is reported by the caller
        targets = [{"block": c.index, "port": p.index} for c, p in ends]
        conn["to"] = targets if len(targets) > 1 else targets[0]
        connections.append(conn)
    number: dict[tuple[int, int], int] = {}

    def head_for(feed: Feed) -> dict:
        key = (feed.stage.index, feed.port.index)
        number[key] = number.get(key, 0) + 1
        point = _head(feed, number[key] - 1, tweaks.far(feed.stage.index))
        return {**point, "items": [feed.item], "rate": _up(feed.rate)}

    for join in joins:
        second = join.second
        other = _ref(second) if isinstance(second, Link) else head_for(second)
        # a join needs a turn in the first belt for the second to meet: a tile off the straight line to the port
        px, py = _absolute(join.consumer, join.port)
        if join.consumer.index in tweaks.flipped:
            via = [px - 8, py - 3]
        elif join.consumer.index in tweaks.roomy:
            via = [px - 12, py + 4]
        else:
            via = [px - 8, py + 3]
        connections.append({
            "id": f"{join.first.item}+{second.item}-{join.consumer.index}-{join.port.index}",
            "from": [_ref(join.first), other],
            "to": {"block": join.consumer.index, "port": join.port.index},
            "via": [via],
        })
    for link in sorted((l for l in links if l.mode == "onto"), key=lambda l: (_reach(l), l.producer.index, l.item)):
        consumer, port = link.ends[0]
        connections.append({
            "id": f"{link.item}-{link.producer.index}-merge", "from": _ref(link),
            "onto": {"block": consumer.index, "port": port.index},
        })
    sourced: dict[tuple[int, int], list[Feed]] = {}
    for feed in feeds:
        if feed.how == "source":
            sourced.setdefault((feed.stage.index, feed.port.index), []).append(feed)
        elif feed.how == "merge":
            connections.append({
                "id": f"{feed.item}-in-{feed.stage.index}-{feed.port.index}", "from": head_for(feed),
                "onto": {"block": feed.stage.index, "port": feed.port.index, "lane": "auto"},
            })
    for (block, port), group in sourced.items():
        goods = [f.item for f in sorted(group, key=lambda f: group[0].port.lanes.index(f.item)
                                        if f.item in group[0].port.lanes else 9)]
        lanes = group[0].port.lanes
        items = goods if len(goods) == 1 else [lanes[0], lanes[-1]]
        plan["sources"].append({
            "id": "-".join(goods) + f"-in-{block}-{port}", "block": block, "port": port, "items": items,
            "rate": _up(sum(f.rate for f in group)),
        })
    return plan


def draft(spec: request.Spec, *, tweaks: Tweaks | None = None, spread: int = _FEEDER_SPREAD,
          reviewed: request.Review | None = None, label: str | None = None) -> Draft:
    """The plan for an order, with what could not be done and what was assumed."""
    reviewed = reviewed or request.review(spec)
    if not reviewed.ready:
        raise ChainError("the request is not ready: " + "; ".join(reviewed.problems or ["no bill of materials"]))
    bill = reviewed.bill
    tiers, assumed = _tiers(spec, reviewed)
    stages = [make_stage(i, line, spec, tiers) for i, line in enumerate(bill.lines)]
    if not stages:
        raise ChainError("the bill of materials has no machine lines: nothing to build")
    issues: list[str] = []
    links, feeds, joins = wire(stages, set(spec.boundary), issues)
    lay_out(stages, links, tweaks, spread)
    name = label or "chain-" + "-".join(i for i, _ in spec.targets)
    plan = to_plan(stages, links, feeds, joins, spec, tiers, name, tweaks)
    for link in links:
        if link.out.kind == "pipe" and len(link.ends) > 1:
            issues.append(
                f"{link.item} from stage {link.producer.index} goes to {len(link.ends)} stages by pipe: "
                "pipes do not split, so route all but the first by hand")
    reused = [text for s in stages for text in s.reuse]
    for stage in stages:
        for note in stage.notes:
            if note.startswith("a row holds more"):
                issues.append(f"stage {stage.index} ({stage.recipe}): {note}")
    return Draft(plan, stages, issues, assumed + [a for a in reviewed.assumptions if a.startswith("machines:")], links, reused)


# --------------------------------------------------------------------------
# building the draft, mending it twice at most
# --------------------------------------------------------------------------


@dataclass
class Attempt:
    number: int
    changed: str
    problems: list[str]
    result: object = field(repr=False, default=None)
    plan: dict = field(repr=False, default_factory=dict)


def failed_spots(result, plan: dict, stages: list[Stage] | None = None) -> dict[int, list[str]]:
    """The blocks at the end of a connection that failed, with what went wrong.

    A merge that failed only because the route it merges onto was never built is not blamed:
    that route is the failure.
    """
    ends: dict[str, list[int]] = {}
    for conn in plan["connections"]:
        target = conn.get("onto") or conn.get("to")
        targets = target if isinstance(target, list) else [target]
        ends[conn["id"]] = [t["block"] for t in targets if isinstance(t, dict) and "block" in t]
    spots: dict[int, list[str]] = {}
    for r in result.routes:
        if r.ok or "was not built" in (r.reason or ""):
            continue
        base, _, number = r.id.partition("/")
        blocks = ends.get(r.connection.split("/")[0] or base, [])
        if number.isdigit() and int(number) < len(blocks):
            blocks = [blocks[int(number)]]
        for block in blocks:
            spots.setdefault(block, []).append(f"route {r.id}: {r.reason}")
    from . import inspection

    for f in result.findings:
        if f.severity is not inspection.Severity.PROBLEM or f.code.startswith("route-") or not f.position:
            continue
        if f.code in {"power-split"}:
            continue
        for stage in stages or []:
            x, y = f.position
            if stage.x <= x < stage.x + stage.width and stage.y <= y < stage.y + stage.height:
                spots.setdefault(stage.index, []).append(f"{f.code}: {f.summary}")
                break
    return spots


def build_salvaging(d: Draft):
    """Build the draft's plan. A connection or source the plan check refuses is left out and listed, so the rest builds.

    Returns the result, or None and the reason when nothing can be built.
    """
    import re

    from . import plan as planner

    for _ in range(8):
        try:
            return planner.build(d.plan, planner.planned_from_targets(d.plan)[0]), ""
        except planner.PlanError as exc:
            text = str(exc)
            found = re.search(r"connection \d+ \(([^)]*)\)", text)
            if found:
                section, name = "connections", found.group(1)
            else:
                found = re.search(r"source '([^']*)'", text)
                section, name = "sources", found.group(1) if found else ""
            kept = [c for c in d.plan[section] if c.get("id") != name]
            if not found or len(kept) == len(d.plan[section]):
                return None, text
            d.plan[section] = kept
            d.issues.append(f"{name} was refused and is left out ({text.split(': ', 1)[-1]}): do it by hand")
    return None, "too many refused connections"


def build_with_retries(spec: request.Spec, *, label: str | None = None, max_variants: int = 2,
                       spread: int = _FEEDER_SPREAD, reviewed: request.Review | None = None) -> tuple[Draft, list[Attempt]]:
    """Build the draft; for a block whose connections failed, try at most two other placements.

    The first gives the block more room; the second puts its feeders on the other side of it.
    Each spot gets each alternative once. The build with the fewest problems is returned first
    in the attempts' order of merit; `best_attempt` picks it.
    """
    reviewed = reviewed or request.review(spec)
    tweaks = Tweaks()
    tried: dict[int, int] = {}
    attempts: list[Attempt] = []
    drafts: dict[int, Draft] = {}
    note = "first placement"
    d: Draft | None = None
    while True:
        number = len(attempts) + 1
        if d is None:
            d = draft(spec, tweaks=tweaks, spread=spread, reviewed=reviewed, label=label)
        drafts[number] = d
        result, refusal = build_salvaging(d)
        if result is None:
            attempts.append(Attempt(number, note, [f"plan refused: {refusal}"], None, d.plan))
            break
        attempts.append(Attempt(number, note, [str(f) for f in result.problems], result, copy.deepcopy(d.plan)))
        spots = failed_spots(result, d.plan, d.stages)
        if not spots:
            break
        d = None
        moved = []
        for spot in sorted(spots):
            if tried.get(spot, 0) >= max_variants:
                continue
            tried[spot] = tried.get(spot, 0) + 1
            if tried[spot] == 1:
                tweaks.roomy.add(spot)
                moved.append(f"block {spot}: more room (moved right, feeders twice as far apart, raw belts start further out)")
            else:
                tweaks.roomy.discard(spot)
                tweaks.flipped.add(spot)
                moved.append(f"block {spot}: other side (its feeders swap sides, raw belts start further out)")
        if not moved:
            break
        note = "; ".join(moved)
    best = best_attempt(attempts)
    return drafts[(best or attempts[-1]).number], attempts


def best_attempt(attempts: list[Attempt]) -> Attempt | None:
    """The built attempt with the fewest problems; the earliest wins a tie."""
    built = [a for a in attempts if a.result is not None]
    return min(built, key=lambda a: (len(a.problems), a.number), default=None)


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------


def _spec_from_words(args) -> request.Spec:
    words = list(args.order)
    path = Path(words[0])
    if len(words) == 1 and "=" not in words[0]:
        try:
            return request.load(path)
        except (OSError, request.RequestError) as exc:
            raise ChainError(f"{path}: {exc}") from exc
    data: dict = {"targets": parse_targets(words)}
    if args.boundary:
        data["boundary"] = [b for b in args.boundary.split(",") if b]
    if args.surface:
        data["surface"] = args.surface
    try:
        return request.parse(data)
    except request.RequestError as exc:
        raise ChainError(str(exc)) from exc


def run(args) -> int:
    import json
    import re
    import sys
    import time

    from . import render

    started = time.monotonic()
    try:
        spec = _spec_from_words(args)
        reviewed = request.review(spec)
        if not reviewed.ready:
            for problem in reviewed.problems or ["no bill of materials"]:
                print(f"problem: {problem}", file=sys.stderr)
            return 1
        if args.no_build:
            result_draft, attempts = draft(spec, reviewed=reviewed), []
        else:
            result_draft, attempts = build_with_retries(spec, reviewed=reviewed)
    except ChainError as exc:
        print(f"chain: {exc}", file=sys.stderr)
        return 1

    plan_path = Path(args.output)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(result_draft.plan, indent=1, ensure_ascii=False), encoding="utf-8")
    plan = result_draft.plan
    print(f"{plan['label']}: {len(plan['blocks'])} blocks, {len(plan['connections'])} connections, "
          f"{len(plan['sources'])} sources")
    for stage in result_draft.stages:
        shape = f"rows {stage.block['rows']}" if "rows" in stage.block else f"library {stage.block['library']}"
        print(f"  block {stage.index}: {stage.line.machines} x {stage.line.machine} on {stage.recipe}, "
              f"{shape}, column {stage.column}, at ({stage.x}, {stage.y})")
    for line in result_draft.reused:
        print(line)
    for line in result_draft.assumptions:
        print(f"assumed: {line}")
    print(f"plan: {plan_path.resolve()}")

    best = best_attempt(attempts)
    final = best
    if best is not None:
        for a in attempts:
            print(f"build {a.number} ({a.changed}): {len(a.problems)} problem(s)")
            if len(attempts) > 1:
                for p in a.problems:
                    print(f"    {p}")
        out = plan_path.resolve().parent
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", final.result.label).strip("-") or "layout"
        (out / f"{slug}.txt").write_text(final.result.blueprint.to_string(), encoding="utf-8")
        (out / f"{slug}.report.json").write_text(
            json.dumps(final.result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"blueprint: {(out / f'{slug}.txt')}")
        if not args.no_render:
            html = render.write_html(final.result.blueprint, out / f"{slug}.html", title=final.result.label,
                                     findings=final.result.findings)
            print(f"drawing:   {html.resolve()}")
        shown = [f for f in final.result.findings if f.severity.value != "note"]
        print(f"checks: {len(shown)} finding(s) beyond notes" if shown else "checks: nothing to report")
        for f in shown:
            where = f" at {f.position}" if f.position else ""
            print(f"  [{f.severity.value}] {f.summary}{where}")
        failed = [r for r in final.result.routes if not r.ok]
        for r in failed:
            print(f"  route {r.id}: FAILED - {r.reason}")
    elif attempts:
        for a in attempts:
            for p in a.problems:
                print(f"  {p}")
    for issue in result_draft.issues:
        print(f"left to you: {issue}")
    print(f"took {time.monotonic() - started:.1f} s")
    return 0 if best is None or not best.problems else 1
