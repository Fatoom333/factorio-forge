"""Small changes to a plan, and a rebuild that only redoes what they touched.

    factorio-forge edit plan.json move 2 0 5  set red recipe=advanced-circuit  rows 1 +1  remove old

Each operation is a verb and a few words; the plan file is rewritten (or a copy,
with `-o`) and built again. The operations:

    move <block> <dx> <dy>            shift the block by whole tiles
    set <block> <field>=<value>       a block field: recipe, machine, belt, inserter, long_inserter,
                                      pole, pipe, pipe_to_ground, stack, align, speed_bonus, stack_size,
                                      input_belts, planned, rotate; `none` clears an optional one
    rows <block> +1 | -1 | per=<n>    one row more or fewer (a new row copies the last), or n machines in every row
    remove <block>                    drop the block and the connections and sources that name it

A block is its index or its `id`. Connections and sources that name a block
by index are renumbered when a block is removed; those that name it by `id` need no care.

A rebuild keeps `BuildCache` beside the plan (`.<plan>.cache/`): each block's
expansion under a hash of the block's own definition, and each route under its
connection, the plan's routing options and the tiles of the grid its search
actually looked at. A block or route whose key and looked-at tiles are the same
is read back instead of computed; one that is not is redone, so an edit costs
what it touched. The result is the same as a build without the cache.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import enum
import hashlib
import io
import json
import os
import pickle
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import plan as plan_module
from . import route

# --------------------------------------------------------------------------
# operations
# --------------------------------------------------------------------------


class EditError(Exception):
    """An operation that cannot be applied to this plan; the message is one line."""


_VERBS = {"move": 4, "set": 3, "rows": 3, "remove": 2}  # words each operation takes, the verb included

_TEXT_FIELDS = ("recipe", "machine", "belt", "inserter", "long_inserter", "pole", "pipe", "pipe_to_ground",
                "stack", "align")
_NUMBER_FIELDS = {"speed_bonus": float, "planned": float, "stack_size": int, "input_belts": int, "rotate": int}
_REQUIRED = ("recipe", "machine")
SETTABLE = _TEXT_FIELDS + tuple(_NUMBER_FIELDS)


def split_operations(words: list[str]) -> list[list[str]]:
    """The command line's words cut into operations: [verb, argument, ...] each."""
    ops: list[list[str]] = []
    i = 0
    while i < len(words):
        verb = words[i]
        if verb not in _VERBS:
            raise EditError(f"unknown operation {verb!r}; the operations are {', '.join(_VERBS)}")
        need = _VERBS[verb]
        chunk = words[i:i + need]
        if len(chunk) < need:
            raise EditError(f"{_USAGE[verb]} (got {' '.join(chunk)!r})")
        ops.append(chunk)
        i += need
    if not ops:
        raise EditError("no operation; give " + ", ".join(_VERBS))
    return ops


_USAGE = {
    "move": "move <block> <dx> <dy>",
    "set": "set <block> <field>=<value>",
    "rows": "rows <block> +1 | -1 | per=<n>",
    "remove": "remove <block>",
}


def find_block(plan: dict, word: str) -> int:
    blocks = plan.get("blocks") or []
    if re.fullmatch(r"\d+", word):
        index = int(word)
        if index >= len(blocks):
            raise EditError(f"no block {index}; the plan has {len(blocks)}")
        return index
    ids = plan_module.block_ids(plan)
    if word not in ids:
        known = ", ".join(sorted(ids)) or "none"
        raise EditError(f"no block {word!r}; the ids are: {known}")
    return ids[word]


def _whole(word: str, what: str) -> int:
    try:
        return int(word)
    except ValueError:
        raise EditError(f"{what} is a whole number of tiles, not {word!r}") from None


