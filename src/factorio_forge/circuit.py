"""What a circuit of combinators does, tick by tick.

Combinator behaviour lives in the engine, not in the game's data files, so
from outside it can only be modelled. This is that model, written to the rules
the game documents (the wiki's circuit network, decider, arithmetic and
selector pages for 2.0) and held against circuits players build and describe.
The companion mod can run the same blueprint inside the game and record every
network on every tick; `compare` sets the two side by side, and where they
disagree the model is wrong, not the game.

## The tick

Everything is built on one rule. On each tick every network carries the sum of
what its senders put out on the previous tick; every combinator reads the
networks on its input side and computes its output, which the networks carry
on the next tick. So a combinator is one tick of delay, a wire from a
combinator's output back to its own input is memory, and counters, clocks and
latches are all this delay put to work.

A constant combinator needs no tick: what it holds is on the network from the
first tick. Other senders -- a belt reading its contents, an inserter its hand
-- are not modelled; a test or a caller sets what they send (`drive`).

## Signals

A signal is a type, a name and a quality: `("item", "iron-plate", "normal")`.
Values are 32-bit and wrap. A signal whose value is zero is not on the network
at all, which is why a condition like `Anything = 0` is never true.

## What is not certain

Some rules the documentation leaves open. Each is written down in
`ASSUMPTIONS` with the choice made here, so a recording from the game can
settle it rather than an argument.
"""

from __future__ import annotations

import base64
import json
import operator
import zlib
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping

Signal = tuple[str, str, str]  # (type, name, quality)
Signals = dict[Signal, int]

RED, GREEN = "red", "green"

_INT32 = 2**32
_HALF = 2**31


def wrap(value: int) -> int:
    """A value as the circuit network holds it: signed 32 bits, wrapping."""
    return (value + _HALF) % _INT32 - _HALF


# Choices made where the documentation is silent. Each names what a recording
# from the game would have to show to confirm or overturn it.
ASSUMPTIONS: dict[str, str] = {
    "each-iterates-both-wires": (
        "A decider with Each evaluates its conditions for every signal present "
        "on either input wire, whichever wires the Each operands select."
    ),
    "each-specific-output-counts-each": (
        "With Each in the conditions, an output of a specific signal that copies "
        "the input count sums the counts of the Each signals that passed, not the "
        "count of the output signal itself (the wiki: results are output on the "
        "specified signal instead)."
    ),
    "anything-output-picks-first-match": (
        "An Anything output picks the first signal, in Factoriopedia order, among "
        "those that matched an Anything condition; without one, among the signals "
        "on the output's wires."
    ),
    "arithmetic-each-own-wires": (
        "An arithmetic combinator with Each iterates the signals on the wires "
        "selected for the Each operand only."
    ),
    "division-by-zero-is-zero": "Division and modulo by zero give 0.",
    "negative-power-is-zero": (
        "A negative exponent gives 0, except for bases 1 and -1."
    ),
    "shift-amount-masked": "A shift uses the low five bits of its amount, as x86 does.",
    "selector-skips-index-signal": (
        "In select mode the signal that carries the index is not among the "
        "signals sorted and selected."
    ),
    "empty-condition-is-false": "A condition with no first signal is false.",
}


class CircuitError(Exception):
    """The blueprint cannot be simulated."""


# --------------------------------------------------------------------------
# signals
# --------------------------------------------------------------------------


def signal_of(data: Mapping | None) -> Signal | None:
    """A signal from its blueprint form, where the type of an item is left out."""
    if not data or not data.get("name"):
        return None
    return (data.get("type") or "item", data["name"], data.get("quality") or "normal")


def _wildcard(signal: Signal | None, name: str) -> bool:
    return signal is not None and signal[0] == "virtual" and signal[1] == name


def _is_each(signal: Signal | None) -> bool:
    return _wildcard(signal, "signal-each")


def _is_anything(signal: Signal | None) -> bool:
    return _wildcard(signal, "signal-anything")


