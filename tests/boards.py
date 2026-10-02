"""A terse way to lay out combinators for tests, in the game's own blueprint form.

Draftsman validates every signal against the active game data, which differs
between a player's profile and CI; the circuit model reads plain blueprint
dicts, and these are those dicts, written the way the game writes them.
"""

from __future__ import annotations

BOTH, RED_ONLY, GREEN_ONLY = "RG", "R", "G"


def signal(name: str, quality: str | None = None) -> dict:
    if ":" in name:
        kind, name = name.split(":", 1)
    elif name.startswith("signal-"):
        kind = "virtual"
    elif name in ("water", "crude-oil", "petroleum-gas", "light-oil", "heavy-oil", "steam", "lubricant", "sulfuric-acid"):
        kind = "fluid"
    else:
        kind = "item"
    data = {"name": name} if kind == "item" else {"type": kind, "name": name}
    if quality:
        data["quality"] = quality
    return data


def wires(on: str) -> dict:
    return {"red": "R" in on, "green": "G" in on}


def when(first: str | None, comparator: str, second: str | int = 0, *, first_on: str = BOTH,
         second_on: str = BOTH, join: str = "or") -> dict:
    condition = {"comparator": comparator, "compare_type": join}
    if first is not None:
        condition["first_signal"] = signal(first)
        condition["first_signal_networks"] = wires(first_on)
    if isinstance(second, str):
        condition["second_signal"] = signal(second)
        condition["second_signal_networks"] = wires(second_on)
    else:
        condition["constant"] = second
    return condition


def also(first, comparator, second=0, **kw) -> dict:
    """A condition joined to the one before with AND."""
    return when(first, comparator, second, join="and", **kw)


def out(name: str, count: bool | int = True, on: str = BOTH) -> dict:
    output = {"signal": signal(name), "networks": wires(on)}
    if count is True:
        output["copy_count_from_input"] = True
    else:
        output["copy_count_from_input"] = False
        output["constant"] = int(count)
    return output


class Board:
    """Entities in a row, numbered from 1 in the order they are added."""

    CONNECTOR = {("red", "in"): 1, ("green", "in"): 2, ("red", "out"): 3, ("green", "out"): 4}

    def __init__(self) -> None:
        self.entities: list[dict] = []
        self.wires: list[list[int]] = []
        self.dual: set[int] = set()

    def _add(self, name: str, control: dict | None = None, dual: bool = False) -> int:
        number = len(self.entities) + 1
        entity = {"entity_number": number, "name": name, "position": {"x": 2 * number + 0.5, "y": 0.5}}
        if control:
            entity["control_behavior"] = control
        self.entities.append(entity)
        if dual:
            self.dual.add(number)
        return number

    def constant(self, signals: dict[str, int] | None = None, on: bool = True) -> int:
        filters = [
            {"index": i + 1, **signal(name), "quality": "normal", "comparator": "=", "count": count}
            for i, (name, count) in enumerate((signals or {}).items())
        ]
        control = {"sections": {"sections": [{"index": 1, "filters": filters}]}}
        if not on:
            control["is_on"] = False
        return self._add("constant-combinator", control)

    def decider(self, conditions: list[dict], outputs: list[dict], else_outputs: list[dict] | None = None) -> int:
        params = {"conditions": conditions, "outputs": outputs}
        if else_outputs:
            params["else_outputs"] = else_outputs
        return self._add("decider-combinator", {"decider_conditions": params}, dual=True)

    def arithmetic(self, first: str | int, operation: str, second: str | int, output: str, *,
                   first_on: str = BOTH, second_on: str = BOTH) -> int:
        params = {"operation": operation, "output_signal": signal(output)}
        for side, value, on in (("first", first, first_on), ("second", second, second_on)):
            if isinstance(value, str):
                params[f"{side}_signal"] = signal(value)
                params[f"{side}_signal_networks"] = wires(on)
            else:
                params[f"{side}_constant"] = value
        return self._add("arithmetic-combinator", {"arithmetic_conditions": params}, dual=True)

    def selector(self, **params) -> int:
        for key in ("index_signal", "count_signal"):
            if isinstance(params.get(key), str):
                params[key] = signal(params[key])
        return self._add("selector-combinator", params, dual=True)

    def lamp(self, first: str, comparator: str, constant: int = 0) -> int:
        condition = {"first_signal": signal(first), "comparator": comparator, "constant": constant}
        return self._add("small-lamp", {"circuit_enabled": True, "circuit_condition": condition})

    def sender(self, name: str = "iron-chest") -> int:
        """Something outside the model that puts signals on a wire: set with `drive`."""
        return self._add(name)

    def pole(self) -> int:
        return self._add("small-electric-pole")

    def wire(self, colour: str, a: int, b: int, a_side: str = "out", b_side: str = "in") -> None:
        def connector(number: int, side: str) -> int:
            if number in self.dual:
                return self.CONNECTOR[(colour, side)]
            return 1 if colour == "red" else 2

        self.wires.append([a, connector(a, a_side), b, connector(b, b_side)])

    def loop(self, colour: str, number: int) -> None:
        """A combinator's output wired back to its own input: memory."""
        self.wire(colour, number, number, "out", "in")

    def blueprint(self) -> dict:
        return {"item": "blueprint", "entities": self.entities, "wires": self.wires, "version": 562949956239363}
