"""Profiles: one per base, holding everything that makes its blueprints fit.

A player has several saves and each is its own world: a different mod set, and
so different recipes, different entities, different sizes. A blueprint that is
correct for one is nonsense in another. A profile is the unit that keeps them
apart.

Each profile owns:

    profile.json    which save it came from, game version, exact mod set
    mods/           an isolated mod folder for this set alone
    data/           game data extracted for this set, and no other
    blueprints/     reference blueprints, the source of the base's style
    notes.md        accumulated preferences, in the player's own words

The isolation is not decorative. Draftsman extracts prototype data into one
fixed directory inside its own installation, and there is no setting to move
it, so a second mod set would silently overwrite the first. This module works
around that: each profile extracts into its own mod folder, keeps the resulting
data files, and swaps them into place when it is activated.

Mod archives are hard-linked rather than copied. A player's mod folder here ran
to 4.3 GB across 283 archives; duplicating a subset of that per profile would be
absurd, while a hard link costs nothing and cannot go stale, since a mod archive
of a given version is immutable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from draftsman.environment.mod_settings import ModSettings, write_mod_settings

from . import paths
from .environment import EnvironmentError, read_environment
from .save import ModRef, SaveInfo

# Wube's own modules live in the game's data folder, not the user mod folder,
# so they are never linked -- only enabled or disabled in mod-list.json.
OFFICIAL_MODS = frozenset({"base", "core", "space-age", "quality", "elevated-rails"})

PROFILE_FILE = "profile.json"

# Dropped into Draftsman's data directory to record whose data is loaded there.
ACTIVE_MARKER = ".factorio-forge-profile"


class ProfileError(Exception):
    """Something went wrong setting up or using a profile."""


# Run in a child interpreter by extract_data(). Arguments arrive positionally so
# that paths containing quotes, spaces or non-Latin characters need no escaping.
_EXTRACT_PROGRAM = """
import sys
from draftsman.environment.update import update_draftsman_data

game_path, mods_path, owned_csv, verbose = sys.argv[1:5]
update_draftsman_data(
    game_path=game_path,
    mods_path=mods_path,
    owned_dlc=[d for d in owned_csv.split(",") if d],
    no_mods=False,
    verbose=verbose == "1",
    show_logs=False,
)
"""


def slugify(name: str) -> str:
    """Turn a save name into a directory name that is safe everywhere.

    Save names here include Cyrillic, spaces and punctuation, and the result has
    to work as a folder name on three platforms, so anything outside a
    conservative set becomes a hyphen.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name)
    # A hyphen is allowed through, so " - " would otherwise become "---".
    slug = re.sub(r"-{2,}", "-", slug).strip("-.")
    return slug.lower() or "profile"


@dataclass(frozen=True)
class Substitution:
    """A mod used at a version other than the one the save recorded."""

    name: str
    wanted: tuple[int, int, int]
    used: tuple[int, int, int]

    def __str__(self) -> str:
        wanted = "{}.{}.{}".format(*self.wanted)
        used = "{}.{}.{}".format(*self.used)
        return f"{self.name} {wanted} -> {used}"

    def to_dict(self) -> dict:
        return {"name": self.name, "wanted": list(self.wanted), "used": list(self.used)}


@dataclass
class ModLinkReport:
    """Outcome of assembling a profile's mod folder."""

    linked: list[str] = field(default_factory=list)
    copied: list[str] = field(default_factory=list)
    repacked: list[str] = field(default_factory=list)
    substituted: list[Substitution] = field(default_factory=list)
    missing: list[ModRef] = field(default_factory=list)
    outdated: list[Substitution] = field(default_factory=list)
    official: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.outdated

    def summary(self) -> str:
        parts = [f"{len(self.linked)} linked"]
        if self.copied:
            parts.append(f"{len(self.copied)} copied")
        if self.repacked:
            parts.append(f"{len(self.repacked)} repacked")
        if self.substituted:
            parts.append(f"{len(self.substituted)} newer")
        if self.official:
            parts.append(f"{len(self.official)} official")
        if self.outdated:
            parts.append(f"{len(self.outdated)} TOO OLD")
        if self.missing:
            parts.append(f"{len(self.missing)} MISSING")
        return ", ".join(parts)


