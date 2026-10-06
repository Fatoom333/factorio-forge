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
    for line in report.details():
        print(f"  {line}")
    if profile.mod_settings_source == "environment":
        print("startup settings: from the companion mod's export, which matches this mod set")
    else:
        print(
            "startup settings: the game's current mod-settings.dat "
            "(no /forge-export with exactly this mod set yet)"
        )
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


def _cmd_notes(args: argparse.Namespace) -> int:
    from .profile import Profile, ProfileError

    name = args.profile or Profile.active_profile_name()
    if not name:
        print("no active profile — name one, or activate it first", file=sys.stderr)
        return 1
    try:
        profile = Profile.load(name)
    except ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"profile:   {profile.directory}")
    print(f"notes:     {profile.notes_path}" + ("" if profile.notes_path.exists() else " (missing)"))
    reference = profile.directory / "reference"
    if reference.is_dir():
        files = sorted(p.name for p in reference.iterdir() if p.is_file())
        print(f"reference: {reference}" + (f" ({len(files)} files)" if files else " (empty)"))
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


def _prototype_menu(kind: str) -> list[str]:
    """One line per prototype of a kind, with the numbers that tell them apart."""
    from draftsman.data import entities

    from . import layout

    lines = []
    for name, data in sorted(entities.raw.items()):
        if data.get("type") != kind:
            continue
        if kind == "transport-belt":
            detail = f"{layout.belt_throughput(name):g}/s ({layout.lane_throughput(name):g}/s a lane)"
        elif kind == "inserter":
            try:
                detail = (
                    f"reach {layout.inserter_reach(name)}, up to {layout.inserter_rate(name):.3g} items/s "
                    "per stack, chest to chest"
                )
            except layout.LayoutError:
                continue
        elif kind == "electric-pole":
            w, h = layout.machine_size(name)
            detail = (
                f"{w}x{h}, supply {data.get('supply_area_distance')}, "
                f"wire {data.get('maximum_wire_distance')}"
            )
        else:
            detail = ""
        lines.append(f"  {name}  {detail}")
    return lines


