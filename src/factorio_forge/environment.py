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
from pathlib import Path
from typing import Any, Iterable

from . import paths


class EnvironmentError(Exception):
    """The environment export exists but could not be understood."""


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

    def matches_mods(self, mods: Iterable[Any]) -> bool:
        """Whether this snapshot was taken with exactly the given mod set loaded.

        `mods` is any iterable of objects with `.name` and `.version_string`,
        such as `save.ModRef` -- the same shape `Profile.mods` holds.
        """
        wanted = {f"{mod.name}@{mod.version_string}" for mod in mods}
        seen = {f"{name}@{version}" for name, version in self.mods.items()}
        return bool(wanted) and wanted == seen


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
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EnvironmentError(f"{path.name} is not in the expected shape: {exc}") from exc
