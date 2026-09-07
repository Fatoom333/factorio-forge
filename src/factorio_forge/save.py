"""Read what a Factorio save was made with, without launching the game.

A save is a zip whose real contents (``level.dat*``) are Factorio's internal
serialisation: undocumented, version-specific and not worth parsing. The header
in ``level-init.dat`` is a different matter. It begins with the game version and
the exact list of mods and their versions that the save requires, in a simple
length-prefixed layout that has been stable for a long time.

That header is all we need to tell one of the player's saves from another and to
assemble the right mod set for it, so this module reads it and nothing else. For
anything deeper — what is actually built on the map, what is researched — the
companion mod exports it from inside the game, where the game itself is the
parser.

The parse is deliberately self-validating rather than offset-based. A few fields
sit between the version and the mod count whose meaning we have not pinned down,
and their size could plausibly change between game versions. So instead of
trusting a fixed offset, we try every plausible starting point and accept the
first one where the whole mod table parses cleanly and every name looks like a
mod name. A layout change then costs us nothing.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

# Mod names are restricted by the portal to this alphabet; used to tell a real
# parse from a coincidental one.
_MOD_NAME = re.compile(r"^[A-Za-z0-9_\-. +'!]{1,120}$")

# The mod table starts somewhere shortly after two short strings; searching this
# far covers every layout seen so far with room to spare.
_SEARCH_LIMIT = 256

_MIN_MODS = 1
_MAX_MODS = 500

# Two different thresholds, and the distinction matters for how we report a
# save we cannot use.
#
# Below MIN_PARSEABLE the header is laid out differently and we genuinely
# cannot read it. Players accumulate saves over many years — a decade-old save
# from 0.12 is not a bug to investigate, it is simply out of scope.
#
# MIN_SUPPORTED is a stricter, separate question: we can read the header of a
# 1.1 save perfectly well, but blueprint generation targets 2.0, so such a save
# is understood and still unusable for our purposes.
MIN_PARSEABLE = (0, 17, 0)
MIN_SUPPORTED = (2, 0, 0)


class SaveFormatError(Exception):
    """The save header could not be understood."""


class UnsupportedSaveVersion(SaveFormatError):
    """The save is readable in principle but its game version is out of scope."""

    def __init__(self, version: tuple[int, int, int], reason: str) -> None:
        super().__init__(reason)
        self.version = version


@dataclass(frozen=True)
class ModRef:
    """One mod a save depends on."""

    name: str
    version: tuple[int, int, int]

    @property
    def version_string(self) -> str:
        return "{}.{}.{}".format(*self.version)

    def __str__(self) -> str:
        return f"{self.name} {self.version_string}"


@dataclass(frozen=True)
class SaveInfo:
    """What we can learn about a save from its header alone."""

    path: Path
    name: str
    game_version: tuple[int, int, int]
    mods: tuple[ModRef, ...] = field(default_factory=tuple)

    @property
    def game_version_string(self) -> str:
        return "{}.{}.{}".format(*self.game_version)

    @property
    def mod_names(self) -> frozenset[str]:
        return frozenset(mod.name for mod in self.mods)

    @property
    def is_supported(self) -> bool:
        """Whether blueprints can be generated for this save at all."""
        return self.game_version >= MIN_SUPPORTED

    @property
    def is_vanilla(self) -> bool:
        """True when only Wube's own mods are present."""
        return self.mod_names <= {"base", "space-age", "quality", "elevated-rails"}

    @property
    def has_space_age(self) -> bool:
        return "space-age" in self.mod_names

    def fingerprint(self) -> str:
        """Stable identity of this mod set, for matching saves to a profile.

        Two saves with the same mods at the same versions share a fingerprint
        and can therefore share extracted game data.
        """
        import hashlib

        joined = ";".join(sorted(f"{m.name}@{m.version_string}" for m in self.mods))
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# primitive readers for Factorio's serialisation
# --------------------------------------------------------------------------


class _Reader:
    """Cursor over the header bytes."""

    def __init__(self, data: bytes, offset: int = 0) -> None:
        self.data = data
        self.pos = offset

    def u8(self) -> int:
        if self.pos >= len(self.data):
            raise SaveFormatError("ran past the end of the header")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def u16(self) -> int:
        if self.pos + 2 > len(self.data):
            raise SaveFormatError("ran past the end of the header")
        value = int.from_bytes(self.data[self.pos : self.pos + 2], "little")
        self.pos += 2
        return value

    def u32(self) -> int:
        if self.pos + 4 > len(self.data):
            raise SaveFormatError("ran past the end of the header")
        value = int.from_bytes(self.data[self.pos : self.pos + 4], "little")
        self.pos += 4
        return value

    def optimised_u16(self) -> int:
        """A byte, or 0xFF followed by a full 16-bit value.

        Factorio stores most small numbers this way; version components are
        almost always a single byte.
        """
        first = self.u8()
        return self.u16() if first == 0xFF else first

    def optimised_u32(self) -> int:
        first = self.u8()
        return self.u32() if first == 0xFF else first

    def string(self) -> str:
        length = self.optimised_u32()
        if self.pos + length > len(self.data):
            raise SaveFormatError("string runs past the end of the header")
        raw = self.data[self.pos : self.pos + length]
        self.pos += length
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SaveFormatError("string is not valid UTF-8") from exc