def apply_operation(plan: dict, op: list[str]) -> list[str]:
    """Apply one operation to the plan in place; returns what it did, one line each."""
    verb = op[0]
    index = find_block(plan, op[1])
    block = plan["blocks"][index]
    name = f"block {index}" + (f" ({block['id']})" if isinstance(block.get("id"), str) else "")
    if verb == "move":
        dx, dy = _whole(op[2], "dx"), _whole(op[3], "dy")
        x, y = block.get("at") or (0, 0)
        block["at"] = [int(x) + dx, int(y) + dy]
        return [f"{name} moved by ({dx}, {dy}) to {block['at']}"]
    if verb == "set":
        return [_set_field(block, name, op[2])]
    if verb == "rows":
        return [_change_rows(block, name, op[2])]
    return _remove_block(plan, index, name)


def apply_operations(plan: dict, ops: list[list[str]]) -> list[str]:
    said: list[str] = []
    for op in ops:
        said.extend(apply_operation(plan, op))
    return said


def _set_field(block: dict, name: str, text: str) -> str:
    key, sep, value = text.partition("=")
    if not sep or not key:
        raise EditError(f"{_USAGE['set']}, not {text!r}")
    if key == "modules":
        raise EditError("a block has no 'modules' yet; the speed they give is 'speed_bonus=<fraction>'")
    if key not in SETTABLE:
        raise EditError(f"a block has no settable field {key!r}; the fields are {', '.join(SETTABLE)}")
    if value.lower() == "none":
        if key in _REQUIRED:
            raise EditError(f"a block cannot go without {key}")
        had = block.pop(key, None)
        return f"{name}: {key} cleared" + ("" if had is not None else " (it was not set)")
    if key in _NUMBER_FIELDS:
        try:
            parsed = _NUMBER_FIELDS[key](value)
        except ValueError:
            raise EditError(f"{key} is a number, not {value!r}") from None
        if key == "rotate" and parsed not in (0, 4, 8, 12):
            raise EditError(f"rotate is 0, 4, 8 or 12, not {parsed}")
    else:
        if not value:
            raise EditError(f"{key}= needs a value")
        parsed = value
    before = block.get(key)
    block[key] = parsed
    return f"{name}: {key} {before if before is not None else '(unset)'} -> {parsed}"


def _change_rows(block: dict, name: str, text: str) -> str:
    value = block.get("rows")
    count = value if isinstance(value, int) and not isinstance(value, bool) else (
        len(value) if isinstance(value, list) else None)
    if count is None:
        raise EditError(f"{name} has no rows to change")
    if text in ("+1", "-1"):
        step = int(text)
        if count + step < 1:
            raise EditError(f"{name} has {count} row; a block keeps at least one (use remove)")
        if isinstance(value, list):
            if step > 0:
                value.append(value[-1])
            else:
                value.pop()
        else:
            block["rows"] = value + step
        return f"{name}: rows {count} -> {count + step}"
    key, sep, number = text.partition("=")
    if sep and key == "per":
        n = _whole(number, "per")
        if n < 1:
            raise EditError("per= is at least 1 machine")
        if isinstance(value, list):
            block["rows"] = [n] * len(value)
        else:
            block["per_row"] = n
        return f"{name}: {n} machines in each of {count} row(s)"
    raise EditError(f"{_USAGE['rows']}, not {text!r}")


def _renumber(node, index: int) -> None:
    for d in plan_module._flat(node):
        b = d.get("block")
        if isinstance(b, int) and not isinstance(b, bool) and b > index:
            d["block"] = b - 1


