"""Filesystem path resolution for factorio-forge.

This is the only module in the project that is allowed to know about
machine-specific locations. Every other module asks it instead of hardcoding a
path. That boundary is what lets this repository be cloned onto someone else's
computer and work without a single edit.

Three things need locating, and each follows the same precedence rule: an
explicit environment variable wins, then a value remembered in the local config
file, then automatic detection, and only then a platform default.

    forge home      where profiles, cache and local config live
    game install    where the Factorio binary and its bundled data live
    game user dir   where saves, mods and script-output live

Nothing resolved here is ever written into the repository. The local config file
lives under the forge home, which by default sits in the platform's user data
directory, outside any checkout.

Run ``python -m factorio_forge.paths`` for a report of what resolves to what on
the current machine, and where each answer came from.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

APP_DIRNAME = "factorio-forge"

ENV_HOME = "FACTORIO_FORGE_HOME"
ENV_GAME = "FACTORIO_PATH"
ENV_USER = "FACTORIO_USER_DIR"

CONFIG_FILENAME = "config.json"
LOG_FILENAME = "factorio-current.log"


# --------------------------------------------------------------------------
# forge home: our own data, deliberately outside the repository
# --------------------------------------------------------------------------


def _platform_data_root() -> Path:
    """The conventional per-user data directory for the current platform."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    xdg = os.environ.get("XDG_DATA_HOME")
    return Path(xdg) if xdg else Path.home() / ".local" / "share"


def _resolve_forge_home() -> tuple[Path, str]:
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser(), f"set by {ENV_HOME}"
    return _platform_data_root() / APP_DIRNAME, "default location"


def forge_home() -> Path:
    """Root of all data this tool owns: profiles, cache and local config."""
    return _resolve_forge_home()[0]


def config_path() -> Path:
    return forge_home() / CONFIG_FILENAME


def profiles_root() -> Path:
    return forge_home() / "profiles"


def profile_dir(name: str) -> Path:
    return profiles_root() / name


def cache_root() -> Path:
    return forge_home() / "cache"


def ensure_layout() -> Path:
    """Create the forge home directory tree if it is not there yet."""
    home = forge_home()
    for path in (home, profiles_root(), cache_root()):
        path.mkdir(parents=True, exist_ok=True)
    return home


# --------------------------------------------------------------------------
# local config: remembers what detection worked, so it runs once
# --------------------------------------------------------------------------


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(config: dict[str, Any]) -> Path:
    ensure_layout()
    path = config_path()
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return path


def _config_path_value(config: dict[str, Any], key: str) -> Path | None:
    value = config.get(key)
    if isinstance(value, str) and value:
        candidate = Path(value).expanduser()
        if candidate.exists():
            return candidate
    return None


# --------------------------------------------------------------------------
# the game's user directory: saves, mods, script-output
# --------------------------------------------------------------------------


