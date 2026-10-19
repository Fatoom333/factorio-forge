"""Tests for handing a blueprint over only after the game has placed it.

The game is never started: the runner's subprocess is replaced by a function
that writes the files the runner would, and the build is replaced by a stand-in
result, so nothing here depends on a profile's recipes or on Factorio.
"""

from __future__ import annotations

import ast
import json
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from factorio_forge import cli, ingame, paths, plan

# What a mod could name an entity: a newline, both kinds of quote, a comment and code.
# It is only ever data in these tests (written as a literal, never run).
HOSTILE = "evil\n'\"\"\"; import os; os.system('x') #\r\x1b[31m"


def verdict_of(**report) -> ingame.Verdict:
    return ingame.judge({"placed": 3, "expected": 3, "missing": [], "refused": [], **report})


# --------------------------------------------------------------------------
# the verdict
# --------------------------------------------------------------------------


class TestJudge:
    def test_everything_placed_is_verified(self) -> None:
        v = verdict_of()
        assert v.status == "verified" and v.delivered

    def test_an_empty_lua_table_arrives_as_a_list_and_means_none(self) -> None:
        assert ingame.judge({"placed": 1, "expected": 1, "missing": [], "refused": []}).status == "verified"

    def test_a_refused_entity_withholds(self) -> None:
        v = verdict_of(placed=2, refused={"some-machine": 1})
        assert v.status == "refused" and not v.delivered and v.refused == {"some-machine": 1}

    def test_a_missing_prototype_withholds(self) -> None:
        assert verdict_of(placed=2, missing={"gone": 1}).status == "refused"

    def test_fewer_placed_than_expected_withholds_even_when_nothing_is_named(self) -> None:
        assert verdict_of(placed=2).status == "refused"

    def test_the_game_reading_fewer_entities_than_forge_wrote_withholds(self) -> None:
        # An entity the game does not know is dropped when it reads the string.
        v = ingame.judge({"placed": 3, "expected": 3, "missing": [], "refused": []}, entities=4)
        assert v.status == "refused" and v.notes

    @pytest.mark.parametrize("report", [
        None, [], "ok", {}, {"placed": 3}, {"placed": "3", "expected": 3},
        {"placed": True, "expected": True}, {"placed": -1, "expected": 3},
    ])
    def test_a_report_that_cannot_be_read_never_passes(self, report) -> None:
        v = ingame.judge(report)
        assert v.status == "failed" and not v.delivered

    def test_odd_tables_are_ignored_not_trusted(self) -> None:
        v = verdict_of(placed=2, refused={"a": "many", 5: 1, "b": -2, "c": True, "ok": 2})
        assert v.refused == {"ok": 2}


class TestDescribe:
    def test_a_hostile_name_is_shown_escaped_on_one_line(self) -> None:
        v = verdict_of(placed=2, refused={HOSTILE: 1}, missing={HOSTILE + "2": 2})
        lines = ingame.describe(v)
        text = "\n".join(lines)
        assert "\x1b" not in text and "\r" not in text
        assert all("\n" not in line for line in lines)
        assert len(lines) == 3  # the header and one line per table

    def test_not_checked_says_why_plainly(self) -> None:
        (line,) = ingame.describe(ingame.Verdict("not-checked", "Factorio is not installed"))
        assert line == "not checked in the game: Factorio is not installed"


# --------------------------------------------------------------------------
# the failing case as a test
# --------------------------------------------------------------------------