def _remove_block(plan: dict, index: int, name: str) -> list[str]:
    ids = plan_module.block_ids(plan)

    def at(ref):
        """The index a block reference means before the removal, or None for anything else."""
        if isinstance(ref, bool):
            return None
        return ids.get(ref) if isinstance(ref, str) else ref if isinstance(ref, int) else None

    def mentions(node) -> bool:
        return any(at(d.get("block")) == index for d in plan_module._flat(node))

    def ports_in(node, key: str) -> set[tuple[int, int]]:
        """The (block, port) pairs a connection's `key` ('to') side ends at."""
        found = set()
        for d in plan_module._flat(node.get(key)):
            if at(d.get("block")) is not None and isinstance(d.get("port"), int):
                found.add((at(d["block"]), d["port"]))
        return found

    dropped: list[str] = []
    sources = plan.get("sources") or []
    keep_sources = []
    for i, entry in enumerate(sources):
        if mentions(entry):
            dropped.append(f"source {entry.get('id', i)!r}")
        else:
            keep_sources.append(entry)
    connections = [c for c in plan.get("connections") or []]
    gone: set[str] = set()
    ended: set[tuple[int, int]] = set()  # in ports whose route is gone
    alive = []
    for i, conn in enumerate(connections):
        if isinstance(conn, dict) and mentions(conn):
            gone.add(plan_module._ident(conn, i))
            ended |= ports_in(conn, "to")
            dropped.append(f"connection {plan_module._ident(conn, i)!r}")
        else:
            alive.append((i, conn))
    # a merge onto a route or an in port whose route is gone has nothing to merge onto
    again = True
    while again:
        again = False
        for i, conn in list(alive):
            onto = conn.get("onto") if isinstance(conn, dict) else None
            if not isinstance(onto, dict):
                continue
            route_name = onto.get("route")
            by_route = isinstance(route_name, str) and route_name.split("/")[0] in gone
            by_port = isinstance(onto.get("port"), int) and (at(onto.get("block")), onto["port"]) in ended
            if by_route or by_port:
                ident = plan_module._ident(conn, i)
                gone.add(ident)
                ended |= ports_in(conn, "to")
                dropped.append(f"connection {ident!r}")
                alive.remove((i, conn))
                again = True
    if "sources" in plan:
        plan["sources"] = keep_sources
    if "connections" in plan:
        plan["connections"] = [conn for _, conn in alive]
    del plan["blocks"][index]
    for field_ in ("connections", "sources"):
        _renumber(plan.get(field_), index)
    said = [f"{name} removed"]
    if dropped:
        said.append("dropped with it: " + ", ".join(dropped))
    return said


def load_plan_text(path: Path) -> dict:
    return plan_module.load(path)


def write_plan(plan: dict, path: Path) -> None:
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# what a cache can see
# --------------------------------------------------------------------------

_MISSING = ("<missing>",)


def canonical(value):
    """A value as plain tuples, strings and numbers that read the same in every run (sets sorted)."""
    kind = type(value)
    if kind is str or kind is int or kind is bool or value is None:
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return (type(value).__name__, tuple((f.name, canonical(getattr(value, f.name)))
                                            for f in dataclasses.fields(value)))
    if isinstance(value, (set, frozenset)):
        return ("set", tuple(sorted((canonical(x) for x in value), key=repr)))
    if isinstance(value, dict):
        return ("dict", tuple(sorted(((canonical(k), canonical(v)) for k, v in value.items()), key=repr)))
    if isinstance(value, (list, tuple)):
        return tuple(canonical(x) for x in value)
    if isinstance(value, enum.Enum):
        return ("enum", type(value).__name__, value.name)
    return value


class Log:
    """What a stretch of routing read: the value every grid entry had when first touched."""

    def __init__(self) -> None:
        self.before: dict[str, dict] = {}
        self.inside: dict[tuple[int, int], bool] = {}
        self.whole = False  # the code walked a whole table: the entry then depends on all of it
        self.area_read = False