def _is_everything(signal: Signal | None) -> bool:
    return _wildcard(signal, "signal-everything")


def label(signal: Signal) -> str:
    """How a signal is written in reports: its name, and its quality if any."""
    kind, name, quality = signal
    text = name if kind in ("item", "virtual", "fluid") else f"{kind}:{name}"
    return text if quality == "normal" else f"{text}/{quality}"


def parse_signals(text: Mapping[str, int] | Iterable[tuple[str, int]]) -> Signals:
    """Signals from a plain mapping, `{"signal-A": 5, "iron-plate": 10}`.

    A name alone is looked up: a virtual signal if it is one, a fluid if it is
    one, an item otherwise. `type:name` and `name/quality` are also accepted.
    """
    pairs = text.items() if isinstance(text, Mapping) else text
    out: Signals = {}
    for key, value in pairs:
        quality = "normal"
        if "/" in key:
            key, quality = key.rsplit("/", 1)
        if ":" in key:
            kind, name = key.split(":", 1)
        else:
            kind, name = _guess_type(key), key
        signal = (kind, name, quality)
        out[signal] = wrap(out.get(signal, 0) + int(value))
    return {s: v for s, v in out.items() if v}


def _guess_type(name: str) -> str:
    from draftsman.data import fluids, signals

    if name in getattr(signals, "virtual", ()) or name.startswith("signal-"):
        return "virtual"
    if name in fluids.raw:
        return "fluid"
    return "item"


# --------------------------------------------------------------------------
# order
# --------------------------------------------------------------------------


def factoriopedia_order() -> Callable[[Signal], tuple]:
    """A sort key putting signals in the order the game picks among them.

    The game picks the leftmost tab, then the topmost row, then the leftmost
    signal: item group, subgroup, the prototype's `order`, its name. Anything
    the data does not place sorts after everything it does, by name.
    """
    from draftsman.data import fluids, items, recipes, signals
    from draftsman.data import entities as entity_data

    groups = {name: (data.get("order") or "", name) for name, data in items.groups.items()}
    subgroups = {
        name: (groups.get(data.get("group"), ("~", "")), data.get("order") or "", name)
        for name, data in items.subgroups.items()
    }
    tables = {
        "item": items.raw,
        "fluid": fluids.raw,
        "recipe": recipes.raw,
        "entity": entity_data.raw,
        "virtual": signals.raw,
    }

    cache: dict[tuple[str, str], tuple] = {}

    def prototype_key(kind: str, name: str) -> tuple:
        known = cache.get((kind, name))
        if known is not None:
            return known
        data = tables.get(kind, {}).get(name) or {}
        subgroup = data.get("subgroup")
        if subgroup is None and kind in ("entity", "recipe"):
            # Placed by, or making, an item of the same name more often than not.
            subgroup = (items.raw.get(name) or {}).get("subgroup")
        where = subgroups.get(subgroup)
        key = (0, where, data.get("order") or "", name) if where else (1, (), "", name)
        cache[(kind, name)] = key
        return key

    def key(signal: Signal) -> tuple:
        kind, name, quality = signal
        return (prototype_key(kind, name), quality)

    return key


# --------------------------------------------------------------------------
# reading inputs
# --------------------------------------------------------------------------


def _selected(networks: Mapping | None) -> tuple[bool, bool]:
    networks = networks or {}
    return bool(networks.get("red", True)), bool(networks.get("green", True))


def _read(signal: Signal, networks: Mapping | None, red: Signals, green: Signals) -> int:
    use_red, use_green = _selected(networks)
    return wrap((red.get(signal, 0) if use_red else 0) + (green.get(signal, 0) if use_green else 0))


def _present(networks: Mapping | None, red: Signals, green: Signals) -> Signals:
    use_red, use_green = _selected(networks)
    total: Signals = {}
    for source, used in ((red, use_red), (green, use_green)):
        if used:
            for signal, value in source.items():
                total[signal] = wrap(total.get(signal, 0) + value)
    return {s: v for s, v in total.items() if v}