class TestStub:
    PLAN = {"label": HOSTILE, "blocks": [{"recipe": HOSTILE, "at": [0, 0], "ok": True, "none": None}]}

    def verdict(self) -> ingame.Verdict:
        return verdict_of(placed=1, refused={HOSTILE: 2}, missing={"q'\"": 1})

    def test_the_stub_is_python_that_reads_back_as_the_case(self) -> None:
        v = self.verdict()
        source = ingame.test_stub(self.PLAN, v)
        ast.parse(source)
        namespace: dict = {}
        exec(compile(source.split("\n\n\ndef ")[0].replace("from factorio_forge import plan as plan_module\n", ""),
                     "<stub>", "exec"), namespace)
        assert namespace["PLAN"] == self.PLAN
        assert namespace["GAME_REFUSED"] == {HOSTILE: 2}
        assert namespace["GAME_MISSING"] == {"q'\"": 1}
        assert ingame.stub_reads_back(source, self.PLAN, v)

    def test_no_game_text_reaches_a_comment_or_the_code_around_the_literals(self) -> None:
        source = ingame.test_stub(self.PLAN, self.verdict())
        tree = ast.parse(source)
        # Nothing but the literals carries the names: removing the three
        # assignments leaves text without a trace of them.
        rest = "\n".join(
            line for line in source.splitlines()
            if not line.startswith(("PLAN = ", "GAME_MISSING = ", "GAME_REFUSED = ")))
        assert "evil" not in rest and "import os" not in rest
        assert [n.names[0].name for n in tree.body if isinstance(n, ast.ImportFrom)] == ["plan"]
        assert "\n'" not in source and "\x1b" not in source and "\r" not in source

    def test_a_stub_that_does_not_read_back_is_caught(self) -> None:
        v = self.verdict()
        assert not ingame.stub_reads_back("PLAN = {", self.PLAN, v)
        assert not ingame.stub_reads_back(ingame.test_stub(self.PLAN, v), {"other": 1}, v)

    def test_the_stub_asks_the_model_to_see_a_problem(self) -> None:
        source = ingame.test_stub({}, verdict_of(placed=0, refused={"x": 1}))
        assert "result.problems" in source


# --------------------------------------------------------------------------
# the companion zip and the setup
# --------------------------------------------------------------------------


@pytest.fixture
def companion(tmp_path: Path) -> Path:
    root = tmp_path / "companion"
    (root / "tools").mkdir(parents=True)
    (root / "locale" / "en").mkdir(parents=True)
    (root / "info.json").write_text(json.dumps({"name": "demo-mod", "version": "1.2.3"}))
    (root / "control.lua").write_text("-- lua")
    (root / "locale" / "en" / "strings.cfg").write_text("[forge]")
    (root / "tools" / "headless-run.ps1").write_text("# runner")
    (root / "tools" / "lint.py").write_text("# not shipped")
    return root


class TestPack:
    def test_the_zip_has_one_folder_with_forward_slashes(self, companion: Path, tmp_path: Path) -> None:
        archive = ingame.pack_companion(companion, tmp_path)
        assert archive.name == "demo-mod_1.2.3.zip"
        names = zipfile.ZipFile(archive).namelist()
        assert "demo-mod_1.2.3/info.json" in names and "demo-mod_1.2.3/locale/en/strings.cfg" in names
        assert all("\\" not in n and n.startswith("demo-mod_1.2.3/") for n in names)
        assert not any("tools" in n for n in names)

    @pytest.mark.parametrize("info", ["not json", json.dumps({"name": "../x", "version": "1.0.0"}),
                                      json.dumps({"name": "ok", "version": "1.0"})])
    def test_an_odd_info_json_is_not_packed(self, companion: Path, tmp_path: Path, info: str) -> None:
        (companion / "info.json").write_text(info)
        with pytest.raises(ingame.NotChecked):
            ingame.pack_companion(companion, tmp_path)