class WatchedDict(dict):
    """A dict that tells its Log which keys the routing code looked at or changed."""

    def __init__(self, name: str, log: Log | None, data=()) -> None:
        dict.__init__(self, data)
        self.name = name
        self.log = log
        self.seen = None if log is None else log.before.setdefault(name, {})

    def _touch(self, key) -> None:
        seen = self.seen
        if seen is not None and key not in seen:
            seen[key] = canonical(dict.get(self, key, _MISSING))

    def _walk(self) -> None:
        if self.log is not None:
            self.log.whole = True

    def __getitem__(self, key):
        seen = self.seen
        if seen is not None and key not in seen:
            seen[key] = canonical(dict.get(self, key, _MISSING))
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        seen = self.seen
        if seen is not None and key not in seen:
            seen[key] = canonical(dict.get(self, key, _MISSING))
        return dict.get(self, key, default)

    def __contains__(self, key):
        seen = self.seen
        if seen is not None and key not in seen:
            seen[key] = canonical(dict.get(self, key, _MISSING))
        return dict.__contains__(self, key)

    def __setitem__(self, key, value):
        self._touch(key)
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        self._touch(key)
        dict.__delitem__(self, key)

    def setdefault(self, key, default=None):
        self._touch(key)
        return dict.setdefault(self, key, default)

    def pop(self, key, *default):
        self._touch(key)
        return dict.pop(self, key, *default)

    def popitem(self):
        self._walk()
        return dict.popitem(self)

    def update(self, *args, **kwargs):
        self._walk()
        dict.update(self, *args, **kwargs)

    def clear(self):
        self._walk()
        dict.clear(self)

    def __iter__(self):
        self._walk()
        return dict.__iter__(self)

    def __len__(self):
        self._walk()
        return dict.__len__(self)

    def keys(self):
        self._walk()
        return dict.keys(self)

    def values(self):
        self._walk()
        return dict.values(self)

    def items(self):
        self._walk()
        return dict.items(self)

    def copy(self):
        self._walk()
        return dict(dict.items(self))

    def __eq__(self, other):
        self._walk()
        return dict.__eq__(self, other)

    __hash__ = None  # type: ignore[assignment]


class WatchedSet(set):
    def __init__(self, name: str, log: Log | None, data=()) -> None:
        set.__init__(self, data)
        self.name = name
        self.log = log
        self.seen = None if log is None else log.before.setdefault(name, {})

    def _touch(self, key) -> None:
        seen = self.seen
        if seen is not None and key not in seen:
            seen[key] = set.__contains__(self, key)

    def _walk(self) -> None:
        if self.log is not None:
            self.log.whole = True

    def __contains__(self, key):
        seen = self.seen
        if seen is not None and key not in seen:
            seen[key] = set.__contains__(self, key)
        return set.__contains__(self, key)

    def add(self, key):
        self._touch(key)
        set.add(self, key)

    def discard(self, key):
        self._touch(key)
        set.discard(self, key)

    def remove(self, key):
        self._touch(key)
        set.remove(self, key)

    def __iter__(self):
        self._walk()
        return set.__iter__(self)

    def __len__(self):
        self._walk()
        return set.__len__(self)

    def copy(self):
        self._walk()
        return set(set.copy(self))

    def update(self, *others):
        self._walk()
        set.update(self, *others)

    def clear(self):
        self._walk()
        set.clear(self)

    def __eq__(self, other):
        self._walk()
        return set.__eq__(self, other)

    __hash__ = None  # type: ignore[assignment]


_TABLES = ("occupied", "reserved", "belt_fed", "hands", "fluid_targets", "feeders", "fluid_owner", "belt_entry")