_COMPARATORS: dict[str, Callable[[int, int], bool]] = {
    "<": operator.lt,
    ">": operator.gt,
    "=": operator.eq,
    "≥": operator.ge,
    ">=": operator.ge,
    "≤": operator.le,
    "<=": operator.le,
    "≠": operator.ne,
    "!=": operator.ne,
}


def _comparator(name: str | None) -> Callable[[int, int], bool]:
    try:
        return _COMPARATORS[name or "<"]
    except KeyError:
        raise CircuitError(f"unknown comparator {name!r}") from None


# --------------------------------------------------------------------------
# decider
# --------------------------------------------------------------------------


@dataclass
class _Condition:
    first: Signal | None
    first_networks: Mapping | None
    second: Signal | None
    second_networks: Mapping | None
    constant: int
    compare: Callable[[int, int], bool]
    joins_with_and: bool

    @classmethod
    def parse(cls, data: Mapping, index: int) -> "_Condition":
        return cls(
            first=signal_of(data.get("first_signal")),
            first_networks=data.get("first_signal_networks"),
            second=signal_of(data.get("second_signal")),
            second_networks=data.get("second_signal_networks"),
            constant=int(data.get("constant") or 0),
            compare=_comparator(data.get("comparator")),
            joins_with_and=index > 0 and data.get("compare_type") == "and",
        )

    def uses_each(self) -> bool:
        return _is_each(self.first) or _is_each(self.second)

    def right(self, red: Signals, green: Signals, each: Signal | None) -> int:
        if self.second is None:
            return self.constant
        if _is_each(self.second):
            return _read(each, self.second_networks, red, green) if each else 0
        return _read(self.second, self.second_networks, red, green)

    def holds(self, red: Signals, green: Signals, each: Signal | None, matched: list[Signal]) -> bool:
        if self.first is None:
            return False
        right = self.right(red, green, each)
        if _is_each(self.first):
            return self.compare(_read(each, self.first_networks, red, green), right) if each else False
        if _is_everything(self.first) or _is_anything(self.first):
            # A signal on the right is left out of what the wildcard checks, so
            # `Everything > X` does not fail on X itself.
            pool = _present(self.first_networks, red, green)
            if self.second is not None and not _is_each(self.second):
                pool.pop(self.second, None)
            if _is_everything(self.first):
                return all(self.compare(v, right) for v in pool.values())
            hits = [s for s, v in pool.items() if self.compare(v, right)]
            matched.extend(hits)
            return bool(hits)
        return self.compare(_read(self.first, self.first_networks, red, green), right)


def _groups(conditions: list[_Condition]) -> list[list[_Condition]]:
    """OR of AND-groups: AND binds tighter, as in the game."""
    groups: list[list[_Condition]] = []
    for condition in conditions:
        if condition.joins_with_and and groups:
            groups[-1].append(condition)
        else:
            groups.append([condition])
    return groups


def _evaluate(groups, red, green, each, order) -> tuple[bool, list[Signal]]:
    for group in groups:
        matched: list[Signal] = []
        if all(c.holds(red, green, each, matched) for c in group):
            return True, sorted(set(matched), key=order)
    return False, []


def _add(result: Signals, signal: Signal, value: int) -> None:
    result[signal] = wrap(result.get(signal, 0) + value)