def _cmd_options(args: argparse.Namespace) -> int:
    """The numbers to decide a row layout with, for one recipe and machine."""
    import json
    import math

    from . import layout, rows

    missing = [
        (flag, kind)
        for flag, kind, value in (
            ("--belt", "transport-belt", args.belt),
            ("--inserter", "inserter", args.inserter),
            ("--pole", "electric-pole", args.pole),
        )
        if value is None
    ]
    if missing:
        for flag, kind in missing:
            print(f"{flag}: choose one of", file=sys.stderr)
            for line in _prototype_menu(kind):
                print(line, file=sys.stderr)
        return 2

    def spec(counts: list[int], stack: str) -> rows.RowBlockSpec:
        return rows.RowBlockSpec(
            recipe=args.recipe, machine=args.machine, rows=counts, belt=args.belt,
            inserter=args.inserter, long_inserter=args.long_inserter, pole=args.pole,
            stack=stack, input_belts=args.input_belts, speed_bonus=args.speed_bonus,
            stack_size=args.stack_size,
        )

    hands, hand_note = rows.hand_sizes(spec([1], "mirror"))
    try:
        alone = layout.row_capacity(
            args.recipe, args.machine, args.belt, inserter=args.inserter,
            long_inserter=args.long_inserter, input_belts=args.input_belts,
            stack_size=hands, speed_bonus=args.speed_bonus,
        )
        shared = layout.row_capacity(
            args.recipe, args.machine, args.belt, inserter=args.inserter,
            long_inserter=args.long_inserter, input_belts=args.input_belts,
            rows_per_input_belt=2, stack_size=hands, speed_bonus=args.speed_bonus,
        )
    except layout.LayoutError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    report: dict = {
        "recipe": args.recipe,
        "machine": args.machine,
        "machine_size": layout.machine_size(args.machine),
        "crafts_per_second_each": layout.crafts_per_second(args.recipe, args.machine, args.speed_bonus),
        "per_machine": [f.__dict__ for f in alone.per_machine + alone.fluids_per_machine],
        "row_capacity": {"own_input_belts": alone.per_row, "input_belts_shared": shared.per_row},
        "limit": {"own_input_belts": alone.limit, "input_belts_shared": shared.limit},
        "input_lanes": alone.input_lanes,
        "inserters_per_machine": alone.inserters,
        "notes": ([hand_note] if hand_note else []) + alone.notes,
        "stacks": {},
        "suggestions": [],
    }
    for stack in ("mirror", "repeat"):
        try:
            sample = rows.build_block(spec([2, 2], stack))
            report["stacks"][stack] = {"pitch_of_two_rows": sample.height / 2, "height_of_two_rows": sample.height}
        except layout.LayoutError as exc:
            report["stacks"][stack] = {"refused": str(exc)}

    if args.machines:
        for count in range(1, min(args.machines, 8) + 1):
            per_row = math.ceil(args.machines / count)
            counts = [per_row] * (count - 1) + [args.machines - per_row * (count - 1)]
            if counts[-1] < 1:
                continue
            try:
                block = rows.build_block(spec(counts, args.stack))
            except layout.LayoutError as exc:
                report["suggestions"].append({"rows": counts, "refused": str(exc)})
                continue
            overfull = [r.index + 1 for r in block.rows if r.capacity and r.machines > r.capacity]
            report["suggestions"].append({
                "rows": counts, "size": [block.width, block.height],
                "overfed_rows": overfull, "notes": block.notes,
            })
            if per_row <= 2:
                break

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    w, h = report["machine_size"]
    print(f"{args.recipe} in {args.machine} ({w}x{h}), {report['crafts_per_second_each']:.4g} crafts/s each")
    for flow in alone.per_machine + alone.fluids_per_machine:
        print(f"  {flow.direction:3s} {flow.item}: {flow.rate:.4g}/s per machine")
    if alone.per_row:
        print(f"\none row keeps {alone.per_row} running on its own input belts")
        print(f"  because {alone.limit}")
        if shared.per_row != alone.per_row:
            print(f"  {shared.per_row} when its input belts are shared with a mirrored row")
    else:
        print(f"\n{alone.limit}")
    if alone.input_lanes:
        print("input lanes: " + "  |  ".join(" + ".join(lanes) for lanes in alone.input_lanes))
    if alone.inserters:
        print("inserters per machine: " + ", ".join(f"{k} x{n}" for k, n in alone.inserters.items()))
    for note in report["notes"]:
        print(f"  note: {note}")
    print()
    for stack, info in report["stacks"].items():
        if "refused" in info:
            print(f"{stack}: cannot be built -- {info['refused']}")
        else:
            print(f"{stack}: {info['pitch_of_two_rows']:g} tiles per row")
    if report["suggestions"]:
        print(f"\n{args.machines} machines, stacked {args.stack}:")
        for s in report["suggestions"]:
            shape = " + ".join(str(n) for n in s["rows"])
            if "refused" in s:
                print(f"  {shape}: refused -- {s['refused']}")
                continue
            warn = f"  (overfed rows: {s['overfed_rows']})" if s["overfed_rows"] else ""
            print(f"  {len(s['rows'])} row(s) of {shape}: {s['size'][0]}x{s['size'][1]}{warn}")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    """A layout plan in; blueprint string, drawing and report out."""
    import json
    import re

    from . import plan, render

    try:
        data = plan.load(Path(args.plan))
        planned, warned = None, []
        if data.get("request"):
            # Rates the request asks for, next to what the blocks can do at full load.
            planned, warned = plan.planned_from_request(data, Path(args.plan))
        result = plan.build(data, planned)
    except plan.PlanError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    result.warnings.extend(warned)

    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", result.label).strip("-") or "layout"
    out = Path(args.output) if args.output else Path(args.plan).resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    string_path = out / f"{slug}.txt"
    string_path.write_text(result.blueprint.to_string(), encoding="utf-8")
    report_path = out / f"{slug}.report.json"
    report_path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    html_path = None
    if not args.no_render:
        html_path = render.write_html(result.blueprint, out / f"{slug}.html", title=result.label, findings=result.findings)

    print(f"{result.label}: {_plural(len(result.blueprint.entities), 'entity', 'entities')}, "
          f"{_plural(result.power_networks, 'power network', 'power networks')}")
    for warning in warned:
        print(f"warning: {warning}")
    for block in result.blocks:
        if block.planned_crafts_per_second is None:
            rate = f"{block.crafts_per_second:.4g} crafts/s at full load"
        else:
            rate = f"{block.planned_crafts_per_second:.4g}/s planned of {block.crafts_per_second:.4g} crafts/s"
        print(f"\nblock {block.index}: {block.machines} x {block.machine} on {block.recipe}, "
              f"{block.width}x{block.height} at {tuple(block.at)}, {rate}")
        for row in block.rows:
            limit = f" of {row['capacity']}" if row["capacity"] else ""
            print(f"  row {row['index'] + 1}: {row['machines']}{limit}")
        for port in block.ports:
            items = " | ".join(i or "-" for i in port["items"])
            if port.get("planned_rate") is None:
                flow = f"{port['rate']:.4g}/s at full load"
            else:
                flow = f"{port['planned_rate']:.4g}/s planned ({port['rate']:.4g}/s at full load)"
            arrives = f", arrives {' | '.join(port['arrives'])}" if port.get("arrives") else ""
            print(f"  [{port['index']}] {port['io']:3s} {port['kind']:4s} ({port['x']}, {port['y']}) "
                  f"flowing {port['direction']}: {items} at {flow}{arrives}")
        for note in block.notes:
            print(f"  note: {note}")
    if result.routes:
        print("\nroutes:")
        for r in result.routes:
            if not r.ok:
                print(f"  route {r.id}: FAILED - {r.reason}")
                continue
            line = f"  route {r.id}: {r.kind} {_plural(r.length, 'tile', 'tiles')}"
            if r.hops:
                line += f", {_plural(r.hops, 'underground pair', 'underground pairs')}"
            line += f", {_plural(r.turns, 'turn', 'turns')}"
            if r.lanes:
                line += f", {'lanes' if r.kind == 'belt' else 'carries'} " + "|".join(i or "-" for i in r.lanes)
            if r.junction:
                line += f", {r.junction.kind} at {r.junction.tile}"
            if r.delivered:
                line += f", delivered {' | '.join(r.delivered)}"
            print(line)
    shown = [f for f in result.findings if f.severity.value != "note"]
    if shown:
        print(f"\n{_plural(len(shown), 'finding', 'findings')}:")
        for finding in shown:
            where = f" at {finding.position}" if finding.position else ""
            print(f"  [{finding.severity.value}] {finding.summary}{where}")
    else:
        print("\nchecks: nothing to report")
    print(f"\nblueprint: {string_path.resolve()}")
    print(f"report:    {report_path.resolve()}")
    if html_path:
        print(f"drawing:   {html_path.resolve()}")
    return 0