class WatchedGrid(route.Grid):
    """A routing grid that reports which of its tiles the search looked at.

    Between `watch(log)` and `unwatch()` every read or change of a table entry,
    every `inside` question and any read of `area` is written to the log; a
    whole-table walk (iteration, length, copy of a table other than through
    `copy()`) marks the log as depending on everything.
    """

    def __init__(self, area, occupied, reserved, belt_fed, hands, fluid_targets, tunnels,
                 feeders=None, fluid_owner=None, belt_entry=None, log: Log | None = None) -> None:
        self._log = log
        super().__init__(
            area,
            WatchedDict("occupied", log, occupied), WatchedSet("reserved", log, reserved),
            WatchedDict("belt_fed", log, belt_fed), WatchedDict("hands", log, hands),
            WatchedDict("fluid_targets", log, fluid_targets), tunnels,
            WatchedDict("feeders", log, feeders or {}), WatchedDict("fluid_owner", log, fluid_owner or {}),
            WatchedDict("belt_entry", log, belt_entry or {}),
        )

    @property
    def area(self):
        if self._log is not None:
            self._log.area_read = True
        return self._area

    @area.setter
    def area(self, value) -> None:
        self._area = value

    @classmethod
    def of(cls, grid: route.Grid) -> "WatchedGrid":
        return cls(grid._area if isinstance(grid, WatchedGrid) else grid.area, dict(grid.occupied), set(grid.reserved),
                   dict(grid.belt_fed), dict(grid.hands), {k: list(v) for k, v in grid.fluid_targets.items()},
                   list(grid.tunnels), {k: set(v) for k, v in grid.feeders.items()}, dict(grid.fluid_owner),
                   dict(grid.belt_entry))

    def watch(self, log: Log | None) -> None:
        self._log = log
        for name in _TABLES:
            table = getattr(self, name)
            table.log = log
            table.seen = None if log is None else log.before.setdefault(name, {})

    def inside(self, tile) -> bool:
        x0, y0, x1, y1 = self._area
        answer = x0 <= tile[0] <= x1 and y0 <= tile[1] <= y1
        if self._log is not None:
            self._log.inside.setdefault(tile, answer)
        return answer

    def copy(self) -> "WatchedGrid":
        """A trial grid that reports to the same log."""
        return WatchedGrid(
            self._area, dict(dict.items(self.occupied)), set(set.copy(self.reserved)),
            dict(dict.items(self.belt_fed)), dict(dict.items(self.hands)),
            {k: list(v) for k, v in dict.items(self.fluid_targets)}, list(self.tunnels),
            {k: set(v) for k, v in dict.items(self.feeders)}, dict(dict.items(self.fluid_owner)),
            dict(dict.items(self.belt_entry)), log=self._log,
        )

    def now(self, table: str, key):
        """What a table holds at a key, as the log would have recorded it."""
        t = getattr(self, table)
        if isinstance(t, WatchedSet):
            return set.__contains__(t, key)
        return canonical(dict.get(t, key, _MISSING))

    def raw(self, table: str, key):
        return dict.get(getattr(self, table), key, _MISSING)


# --------------------------------------------------------------------------
# the cache
# --------------------------------------------------------------------------

ENTRIES_FILE = "entries.pkl"
_ALLOWED_MODULES = {"factorio_forge.route", "factorio_forge.rows", "factorio_forge.inspection",
                    "factorio_forge.layout", "factorio_forge.fluids"}
_ALLOWED_BUILTINS = {"set", "frozenset", "tuple", "list", "dict"}


class _SafeUnpickler(pickle.Unpickler):
    """Reads back only this package's plain data classes.

    A cache folder sits beside a plan that may have come from anywhere, so a pickle in it is not
    trusted: nothing but dataclasses and enums of the modules below, and a few built-in containers,
    can be named, and none of them runs code when built.
    """

    def find_class(self, module, name):
        if module == "builtins" and name in _ALLOWED_BUILTINS:
            return super().find_class(module, name)
        if module in _ALLOWED_MODULES:
            obj = super().find_class(module, name)
            if isinstance(obj, type) and (dataclasses.is_dataclass(obj) or issubclass(obj, enum.Enum)):
                return obj
        raise pickle.UnpicklingError(f"{module}.{name} is not data this cache holds")


def _loads(data: bytes):
    return _SafeUnpickler(io.BytesIO(data)).load()