def _default_user_dirs() -> Iterator[tuple[Path, str]]:
    """Where the game keeps its user data unless told otherwise, with a label."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            yield Path(appdata) / "Factorio", "default location, %APPDATA%\\Factorio"
    elif sys.platform == "darwin":
        yield (
            Path.home() / "Library" / "Application Support" / "factorio",
            "default location, ~/Library/Application Support/factorio",
        )
    else:
        yield Path.home() / ".factorio", "default location, ~/.factorio"
    # A standalone (non-Steam) install keeps its user data next to the binary.
    install = _config_path_value(load_config(), "factorio_install_dir")
    if install:
        yield install, f"next to the standalone install remembered in {CONFIG_FILENAME}"


def _resolve_user_dir(config: dict[str, Any] | None = None) -> tuple[Path | None, str | None]:
    override = os.environ.get(ENV_USER)
    if override:
        candidate = Path(override).expanduser()
        return (candidate, f"set by {ENV_USER}") if candidate.exists() else (None, None)

    config = load_config() if config is None else config
    remembered = _config_path_value(config, "factorio_user_dir")
    if remembered:
        return remembered, f"remembered in {CONFIG_FILENAME} by `init`"

    for candidate, label in _default_user_dirs():
        if (candidate / "saves").is_dir() or (candidate / "config").is_dir():
            return candidate, label
    return None, None


def factorio_user_dir(config: dict[str, Any] | None = None) -> Path | None:
    """Where Factorio writes saves, mods, script-output and its log."""
    return _resolve_user_dir(config)[0]


def saves_dir() -> Path | None:
    user_dir = factorio_user_dir()
    return user_dir / "saves" if user_dir else None


def mods_dir() -> Path | None:
    user_dir = factorio_user_dir()
    return user_dir / "mods" if user_dir else None


def script_output_dir() -> Path | None:
    """Where the companion mod writes its exports."""
    user_dir = factorio_user_dir()
    return user_dir / "script-output" if user_dir else None


# --------------------------------------------------------------------------
# the game's installation: binary and bundled data
# --------------------------------------------------------------------------


_LOG_FACTS = {
    "version": re.compile(r"Factorio (\d+\.\d+\.\d+)"),
    "binaries": re.compile(r"Binaries path:\s*(.+?)\s*$"),
    # "Write data path: C:/Users/x/AppData/Roaming/Factorio [133969/957793MB]"
    "write_data": re.compile(r"Write data path:\s*(.+?)(?:\s*\[[^\]]*\])?\s*$"),
}


def read_log(log: Path) -> dict[str, str]:
    """The version and paths Factorio recorded at the top of one log file.

    Keys are those of `_LOG_FACTS` that were found; an unreadable log gives an
    empty dict.
    """
    facts: dict[str, str] = {}
    try:
        with log.open(encoding="utf-8", errors="replace") as handle:
            for _, line in zip(range(200), handle):
                for key, pattern in _LOG_FACTS.items():
                    if key not in facts:
                        match = pattern.search(line)
                        if match:
                            facts[key] = match.group(1)
                if len(facts) == len(_LOG_FACTS):
                    break
    except OSError:
        return {}
    return facts


def _log_file() -> Path | None:
    user_dir = factorio_user_dir()
    if not user_dir:
        return None
    log = user_dir / LOG_FILENAME
    return log if log.is_file() else None


def _log_label(log: Path) -> str:
    """Which log an answer came from, dated so that a stale one stands out."""
    try:
        written = datetime.fromtimestamp(log.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        return f"from {log.name}"
    return f"from {log.name} of {written}"


def _log_paths() -> tuple[Path | None, str | None]:
    """Read the install directory and version out of Factorio's own log.

    The log records the paths the game actually used on its last run, which
    beats guessing: it is correct for Steam libraries on any drive, for
    standalone installs and for platforms we have no probe list for.
    """
    log = _log_file()
    if log is None:
        return None, None
    facts = read_log(log)
    install: Path | None = None
    if "binaries" in facts:
        # ".../Factorio/bin" -> ".../Factorio"
        install = Path(facts["binaries"]).parent
        if not install.is_dir():
            install = None
    return install, facts.get("version")


def _steam_library_roots() -> Iterator[Path]:
    """Steam libraries can live on any drive; read them from Steam itself."""
    steam_roots: list[Path] = []
    if sys.platform == "win32":
        try:
            import winreg

            for hive, key in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            ):
                try:
                    with winreg.OpenKey(hive, key) as handle:
                        value, _ = winreg.QueryValueEx(
                            handle, "SteamPath" if hive == winreg.HKEY_CURRENT_USER else "InstallPath"
                        )
                    steam_roots.append(Path(value))
                except OSError:
                    continue
        except ImportError:
            pass
    elif sys.platform == "darwin":
        steam_roots.append(Path.home() / "Library" / "Application Support" / "Steam")
    else:
        steam_roots.append(Path.home() / ".steam" / "steam")
        steam_roots.append(Path.home() / ".local" / "share" / "Steam")

    for root in steam_roots:
        yield root
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if not vdf.is_file():
            continue
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(r'"path"\s*"([^"]+)"', text):
            yield Path(match.group(1).replace("\\\\", "\\"))


def _probe_install_dirs() -> Iterator[Path]:
    for library in _steam_library_roots():
        yield library / "steamapps" / "common" / "Factorio"
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                yield Path(base) / "Factorio"
    elif sys.platform == "darwin":
        yield Path("/Applications/factorio.app")
    else:
        yield Path.home() / ".factorio"
        yield Path("/opt/factorio")


def _looks_like_install(path: Path) -> bool:
    return (path / "data" / "base" / "info.json").is_file()


def _resolve_install_dir(config: dict[str, Any] | None = None) -> tuple[Path | None, str | None]:
    override = os.environ.get(ENV_GAME)
    if override:
        candidate = Path(override).expanduser()
        return (candidate, f"set by {ENV_GAME}") if candidate.is_dir() else (None, None)

    config = load_config() if config is None else config
    remembered = _config_path_value(config, "factorio_install_dir")
    if remembered:
        return remembered, f"remembered in {CONFIG_FILENAME} by `init`"

    from_log, _ = _log_paths()
    if from_log and _looks_like_install(from_log):
        log = _log_file()
        return from_log, f"Binaries path {_log_label(log)}" if log else "from the game's log"

    for candidate in _probe_install_dirs():
        if _looks_like_install(candidate):
            return candidate, "found in a Steam library or a standard install folder"
    return None, None


def factorio_install_dir(config: dict[str, Any] | None = None) -> Path | None:
    """Root of the Factorio installation (the directory holding bin/ and data/)."""
    return _resolve_install_dir(config)[0]


def factorio_binary() -> Path | None:
    install = factorio_install_dir()
    if not install:
        return None
    candidates = [
        install / "bin" / "x64" / "factorio.exe",
        install / "bin" / "x64" / "factorio",
        install / "Contents" / "MacOS" / "factorio",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def factorio_data_dir() -> Path | None:
    install = factorio_install_dir()
    if not install:
        return None
    data = install / "data"
    return data if data.is_dir() else None


def factorio_version() -> str | None:
    """Version of the installed game, as last recorded in its log."""
    _, version = _log_paths()
    return version


# --------------------------------------------------------------------------
# diagnostics
# --------------------------------------------------------------------------


def resolve_all() -> dict[str, str | None]:
    """Everything this module can resolve, for setup and troubleshooting."""

    def show(value: Path | None) -> str | None:
        return str(value) if value else None

    return {
        "forge_home": show(forge_home()),
        "config": show(config_path()),
        "profiles": show(profiles_root()),
        "cache": show(cache_root()),
        "factorio_install_dir": show(factorio_install_dir()),
        "factorio_binary": show(factorio_binary()),
        "factorio_data_dir": show(factorio_data_dir()),
        "factorio_user_dir": show(factorio_user_dir()),
        "saves_dir": show(saves_dir()),
        "mods_dir": show(mods_dir()),
        "script_output_dir": show(script_output_dir()),
        "factorio_version": factorio_version(),
    }


def resolve_sources() -> dict[str, str]:
    """Where each independently found entry of `resolve_all()` came from.

    Only the roots are listed: everything else is derived from one of them
    (saves_dir from factorio_user_dir, the binary from the install, ...).
    """
    sources: dict[str, str] = {"forge_home": _resolve_forge_home()[1]}
    config = load_config()
    _, user_source = _resolve_user_dir(config)
    if user_source:
        sources["factorio_user_dir"] = user_source
    _, install_source = _resolve_install_dir(config)
    if install_source:
        sources["factorio_install_dir"] = install_source
    log = _log_file()
    if log and read_log(log).get("version"):
        sources["factorio_version"] = _log_label(log)
    return sources


def _same_place(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def write_data_elsewhere() -> Path | None:
    """The folder the game says it last wrote to, when that is not the one chosen.

    Factorio records its user data folder ("Write data path") in the log it
    keeps there. A log that names a different folder means the chosen one
    holds a copy -- an old install, a moved profile -- and the game now plays
    from somewhere else.
    """
    user_dir = factorio_user_dir()
    log = _log_file()
    if user_dir is None or log is None:
        return None
    written = read_log(log).get("write_data")
    if not written:
        return None
    written_path = Path(written)
    return None if _same_place(written_path, user_dir) else written_path


def main() -> int:
    resolved = resolve_all()
    sources = resolve_sources()
    width = max(len(key) for key in resolved)
    missing = 0
    for key, value in resolved.items():
        if value is None:
            missing += 1
            value = "NOT FOUND"
        source = f"  ({sources[key]})" if key in sources else ""
        print(f"{key.ljust(width)}  {value}{source}")
    elsewhere = write_data_elsewhere()
    if elsewhere is not None:
        print(
            f"\nThe game's log in factorio_user_dir says it last wrote its data to "
            f"{elsewhere}. If that is where you play, set {ENV_USER} to it.",
            file=sys.stderr,
        )
    if missing:
        print(
            f"\n{missing} item(s) not found. Set {ENV_GAME} or {ENV_USER}, "
            "or record them in the config file above.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