def _read_mod(reader: _Reader) -> ModRef:
    name = reader.string()
    if not _MOD_NAME.match(name):
        raise SaveFormatError(f"implausible mod name: {name!r}")
    version = (
        reader.optimised_u16(),
        reader.optimised_u16(),
        reader.optimised_u16(),
    )
    reader.u32()  # per-mod checksum, not useful to us
    return ModRef(name=name, version=version)


def _try_parse_mod_table(data: bytes, offset: int) -> tuple[ModRef, ...] | None:
    """Attempt to read a mod count and that many mod entries at `offset`."""
    reader = _Reader(data, offset)
    try:
        count = reader.optimised_u32()
        if not _MIN_MODS <= count <= _MAX_MODS:
            return None
        mods = tuple(_read_mod(reader) for _ in range(count))
    except SaveFormatError:
        return None

    # A run of plausible names is convincing on its own, but every real save
    # depends on "base"; requiring it rules out coincidental parses.
    if not any(mod.name == "base" for mod in mods):
        return None
    return mods


# --------------------------------------------------------------------------
# public interface
# --------------------------------------------------------------------------


def read_save_info(save_path: Path) -> SaveInfo:
    """Read the game version and mod set a save requires."""
    save_path = Path(save_path)
    try:
        with zipfile.ZipFile(save_path) as archive:
            entry = next(
                (n for n in archive.namelist() if n.endswith("level-init.dat")), None
            )
            if entry is None:
                raise SaveFormatError("no level-init.dat in the archive")
            # The header lives at the very start; a few KB is far more than enough.
            with archive.open(entry) as handle:
                head = handle.read(64 * 1024)
    except (OSError, zipfile.BadZipFile) as exc:
        raise SaveFormatError(f"cannot read {save_path.name}: {exc}") from exc

    if len(head) < 16:
        raise SaveFormatError("header is too short to be a save")

    # The version sits at the very start and has been in the same place for as
    # long as anyone cares about, so it is readable even when nothing else is.
    version = (
        int.from_bytes(head[0:2], "little"),
        int.from_bytes(head[2:4], "little"),
        int.from_bytes(head[4:6], "little"),
    )
    if version < MIN_PARSEABLE:
        raise UnsupportedSaveVersion(
            version,
            "game version {}.{}.{} predates the current header layout".format(*version),
        )

    for offset in range(8, min(_SEARCH_LIMIT, len(head))):
        mods = _try_parse_mod_table(head, offset)
        if mods is not None:
            return SaveInfo(
                path=save_path,
                name=save_path.stem,
                game_version=version,
                mods=mods,
            )

    raise SaveFormatError(
        f"could not locate the mod table in {save_path.name}; "
        "the header layout may have changed"
    )


def list_saves(include_autosaves: bool = False) -> list[Path]:
    """Every save file in the player's save directory, newest first."""
    directory = paths.saves_dir()
    if directory is None or not directory.is_dir():
        return []
    saves = [p for p in directory.glob("*.zip") if p.is_file()]
    if not include_autosaves:
        saves = [p for p in saves if not p.stem.startswith("_autosave")]
    return sorted(saves, key=lambda p: p.stat().st_mtime, reverse=True)


@dataclass(frozen=True)
class SaveProblem:
    """A save we could not turn into a SaveInfo, and why.

    Kept as a value rather than an exception so a survey of a save folder can
    report everything it found in one pass. `too_old` separates "this predates
    what we can read", which is expected and uninteresting, from a real failure
    worth looking into.
    """

    path: Path
    name: str
    reason: str
    too_old: bool = False
    game_version: tuple[int, int, int] | None = None

    @property
    def game_version_string(self) -> str:
        return "{}.{}.{}".format(*self.game_version) if self.game_version else "unknown"


def survey_saves(include_autosaves: bool = False) -> list[SaveInfo | SaveProblem]:
    """Read every save in the save folder, reporting rather than raising."""
    results: list[SaveInfo | SaveProblem] = []
    for path in list_saves(include_autosaves):
        try:
            results.append(read_save_info(path))
        except UnsupportedSaveVersion as exc:
            results.append(
                SaveProblem(
                    path=path,
                    name=path.stem,
                    reason=str(exc),
                    too_old=True,
                    game_version=exc.version,
                )
            )
        except SaveFormatError as exc:
            results.append(SaveProblem(path=path, name=path.stem, reason=str(exc)))
    return results
