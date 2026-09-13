"""Read what only a running game knows, as the companion mod exported it.

A save's header lists the mods it needs, but not their startup settings, and
not what has been researched -- those are state, not definition, and nothing
outside the game can compute them. The companion mod runs inside the game and
writes the answers to ``script-output/factorio-forge/environment.json``; this
module reads that file back.

The export is a snapshot of one play session, not tied to any particular
profile. `matches_mods()` is how a caller checks whether a snapshot was taken
with the same mod set a profile was built from, before trusting anything in it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Iterable

from . import paths

COMPANION = "factorio-forge-companion"


class EnvironmentError(Exception):
    """The environment export exists but could not be understood."""


@dataclass(frozen=True)
class Bonuses:
    """What research and everything else have added up to, as the force holds it.

    Read from the force rather than summed from technology effects, because
    not all of it comes from the research tree: in 2.0 the bulk inserter
    technology itself raises bulk inserter capacity, and mods and scripts set
    bonuses directly. `force` is keyed by Lua API attribute name
    (`inserter_stack_size_bonus`, `bulk_inserter_capacity_bonus`,
    `mining_drill_productivity_bonus`, ...); the rest only list what is
    non-zero.
    """

    force: dict[str, float] = field(default_factory=dict)
    recipe_productivity: dict[str, float] = field(default_factory=dict)
    ammo_damage: dict[str, float] = field(default_factory=dict)
    gun_speed: dict[str, float] = field(default_factory=dict)
    turret_attack: dict[str, float] = field(default_factory=dict)

    def get(self, name: str, default: float = 0.0) -> float:
        return float(self.force.get(name, default))


@dataclass(frozen=True)
class Environment:
    """One export from `/forge-export`, or the window's export button."""

    game_version: str
    tick: int
    force: str
    mods: dict[str, str] = field(default_factory=dict)
    startup_settings: dict[str, Any] = field(default_factory=dict)
    researched: tuple[str, ...] = field(default_factory=tuple)
    available_to_research: tuple[str, ...] = field(default_factory=tuple)
    recipes_enabled: tuple[str, ...] = field(default_factory=tuple)
    # None when the export predates companion 0.7.0, which is different from
    # an export that had nothing to report.
    bonuses: Bonuses | None = None

    @cached_property
    def makeable(self) -> frozenset[str]:
        """Items and fluids some enabled recipe produces."""
        from draftsman.data import recipes

        made = set()
        for name in self.recipes_enabled:
            for result in (recipes.raw.get(name) or {}).get("results", []) or []:
                if result.get("name"):
                    made.add(result["name"])
        return frozenset(made)

    def can_build(self, entity: str) -> bool:
        """Whether an item that places this entity can be made with enabled recipes."""
        from draftsman.data import items

        return any(
            isinstance(item, dict) and item.get("place_result") == entity and name in self.makeable
            for name, item in items.raw.items()
        )

    def matches_mods(self, mods: Iterable[Any]) -> bool:
        """Whether this snapshot was taken with exactly the given mod set loaded.

        `mods` is any iterable of objects with `.name` and `.version_string`,
        such as `save.ModRef` -- the same shape `Profile.mods` holds.

        The companion mod itself is left out of the comparison on both sides:
        nothing can be exported without it, so a profile made from a save
        before it was added would otherwise never match its own export, and it
        adds no recipe or entity a blueprint could use.
        """
        wanted = {f"{mod.name}@{mod.version_string}" for mod in mods if mod.name != COMPANION}
        seen = {f"{name}@{version}" for name, version in self.mods.items() if name != COMPANION}
        return bool(wanted) and wanted == seen


def _mapping(value: Any) -> dict:
    """A JSON object, where the game's encoder may have written an empty one as []."""
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if value in (None, []):
        return {}
    raise TypeError(f"expected an object, got {type(value).__name__}")


def _bonuses(data: Any) -> Bonuses | None:
    if data is None:
        return None
    data = _mapping(data)
    return Bonuses(
        force={k: float(v) for k, v in _mapping(data.get("force")).items()},
        recipe_productivity={k: float(v) for k, v in _mapping(data.get("recipe_productivity")).items()},
        ammo_damage={k: float(v) for k, v in _mapping(data.get("ammo_damage")).items()},
        gun_speed={k: float(v) for k, v in _mapping(data.get("gun_speed")).items()},
        turret_attack={k: float(v) for k, v in _mapping(data.get("turret_attack")).items()},
    )


def _export_path() -> Path | None:
    output = paths.script_output_dir()
    return output / "factorio-forge" / "environment.json" if output else None


def read_environment() -> Environment | None:
    """Read the companion mod's last export, or None if it has never run."""
    path = _export_path()
    if path is None or not path.is_file():
        return None

    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvironmentError(f"cannot read {path.name}: {exc}") from exc

    try:
        return Environment(
            game_version=str(data["game_version"]),
            tick=int(data["tick"]),
            force=str(data["force"]),
            mods=dict(data.get("mods", {})),
            startup_settings=dict(data.get("startup_settings", {})),
            researched=tuple(data.get("researched", [])),
            available_to_research=tuple(data.get("available_to_research", [])),
            recipes_enabled=tuple(data.get("recipes_enabled", [])),
            bonuses=_bonuses(data.get("bonuses")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EnvironmentError(f"{path.name} is not in the expected shape: {exc}") from exc


def for_active_profile() -> tuple[Environment | None, str]:
    """The last export, if it was taken with the active profile's exact mod set.

    Returns the environment or None, and a sentence saying which and why --
    an export from a different save is common (the player exported somewhere
    else last) and using it would be silently wrong.
    """
    try:
        from .profile import Profile

        name = Profile.active_profile_name()
        if not name:
            return None, "no profile is active"
        profile = Profile.load(name)
    except Exception as exc:  # noqa: BLE001 - the answer is "not available", with the reason
        return None, f"the active profile could not be read ({exc})"
    try:
        environment = read_environment()
    except EnvironmentError as exc:
        return None, str(exc)
    if environment is None:
        return None, "the companion mod has not exported this game (run /forge-export)"
    if not environment.matches_mods(profile.mods):
        return None, (
            f"the last /forge-export was taken with a different mod set than profile {name!r}; "
            "export again from that save"
        )
    return environment, f"from /forge-export at tick {environment.tick}, matching profile {name!r}"