def decide(parameters: Mapping, red: Signals, green: Signals, order=None) -> Signals:
    """The output of a decider combinator for one tick's inputs."""
    order = order or factoriopedia_order()
    conditions = [_Condition.parse(c, i) for i, c in enumerate(parameters.get("conditions") or [])]
    groups = _groups(conditions)
    outputs = parameters.get("outputs") or []
    # Else outputs arrived in 2.1; a 2.0 blueprint has none.
    else_outputs = parameters.get("else_outputs") or []
    result: Signals = {}

    def value(output: Mapping, source: Signal) -> int:
        if output.get("copy_count_from_input", True):
            return _read(source, output.get("networks"), red, green)
        return int(output.get("constant", 1))

    if any(c.uses_each() for c in conditions):
        candidates = sorted(set(red) | set(green), key=order)
        passed, failed = [], []
        for signal in candidates:
            (passed if _evaluate(groups, red, green, signal, order)[0] else failed).append(signal)
        for chosen, active in ((outputs, passed), (else_outputs, failed)):
            for output in chosen:
                target = signal_of(output.get("signal"))
                if target is None or _is_everything(target) or not active:
                    continue
                if _is_each(target):
                    for signal in active:
                        _add(result, signal, value(output, signal))
                elif _is_anything(target):
                    _add(result, active[0], value(output, active[0]))
                else:
                    for signal in active:
                        _add(result, target, value(output, signal))
    else:
        holds, matched = _evaluate(groups, red, green, None, order)
        for output in outputs if holds else else_outputs:
            target = signal_of(output.get("signal"))
            if target is None or _is_each(target):
                continue
            if _is_everything(target):
                for signal in sorted(_present(output.get("networks"), red, green), key=order):
                    _add(result, signal, value(output, signal))
            elif _is_anything(target):
                pool = matched or sorted(_present(output.get("networks"), red, green), key=order)
                if pool:
                    _add(result, pool[0], value(output, pool[0]))
            else:
                _add(result, target, value(output, target))
    return {s: v for s, v in result.items() if v}


# --------------------------------------------------------------------------
# arithmetic
# --------------------------------------------------------------------------


def _divide(a: int, b: int) -> int:
    if b == 0:
        return 0
    quotient = abs(a) // abs(b)
    return wrap(quotient if (a < 0) == (b < 0) else -quotient)


def _modulo(a: int, b: int) -> int:
    if b == 0:
        return 0
    return wrap(a - _divide(a, b) * b) if not (a == -_HALF and b == -1) else 0


def _power(a: int, b: int) -> int:
    if b < 0:
        return 1 if a == 1 else (-1 if b % 2 else 1) if a == -1 else 0
    return wrap(pow(a, b, _INT32))


def _unsigned(a: int) -> int:
    return a % _INT32


OPERATIONS: dict[str, Callable[[int, int], int]] = {
    "+": lambda a, b: wrap(a + b),
    "-": lambda a, b: wrap(a - b),
    "*": lambda a, b: wrap(a * b),
    "/": _divide,
    "%": _modulo,
    "^": _power,
    "<<": lambda a, b: wrap(_unsigned(a) << (b & 31)),
    ">>": lambda a, b: a >> (b & 31),
    "AND": lambda a, b: wrap(_unsigned(a) & _unsigned(b)),
    "OR": lambda a, b: wrap(_unsigned(a) | _unsigned(b)),
    "XOR": lambda a, b: wrap(_unsigned(a) ^ _unsigned(b)),
}


def calculate(parameters: Mapping, red: Signals, green: Signals, order=None) -> Signals:
    """The output of an arithmetic combinator for one tick's inputs."""
    order = order or factoriopedia_order()
    try:
        apply = OPERATIONS[parameters.get("operation") or "*"]
    except KeyError:
        raise CircuitError(f"unknown operation {parameters.get('operation')!r}") from None
    first = signal_of(parameters.get("first_signal"))
    second = signal_of(parameters.get("second_signal"))
    first_networks = parameters.get("first_signal_networks")
    second_networks = parameters.get("second_signal_networks")
    output = signal_of(parameters.get("output_signal"))
    if output is None:
        return {}

    def operand(signal, networks, constant, each):
        if signal is None:
            return int(constant or 0)
        return _read(each if _is_each(signal) else signal, networks, red, green)

    if not (_is_each(first) or _is_each(second)):
        if _is_each(output) or _is_everything(output) or _is_anything(output):
            return {}
        result = apply(
            operand(first, first_networks, parameters.get("first_constant"), None),
            operand(second, second_networks, parameters.get("second_constant"), None),
        )
        return {output: result} if result else {}

    each_networks = first_networks if _is_each(first) else second_networks
    result: Signals = {}
    for signal in sorted(_present(each_networks, red, green), key=order):
        value = apply(
            operand(first, first_networks, parameters.get("first_constant"), signal),
            operand(second, second_networks, parameters.get("second_constant"), signal),
        )
        _add(result, signal if _is_each(output) else output, value)
    return {s: v for s, v in result.items() if v}