# Directories some archiving tools add at the root of a zip. Factorio ignores
# them, but a loader that expects exactly one folder at the root does not.
JUNK_TOP_LEVEL = frozenset({"__MACOSX"})

# Mod archives are named `<name>_<version>.zip`, and mod names may themselves
# contain underscores (AAI_Language_Pack_0.1.0.zip), so the split is on the last
# underscore that is followed by something version-shaped.
_ARCHIVE_NAME = re.compile(r"^(?P<name>.+)_(?P<version>\d+\.\d+\.\d+)$")


def parse_archive_name(path: Path) -> tuple[str, tuple[int, int, int]] | None:
    """Split a mod archive filename into its mod name and version."""
    match = _ARCHIVE_NAME.match(path.stem)
    if not match:
        return None
    major, minor, patch = (int(p) for p in match.group("version").split("."))
    return match.group("name"), (major, minor, patch)


def available_versions(mods_dir: Path, mod_name: str) -> list[tuple[int, int, int]]:
    """Every version of one mod present in a mod folder, newest first."""
    found = []
    for archive in mods_dir.glob(f"{mod_name}_*.zip"):
        parsed = parse_archive_name(archive)
        if parsed and parsed[0] == mod_name:
            found.append(parsed[1])
    return sorted(found, reverse=True)


def _top_level_names(archive: zipfile.ZipFile) -> set[str]:
    return {name.split("/")[0] for name in archive.namelist() if name.strip("/")}