def code_digest() -> str:
    """A digest of this package's own source: a cache made by other code is not read."""
    h = hashlib.sha256()
    for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def data_identity() -> str:
    """Which game data the build reads: draftsman's, the active profile's, and the exported research."""
    parts = [code_digest()]
    try:
        import draftsman

        parts.append(f"draftsman {draftsman.__version__}")
    except Exception as exc:  # noqa: BLE001
        parts.append(f"draftsman? {exc.__class__.__name__}")
    try:
        from .profile import ACTIVE_MARKER, Profile

        marker = Profile._draftsman_data_dir() / ACTIVE_MARKER
        parts.append(marker.read_text(encoding="utf-8") if marker.is_file() else "no marker")
    except Exception as exc:  # noqa: BLE001
        parts.append(f"profile? {exc.__class__.__name__}")
    try:
        from . import environment

        found, why = environment.for_active_profile()
        parts.append(repr(canonical(found.bonuses)) if found is not None and found.bonuses is not None else "")
        parts.append(why)
    except Exception as exc:  # noqa: BLE001
        parts.append(f"environment? {exc.__class__.__name__}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


@dataclass
class CacheStats:
    blocks_reused: int = 0
    blocks_built: int = 0
    routes_reused: int = 0
    routes_built: int = 0
    walked: int = 0  # routes whose search walked a whole table, so they depend on all of the grid
    reused_ids: list[str] = field(default_factory=list)
    built_ids: list[str] = field(default_factory=list)


class BuildCache:
    """Blocks and routes kept from the last build of a plan, keyed by what they depend on.

    With `folder` None the cache lives in memory only. A hit returns exactly what a build
    would have computed, so a cached build and a clean one produce the same blueprint.
    """

    def __init__(self, folder: Path | None = None, identity: str | None = None) -> None:
        self.folder = folder
        self.identity = identity if identity is not None else data_identity()
        self.memory: dict[tuple[str, str], bytes] = {}
        self.used: set[tuple[str, str]] = set()
        self.stats = CacheStats()
        self.loaded = False
        self.dirty = False

    # -- storage -----------------------------------------------------------

    def _key(self, kind: str, text: str) -> str:
        return hashlib.sha256(f"{self.identity}\n{kind}\n{text}".encode("utf-8")).hexdigest()[:40]

    def _entries_path(self) -> Path | None:
        return None if self.folder is None else self.folder / ENTRIES_FILE

    def _load_disk(self) -> None:
        """Read the saved entries once: one file, because opening many small ones is slow on some systems."""
        if self.loaded:
            return
        self.loaded = True
        path = self._entries_path()
        if path is None or not path.is_file():
            return
        try:
            saved = _loads(path.read_bytes())
        except Exception:  # noqa: BLE001 - an unreadable cache is an empty one
            return
        if isinstance(saved, dict):
            self.memory.update((k, v) for k, v in saved.items()
                               if isinstance(k, tuple) and len(k) == 2 and isinstance(v, bytes))

    def _read(self, kind: str, key: str):
        self._load_disk()
        data = self.memory.get((kind, key))
        if data is None:
            return None
        try:
            return _loads(data)
        except Exception:  # noqa: BLE001 - an unreadable entry is a miss
            return None

    def _write(self, kind: str, key: str, value) -> None:
        try:
            data = pickle.dumps(value, protocol=4)
        except Exception:  # noqa: BLE001 - what cannot be kept is simply built again
            return
        self.memory[(kind, key)] = data
        self.dirty = True

    def save(self, prune: bool = True) -> None:
        """Write the cache to its folder; with `prune`, keep only the entries this build used."""
        self._load_disk()
        if prune:
            gone = [k for k in self.memory if k not in self.used]
            for k in gone:
                del self.memory[k]
            self.dirty = self.dirty or bool(gone)
        path = self._entries_path()
        if path is None or not self.dirty:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".tmp{os.getpid()}")
            tmp.write_bytes(pickle.dumps(dict(self.memory), protocol=4))
            os.replace(tmp, path)
            self.dirty = False
        except OSError:
            pass

    prune = save

    # -- blocks ------------------------------------------------------------

    def block(self, text: str, compute):
        key = self._key("blocks", text)
        self.used.add(("blocks", key))
        got = self._read("blocks", key)
        if got is not None:
            self.stats.blocks_reused += 1
            return got
        value = compute()
        self._write("blocks", key, value)
        self.stats.blocks_built += 1
        return value

    # -- routes ------------------------------------------------------------

    def watched(self, grid: route.Grid) -> WatchedGrid:
        return WatchedGrid.of(grid)

    def stage(self, spec, options: tuple, grid: WatchedGrid, target, run):
        """The results of routing one connection: read back if what its search looked at is unchanged."""
        text = repr((canonical(spec), options,
                     canonical(target) if target is not None else None,
                     canonical(target.spec) if target is not None else None))
        key = self._key("routes", text)
        self.used.add(("routes", key))
        entry = self._read("routes", key)
        if entry is not None and self._holds(entry, grid):
            self._apply(entry, grid, target)
            self.stats.routes_reused += 1
            self.stats.reused_ids.append(getattr(spec, "id", "?"))
            return entry["results"]
        self.stats.routes_built += 1
        self.stats.built_ids.append(getattr(spec, "id", "?"))

        before_tunnels = canonical(grid.tunnels)
        merges_before = list(target.merges) if target is not None else None
        shell_before = canonical(dataclasses.replace(target, merges=[])) if target is not None else None
        log = Log()
        grid.watch(log)
        try:
            done = run()
        finally:
            grid.watch(None)
        keep = not log.whole  # a search that walked a whole table depends on all of the grid: not kept
        if log.whole:
            self.stats.walked += 1
        record = {
            "area": grid._area if log.area_read else None,
            "inside": dict(log.inside),
            "before": log.before,
            "tunnels": before_tunnels,
            "results": done,
            "changes": {},
            "tunnels_after": None,
            "merges": None,
        }
        for table, seen in log.before.items():
            changes = {}
            for k, was in seen.items():
                if table == "reserved":
                    if grid.now(table, k) != was:
                        keep = False
                    continue
                if grid.now(table, k) != was:
                    value = grid.raw(table, k)
                    gone = isinstance(value, tuple) and value == _MISSING
                    changes[k] = ("gone", None) if gone else ("set", copy.deepcopy(value))
            if changes:
                record["changes"][table] = changes
        if canonical(grid.tunnels) != before_tunnels:
            record["tunnels_after"] = list(grid.tunnels)
        if target is not None:
            if canonical(dataclasses.replace(target, merges=[])) != shell_before:
                keep = False  # the stage changed more of the target than its merges
            elif list(target.merges) != merges_before:
                record["merges"] = list(target.merges)
        if keep:
            self._write("routes", key, record)
        return done

    @staticmethod
    def _holds(entry: dict, grid: WatchedGrid) -> bool:
        if canonical(grid.tunnels) != entry["tunnels"]:
            return False
        if entry["area"] is not None and grid._area != entry["area"]:
            return False
        x0, y0, x1, y1 = grid._area
        for tile, answer in entry["inside"].items():
            if (x0 <= tile[0] <= x1 and y0 <= tile[1] <= y1) != answer:
                return False
        for table, seen in entry["before"].items():
            for k, was in seen.items():
                if grid.now(table, k) != was:
                    return False
        return True

    @staticmethod
    def _apply(entry: dict, grid: WatchedGrid, target) -> None:
        for table, changes in entry["changes"].items():
            t = getattr(grid, table)
            for k, (how, value) in changes.items():
                if how == "gone":
                    dict.pop(t, k, None)
                else:
                    dict.__setitem__(t, k, value)
        if entry["tunnels_after"] is not None:
            grid.tunnels = list(entry["tunnels_after"])
        if target is not None and entry["merges"] is not None:
            target.merges[:] = entry["merges"]


