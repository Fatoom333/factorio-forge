"""Tests for the block library: names, ports read from a blueprint, the check, plan blocks, the measured save.

Blocks are built from whatever the active game data has (a recipe of the right shape and a machine that crafts it),
as in the rest of the suite, so nothing here names a vanilla prototype. The profile is a throwaway one in a
temporary forge home; the player's own profiles are never touched.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

import prototypes
from factorio_forge import cli, library, measure, paths, plan
from factorio_forge.profile import Profile
from factorio_forge.save import ModRef, SaveInfo
from test_route import block_spec, chained_recipes


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "forge"))
    return tmp_path


def make_profile(name: str = "Library") -> Profile:
    save = SaveInfo(path=Path(f"{name}.zip"), name=name, game_version=(2, 0, 77), mods=(ModRef("base", (2, 0, 77)),))
    profile = Profile.from_save(save)
    profile.write()
    return profile


@pytest.fixture
def profile(monkeypatch: pytest.MonkeyPatch) -> Profile:
    made = make_profile()
    monkeypatch.setattr(Profile, "active_profile_name", staticmethod(lambda: made.name))
    return made


def built(shape: tuple[int, int, int, int], rows: list[int] | None = None):
    recipe, machine = prototypes.crafting_setup(*shape)
    return plan.build({"blocks": [block_spec(recipe, machine, rows or [2])]})


def region_file(tmp_path: Path, blueprint, name: str = "region-1.txt") -> Path:
    path = tmp_path / name
    path.write_text(blueprint.to_string() + "\n", encoding="utf-8-sig")
    return path


def rel(box: library.Box, port: dict) -> list[int]:
    return [port["x"] - box.left, port["y"] - box.top]


# --------------------------------------------------------------------------
# names
# --------------------------------------------------------------------------


class TestNames:
    @pytest.mark.parametrize("name", ["engines", "engine-line-2", "3d", "a", "x" * 64])
    def test_a_plain_name_is_accepted(self, name: str, tmp_path: Path) -> None:
        assert library.check_name(name) == name
        assert library.path_of(tmp_path, name) == tmp_path / f"{name}.json"

    @pytest.mark.parametrize("name", [
        "", "..", "../evil", "a/b", "a\\b", "..\\..\\evil", "/abs", "C:evil", "C:\\evil", "Engines", "a b", "a.b", "x.json",
        "-x", "x-", "a--b", "é", "x" * 65, "con", "nul", "com1", "lpt9", "a\x00b", "a\nb", "~", "a:b",
    ])
    def test_a_name_that_could_be_a_path_is_refused(self, name: str, tmp_path: Path) -> None:
        with pytest.raises(library.LibraryError):
            library.check_name(name)
        with pytest.raises(library.LibraryError):
            library.path_of(tmp_path, name)

    def test_the_commands_refuse_such_a_name_and_write_nothing(self, profile: Profile, tmp_path: Path, capsys) -> None:
        result = built((1, 0, 1, 0))
        source = region_file(tmp_path, result.blueprint)
        before = {p for p in tmp_path.rglob("*")}
        for name in ("../evil", "..\\evil", "a/b", "CON"):
            assert cli.main(["library", "add", name, str(source)]) == 1
            assert cli.main(["library", "show", name]) == 1
            assert cli.main(["library", "remove", name]) == 1
        assert "not a library name" in capsys.readouterr().err
        assert {p for p in tmp_path.rglob("*")} == before


# --------------------------------------------------------------------------
# ports read from a blueprint
# --------------------------------------------------------------------------


class TestPortsFromABlueprint:
    def test_a_block_with_a_belt_in_and_a_belt_out_has_exactly_those_ports(self, profile: Profile, tmp_path: Path) -> None:
        result = built((1, 0, 1, 0))
        folder = profile.library_dir
        entry = library.add_blueprint(folder, "belt-block", str(region_file(tmp_path, result.blueprint)))
        box = library.bounding_box(list(result.blueprint.entities))
        assert entry["size"] == [result.blocks[0].width, result.blocks[0].height]
        planned = sorted(result.blocks[0].ports, key=lambda p: p["io"] != "in")
        found = entry["ports"]
        assert [(p["kind"], p["dir"]) for p in found] == [(p["kind"], p["io"]) for p in planned] == [("belt", "in"), ("belt", "out")]
        for mine, theirs in zip(found, planned):
            assert mine["at"] == rel(box, theirs)
            assert mine["direction"] == theirs["direction"]
            # lane by lane: a port with its two lanes swapped is a different port. An in port is told by what the
            # machines take (inserters pick from either lane), so there both lanes hold the union.
            lanes_of = lambda items: [set(i.split("+")) - {""} for i in items]  # noqa: E731
            if mine["dir"] == "in":
                union = set().union(*lanes_of(theirs["items"]))
                assert lanes_of(mine["items"]) == [union, union]
            else:
                assert lanes_of(mine["items"]) == lanes_of(theirs["items"])
            assert mine["rate"] == pytest.approx(theirs["rate"])
        assert [p["id"] for p in found] == ["belt-in-1", "belt-out-1"]
        assert entry["source"] == "player"
        assert "region-1.txt" in entry["found_from"]
        # the lines of the block that merely end at the edge are said to be, not made ports
        assert any("tail of an input line" in n for n in entry["notes"])

    def test_the_shape_counts_the_recipes_goods(self, profile: Profile, tmp_path: Path) -> None:
        recipe, machine = prototypes.crafting_setup(1, 1, 1, 0)
        result = plan.build({"blocks": [block_spec(recipe, machine, [2])]})
        entry = library.add_blueprint(profile.library_dir, "shape", str(region_file(tmp_path, result.blueprint)))
        shape = entry["shape"]
        assert (shape["machine"], shape["recipe"], shape["machines"]) == (machine, recipe, 2)
        assert (shape["solid_in"], shape["fluid_in"], shape["solid_out"], shape["fluid_out"]) == (1, 1, 1, 0)
        assert shape["tiers"]["machine"] == machine and shape["tiers"]["belt"] == prototypes.fastest_belt()

    def test_a_pipe_line_ending_on_the_edge_is_a_pipe_port_with_the_recipes_fluid(self, profile: Profile, tmp_path: Path) -> None:
        result = built((1, 1, 1, 0))
        recipe = result.blocks[0].recipe
        from draftsman.data import recipes
        (fluid,) = [p["name"] for p in recipes.raw[recipe]["ingredients"] if p.get("type") == "fluid"]
        entry = library.add_blueprint(profile.library_dir, "pipe-block", str(region_file(tmp_path, result.blueprint)))
        pipes = [p for p in entry["ports"] if p["kind"] == "pipe"]
        assert len(pipes) == 1
        (pipe,) = pipes
        assert (pipe["dir"], pipe["items"]) == ("in", [fluid])
        box = library.bounding_box(list(result.blueprint.entities))
        (planned,) = [p for p in result.blocks[0].ports if p["kind"] == "pipe"]
        # a line along the block has two ends: the plan's port is one of them
        assert rel(box, planned) in [pipe["at"], *pipe.get("also_at", [])]
        assert pipe["rate"] > 0
        assert [p["kind"] for p in entry["ports"]].count("belt") == 2

    def test_an_output_fluid_is_an_out_port(self, profile: Profile, tmp_path: Path) -> None:
        try:
            result = built((1, 1, 0, 1))
        except pytest.skip.Exception:
            pytest.skip("the active data has no recipe taking a solid and a fluid and making only a fluid")
        entry = library.add_blueprint(profile.library_dir, "fluid-out", str(region_file(tmp_path, result.blueprint)))
        assert sorted((p["dir"], p["kind"]) for p in entry["ports"] if p["kind"] == "pipe") == [("in", "pipe"), ("out", "pipe")]

    def test_a_block_with_nothing_crossing_its_edge_says_so(self, profile: Profile, tmp_path: Path) -> None:
        from draftsman.blueprintable import Blueprint

        recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
        blueprint = Blueprint()
        blueprint.entities.append(machine, tile_position=(0, 0), recipe=recipe)
        entry = library.add_blueprint(profile.library_dir, "alone", str(region_file(tmp_path, blueprint)))
        assert entry["ports"] == [] and entry["size"] == [3, 3]
        assert any("no ports" in n for n in entry["notes"])

    def test_a_file_whose_name_starts_with_zero_is_read_as_a_file(self, profile: Profile, tmp_path: Path) -> None:
        source = region_file(tmp_path, built((1, 0, 1, 0)).blueprint, "0-smelter.txt")
        entry = library.add_blueprint(profile.library_dir, "zero", str(source))
        assert entry["found_from"] == "blueprint file 0-smelter.txt" and len(entry["ports"]) == 2
        nested = tmp_path / "0"
        nested.mkdir()
        entry = library.add_blueprint(profile.library_dir, "zero-dir", str(region_file(nested, built((1, 0, 1, 0)).blueprint)))
        assert "region-1.txt" in entry["found_from"]

    def test_a_blueprint_string_works_as_well_as_a_file(self, profile: Profile) -> None:
        result = built((1, 0, 1, 0))
        entry = library.add_blueprint(profile.library_dir, "from-string", result.blueprint.to_string())
        assert len(entry["ports"]) == 2 and entry["found_from"] == "a blueprint string"

    def test_the_blueprint_in_the_entry_is_the_block_alone(self, profile: Profile, tmp_path: Path) -> None:
        from draftsman.blueprintable import get_blueprintable_from_string

        result = built((1, 0, 1, 0))
        entry = library.add_blueprint(profile.library_dir, "alone-again", str(region_file(tmp_path, result.blueprint)))
        again = get_blueprintable_from_string(entry["blueprint"])
        assert len(again.entities) == len(result.blueprint.entities)

    def test_a_book_needs_an_index_and_garbage_is_explained(self, profile: Profile, tmp_path: Path) -> None:
        from draftsman.blueprintable import BlueprintBook

        result = built((1, 0, 1, 0))
        book = BlueprintBook()
        book.blueprints.append(result.blueprint)
        path = tmp_path / "book.txt"
        path.write_text(book.to_string(), encoding="utf-8")
        with pytest.raises(library.LibraryError, match="--index"):
            library.add_blueprint(profile.library_dir, "book", str(path))
        assert library.add_blueprint(profile.library_dir, "book", str(path), index=0)["name"] == "book"
        with pytest.raises(library.LibraryError, match="not a blueprint string"):
            library.add_blueprint(profile.library_dir, "junk", "0eNotAString")
        with pytest.raises(library.LibraryError, match="neither"):
            library.add_blueprint(profile.library_dir, "nofile", str(tmp_path / "missing.txt"))


def swapped(blueprint, tile: tuple[int, int], name: str, **fields):
    """A copy of the blueprint with the belt on `tile` replaced by another entity."""
    from draftsman.blueprintable import get_blueprintable_from_string

    copy = get_blueprintable_from_string(blueprint.to_string())
    old = next(e for e in copy.entities if (int(e.tile_position.x), int(e.tile_position.y)) == tile and e.type == "transport-belt")
    copy.entities.remove(old)
    copy.entities.append(name, tile_position=tile, **fields)
    return copy


def ports_of(blueprint) -> list[tuple]:
    entities = list(blueprint.entities)
    found = library.find_ports(entities, library.bounding_box(entities), [])
    return [(p["kind"], p["dir"], tuple(p["at"]), p["direction"], tuple(p["items"]), p["rate"]) for p in found]


class TestEdgesThatAreNotPlainBelts:
    def test_an_underground_entrance_at_the_out_port_and_an_exit_at_the_in_port_keep_the_ports(self) -> None:
        result = built((1, 0, 1, 0))
        base = ports_of(result.blueprint)
        assert [p[:2] for p in base] == [("belt", "in"), ("belt", "out")]
        (_, _, in_at, direction, *_), (_, _, out_at, *_) = base
        underground = prototypes.underground_belt()
        out_swapped = swapped(result.blueprint, out_at, underground, direction=direction, io_type="input")
        assert library.problems_in(out_swapped) == []
        assert ports_of(out_swapped) == base
        in_swapped = swapped(result.blueprint, in_at, underground, direction=direction, io_type="output")
        assert library.problems_in(in_swapped) == []
        assert ports_of(in_swapped) == base

    def test_a_line_running_through_the_block_is_one_in_port_and_a_note_on_its_tail(self, profile: Profile, tmp_path: Path) -> None:
        result = built((1, 0, 1, 0))
        entry = library.add_blueprint(profile.library_dir, "through", str(region_file(tmp_path, result.blueprint)))
        assert [p["dir"] for p in entry["ports"]].count("in") == 1
        assert [p["dir"] for p in entry["ports"]].count("out") == 1  # only the line the block makes
        tails = [n for n in entry["notes"] if "tail of an input line" in n]
        assert len(tails) == 1  # the line that carries on out of the block is said to, not dropped

    def test_a_splitter_at_the_edge_gives_an_out_port_for_each_of_its_halves(self) -> None:
        result = built((1, 0, 1, 0))
        base = ports_of(result.blueprint)
        (_, _, out_at, direction, *_) = base[1]
        copy = swapped(result.blueprint, out_at, prototypes.splitter(), direction=direction)
        found = ports_of(copy)
        outs = [p for p in found if p[1] == "out"]
        assert len(outs) == 2 and {p[2][0] for p in outs} == {out_at[0]} and {p[2][1] for p in outs} == {out_at[1], out_at[1] + 1}
        assert sum(p[5] for p in outs) == pytest.approx(base[1][5])
        assert [p for p in found if p[1] == "in"] == [base[0]]


# --------------------------------------------------------------------------
# the check
# --------------------------------------------------------------------------


def with_an_overlap(blueprint):
    """The same block with a chest standing on its first machine."""
    machine = next(e for e in blueprint.entities if getattr(e, "recipe", None))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        blueprint.entities.append(prototypes.chest(), tile_position=(int(machine.tile_position.x), int(machine.tile_position.y)))
    return blueprint


class TestTheCheck:
    def test_a_block_with_errors_is_refused_with_the_reasons_and_nothing_is_written(self, profile: Profile, tmp_path: Path, capsys) -> None:
        bad = with_an_overlap(built((1, 0, 1, 0)).blueprint)
        source = region_file(tmp_path, bad)
        assert cli.main(["library", "add", "broken", str(source)]) == 1
        message = capsys.readouterr().err
        assert "error(s)" in message and "overlap" in message and "not saved" in message
        assert not (profile.library_dir / "broken.json").exists()

    def test_force_saves_it_marked_as_unchecked(self, profile: Profile, tmp_path: Path) -> None:
        bad = with_an_overlap(built((1, 0, 1, 0)).blueprint)
        entry = library.add_blueprint(profile.library_dir, "broken", str(region_file(tmp_path, bad)), allow_errors=True)
        assert "error" in entry["check"] and any("overlap" in n for n in entry["notes"])

    def test_a_clean_block_carries_no_check_mark(self, profile: Profile, tmp_path: Path) -> None:
        entry = library.add_blueprint(profile.library_dir, "clean", str(region_file(tmp_path, built((1, 0, 1, 0)).blueprint)))
        assert "check" not in entry


# --------------------------------------------------------------------------
# list, show, remove; the files
# --------------------------------------------------------------------------


class TestCommands:
    def test_add_list_show_remove(self, profile: Profile, tmp_path: Path, capsys) -> None:
        source = region_file(tmp_path, built((1, 0, 1, 0)).blueprint)
        assert cli.main(["library", "add", "first", str(source), "--why", "it works"]) == 0
        assert cli.main(["library", "add", "second", str(source)]) == 0
        capsys.readouterr()
        assert cli.main(["library", "list"]) == 0
        listing = capsys.readouterr().out
        assert listing.index("first") < listing.index("second") and "player" in listing and "ports 2 (1 in, 1 out)" in listing
        assert cli.main(["library", "show", "first"]) == 0
        shown = capsys.readouterr().out
        assert "it works" in shown and "belt-in-1" in shown and "belt-out-1" in shown
        assert cli.main(["library", "remove", "first"]) == 0
        assert not (profile.library_dir / "first.json").exists() and (profile.library_dir / "second.json").exists()
        assert cli.main(["library", "show", "first"]) == 1
        assert "second" in capsys.readouterr().err  # the message lists what there is

    def test_a_name_in_use_is_kept_unless_replace_is_asked_for(self, profile: Profile, tmp_path: Path, capsys) -> None:
        source = region_file(tmp_path, built((1, 0, 1, 0)).blueprint)
        assert cli.main(["library", "add", "same", str(source)]) == 0
        assert cli.main(["library", "add", "same", str(source)]) == 1
        assert "already has" in capsys.readouterr().err
        assert cli.main(["library", "add", "same", str(source), "--replace", "--why", "again"]) == 0
        assert json.loads((profile.library_dir / "same.json").read_text(encoding="utf-8"))["why"] == "again"

    def test_the_file_has_the_agreed_fields(self, profile: Profile, tmp_path: Path) -> None:
        library.add_blueprint(profile.library_dir, "fields", str(region_file(tmp_path, built((1, 1, 1, 0)).blueprint)))
        data = json.loads((profile.library_dir / "fields.json").read_text(encoding="utf-8"))
        assert {"name", "source", "found_from", "why", "blueprint", "size", "ports", "shape"} <= set(data)
        assert data["source"] in library.SOURCES and len(data["size"]) == 2
        for port in data["ports"]:
            assert {"id", "kind", "dir", "at", "direction", "items", "rate"} <= set(port)
            assert port["kind"] in ("belt", "pipe") and port["dir"] in ("in", "out") and port["direction"] in (0, 4, 8, 12)
            assert len(port["items"]) == (2 if port["kind"] == "belt" else 1)
        assert {"machine", "recipe", "solid_in", "fluid_in", "solid_out", "fluid_out", "tiers"} <= set(data["shape"])
        assert not list(profile.library_dir.glob("*.tmp"))

    def test_an_unreadable_file_is_reported_not_fatal(self, profile: Profile, tmp_path: Path, capsys) -> None:
        library.add_blueprint(profile.library_dir, "good", str(region_file(tmp_path, built((1, 0, 1, 0)).blueprint)))
        (profile.library_dir / "bad.json").write_text("{not json", encoding="utf-8")
        (profile.library_dir / "Bad Name.json").write_text("{}", encoding="utf-8")
        assert cli.main(["library", "list"]) == 0
        captured = capsys.readouterr()
        assert "good" in captured.out and "bad.json" in captured.err

    def test_an_empty_library_and_a_missing_profile_are_said_plainly(self, profile: Profile, capsys) -> None:
        assert cli.main(["library", "list"]) == 0
        assert "empty" in capsys.readouterr().out
        assert cli.main(["library", "list", "--profile", "nobody"]) == 1
        assert "no profile named" in capsys.readouterr().err

    @pytest.mark.parametrize("name", ["../x", "..", "../..", "a/b", "a\\b", "C:x", "x\0y", " "])
    def test_a_profile_name_that_is_a_path_is_refused_and_nothing_is_written(self, profile: Profile, tmp_path: Path, capsys, name: str) -> None:
        source = region_file(tmp_path, built((1, 0, 1, 0)).blueprint)
        before = {p for p in tmp_path.rglob("*")}
        assert cli.main(["library", "add", "x", str(source), "--profile", name]) == 1
        assert cli.main(["library", "list", "--profile", name]) == 1
        assert "not a profile name" in capsys.readouterr().err
        assert {p for p in tmp_path.rglob("*")} == before

    def test_no_active_profile_is_said(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        monkeypatch.setattr(Profile, "active_profile_name", staticmethod(lambda: None))
        assert cli.main(["library", "list"]) == 1
        assert "no active profile" in capsys.readouterr().err


# --------------------------------------------------------------------------
# a block forge built
# --------------------------------------------------------------------------


def two_block_plan() -> dict:
    first, second = chained_recipes()
    return {"label": "pair", "blocks": [
        block_spec(*first, [2], id="maker"),
        block_spec(*second, [2], id="user", at=[40, 0]),
    ]}


class TestFromAPlan:
    def test_a_block_by_index_or_id_keeps_the_plans_own_ports(self, profile: Profile, tmp_path: Path, capsys) -> None:
        the_plan = two_block_plan()
        path = tmp_path / "plan.json"
        path.write_text(json.dumps(the_plan), encoding="utf-8")
        assert cli.main(["library", "add", "maker-block", "--from-plan", str(path), "--block", "maker"]) == 0
        assert cli.main(["library", "add", "user-block", "--from-plan", str(path), "--block", "1", "--why", "chosen"]) == 0
        entry = json.loads((profile.library_dir / "maker-block.json").read_text(encoding="utf-8"))
        assert entry["source"] == "approved" and "maker" in entry["found_from"]
        alone = plan.build({"blocks": [{k: v for k, v in the_plan["blocks"][0].items() if k not in ("id", "at")}]})
        box = library.bounding_box(list(alone.blueprint.entities))
        assert entry["size"] == [alone.blocks[0].width, alone.blocks[0].height]
        assert [(p["kind"], p["dir"], p["at"], p["direction"]) for p in entry["ports"]] == sorted(
            [(p["kind"], p["io"], rel(box, p), p["direction"]) for p in alone.blocks[0].ports], key=lambda t: (t[1] != "in", t[0], t[2][1], t[2][0]))
        assert json.loads((profile.library_dir / "user-block.json").read_text(encoding="utf-8"))["why"] == "chosen"

    def test_the_plan_blocks_ports_agree_with_the_ones_read_from_its_edge(self, profile: Profile, tmp_path: Path) -> None:
        the_plan = two_block_plan()
        path = tmp_path / "plan.json"
        path.write_text(json.dumps(the_plan), encoding="utf-8")
        from_plan = library.add_from_plan(profile.library_dir, "by-plan", the_plan, "maker")
        from_edge = library.add_blueprint(profile.library_dir, "by-edge", from_plan["blueprint"])
        key = lambda e: sorted((p["kind"], p["dir"], tuple(p["at"]), p["direction"]) for p in e["ports"])  # noqa: E731
        assert key(from_plan) == key(from_edge)

    def test_a_block_that_is_not_there_is_refused(self, profile: Profile, tmp_path: Path, capsys) -> None:
        path = tmp_path / "plan.json"
        path.write_text(json.dumps(two_block_plan()), encoding="utf-8")
        assert cli.main(["library", "add", "nope", "--from-plan", str(path), "--block", "9"]) == 1
        assert "no block" in capsys.readouterr().err
        assert cli.main(["library", "add", "nope", "--from-plan", str(path)]) == 1
        assert not list(profile.library_dir.glob("*.json"))


# --------------------------------------------------------------------------
# forge's own copies after a measured run
# --------------------------------------------------------------------------


def comparison(met: bool = True, groups: list | None = None, plan_data: dict | None = None, produced: dict | None = None):
    plan_data = plan_data or two_block_plan()
    groups = groups if groups is not None else [
        measure.MachineGroup(b["machine"], b["recipe"], 2, {"working": 0.8, "waiting_input": 0.1}, {}) for b in plan_data["blocks"]
    ]
    target = SimpleNamespace(item="x", measured=1.0 if met else 0.1, met=met)
    return SimpleNamespace(all_met=met, targets=[target], groups=groups, produced=produced or {})


class TestMeasuredSave:
    def test_every_target_met_offers_the_blocks_as_found_by_forge(self, profile: Profile) -> None:
        the_plan = two_block_plan()
        report = library.save_measured(the_plan, comparison(plan_data=the_plan), profile.library_dir)
        assert len(report.saved) == 2 and not report.skipped
        for name in report.saved:
            entry = library.load(profile.library_dir, name)
            assert entry["source"] == "forge" and "80%" in entry["why"] and "every target" in entry["why"]
            assert entry["measured"]["working_share"] == pytest.approx(0.8) and entry["measured"]["plan_targets"] == {"x": 1.0}
            assert entry["ports"] and entry["shape"]["recipe"]
        assert any(line.startswith("library: saved") for line in report.lines())

    def test_a_blocks_measured_rate_is_its_own_products_not_the_plans_targets(self, profile: Profile) -> None:
        from draftsman.data import recipes

        the_plan = two_block_plan()
        first, second = the_plan["blocks"]
        mine = {p["name"] for p in recipes.raw[first["recipe"]]["results"]}
        theirs = {p["name"] for p in recipes.raw[second["recipe"]]["results"]}
        if mine & theirs:
            pytest.skip("both blocks make the same good")
        produced = {**{g: 0.25 for g in mine}, **{g: 0.75 for g in theirs}}
        report = library.save_measured(the_plan, comparison(plan_data=the_plan, produced=produced), profile.library_dir)
        by_recipe = {library.load(profile.library_dir, n)["shape"]["recipe"]: library.load(profile.library_dir, n) for n in report.saved}
        assert set(by_recipe[first["recipe"]]["measured"]["rate"]) == mine
        assert set(by_recipe[second["recipe"]]["measured"]["rate"]) == theirs
        assert by_recipe[first["recipe"]]["measured"]["plan_targets"] == {"x": 1.0}

    def test_without_production_figures_the_rate_is_the_out_capacity_times_the_share(self, profile: Profile) -> None:
        the_plan = two_block_plan()
        report = library.save_measured(the_plan, comparison(plan_data=the_plan), profile.library_dir)
        assert report.saved
        for name in report.saved:
            entry = library.load(profile.library_dir, name)
            capacity = {g: r for p in entry["ports"] if p["dir"] == "out" for g, r in p.get("item_rates", {}).items()}
            assert capacity and entry["measured"]["rate"] == {g: pytest.approx(r * 0.8, abs=1e-5) for g, r in capacity.items()}

    def test_a_block_whose_machines_lacked_power_is_left_out(self, profile: Profile) -> None:
        the_plan = two_block_plan()
        first, second = the_plan["blocks"]
        if (first["machine"], first["recipe"]) == (second["machine"], second["recipe"]):
            pytest.skip("both blocks run the same recipe on the same machine")
        groups = [
            measure.MachineGroup(first["machine"], first["recipe"], 2, {"working": 0.7, "no_power": 0.3}, {}),
            measure.MachineGroup(second["machine"], second["recipe"], 2, {"working": 0.9}, {}),
        ]
        report = library.save_measured(the_plan, comparison(groups=groups, plan_data=the_plan), profile.library_dir)
        assert len(report.saved) == 1 and "power" in report.skipped[0]

    def test_a_block_with_no_measured_machines_of_its_own_is_left_out(self, profile: Profile) -> None:
        the_plan = two_block_plan()
        first, second = the_plan["blocks"]
        if (first["machine"], first["recipe"]) == (second["machine"], second["recipe"]):
            pytest.skip("both blocks run the same recipe on the same machine")
        groups = [measure.MachineGroup(second["machine"], second["recipe"], 2, {"working": 0.9}, {})]
        report = library.save_measured(the_plan, comparison(groups=groups, plan_data=the_plan), profile.library_dir)
        assert len(report.saved) == 1 and "no measured machines" in report.skipped[0]

    def test_the_same_blocks_are_not_saved_twice(self, profile: Profile) -> None:
        the_plan = two_block_plan()
        library.save_measured(the_plan, comparison(plan_data=the_plan), profile.library_dir)
        again = library.save_measured(the_plan, comparison(plan_data=the_plan), profile.library_dir)
        assert not again.saved and len(again.skipped) == 2 and "already holds" in again.skipped[0]
        assert len(library.entries(profile.library_dir)[0]) == 2

    def test_a_missed_target_saves_nothing(self, profile: Profile) -> None:
        report = library.save_measured(two_block_plan(), comparison(met=False), profile.library_dir)
        assert not report.saved and not list(profile.library_dir.glob("*.json"))

    def test_a_block_whose_machines_stood_starved_is_left_out(self, profile: Profile) -> None:
        the_plan = two_block_plan()
        first, second = the_plan["blocks"]
        groups = [
            measure.MachineGroup(first["machine"], first["recipe"], 2, {"working": 0.1, "waiting_input": 0.8}, {}),
            measure.MachineGroup(second["machine"], second["recipe"], 2, {"working": 0.9}, {}),
        ]
        if (first["machine"], first["recipe"]) == (second["machine"], second["recipe"]):
            pytest.skip("both blocks run the same recipe on the same machine")
        report = library.save_measured(the_plan, comparison(groups=groups, plan_data=the_plan), profile.library_dir)
        assert len(report.saved) == 1 and "starved" in report.skipped[0]

    def test_names_never_collide(self, profile: Profile) -> None:
        the_plan = two_block_plan()
        the_plan["blocks"][1] = {**the_plan["blocks"][0], "id": "again", "rows": [3]}
        report = library.save_measured(the_plan, comparison(plan_data=the_plan), profile.library_dir)
        assert len(set(report.saved)) == len(report.saved) == 2

    def test_the_result_command_offers_blocks_unless_told_not_to(self, profile: Profile, tmp_path: Path, monkeypatch, capsys) -> None:
        from test_measure import answer, write

        the_plan = two_block_plan()
        plan_path = tmp_path / "chain.json"
        plan_path.write_text(json.dumps(the_plan), encoding="utf-8")
        spec = tmp_path / "chain.measure-spec.json"
        spec.write_text(json.dumps({"targets": [{"item": "gear", "rate": 0.5}]}), encoding="utf-8")
        result = write(tmp_path, answer())
        seen = []
        monkeypatch.setattr(library, "save_measured", lambda p, c, folder=None, found_from=None: seen.append((p, c, found_from)) or library.SaveReport(["demo"]))
        assert cli.main(["measure", "result", str(result), "--spec", str(spec), "--no-library"]) == 0
        assert not seen
        assert cli.main(["measure", "result", str(result), "--spec", str(spec)]) == 0
        assert len(seen) == 1 and seen[0][0] == the_plan and "chain.json" in seen[0][2]
        assert "library: saved demo" in capsys.readouterr().out
        # a short result offers nothing
        short = write(tmp_path, answer(outputs=[{"at": [4, 6], "delivered": {"gear": 0.1}}]))
        assert cli.main(["measure", "result", str(short), "--spec", str(spec)]) == 1
        assert len(seen) == 1

    def test_without_a_known_plan_it_says_so_and_saves_nothing(self, profile: Profile, tmp_path: Path, capsys) -> None:
        from test_measure import answer, write

        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps({"targets": [{"item": "gear", "rate": 0.5}]}), encoding="utf-8")
        assert cli.main(["measure", "result", str(write(tmp_path, answer())), "--spec", str(spec)]) == 0
        assert "no plan known" in capsys.readouterr().err
        assert not list(profile.library_dir.glob("*.json"))