def needs_repacking(path: Path) -> bool:
    """Whether an archive carries junk that would confuse the mod loader.

    Mods zipped on macOS often ship a ``__MACOSX`` folder beside the real one.
    Draftsman tolerates that only when the real folder is named
    ``name_version``; a mod whose inner folder is just ``name`` then fails to
    load at all. Rather than lose the mod -- and with it every entity and recipe
    it contributes -- the archive is rewritten without the junk.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            tops = _top_level_names(archive)
    except (OSError, zipfile.BadZipFile):
        return False
    return bool(tops & JUNK_TOP_LEVEL) and len(tops - JUNK_TOP_LEVEL) == 1


def repack_without_junk(source: Path, target: Path) -> None:
    """Copy an archive, dropping the junk directories at its root.

    Only those directories are removed; everything the mod actually contains is
    preserved byte for byte, so the loaded mod is the same mod.
    """
    with zipfile.ZipFile(source) as original:
        entries = [
            item
            for item in original.infolist()
            if item.filename.split("/")[0] not in JUNK_TOP_LEVEL
        ]
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as cleaned:
            for item in entries:
                cleaned.writestr(item, original.read(item.filename))


@dataclass
class Profile:
    """One base: its save, its mod set, its data and its style."""

    name: str
    game_version: tuple[int, int, int]
    mods: tuple[ModRef, ...]
    fingerprint: str
    source_save: str | None = None
    created_at: str = ""
    updated_at: str = ""
    # What the last extraction produced. Recorded because extraction is not
    # reproducible -- see `data_fingerprint()` and docs/draftsman-notes.md.
    data_fingerprint: dict | None = None
    # Mods used at a version the save did not record, because the player has
    # since updated them. See docs/profiles.md.
    mod_substitutions: list[dict] = field(default_factory=list)
    # Where mod-settings.dat came from: "environment" when the companion mod's
    # export matched this profile's mod set exactly, "global-approximation"
    # when it did not (or the mod has never exported), None before this was
    # tracked. See docs/profiles.md.
    mod_settings_source: str | None = None

    # ------------------------------------------------------------------
    # locations
    # ------------------------------------------------------------------

    @property
    def directory(self) -> Path:
        return paths.profile_dir(self.name)

    @property
    def mods_dir(self) -> Path:
        return self.directory / "mods"

    @property
    def data_dir(self) -> Path:
        return self.directory / "data"

    @property
    def blueprints_dir(self) -> Path:
        return self.directory / "blueprints"

    @property
    def notes_path(self) -> Path:
        return self.directory / "notes.md"

    @property
    def game_version_string(self) -> str:
        return "{}.{}.{}".format(*self.game_version)

    @property
    def mod_names(self) -> frozenset[str]:
        return frozenset(mod.name for mod in self.mods)

    @property
    def has_space_age(self) -> bool:
        return "space-age" in self.mod_names

    @property
    def has_extracted_data(self) -> bool:
        return self.data_dir.is_dir() and any(self.data_dir.glob("*.pkl"))

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source_save": self.source_save,
            "game_version": list(self.game_version),
            "fingerprint": self.fingerprint,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "data_fingerprint": self.data_fingerprint,
            "mod_substitutions": self.mod_substitutions,
            "mod_settings_source": self.mod_settings_source,
            "mods": [
                {"name": m.name, "version": list(m.version)}
                for m in sorted(self.mods, key=lambda m: m.name.lower())
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        return cls(
            name=data["name"],
            game_version=tuple(data["game_version"]),  # type: ignore[arg-type]
            mods=tuple(
                ModRef(name=m["name"], version=tuple(m["version"]))  # type: ignore[arg-type]
                for m in data.get("mods", [])
            ),
            fingerprint=data.get("fingerprint", ""),
            source_save=data.get("source_save"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            data_fingerprint=data.get("data_fingerprint"),
            mod_substitutions=data.get("mod_substitutions", []),
            mod_settings_source=data.get("mod_settings_source"),
        )

    def write(self) -> Path:
        """Persist the profile, creating its directory tree if needed."""
        for directory in (
            self.directory,
            self.mods_dir,
            self.data_dir,
            self.blueprints_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        target = self.directory / PROFILE_FILE
        with target.open("w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        if not self.notes_path.exists():
            self.notes_path.write_text(
                f"# {self.name}\n\n"
                "Notes about how this base is built: conventions to follow, things to\n"
                "avoid, corrections made to generated blueprints. Written for humans;\n"
                "read back when generating.\n",
                encoding="utf-8",
            )
        return target

    @classmethod
    def load(cls, name: str) -> "Profile":
        path = paths.profile_dir(name) / PROFILE_FILE
        if not path.is_file():
            raise ProfileError(f"no profile named {name!r}")
        with path.open(encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    @staticmethod
    def list_all() -> list[str]:
        root = paths.profiles_root()
        if not root.is_dir():
            return []
        return sorted(
            entry.name
            for entry in root.iterdir()
            if (entry / PROFILE_FILE).is_file()
        )

    @classmethod
    def from_save(cls, info: SaveInfo, name: str | None = None) -> "Profile":
        """Build a profile from a save's header."""
        if not info.is_supported:
            raise ProfileError(
                f"{info.name} is Factorio {info.game_version_string}; "
                "blueprint generation targets 2.0 and later"
            )
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return cls(
            name=slugify(name or info.name),
            game_version=info.game_version,
            mods=info.mods,
            fingerprint=info.fingerprint(),
            source_save=info.name,
            created_at=now,
            updated_at=now,
        )

    # ------------------------------------------------------------------
    # mod folder
    # ------------------------------------------------------------------

    def prepare_mods(self, force: bool = False) -> ModLinkReport:
        """Assemble this profile's isolated mod folder.

        Every non-official mod at the exact version the save recorded is hard
        linked in from the player's mod folder, and a mod-list.json is written
        that enables exactly this set -- which is also how the official modules
        in the game's data folder are turned on or off, since Draftsman reads
        their enabled state from the same file.
        """
        source = paths.mods_dir()
        if source is None or not source.is_dir():
            raise ProfileError("the game's mod folder was not found")

        self.mods_dir.mkdir(parents=True, exist_ok=True)
        report = ModLinkReport()

        if force:
            for stale in self.mods_dir.glob("*.zip"):
                stale.unlink()

        for mod in sorted(self.mods, key=lambda m: m.name.lower()):
            if mod.name in OFFICIAL_MODS:
                report.official.append(mod.name)
                continue

            archive = source / f"{mod.name}_{mod.version_string}.zip"
            if not archive.is_file():
                # Mirror what the game does when the installed mods have moved
                # on since the save was played.
                versions = available_versions(source, mod.name)
                if not versions:
                    report.missing.append(mod)
                    continue
                newest = versions[0]
                if newest < mod.version:
                    # Only older copies are installed. Factorio refuses a save
                    # whose mods are newer than what is present, because data
                    # migrations only run forwards, and so do we.
                    report.outdated.append(
                        Substitution(mod.name, mod.version, newest)
                    )
                    continue
                # A newer copy is what the game would load, applying migrations.
                # Recorded rather than silent: it can change recipes.
                report.substituted.append(Substitution(mod.name, mod.version, newest))
                archive = source / "{}_{}.{}.{}.zip".format(mod.name, *newest)

            target = self.mods_dir / archive.name
            if target.exists():
                report.linked.append(mod.name)
                continue

            if needs_repacking(archive):
                repack_without_junk(archive, target)
                report.repacked.append(mod.name)
                continue

            try:
                os.link(archive, target)
                report.linked.append(mod.name)
            except OSError:
                # Different volume, or a filesystem without hard links.
                shutil.copy2(archive, target)
                report.copied.append(mod.name)

        self._write_mod_list()
        self.mod_settings_source = self._copy_mod_settings(source)
        # Keep substitutions with the profile: data extracted against a mod the
        # save never saw should be traceable long after this call.
        self.mod_substitutions = [s.to_dict() for s in report.substituted]
        self.write()
        return report

    def _write_mod_list(self) -> None:
        """Enable exactly this profile's mods, and disable everything else.

        Official modules must be listed explicitly: they are discovered from the
        game's data folder regardless, and anything not named here defaults to
        enabled, so Space Age would leak into a profile that does not use it.
        """
        names = self.mod_names | OFFICIAL_MODS
        entries = [
            {"name": name, "enabled": name in self.mod_names or name in ("base", "core")}
            for name in sorted(names)
        ]
        payload = {"mods": entries}
        with (self.mods_dir / "mod-list.json").open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")

    def _copy_mod_settings(self, source: Path) -> str:
        """Assemble mod-settings.dat for this profile's mod folder.

        Startup mod settings feed into the data lifecycle and therefore change
        recipes. A save carries its own settings in its header, but that
        section is not parsed yet. The companion mod's `environment.json`
        export carries the exact startup settings a play session had loaded,
        so when the mod set it recorded matches this profile's exactly, that
        export is trusted and written out as a fresh mod-settings.dat. Failing
        that, the player's current global settings are copied as an
        approximation, which can be subtly wrong if they have since changed --
        worth knowing, so the source used is returned rather than left silent.
        """
        try:
            environment = read_environment()
        except EnvironmentError:
            environment = None

        if environment is not None and environment.matches_mods(self.mods):
            settings: ModSettings = {
                "startup": {
                    name: {"value": value}
                    for name, value in environment.startup_settings.items()
                },
                "runtime-global": {},
                "runtime-per-user": {},
            }
            write_mod_settings(
                str(self.mods_dir), settings, factorio_version=self.game_version
            )
            return "environment"

        settings_path = source / "mod-settings.dat"
        if settings_path.is_file():
            shutil.copy2(settings_path, self.mods_dir / "mod-settings.dat")
        return "global-approximation"

    # ------------------------------------------------------------------
    # game data
    # ------------------------------------------------------------------

    @staticmethod
    def _draftsman_data_dir() -> Path:
        import draftsman.data

        return Path(draftsman.data.__path__[0])

    def extract_data(self, verbose: bool = False) -> subprocess.CompletedProcess:
        """Run Factorio's data lifecycle over this profile's mods.

        This is the step that makes modded profiles trustworthy: the mod set is
        resolved by executing the same Lua the game executes, so cross-mod
        patching and load order come out right instead of being guessed at.
        """
        # Despite what the draftsman CLI's help text suggests, the underlying
        # function wants the game's *data* directory -- the one holding base/,
        # core/ and the DLC modules as folders -- not the installation root.
        data_dir = paths.factorio_data_dir()
        if data_dir is None:
            raise ProfileError("the Factorio installation was not found")
        if not self.mods_dir.is_dir():
            raise ProfileError("call prepare_mods() before extract_data()")

        # Run in a child process rather than in-process. The lifecycle spins up
        # a Lua runtime and rewrites Draftsman's data files, and this process
        # has already imported the old ones; a child keeps the two apart and
        # isolates a crash in mod Lua from us.
        #
        # Owning the DLC is modelled after the profile: a save without Space Age
        # should be resolved as a player who does not have it, so that nothing
        # from it can leak into the data.
        owned_dlc = [dlc for dlc in ("space-age",) if dlc in self.mod_names]

        command = [
            sys.executable,
            "-c",
            _EXTRACT_PROGRAM,
            str(data_dir),
            str(self.mods_dir),
            ",".join(owned_dlc),
            "1" if verbose else "0",
        ]

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            self._store_extracted_data()
            self.data_fingerprint = self.measure_data()
            self.write()
        return result

    def _store_extracted_data(self) -> None:
        """Keep the freshly extracted data with the profile that produced it."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for pickle in self._draftsman_data_dir().glob("*.pkl"):
            shutil.copy2(pickle, self.data_dir / pickle.name)

    # ------------------------------------------------------------------
    # extraction fingerprint
    # ------------------------------------------------------------------

    def measure_data(self) -> dict:
        """Summarise what is currently in Draftsman's data directory.

        Extraction is not reproducible: Factorio's own data stage extends
        `data.raw.recipe` while iterating it, which Lua leaves undefined, and
        the stock Lua underneath seeds its string hashing per process. So two
        runs over an identical mod set can yield slightly different prototype
        sets — a handful of generated recycling recipes, in practice.

        A profile is therefore extracted once and kept, and this records what
        that one extraction produced. It is not a correctness check; it exists
        so that a later re-extraction visibly differs instead of quietly
        replacing the data a blueprint was built against.
        """
        program = (
            "import json, sys\n"
            "out = {}\n"
            "for mod in ('entities', 'recipes', 'items', 'fluids', 'tiles'):\n"
            "    try:\n"
            "        m = __import__('draftsman.data.' + mod, fromlist=['raw'])\n"
            "        out[mod] = sorted(m.raw)\n"
            "    except Exception:\n"
            "        out[mod] = []\n"
            "json.dump(out, sys.stdout)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise ProfileError(f"could not read extracted data: {result.stderr[-300:]}")

        names = json.loads(result.stdout)
        digest = hashlib.sha256()
        summary: dict = {
            "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "counts": {},
        }
        for category in sorted(names):
            summary["counts"][category] = len(names[category])
            digest.update(category.encode("utf-8"))
            for name in names[category]:
                digest.update(b"\0")
                digest.update(name.encode("utf-8"))
        summary["digest"] = digest.hexdigest()[:16]
        return summary

    def data_matches_fingerprint(self) -> bool | None:
        """Whether the data on disk still matches what was recorded.

        Returns None when there is nothing recorded to compare against.
        """
        if not self.data_fingerprint:
            return None
        self.activate()
        return self.measure_data()["digest"] == self.data_fingerprint.get("digest")

    def activate(self) -> None:
        """Put this profile's game data in front of Draftsman.

        Draftsman reads prototype data from one fixed directory in its own
        installation with no way to redirect it, so switching profiles means
        physically swapping those files.
        """
        if not self.has_extracted_data:
            raise ProfileError(
                f"profile {self.name!r} has no extracted data; run extract_data() first"
            )
        target = self._draftsman_data_dir()
        for pickle in self.data_dir.glob("*.pkl"):
            shutil.copy2(pickle, target / pickle.name)
        # Leave a marker so a later run can tell whose data is loaded without
        # having to compare several megabytes of pickles.
        (target / ACTIVE_MARKER).write_text(
            f"{self.name}\n{self.fingerprint}\n", encoding="utf-8"
        )

    @staticmethod
    def active_profile_name() -> str | None:
        """Which profile's data is currently in Draftsman's data directory."""
        marker = Profile._draftsman_data_dir() / ACTIVE_MARKER
        if not marker.is_file():
            return None
        first_line = marker.read_text(encoding="utf-8").splitlines()
        return first_line[0].strip() if first_line else None

    def is_active(self) -> bool:
        return Profile.active_profile_name() == self.name