def cache_folder(plan_path: Path) -> Path:
    plan_path = Path(plan_path).resolve()
    return plan_path.parent / f".{plan_path.stem}.cache"


# --------------------------------------------------------------------------
# rebuild
# --------------------------------------------------------------------------


@dataclass
class Rebuilt:
    result: object
    warnings: list[str]
    stats: CacheStats
    seconds: float


def rebuild(plan: dict, plan_path: Path, cache: BuildCache | None) -> Rebuilt:
    """Build the plan (with the cache, if any); the same call `build` makes, plus the request's planned rates."""
    started = time.perf_counter()
    planned, warned = None, []
    if plan.get("request"):
        planned, warned = plan_module.planned_from_request(plan, Path(plan_path))
    try:
        result = plan_module.build(plan, planned, cache)
    except Exception:
        if cache is not None:
            cache.save(prune=False)  # what was built before the failure is still good
        raise
    result.warnings.extend(warned)
    if cache is not None:
        cache.save()
    return Rebuilt(result, warned, cache.stats if cache is not None else CacheStats(), time.perf_counter() - started)


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------


def register(sub) -> None:
    edit = sub.add_parser(
        "edit",
        help="change a plan with short operations (move, set, rows, remove) and rebuild only what they touched",
        description=(
            "Operations, each a few words:  move <block> <dx> <dy>   |   set <block> <field>=<value>   |   "
            "rows <block> +1|-1|per=<n>   |   remove <block>.  A block is its index or its id. "
            "With no operation the plan is only rebuilt (and the cache filled)."
        ),
    )
    edit.add_argument("plan", help="a plan JSON file")
    edit.add_argument("operations", nargs=argparse.REMAINDER,
                      help="operations, e.g.  move 2 0 5  set red recipe=advanced-circuit; the options below may "
                           "come before or after them")
    edit.add_argument("-o", "--out-plan", help="write the edited plan here instead of over the original")
    edit.add_argument("--output", help="directory for the blueprint and report (default: beside the plan)")
    edit.add_argument("--no-cache", action="store_true", help="build everything again and keep no cache")
    edit.add_argument("--render", action="store_true", help="also write the HTML drawing")
    edit.set_defaults(func=_cmd_edit)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _take_options(words: list[str], args) -> list[str]:
    """The operation words, with any options typed among them applied to `args`."""
    rest: list[str] = []
    i = 0
    while i < len(words):
        word = words[i]
        if word in ("--no-cache", "--render"):
            setattr(args, word[2:].replace("-", "_"), True)
        elif word in ("-o", "--out-plan", "--output"):
            if i + 1 >= len(words):
                raise EditError(f"{word} needs a path")
            setattr(args, "out_plan" if word != "--output" else "output", words[i + 1])
            i += 1
        else:
            rest.append(word)
        i += 1
    return rest


