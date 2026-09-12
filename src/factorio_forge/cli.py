"""Command line entry point for factorio-forge."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, paths


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _cmd_paths(_: argparse.Namespace) -> int:
    return paths.main()


def _cmd_init(_: argparse.Namespace) -> int:
    home = paths.ensure_layout()
    print(f"forge home ready: {home}")

    install = paths.factorio_install_dir()
    user_dir = paths.factorio_user_dir()
    if install is None or user_dir is None:
        print(
            "Factorio was not found automatically. Set the FACTORIO_PATH and/or "
            "FACTORIO_USER_DIR environment variables, then run this again.",
            file=sys.stderr,
        )
        return 1

    # Remember what detection found, so later runs do not repeat the search.
    config = paths.load_config()
    config["factorio_install_dir"] = str(install)
    config["factorio_user_dir"] = str(user_dir)
    version = paths.factorio_version()
    if version:
        config["factorio_version"] = version
    saved = paths.save_config(config)

    print(f"Factorio {version or '(unknown version)'} at {install}")
    print(f"config written: {saved}")
    return 0


def _resolve_save_path(raw: str) -> Path | None:
    """A save file from a path, or by name from the game's own save folder."""
    from . import save as save_module

    candidate = Path(raw)
    if candidate.is_file():
        return candidate

    for path in save_module.list_saves(include_autosaves=True):
        if path.stem.lower() == raw.lower():
            return path
    return None


def _cmd_create_profile(args: argparse.Namespace) -> int:
    from . import save as save_module
    from .profile import Profile, ProfileError

    save_path = _resolve_save_path(args.save)
    if save_path is None:
        print(f"no save named {args.save!r} was found.", file=sys.stderr)
        available = save_module.list_saves()
        if available:
            shown, rest = available[:15], available[15:]
            print("saves in the game's save folder (newest first):", file=sys.stderr)
            for path in shown:
                print(f"  {path.stem}", file=sys.stderr)
            if rest:
                print(f"  ... and {len(rest)} more", file=sys.stderr)
        return 1

    try:
        info = save_module.read_save_info(save_path)
    except save_module.SaveFormatError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        profile = Profile.from_save(info, name=args.name)
    except ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if profile.name in Profile.list_all() and not args.force:
        print(
            f"a profile named {profile.name!r} already exists; pass --force to replace it",
            file=sys.stderr,
        )
        return 1

    profile.write()
    print(
        f"profile {profile.name!r} created from {info.name} "
        f"(Factorio {info.game_version_string}, {_plural(len(info.mods), 'mod', 'mods')})"
    )

    report = profile.prepare_mods(force=args.force)
    print(f"mods: {report.summary()}")
    if not report.ok:
        for mod in report.missing:
            print(f"  missing: {mod}", file=sys.stderr)
        for sub in report.outdated:
            print(f"  too old: {sub}", file=sys.stderr)
        print("cannot extract data until every mod is available.", file=sys.stderr)
        return 1

    result = profile.extract_data()
    if result.returncode != 0:
        print("data extraction failed:", file=sys.stderr)
        print(result.stderr[-2000:], file=sys.stderr)
        return 1

    counts = (profile.data_fingerprint or {}).get("counts", {})
    if counts:
        print("data extracted: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    print(f"ready — activate with: factorio-forge activate-profile {profile.name}")
    return 0


def _cmd_activate_profile(args: argparse.Namespace) -> int:
    from .profile import Profile, ProfileError

    try:
        profile = Profile.load(args.profile)
    except ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        profile.activate()
    except ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"profile {profile.name!r} is now active")
    return 0


def _cmd_list_profiles(_: argparse.Namespace) -> int:
    from .profile import Profile

    names = Profile.list_all()
    if not names:
        print("no profiles yet — create one with create-profile")
        return 0

    active = Profile.active_profile_name()
    for name in names:
        profile = Profile.load(name)
        marker = "*" if name == active else " "
        extracted = "data extracted" if profile.has_extracted_data else "no data extracted"
        styled = "style measured" if profile.has_measured_style else "style not measured"
        source = f" (from {profile.source_save})" if profile.source_save else ""
        print(f"{marker} {name}{source} — {extracted}, {styled}")
    return 0