@pytest.fixture
def world(companion: Path, tmp_path: Path, monkeypatch):
    """A machine that can run the check: Windows, a shell, a game, a save."""
    saves = tmp_path / "saves"
    saves.mkdir()
    (saves / "Mine.zip").write_bytes(b"zip")
    binary = tmp_path / "factorio.exe"
    binary.write_text("")
    monkeypatch.setattr(ingame.sys, "platform", "win32")
    monkeypatch.setattr(ingame.shutil, "which", lambda name: str(tmp_path / "powershell.exe"))
    monkeypatch.setattr(paths, "factorio_binary", lambda: binary)
    monkeypatch.setattr(paths, "saves_dir", lambda: saves)
    monkeypatch.setattr(paths, "factorio_user_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "load_config", lambda: {})
    monkeypatch.delenv(ingame.ENV_COMPANION, raising=False)
    monkeypatch.delenv(ingame.ENV_RUNNER, raising=False)
    return SimpleNamespace(companion=companion, saves=saves, binary=binary)


class TestLocate:
    def test_the_environment_variable_names_the_companion(self, world, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(ingame.ENV_COMPANION, str(world.companion))
        setup = ingame.locate("Mine", work=tmp_path)
        assert setup.runner == world.companion / "tools" / "headless-run.ps1"
        assert setup.save == world.saves / "Mine.zip" and setup.mod_zip.name == "demo-mod_1.2.3.zip"

    def test_the_config_file_works_too(self, world, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(paths, "load_config", lambda: {"companion": str(world.companion)})
        assert ingame.locate("Mine", work=tmp_path).runner.name == "headless-run.ps1"

    def test_a_zip_needs_the_runner_named_separately(self, world, tmp_path, monkeypatch) -> None:
        archive = ingame.pack_companion(world.companion, tmp_path)
        monkeypatch.setenv(ingame.ENV_COMPANION, str(archive))
        with pytest.raises(ingame.NotChecked, match=ingame.ENV_RUNNER):
            ingame.locate("Mine", work=tmp_path)
        monkeypatch.setenv(ingame.ENV_RUNNER, str(world.companion / "tools" / "headless-run.ps1"))
        assert ingame.locate("Mine", work=tmp_path).mod_zip == archive

    @pytest.mark.parametrize("what", ["platform", "no-shell", "no-companion", "no-game", "no-save", "no-profile-save"])
    def test_each_missing_piece_is_a_plain_reason(self, world, tmp_path, monkeypatch, what) -> None:
        monkeypatch.setenv(ingame.ENV_COMPANION, str(world.companion))
        save = "Mine"
        if what == "platform":
            monkeypatch.setattr(ingame.sys, "platform", "linux")
        elif what == "no-shell":
            monkeypatch.setattr(ingame.shutil, "which", lambda name: None)
        elif what == "no-companion":
            monkeypatch.delenv(ingame.ENV_COMPANION)
        elif what == "no-game":
            monkeypatch.setattr(paths, "factorio_binary", lambda: None)
        elif what == "no-save":
            save = "Missing"
        else:
            save = None
        with pytest.raises(ingame.NotChecked) as caught:
            ingame.locate(save, work=tmp_path)
        assert str(caught.value) and "\n" not in str(caught.value)


class TestCheckInGame:
    def runner(self, world, monkeypatch, *, report=None, code=0, seen=None, raises=None):
        monkeypatch.setenv(ingame.ENV_COMPANION, str(world.companion))

        def run(cmd, **kwargs):
            if seen is not None:
                seen.append((cmd, kwargs))
            if raises:
                raise raises
            out = Path(cmd[cmd.index("-Out") + 1])
            if report is not None:
                (out / "verify.json").write_text(json.dumps(report), encoding="utf-8")
            return subprocess.CompletedProcess(cmd, code, "", "stderr line\nlast line")
        return run

    def test_good_report_is_verified_and_the_call_is_an_argument_list(self, world, monkeypatch) -> None:
        seen: list = []
        run = self.runner(world, monkeypatch, report={"placed": 5, "expected": 5, "missing": [], "refused": []}, seen=seen)
        v = ingame.check_in_game("0eJx", entities=5, profile_save="Mine", run=run)
        assert v.status == "verified"
        (cmd, kwargs), = seen
        assert isinstance(cmd, list) and all(isinstance(part, str) for part in cmd)
        assert not kwargs.get("shell") and kwargs["timeout"] > ingame.DEFAULT_TIMEOUT_S
        assert cmd[cmd.index("-Save") + 1].endswith("Mine.zip")
        assert cmd[cmd.index("-TimeoutSec") + 1] == str(ingame.DEFAULT_TIMEOUT_S)
        for flag in ("-Blueprint", "-ModZip", "-Out"):
            assert flag in cmd

    def test_the_blueprint_goes_by_file_not_on_the_command_line(self, world, monkeypatch) -> None:
        seen: list = []
        run = self.runner(world, monkeypatch, report={"placed": 1, "expected": 1}, seen=seen)
        ingame.check_in_game("SECRET-BLUEPRINT-TEXT", entities=1, profile_save="Mine", run=run)
        assert all("SECRET-BLUEPRINT-TEXT" not in part for part in seen[0][0])

    def test_a_refusal_is_a_refusal(self, world, monkeypatch) -> None:
        run = self.runner(world, monkeypatch, report={"placed": 1, "expected": 2, "missing": {}, "refused": {"x": 1}})
        v = ingame.check_in_game("bp", entities=2, profile_save="Mine", run=run)
        assert v.status == "refused" and v.refused == {"x": 1}

    def test_factorio_already_running_skips_the_check(self, world, monkeypatch) -> None:
        v = ingame.check_in_game("bp", entities=1, profile_save="Mine", run=self.runner(world, monkeypatch, code=2))
        assert v.status == "not-checked" and "already running" in v.reason

    def test_a_runner_that_stops_is_a_failure_not_a_pass(self, world, monkeypatch) -> None:
        v = ingame.check_in_game("bp", entities=1, profile_save="Mine", run=self.runner(world, monkeypatch, code=1))
        assert v.status == "failed" and "last line" in v.reason and not v.delivered

    def test_exit_zero_without_a_result_file_is_a_failure(self, world, monkeypatch) -> None:
        v = ingame.check_in_game("bp", entities=1, profile_save="Mine", run=self.runner(world, monkeypatch))
        assert v.status == "failed"

    def test_a_timeout_is_a_failure(self, world, monkeypatch) -> None:
        run = self.runner(world, monkeypatch, raises=subprocess.TimeoutExpired("x", 1))
        assert ingame.check_in_game("bp", entities=1, profile_save="Mine", run=run).status == "failed"

    def test_nothing_set_up_never_starts_a_process(self, world, monkeypatch) -> None:
        def run(*a, **k):
            raise AssertionError("must not run")
        v = ingame.check_in_game("bp", entities=1, profile_save="Mine", run=run)
        assert v.status == "not-checked" and ingame.ENV_COMPANION in v.reason


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------


class FakeBlueprint:
    entities = [1, 2, 3]

    def to_string(self) -> str:
        return "0eJxfake"


@pytest.fixture
def built(monkeypatch):
    """`plan.build` replaced by a result that needs no game data."""
    state = SimpleNamespace(problems=[], routes=[])

    def build(data, planned=None):
        return SimpleNamespace(label="Demo plan", blueprint=FakeBlueprint(), power_networks=1,
                               problems=state.problems, routes=state.routes, findings=[],
                               to_dict=lambda: {"label": "Demo plan"})
    monkeypatch.setattr(plan, "load", lambda path: {"label": "Demo plan"})
    monkeypatch.setattr(plan, "build", build)
    monkeypatch.setattr(ingame, "_profile_save", lambda: "Mine")
    return state


def run_deliver(tmp_path: Path, verdict: ingame.Verdict | None, *extra: str) -> int:
    calls = []

    def check(blueprint, **kwargs):
        calls.append((blueprint, kwargs))
        return verdict
    plan_file = tmp_path / "plan.json"
    plan_file.write_text("{}")
    args = cli.build_parser().parse_args(["deliver", str(plan_file), "--no-render", *extra])
    code = ingame.deliver(args, check=check)
    run_deliver.calls = calls
    return code


class TestDeliver:
    def test_placed_everything_writes_the_blueprint(self, tmp_path, built, capsys) -> None:
        assert run_deliver(tmp_path, verdict_of()) == 0
        assert (tmp_path / "Demo-plan.txt").read_text() == "0eJxfake"
        assert not (tmp_path / "Demo-plan.unverified.txt").exists()
        assert "checked in the game: all 3 of 3" in capsys.readouterr().out
        assert run_deliver.calls[0][1]["entities"] == 3

    def test_a_refusal_withholds_and_writes_the_stub(self, tmp_path, built, capsys) -> None:
        v = verdict_of(placed=2, refused={HOSTILE: 1})
        assert run_deliver(tmp_path, v) == 1
        assert not (tmp_path / "Demo-plan.txt").exists()
        assert (tmp_path / "Demo-plan.unverified.txt").read_text() == "0eJxfake"
        stub = (tmp_path / "Demo-plan.game-refused-test.py").read_text()
        assert ingame.stub_reads_back(stub, {"label": "Demo plan"}, v)
        out = capsys.readouterr().out
        assert "REFUSED" in out and "NOT DELIVERED" in out
        assert "\x1b" not in out and "\r" not in out
        report = json.loads((tmp_path / "Demo-plan.report.json").read_text())
        assert report["game_check"]["status"] == "refused"

    def test_not_checked_delivers_as_before_and_says_so(self, tmp_path, built, capsys) -> None:
        assert run_deliver(tmp_path, ingame.Verdict("not-checked", "Factorio is not installed here")) == 0
        assert (tmp_path / "Demo-plan.txt").exists()
        assert "not checked in the game: Factorio is not installed here" in capsys.readouterr().out

    def test_a_failed_run_withholds(self, tmp_path, built) -> None:
        assert run_deliver(tmp_path, ingame.Verdict("failed", "the runner stopped")) == 1
        assert (tmp_path / "Demo-plan.unverified.txt").exists() and not (tmp_path / "Demo-plan.txt").exists()

    def test_build_problems_stop_before_the_game_is_asked(self, tmp_path, built, capsys) -> None:
        built.problems.append(SimpleNamespace(summary="belt crosses a machine"))
        assert run_deliver(tmp_path, verdict_of()) == 1
        assert run_deliver.calls == []
        assert not list(tmp_path.glob("Demo-plan*"))
        assert "belt crosses a machine" in capsys.readouterr().out

    def test_a_failed_route_stops_too(self, tmp_path, built) -> None:
        built.routes.append(SimpleNamespace(ok=False, id=7, reason="no way through"))
        assert run_deliver(tmp_path, verdict_of()) == 1 and run_deliver.calls == []

    def test_the_explicit_skip_is_said_aloud(self, tmp_path, built, capsys) -> None:
        assert run_deliver(tmp_path, None, "--no-game-check") == 0
        assert run_deliver.calls == []
        assert "not checked in the game: skipped with --no-game-check" in capsys.readouterr().out

    def test_a_later_good_run_removes_the_stale_refusal(self, tmp_path, built) -> None:
        run_deliver(tmp_path, verdict_of(placed=1, refused={"x": 1}))
        assert (tmp_path / "Demo-plan.unverified.txt").exists()
        assert run_deliver(tmp_path, verdict_of()) == 0
        assert not (tmp_path / "Demo-plan.unverified.txt").exists()

    def test_a_refusal_warns_about_an_older_delivered_file(self, tmp_path, built, capsys) -> None:
        run_deliver(tmp_path, verdict_of())
        capsys.readouterr()
        assert run_deliver(tmp_path, verdict_of(placed=1, refused={"x": 1})) == 1
        assert "from an earlier build" in capsys.readouterr().out
