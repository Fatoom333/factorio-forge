"""What the suite is running against, said out loud before it runs.

Draftsman keeps prototype data in one fixed directory inside its own
installation, holding whichever profile was activated last. Nothing in a test
declares that, so the same suite can pass on one mod set and fail on another
for reasons that have nothing to do with the code under test -- which is
exactly what happened when a Krastorio 2 profile was left active and twelve
tests failed complaining about a missing attribute.

The data cannot be pinned from here without taking over the user's own
installation, so it is named instead: every run says which profile it used, and
a failure can be read in that light.
"""

from __future__ import annotations


def pytest_report_header(config) -> list[str]:
    lines = []
    try:
        from factorio_forge.profile import Profile

        name = Profile.active_profile_name()
        lines.append(f"game data: profile {name!r}" if name else "game data: no profile activated")
    except Exception as exc:  # noqa: BLE001 - reporting must never break the run
        lines.append(f"game data: profile unknown ({exc.__class__.__name__})")

    try:
        import draftsman
        from draftsman.data import entities, recipes

        lines.append(
            f"draftsman {draftsman.__version__}, "
            f"{len(entities.raw)} entities and {len(recipes.raw)} recipes loaded"
        )
    except Exception as exc:  # noqa: BLE001
        lines.append(f"draftsman: not readable ({exc.__class__.__name__})")

    return lines