def _cmd_find(args: argparse.Namespace) -> int:
    """The player's words matched to prototype names, with names in their language."""
    from . import bom, environment, names

    found, why = environment.for_active_profile()
    kinds = tuple(args.kind) if args.kind else names.KINDS
    raw = bom._mined_items() | bom._PUMPED_FLUIDS
    text = " ".join(args.words)
    results = names.find(text, kinds=kinds, limit=args.limit, environment=found, raw=raw)
    for phrase, missing, note in names.absent_slang(text):
        print(f"«{phrase}» is known slang for {missing}, which this mod set does not have" + (f" ({note})" if note else ""))
    if not results:
        print("nothing matches; try fewer or other words, English, or the internal name")
        return 1
    for c in results:
        state = {True: "unlocked", False: "LOCKED", None: ""}[c.unlocked]
        titles = " / ".join(t for t in (c.titles.get("ru"), c.titles.get("en")) if t)
        print(f"{c.name:44s} {'+'.join(c.kinds):22s} {state:9s} {titles}")
        if c.via:
            print(f"{'':44s} ^ {c.via}")
    if found is None:
        print(f"\n(unlocked state unknown: {why})")
    return 0


def _cmd_available(args: argparse.Namespace) -> int:
    """What the player has to build with, and the bonuses that change the numbers."""
    from . import environment, request

    found, why = environment.for_active_profile()
    print(f"game state: {why}")
    roles = [args.role] if args.role else list(request.ROLES)
    for role in roles:
        print(f"\n{role}:")
        for option in request.options(role, found):
            if option.unlocked is False and not args.all:
                continue
            state = {True: "", False: "  LOCKED", None: "  (unknown)"}[option.unlocked]
            print(f"  {option.name:36s} {option.detail}{state}")
    if found is not None and found.bonuses is not None:
        print("\nbonuses:")
        for name, value in sorted(found.bonuses.force.items()):
            if value:
                print(f"  {name}: {value:g}")
        for recipe, value in sorted(found.bonuses.recipe_productivity.items()):
            print(f"  productivity of {recipe}: +{value:.0%}")
    elif found is not None:
        print("\nbonuses: not in this export (companion mod before 0.7.0); export again")
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    """Step 1: check a request against the player's game before designing anything."""
    import json

    from . import request

    try:
        spec = request.load(Path(args.request))
    except request.RequestError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    result = request.review(spec)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0 if result.ready else 1

    print(f"game state: {result.environment_note}")
    print(f"surface: {result.surface or 'not chosen'}")
    for heading, entries in (
        ("problems -- fix before designing", result.problems),
        ("ask the player", result.questions),
        ("assumed -- say so, or confirm", result.assumptions),
    ):
        if entries:
            print(f"\n{heading}:")
            for entry in entries:
                print(f"  - {entry}")
    if result.tiers:
        print("\ntiers: " + ", ".join(f"{role} {name}" for role, name in result.tiers.items()))
    if result.bill is not None:
        print("\nbill of materials:")
        for line in result.bill.lines:
            outputs = ", ".join(f"{k} {v:.4g}/s" for k, v in line.outputs.items())
            print(f"  {line.machines:4d} x {line.machine} on {line.recipe} -> {outputs}")
        if result.bill.raw_materials:
            print("  from outside: " + ", ".join(f"{k} {v:.4g}/s" for k, v in sorted(result.bill.raw_materials.items())))
        print(f"  power: {result.bill.total_power / 1e6:.3g} MW")
    if not result.ready:
        print("\nnot ready: fix the problems first")
    elif result.questions:
        print("\nno problems; ask the questions, or say what you assume, before designing")
    else:
        print("\nready to design")
    return 0 if result.ready else 1


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

    notes = sub.add_parser(
        "notes", help="show where a profile's folder, notes.md and reference blueprints are"
    )
    notes.add_argument("profile", nargs="?", help="the profile (default: the active one)")
    notes.set_defaults(func=_cmd_notes)

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

    find = sub.add_parser("find", help="match the player's words to item, fluid, recipe and entity names")
    find.add_argument("words", nargs="+")
    find.add_argument("--kind", action="append", choices=("item", "fluid", "recipe", "entity"))
    find.add_argument("--limit", type=int, default=12)
    find.set_defaults(func=_cmd_find)

    available = sub.add_parser("available", help="belts, inserters and poles the player has, and their bonuses")
    available.add_argument("role", nargs="?", choices=("belt", "inserter", "long_inserter", "pole"))
    available.add_argument("--all", action="store_true", help="include locked ones")
    available.set_defaults(func=_cmd_available)

    review = sub.add_parser("review", help="step 1: check a request against the player's game")
    review.add_argument("request", help="a request JSON file (see factorio_forge/request.py)")
    review.add_argument("--json", action="store_true")
    review.set_defaults(func=_cmd_review)

    options = sub.add_parser(
        "options", help="the numbers to decide a row layout with: capacity, limits, pitch, sizes"
    )
    options.add_argument("recipe")
    options.add_argument("machine")
    options.add_argument("--belt")
    options.add_argument("--inserter", help="serves the nearer input belt and the output")
    options.add_argument("--long-inserter", help="serves a second input belt, further out")
    options.add_argument("--pole")
    options.add_argument("--input-belts", type=int)
    options.add_argument(
        "--stack-size", type=int, help="inserter hand size (default: from the companion mod's export of research)"
    )
    options.add_argument("--speed-bonus", type=float, default=0.0, help="module/beacon speed, 0.5 = +50%%")
    options.add_argument("--machines", type=int, help="also suggest row splits for this many machines")
    options.add_argument("--stack", choices=("mirror", "repeat"), default="mirror")
    options.add_argument("--json", action="store_true")
    options.set_defaults(func=_cmd_options)

    build = sub.add_parser("build", help="build a layout plan into a checked blueprint, drawing and report")
    build.add_argument("plan", help="a plan JSON file (see factorio_forge/plan.py)")
    build.add_argument("-o", "--output", help="directory to write into (default: beside the plan)")
    build.add_argument("--no-render", action="store_true")
    build.set_defaults(func=_cmd_build)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