def _load_blueprint(args: argparse.Namespace):
    """Read a blueprint from a string or a file, explaining any refusal.

    Returns the blueprint, or None once it has said why it could not. Shared by
    every command that takes a blueprint, so they all fail the same way.
    """
    from draftsman.blueprintable import (
        Blueprint,
        BlueprintBook,
        get_blueprintable_from_string,
    )

    source = args.blueprint
    # A blueprint string starts with its version byte; anything else is a path.
    candidate = Path(source)
    if not source.startswith("0") and candidate.is_file():
        source = candidate.read_text(encoding="utf-8").strip()

    try:
        # Parse generically: a book or a planner should be recognised and
        # explained, not reported as a broken blueprint.
        blueprint = get_blueprintable_from_string(source)
    except Exception as exc:
        # The underlying error is about base64 and zlib, which says nothing
        # about what the user did wrong.
        print("That is not a blueprint string.", file=sys.stderr)
        if not source.startswith("0"):
            print(
                "  A blueprint string begins with '0'. If you meant a file, the path "
                f"{args.blueprint!r} does not exist.",
                file=sys.stderr,
            )
        else:
            print(
                "  It begins correctly but does not decode, so it is probably "
                "truncated — copying from a chat window often cuts the end off.",
                file=sys.stderr,
            )
        print(
            "  Export it again from the game with the blueprint's export button.",
            file=sys.stderr,
        )
        print(f"  (underlying error: {exc})", file=sys.stderr)
        return None

    if isinstance(blueprint, BlueprintBook):
        contents = list(blueprint.blueprints)
        if getattr(args, "index", None) is None:
            print(
                f"That is a blueprint book holding {len(contents)} entries. "
                "Choose one with --index:",
                file=sys.stderr,
            )
            for i, item in enumerate(contents):
                label = getattr(item, "label", None) or "(unlabelled)"
                count = len(getattr(item, "entities", []))
                kind = type(item).__name__
                print(
                    f"  --index {i}   {label}  [{kind}, {_plural(count, 'entity', 'entities')}]",
                    file=sys.stderr,
                )
            return None
        if not 0 <= args.index < len(contents):
            print(
                f"The book has {len(contents)} entries, numbered 0 to {len(contents) - 1}.",
                file=sys.stderr,
            )
            return None
        blueprint = contents[args.index]

    if not isinstance(blueprint, Blueprint):
        print(
            f"A {type(blueprint).__name__} has no entities — it is a list of settings "
            "rather than a layout.",
            file=sys.stderr,
        )
        return None

    return blueprint


def _cmd_render(args: argparse.Namespace) -> int:
    from . import render

    blueprint = _load_blueprint(args)
    if blueprint is None:
        return 1

    findings = []
    if args.check:
        from . import inspection

        findings = inspection.inspect(blueprint).findings

    output = Path(args.output) if args.output else Path("blueprint.html")
    written = render.write_html(blueprint, output, title=args.title, findings=findings)
    if findings:
        print(f"{_plural(len(findings), 'finding', 'findings')} marked on the drawing")
    bounds = render.measure(blueprint.entities, blueprint.tiles)
    print(f"{_plural(len(blueprint.entities), 'entity', 'entities')}, {bounds}")
    print(f"written: {written.resolve()}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    from . import inspection

    blueprint = _load_blueprint(args)
    if blueprint is None:
        return 1

    report = inspection.inspect(blueprint)
    print(report.summary())

    groups = [
        ("problems", report.problems, "almost certainly wrong"),
        ("worth a look", report.suspects, "may be deliberate"),
    ]
    if args.all:
        groups.append(("notes", report.notes, ""))

    for heading, findings, aside in groups:
        if not findings:
            continue
        print(f"\n{heading}" + (f" — {aside}" if aside else ""))
        for finding in findings:
            where = f"  ({finding.position[0]}, {finding.position[1]})" if finding.position else ""
            print(f"  {finding.summary}{where}")
            if finding.detail:
                print(f"      {finding.detail}")

    if report.clean:
        print("\nNothing here changes the blueprint; this only reports.")
    else:
        print("\nNothing was changed. Whether any of this is a mistake is your call.")
    # A blueprint with problems is still a valid blueprint, so this is not an
    # error exit; it is a report.
    return 0


def _cmd_measure_style(args: argparse.Namespace) -> int:
    from .profile import Profile, ProfileError
    from .style import StyleError

    try:
        profile = Profile.load(args.profile)
    except ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        measured = profile.measure_style()
    except StyleError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"style measured from {_plural(len(measured.source_files), 'reference blueprint', 'reference blueprints')}")
    print(f"written: {profile.style_path.resolve()}")
    return 0