def _cmd_edit(args) -> int:
    from . import render

    plan_path = Path(args.plan)
    try:
        data = plan_module.load(plan_path)
    except FileNotFoundError:
        print(f"no plan at {plan_path}", file=sys.stderr)
        return 2
    except plan_module.PlanError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        words = _take_options(list(args.operations), args)
        ops = split_operations(words) if words else []
        said = apply_operations(data, ops)
    except (EditError, plan_module.PlanError) as exc:
        print(f"edit: {exc}", file=sys.stderr)
        return 2
    written = Path(args.out_plan) if args.out_plan else plan_path
    if ops:
        write_plan(data, written)
    for line in said:
        print(line)
    if ops:
        print(f"plan:      {written.resolve()}")

    cache = None if args.no_cache else BuildCache(cache_folder(written))
    try:
        rebuilt = rebuild(data, written, cache)
    except plan_module.PlanError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    result = rebuilt.result

    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", result.label).strip("-") or "layout"
    out = Path(args.output) if args.output else written.resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    string_path = out / f"{slug}.txt"
    string_path.write_text(result.blueprint.to_string(), encoding="utf-8")
    report_path = out / f"{slug}.report.json"
    report_path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    html_path = None
    if args.render:
        html_path = render.write_html(result.blueprint, out / f"{slug}.html", title=result.label,
                                      findings=result.findings)

    s = rebuilt.stats
    print(f"{result.label}: {_plural(len(result.blueprint.entities), 'entity', 'entities')} in {rebuilt.seconds:.2f} s"
          + ("" if cache is None else
             f"; blocks {s.blocks_reused} kept / {s.blocks_built} built, routes {s.routes_reused} kept / "
             f"{s.routes_built} built"))
    for r in result.routes:
        if not r.ok:
            print(f"  route {r.id}: FAILED - {r.reason}")
    shown = [f for f in result.findings if f.severity.value != "note"]
    if shown:
        print(f"{_plural(len(shown), 'finding', 'findings')}:")
        for finding in shown:
            where = f" at {finding.position}" if finding.position else ""
            print(f"  [{finding.severity.value}] {finding.summary}{where}")
    else:
        print("checks: nothing to report")
    print(f"blueprint: {string_path.resolve()}")
    print(f"report:    {report_path.resolve()}")
    if html_path:
        print(f"drawing:   {html_path.resolve()}")
    return 0
