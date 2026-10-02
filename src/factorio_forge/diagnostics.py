"""Turn failures into named causes.

Most of what can go wrong here originates outside this project: a mod packaged
in an unusual way, a mod archive that is not valid UTF-8, a version of the
extraction library that does not match the installed game, a mod whose Lua
raises during the data lifecycle. When such a failure surfaces as a traceback
from deep inside a dependency, it says nothing about which input caused it or
what to do next.

So every failure that a user can plausibly hit gets a recognised category, a
message naming the specific thing that failed, and a suggested remedy. Anything
genuinely unrecognised is reported as unrecognised rather than being forced into
a category it does not fit -- a wrong diagnosis is worse than an honest "not
sure", because it sends the reader off in the wrong direction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Pattern


@dataclass(frozen=True)
class Diagnosis:
    """A failure, named and explained."""

    category: str
    summary: str
    detail: str = ""
    remedy: str = ""
    raw: str = ""

    @property
    def recognised(self) -> bool:
        return self.category != "unknown"

    def report(self, include_raw: bool = False) -> str:
        lines = [self.summary]
        if self.detail:
            lines.append(f"  {self.detail}")
        if self.remedy:
            lines.append(f"  What to do: {self.remedy}")
        if include_raw and self.raw:
            lines.append("  Original error:")
            lines.extend(f"    {line}" for line in self.raw.strip().splitlines()[-6:])
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.report()


@dataclass(frozen=True)
class _Rule:
    category: str
    pattern: Pattern[str]
    build: Callable[[re.Match[str]], tuple[str, str, str]]


def _rule(
    category: str,
    pattern: str,
    build: Callable[[re.Match[str]], tuple[str, str, str]],
) -> _Rule:
    return _Rule(category, re.compile(pattern, re.MULTILINE), build)


# Ordered most specific first: the first rule that matches wins, so a narrow
# pattern must not sit behind a broad one that would swallow it.
_RULES: list[_Rule] = [
    _rule(
        "mod-archive-layout",
        r"IncorrectModFormatError: Mod archive '([^']+)' has more than one internal folder",
        lambda m: (
            f"The mod {m.group(1)!r} is packaged in a form the loader cannot read.",
            "Its archive has more than one folder at the root -- usually a "
            "'__MACOSX' folder added when the mod was zipped on a Mac -- and none "
            "of them matches the archive's own name.",
            "factorio-forge normally repacks such archives automatically when "
            "building a profile's mod folder; rerun prepare_mods(force=True) to "
            "rebuild it.",
        ),
    ),
    _rule(
        "mod-archive-invalid",
        r"IncorrectModFormatError: Mod '([^']+)' has no 'info\.json'",
        lambda m: (
            f"The mod {m.group(1)!r} is missing its info.json and cannot be loaded.",
            "Every mod must carry an info.json in its root folder; this archive "
            "does not, so it is either corrupt or not a mod at all.",
            "Redownload the mod, or remove it from the save's mod set.",
        ),
    ),
    _rule(
        "mod-encoding",
        r"UnicodeDecodeError:.*?(?:in position \d+)?.*?\n?.*?([\w./\\-]+\.lua)",
        lambda m: (
            "A mod file is not valid UTF-8 and could not be read.",
            f"The file {m.group(1)} contains bytes that are not valid UTF-8. Mods "
            "are occasionally saved in a legacy encoding such as Windows-1251, "
            "which the loader cannot decode.",
            "Redownload the mod, or report the encoding to its author.",
        ),
    ),
    _rule(
        "mod-encoding",
        r"UnicodeDecodeError: '([\w-]+)' codec can't decode byte (0x[0-9a-f]+)",
        lambda m: (
            "A mod file is not valid UTF-8 and could not be read.",
            f"Decoding failed on byte {m.group(2)} using the {m.group(1)} codec. "
            "Mods are occasionally saved in a legacy encoding rather than UTF-8.",
            "Redownload the mod, or report the encoding to its author.",
        ),
    ),
    _rule(
        "library-game-version-mismatch",
        # Separators repeat because the path arrives inside a Python repr, where
        # backslashes are escaped: `...compatibility\\defines\\2.0.lua`.
        r"No such file or directory: .*compatibility[/\\]+defines[/\\]+([\d.]+)\.lua",
        lambda m: (
            "The extraction library does not support this Factorio version.",
            f"It looked for the constants file for Factorio {m.group(1)} and did "
            "not find it. Version 4.0.0 of factorio-draftsman has this fault for "
            "every version: the published package omits the directory entirely.",
            "Install the factorio-draftsman this project depends on (the "
            "`main-forge` fork in pyproject.toml), which ships the file the "
            "loader expects.",
        ),
    ),
    _rule(
        "game-path-wrong",
        r"No such file or directory: '(.*?)[/\\]+base[/\\]+info\.json'",
        lambda m: (
            "The Factorio data directory was not found where expected.",
            f"There is no base/info.json under {m.group(1)!r}. The extractor needs "
            "the game's data folder -- the one containing base, core and the DLC "
            "modules as subfolders -- not the installation root.",
            "Check `factorio-forge paths`, and set FACTORIO_PATH if the "
            "installation was not detected correctly.",
        ),
    ),
    _rule(
        "collision-box-shape",
        r"entities\.py.*\n.*collision_box\[0\]|KeyError: 0\b(?![\s\S]{0,200}?feature)",
        lambda _: (
            "A mod defines an entity's collision box in a form the extractor "
            "cannot read.",
            "Factorio accepts a corner written positionally, as {-1, -1}, or by "
            "name, as {x = -1, y = -1}. A mod that supplies both at once leaves "
            "the extractor with a table it treats as neither, and reading the "
            "first coordinate fails.",
            "The mod itself is fine and the game loads it. Either exclude it "
            "from the profile, or use a build of the extraction library that "
            "reads a corner by name as well as by position.",
        ),
    ),
    _rule(
        "missing-mod",
        r"MissingModError: Unrecognized mod name '([^']+)'",
        lambda m: (
            f"The mod {m.group(1)!r} is required but not present.",
            "It is listed in the profile's mod set but no matching archive was "
            "found in the mod folder.",
            "Install that mod at the version the save requires, then rebuild the "
            "profile's mod folder.",
        ),
    ),
    _rule(
        "mod-lua-error",
        r"LuaError:(.*)",
        lambda m: (
            "A mod raised an error while the game data was being built.",
            f"Lua reported: {m.group(1).strip()[:200]}",
            "This usually means the mod set is inconsistent -- a mod at a version "
            "that does not match the others, or a missing dependency. Check that "
            "every mod is at the version the save recorded.",
        ),
    ),
    _rule(
        "mod-dependency",
        r"(?:IncompatibleMod|DependencyError|missing dependency)[^\n]*?([\w-]+)",
        lambda m: (
            "The mod set does not satisfy its own dependencies.",
            f"A dependency problem was reported involving {m.group(1)!r}.",
            "Compare the profile's mod list against what the save requires; a "
            "version mismatch is the usual cause.",
        ),
    ),
    _rule(
        "out-of-memory",
        r"MemoryError|Cannot allocate memory",
        lambda _: (
            "Ran out of memory while building the game data.",
            "Large mod sets can need a lot of memory during the data lifecycle.",
            "Close other applications and try again.",
        ),
    ),
]


def diagnose(stderr: str, stdout: str = "", returncode: int = 1) -> Diagnosis:
    """Identify why an extraction run failed."""
    if returncode == 0:
        return Diagnosis(category="none", summary="Completed successfully.")

    haystack = f"{stderr}\n{stdout}"
    for rule in _RULES:
        match = rule.pattern.search(haystack)
        if match:
            summary, detail, remedy = rule.build(match)
            return Diagnosis(
                category=rule.category,
                summary=summary,
                detail=detail,
                remedy=remedy,
                raw=stderr,
            )

    # Nothing matched. Say so, and surface the most informative line rather
    # than inventing an explanation.
    last_line = ""
    for line in reversed(stderr.strip().splitlines()):
        if line.strip() and not line.startswith(" "):
            last_line = line.strip()
            break

    return Diagnosis(
        category="unknown",
        summary="The data extraction failed for a reason not recognised.",
        detail=last_line or f"The process exited with code {returncode}.",
        remedy="Rerun with verbose output to see the full log.",
        raw=stderr,
    )
