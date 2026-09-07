"""Command line entry point for factorio-forge."""

from __future__ import annotations

import argparse
import sys

from . import __version__, paths


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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