# --------------------------------------------------------------------------
# selector
# --------------------------------------------------------------------------


class Unmodelled(CircuitError):
    """Behaviour the model does not reproduce, such as a random pick."""


def _quality_level(name: str | None) -> int | None:
    if name is None:
        return None
    try:
        from draftsman.data import qualities
    except ImportError:  # pragma: no cover - an older fork
        return None
    data = qualities.raw.get(name) if hasattr(qualities, "raw") else None
    return (data or {}).get("level")


def select(parameters: Mapping, red: Signals, green: Signals, order=None) -> Signals:
    """The output of a selector combinator for one tick's inputs."""
    order = order or factoriopedia_order()
    inputs = _present(None, red, green)
    mode = parameters.get("operation") or "select"

    if mode == "select":
        index_signal = signal_of(parameters.get("index_signal"))
        index = inputs.get(index_signal, 0) if index_signal else int(parameters.get("index_constant") or 0)
        pool = {s: v for s, v in inputs.items() if s != index_signal}
        descending = parameters.get("select_max", True)
        ranked = sorted(pool.items(), key=lambda kv: (-kv[1] if descending else kv[1], order(kv[0])))
        if len(ranked) == 1:
            return dict(ranked)
        return dict([ranked[index]]) if 0 <= index < len(ranked) else {}

    if mode == "count":
        target = signal_of(parameters.get("count_signal"))
        return {target: len(inputs)} if target and inputs else {}

    if mode in ("stack-size", "rocket-capacity"):
        from draftsman.data import items

        result: Signals = {}
        for signal in inputs:
            kind, name, _ = signal
            data = items.raw.get(name) if kind == "item" else None
            if data is None:
                continue
            if mode == "stack-size":
                amount = int(data.get("stack_size") or 0)
            else:
                weight = items.get_weight(name) if hasattr(items, "get_weight") else None
                amount = int(items.ROCKET_LIFT_WEIGHT // weight) if weight else 0
            if amount:
                result[signal] = amount
        return result

    if mode == "quality-filter":
        wanted = parameters.get("quality_filter") or {}
        quality = wanted.get("quality")
        if not quality:
            return inputs
        compare = _comparator(wanted.get("comparator") or "=")
        target = _quality_level(quality)
        result = {}
        for signal, value in inputs.items():
            level = _quality_level(signal[2])
            if target is None or level is None:
                keep = compare(0, 0) if signal[2] == quality else compare(0, 1)
            else:
                keep = compare(level, target)
            if keep:
                result[signal] = value
        return result

    raise Unmodelled(f"selector mode {mode!r} is not modelled")


# --------------------------------------------------------------------------
# the circuit
# --------------------------------------------------------------------------

# Combinators keep input and output apart; everything else has one connector
# per colour. The ids are the game's own (`defines.wire_connector_id`).
_DUAL = {1: (RED, "input"), 2: (GREEN, "input"), 3: (RED, "output"), 4: (GREEN, "output")}
_SINGLE = {1: (RED, None), 2: (GREEN, None)}

_COMPUTING = ("decider-combinator", "arithmetic-combinator", "selector-combinator")


def _entity_type(name: str) -> str:
    from draftsman.data import entities as entity_data

    kind = (entity_data.raw.get(name) or {}).get("type")
    if kind:
        return kind
    for known in _COMPUTING + ("constant-combinator",):
        if name.endswith(known):
            return known
    return "unknown"


def load_blueprint(source) -> dict:
    """A blueprint as a plain dict, from a string, a dict or a Draftsman object."""
    if hasattr(source, "to_dict"):
        source = source.to_dict()
    if isinstance(source, str):
        text = source.strip()
        if not text.startswith("0"):
            raise CircuitError("a blueprint string starts with '0'")
        source = json.loads(zlib.decompress(base64.b64decode(text[1:])))
    if "blueprint" in source:
        source = source["blueprint"]
    if "entities" not in source:
        raise CircuitError("not a single blueprint (a book or a planner?)")
    return source


def encode_blueprint(blueprint: Mapping) -> str:
    """The game's string form of a blueprint dict."""
    payload = json.dumps({"blueprint": blueprint}, separators=(",", ":")).encode("utf-8")
    return "0" + base64.b64encode(zlib.compress(payload, 9)).decode("ascii")


@dataclass
class Part:
    number: int
    name: str
    kind: str
    data: dict
    connectors: dict[int, int] = field(default_factory=dict)  # connector id -> network id

    @property
    def dual(self) -> bool:
        return self.kind in _COMPUTING

    def network(self, colour: str, side: str = "input") -> int | None:
        """The network on one connector: a colour, and for a combinator a side."""
        if self.dual:
            connector = {(RED, "input"): 1, (GREEN, "input"): 2, (RED, "output"): 3, (GREEN, "output"): 4}[(colour, side)]
        else:
            connector = 1 if colour == RED else 2
        return self.connectors.get(connector)

    def constant_output(self) -> Signals:
        behaviour = self.data.get("control_behavior") or {}
        if behaviour.get("is_on") is False:
            return {}
        out: Signals = {}
        for section in (behaviour.get("sections") or {}).get("sections") or []:
            if section.get("active") is False:
                continue
            multiplier = section.get("multiplier", 1)
            for entry in section.get("filters") or []:
                signal = signal_of(entry)
                if signal is not None:
                    _add(out, signal, int(entry.get("count") or 0) * int(multiplier))
        return {s: v for s, v in out.items() if v}


@dataclass
class Frame:
    """One tick: what every network carried, and what each part put out."""

    tick: int
    networks: dict[int, Signals]
    outputs: dict[int, Signals]


class Circuit:
    """A blueprint's circuit networks, ready to run.

    `networks` are numbered from 0 in the order they are first met. Each tick
    `step` returns the frame read on that tick: what the networks carry, which
    is what every combinator reads and every lamp and inserter sees.
    """

    def __init__(self, blueprint) -> None:
        self.blueprint = load_blueprint(blueprint)
        self.order = factoriopedia_order()
        self.parts: dict[int, Part] = {}
        for entity in self.blueprint.get("entities") or []:
            number = entity["entity_number"]
            self.parts[number] = Part(number, entity["name"], _entity_type(entity["name"]), entity)

        parent: dict[tuple[int, int], tuple[int, int]] = {}

        def find(node):
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        for wire in self.blueprint.get("wires") or []:
            a, ca, b, cb = wire
            if a not in self.parts or b not in self.parts:
                continue
            table_a = _DUAL if self.parts[a].dual else _SINGLE
            table_b = _DUAL if self.parts[b].dual else _SINGLE
            if ca not in table_a or cb not in table_b:
                continue  # copper, or a connector that carries no signals
            if table_a[ca][0] != table_b[cb][0]:
                raise CircuitError(f"wire {wire} joins a red connector to a green one")
            parent[find((a, ca))] = find((b, cb))

        ids: dict[tuple[int, int], int] = {}
        for node in sorted(parent):
            root = find(node)
            if root not in ids:
                ids[root] = len(ids)
            self.parts[node[0]].connectors[node[1]] = ids[root]
        self.network_count = len(ids)
        self.colours = {
            network: (_DUAL if self.parts[n].dual else _SINGLE)[c][0]
            for n, part in self.parts.items()
            for c, network in part.connectors.items()
        }

        self.tick = 0
        self.outputs: dict[int, Signals] = {}
        self.unmodelled: dict[int, str] = {}
        for number, part in self.parts.items():
            if part.kind == "constant-combinator":
                self.outputs[number] = part.constant_output()
        self.driven: dict[int, Signals] = {}

    # ------------------------------------------------------------------

    def drive(self, number: int, signals: Mapping | Signals | None) -> None:
        """Set what a part sends from now on, as a belt or an inserter would."""
        if not signals:
            self.driven.pop(number, None)
            return
        first = next(iter(signals))
        self.driven[number] = dict(signals) if isinstance(first, tuple) else parse_signals(signals)

    def _sending(self, part: Part) -> Iterable[int]:
        """The networks a part's output reaches."""
        table = _DUAL if part.dual else _SINGLE
        for connector, network in part.connectors.items():
            if not part.dual or table[connector][1] == "output":
                yield network

    def read(self) -> dict[int, Signals]:
        """What every network carries now."""
        totals: dict[int, Signals] = {n: {} for n in range(self.network_count)}
        for number, part in self.parts.items():
            sent = self.driven.get(number, self.outputs.get(number))
            if not sent:
                continue
            for network in set(self._sending(part)):
                bucket = totals[network]
                for signal, value in sent.items():
                    bucket[signal] = wrap(bucket.get(signal, 0) + value)
        return {n: {s: v for s, v in values.items() if v} for n, values in totals.items()}

    def inputs(self, number: int, networks: dict[int, Signals] | None = None) -> tuple[Signals, Signals]:
        networks = self.read() if networks is None else networks
        part = self.parts[number]
        red = part.network(RED)
        green = part.network(GREEN)
        return (networks.get(red, {}) if red is not None else {}), (networks.get(green, {}) if green is not None else {})

    def step(self) -> Frame:
        networks = self.read()
        fresh: dict[int, Signals] = {}
        for number, part in self.parts.items():
            if not part.dual:
                continue
            red, green = self.inputs(number, networks)
            behaviour = part.data.get("control_behavior") or {}
            try:
                if part.kind == "decider-combinator":
                    fresh[number] = decide(behaviour.get("decider_conditions") or {}, red, green, self.order)
                elif part.kind == "arithmetic-combinator":
                    fresh[number] = calculate(behaviour.get("arithmetic_conditions") or {}, red, green, self.order)
                else:
                    fresh[number] = select(behaviour, red, green, self.order)
            except Unmodelled as exc:
                self.unmodelled[number] = str(exc)
                fresh[number] = {}
        frame = Frame(self.tick, networks, {n: dict(v) for n, v in self.outputs.items()})
        self.outputs.update(fresh)
        self.tick += 1
        return frame

    def run(self, ticks: int, drive: Callable[[int], Mapping[int, Mapping]] | None = None) -> list[Frame]:
        """Run for a number of ticks; `drive(tick)` may set senders' outputs first."""
        frames = []
        for _ in range(ticks):
            if drive is not None:
                for number, signals in (drive(self.tick) or {}).items():
                    self.drive(number, signals)
            frames.append(self.step())
        return frames

    # ------------------------------------------------------------------

    def condition_holds(self, number: int, networks: dict[int, Signals] | None = None) -> bool | None:
        """Whether an entity's circuit condition holds -- a lamp lit, an inserter enabled.

        None when it has no condition. Red and green are summed, as every
        entity other than a combinator does.
        """
        behaviour = self.parts[number].data.get("control_behavior") or {}
        condition = behaviour.get("circuit_condition")
        if not condition:
            return None
        red, green = self.inputs(number, networks)
        return _evaluate(_groups([_Condition.parse(condition, 0)]), red, green, None, self.order)[0]


# --------------------------------------------------------------------------
# against the game
# --------------------------------------------------------------------------

_RECORDED_CONNECTORS = {
    "circuit-red": 1,
    "circuit-green": 2,
    "combinator-input-red": 1,
    "combinator-input-green": 2,
    "combinator-output-red": 3,
    "combinator-output-green": 4,
}


@dataclass
class Comparison:
    offset: int  # recorded frame index minus simulated tick
    compared: int
    mismatches: list[str]
    unmatched_entities: list[str]

    @property
    def agrees(self) -> bool:
        return not self.mismatches and not self.unmatched_entities


def _recorded_name(signal: Signal) -> str:
    return signal[1] if signal[2] == "normal" else f"{signal[1]}/{signal[2]}"


def compare(blueprint, recording: Mapping, max_offset: int = 4) -> Comparison:
    """Hold the model against a run of the same blueprint recorded in the game.

    The recording (the companion mod's `circuit-run.json`) names entities by
    position and prototype, and the blueprint was built somewhere else, so
    parts are matched by their arrangement: the offset that lines up the most
    positions. Ticks are matched the same way -- the first recorded frame is
    not necessarily the model's tick 0 -- and the best alignment is reported.
    """
    circuit = Circuit(blueprint)
    wiring = recording.get("wiring") or {}
    recorded = []
    for key, connectors in wiring.items():
        where, name = key.split(" ", 1)
        x, y = (float(v) for v in where.split(","))
        recorded.append((x, y, name, connectors))

    parts = list(circuit.parts.values())
    best, best_offset = -1, (0.0, 0.0)
    for x, y, name, _ in recorded:
        for part in parts:
            if part.name != name:
                continue
            dx = x - part.data["position"]["x"]
            dy = y - part.data["position"]["y"]
            hits = sum(
                1
                for rx, ry, rn, _ in recorded
                for p in parts
                if p.name == rn and abs(rx - dx - p.data["position"]["x"]) < 0.01 and abs(ry - dy - p.data["position"]["y"]) < 0.01
            )
            if hits > best:
                best, best_offset = hits, (dx, dy)

    watch: list[tuple[int, int, str]] = []  # (network in model, recorded network id, description)
    unmatched = []
    for x, y, name, connectors in recorded:
        part = next(
            (
                p
                for p in parts
                if p.name == name
                and abs(x - best_offset[0] - p.data["position"]["x"]) < 0.01
                and abs(y - best_offset[1] - p.data["position"]["y"]) < 0.01
            ),
            None,
        )
        if part is None:
            unmatched.append(f"{name} at {x},{y}")
            continue
        for connector_label, recorded_id in connectors.items():
            connector = _RECORDED_CONNECTORS.get(connector_label)
            network = part.connectors.get(connector) if connector else None
            if network is not None:
                watch.append((network, recorded_id, f"#{part.number} {name} {connector_label}"))

    frames = recording.get("frames") or []
    simulated = [frame.networks for frame in circuit.run(len(frames) + 2 * max_offset + 1)]

    def mismatches_at(offset: int, limit: int | None) -> list[str]:
        found = []
        for index, frame in enumerate(frames):
            tick = index - offset
            if tick < 0 or tick >= len(simulated):
                continue
            for network, recorded_id, where in watch:
                expected = {_recorded_name(s): v for s, v in simulated[tick].get(network, {}).items()}
                actual = {k: v for k, v in (frame.get("networks") or {}).get(str(recorded_id), {}).items() if v}
                if expected != actual:
                    found.append(f"frame {index} (model tick {tick}), {where}: model {expected}, game {actual}")
                    if limit is not None and len(found) >= limit:
                        return found
        return found

    scores = {offset: len(mismatches_at(offset, None)) for offset in range(-max_offset, max_offset + 1)}
    offset = min(scores, key=lambda o: (scores[o], abs(o)))
    return Comparison(offset, len(frames), mismatches_at(offset, 50), unmatched)
