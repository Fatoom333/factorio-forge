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


def _cmd_render(args: argparse.Namespace) -> int:
    from draftsman.blueprintable import (
        Blueprint,
        BlueprintBook,
        get_blueprintable_from_string,
    )

    from . import render

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
        return 1

    if isinstance(blueprint, BlueprintBook):
        contents = list(blueprint.blueprints)
        if args.index is None:
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
            return 1
        if not 0 <= args.index < len(contents):
            print(
                f"The book has {len(contents)} entries, numbered 0 to {len(contents) - 1}.",
                file=sys.stderr,
            )
            return 1
        blueprint = contents[args.index]

    if not isinstance(blueprint, Blueprint):
        print(
            f"A {type(blueprint).__name__} has no entities to draw — it is a list of "
            "settings rather than a layout.",
            file=sys.stderr,
        )
        return 1

    output = Path(args.output) if args.output else Path("blueprint.html")
    written = render.write_html(blueprint, output, title=args.title)
    bounds = render.measure(blueprint.entities, blueprint.tiles)
    print(f"{_plural(len(blueprint.entities), 'entity', 'entities')}, {bounds}")
    print(f"written: {written.resolve()}")
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

    draw = sub.add_parser("render", help="draw a blueprint string as an HTML page")
    draw.add_argument("blueprint", help="a blueprint string, or a file containing one")
    draw.add_argument("-o", "--output", help="where to write (default: blueprint.html)")
    draw.add_argument("--title", help="heading for the page (default: the blueprint's label)")
    draw.add_argument(
        "--index", type=int, help="which entry to draw, when the string is a blueprint book"
    )
    draw.set_defaults(func=_cmd_render)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