def _cmd_show_style(args: argparse.Namespace) -> int:
    from .profile import Profile, ProfileError
    from .style import StyleError

    try:
        profile = Profile.load(args.profile)
    except ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        measured = profile.load_style()
    except StyleError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        f"measured {measured.measured_at} from "
        f"{_plural(len(measured.source_files), 'reference blueprint', 'reference blueprints')}"
    )

    spacing = measured.layout.get("spacing") or {}
    if spacing:
        print("\nspacing")
        for name, info in sorted(spacing.items()):
            print(
                f"  {name}: {info['tiles']} tiles apart "
                f"({info['agreement'] * 100:.0f}% agreement, n={info['sample_size']})"
            )

    alignment = measured.layout.get("alignment") or {}
    if alignment:
        print(f"\nalignment: {alignment['step_x']}x{alignment['step_y']} tile grid")

    symmetry = measured.layout.get("symmetry") or {}
    if symmetry:
        print(
            f"symmetry: {symmetry['horizontal'] * 100:.0f}% horizontal, "
            f"{symmetry['vertical'] * 100:.0f}% vertical"
        )

    identity = measured.identity
    station_names = identity.get("station_names") or []
    if station_names:
        shown = ", ".join(station_names[:5])
        more = "…" if len(station_names) > 5 else ""
        print(f"\nstation names: {shown}{more}")
    if identity.get("annotations"):
        print(f"combinator annotations: {len(identity['annotations'])}")
    if identity.get("colors"):
        print(f"colours used: {len(identity['colors'])}")
    if identity.get("tag_keys"):
        print(f"tag keys: {', '.join(sorted(identity['tag_keys']))}")

    new = profile.new_reference_blueprints()
    if new:
        print(
            f"\n{_plural(len(new), 'new reference blueprint', 'new reference blueprints')} "
            "since this measurement — run measure-style again to include them"
        )

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factorio-forge",
        description="Generate Factorio blueprints tailored to your own save and style.",
    )
    parser.add_argument("--version", action="version", version=f"factorio-forge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("paths", help="show every path this tool resolves on this machine").set_defaults(
        func=_cmd_paths
    )
    sub.add_parser("init", help="create the data directory and remember where Factorio is").set_defaults(
        func=_cmd_init
    )

    create = sub.add_parser(
        "create-profile", help="create a profile from a save and extract its game data"
    )
    create.add_argument("save", help="a save file path, or the name of a save in the game's save folder")
    create.add_argument("--name", help="name for the profile (default: derived from the save name)")
    create.add_argument(
        "--force", action="store_true", help="replace an existing profile of the same name"
    )
    create.set_defaults(func=_cmd_create_profile)

    activate = sub.add_parser(
        "activate-profile", help="put a profile's game data in front of draftsman"
    )
    activate.add_argument("profile", help="the profile to activate")
    activate.set_defaults(func=_cmd_activate_profile)

    sub.add_parser(
        "list-profiles", help="list every profile, and which one is active"
    ).set_defaults(func=_cmd_list_profiles)

    draw = sub.add_parser("render", help="draw a blueprint string as an HTML page")
    draw.add_argument("blueprint", help="a blueprint string, or a file containing one")
    draw.add_argument("-o", "--output", help="where to write (default: blueprint.html)")
    draw.add_argument("--title", help="heading for the page (default: the blueprint's label)")
    draw.add_argument(
        "--index", type=int, help="which entry to draw, when the string is a blueprint book"
    )
    draw.add_argument(
        "--check", action="store_true", help="run the checks and mark what they find"
    )
    draw.set_defaults(func=_cmd_render)

    look = sub.add_parser(
        "check", help="report what looks wrong in a blueprint, without changing it"
    )
    look.add_argument("blueprint", help="a blueprint string, or a file containing one")
    look.add_argument(
        "--all", action="store_true", help="include notes, not just problems and suspects"
    )
    look.set_defaults(func=_cmd_check)

    measure_style = sub.add_parser(
        "measure-style", help="measure a base's building style from its reference blueprints"
    )
    measure_style.add_argument("profile", help="the profile to measure")
    measure_style.set_defaults(func=_cmd_measure_style)

    show_style = sub.add_parser("show-style", help="show a base's previously measured style")
    show_style.add_argument("profile", help="the profile to show")
    show_style.set_defaults(func=_cmd_show_style)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
