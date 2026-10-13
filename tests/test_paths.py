"""Tests for path resolution.

These deliberately avoid depending on a real Factorio installation: they must
pass on a contributor's machine and in CI, where no game is present. What is
tested is the resolution *policy* — that an explicit override wins, that the
platform default is used otherwise, and that nothing ever resolves into the
repository itself.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from factorio_forge import paths


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from a machine with none of our variables set."""
    for name in (paths.ENV_HOME, paths.ENV_GAME, paths.ENV_USER):
        monkeypatch.delenv(name, raising=False)


class TestForgeHome:
    def test_environment_override_wins(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "elsewhere"))
        assert paths.forge_home() == tmp_path / "elsewhere"

    def test_override_expands_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(paths.ENV_HOME, "~/forge-data")
        assert paths.forge_home() == Path.home() / "forge-data"

    def test_platform_default_when_unset(self) -> None:
        home = paths.forge_home()
        assert home.name == paths.APP_DIRNAME
        if sys.platform == "win32":
            assert "AppData" in str(home) or "APPDATA" in str(home).upper()
        elif sys.platform == "darwin":
            assert "Application Support" in str(home)
        else:
            assert ".local/share" in home.as_posix() or "XDG" not in str(home)

    def test_default_is_outside_the_repository(self) -> None:
        """Local data must never land inside a checkout."""
        repo_root = Path(paths.__file__).resolve().parents[2]
        assert repo_root not in paths.forge_home().resolve().parents

    def test_derived_paths_sit_under_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv(paths.ENV_HOME, str(tmp_path))
        assert paths.profiles_root() == tmp_path / "profiles"
        assert paths.cache_root() == tmp_path / "cache"
        assert paths.config_path() == tmp_path / paths.CONFIG_FILENAME
        assert paths.profile_dir("krastorio2") == tmp_path / "profiles" / "krastorio2"


class TestLayoutAndConfig:
    def test_ensure_layout_is_idempotent(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
        first = paths.ensure_layout()
        second = paths.ensure_layout()
        assert first == second
        assert paths.profiles_root().is_dir()
        assert paths.cache_root().is_dir()

    def test_config_round_trip(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv(paths.ENV_HOME, str(tmp_path))
        paths.save_config({"factorio_version": "2.0.77", "note": "кириллица"})
        assert paths.load_config() == {"factorio_version": "2.0.77", "note": "кириллица"}

    def test_missing_config_reads_as_empty(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "nothing-here"))
        assert paths.load_config() == {}

    def test_corrupt_config_reads_as_empty(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv(paths.ENV_HOME, str(tmp_path))
        paths.ensure_layout()
        paths.config_path().write_text("{ this is not json", encoding="utf-8")
        assert paths.load_config() == {}


class TestGameDetection:
    def test_install_override_must_exist(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv(paths.ENV_GAME, str(tmp_path / "no-such-dir"))
        assert paths.factorio_install_dir() is None

    def test_install_override_is_used(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        fake = tmp_path / "Factorio"
        fake.mkdir()
        monkeypatch.setenv(paths.ENV_GAME, str(fake))
        assert paths.factorio_install_dir() == fake

    def test_user_dir_override_must_exist(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv(paths.ENV_USER, str(tmp_path / "absent"))
        assert paths.factorio_user_dir() is None

    def test_subdirectories_follow_the_user_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        user_dir = tmp_path / "Factorio"
        user_dir.mkdir()
        monkeypatch.setenv(paths.ENV_USER, str(user_dir))
        assert paths.saves_dir() == user_dir / "saves"
        assert paths.mods_dir() == user_dir / "mods"
        assert paths.script_output_dir() == user_dir / "script-output"

    def test_missing_game_yields_none_not_a_crash(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv(paths.ENV_USER, str(tmp_path / "absent"))
        assert paths.saves_dir() is None
        assert paths.mods_dir() is None
        assert paths.script_output_dir() is None


class TestDiagnostics:
    def test_resolve_all_reports_every_key(self) -> None:
        resolved = paths.resolve_all()
        expected = {
            "forge_home",
            "config",
            "profiles",
            "cache",
            "factorio_install_dir",
            "factorio_binary",
            "factorio_data_dir",
            "factorio_user_dir",
            "saves_dir",
            "mods_dir",
            "script_output_dir",
            "factorio_version",
        }
        assert set(resolved) == expected

    def test_resolve_all_values_are_strings_or_none(self) -> None:
        for value in paths.resolve_all().values():
            assert value is None or isinstance(value, str)


def write_log(user_dir: Path, install: Path, write_data: Path) -> Path:
    """A factorio-current.log with the lines the game writes at the top."""
    log = user_dir / paths.LOG_FILENAME
    log.write_text(
        "   0.001 2026-10-02 23:47:17; Factorio 2.0.77 (build 84539, win64, steam, space-age)\n"
        f"   0.001 Read data path: {(install / 'data').as_posix()}\n"
        f"   0.001 Write data path: {write_data.as_posix()} [133969/957793MB]\n"
        f"   0.001 Binaries path: {(install / 'bin').as_posix()}\n",
        encoding="utf-8",
    )
    return log


class TestSources:
    """`paths` says where each answer came from, so a wrong pick can be traced."""

    @pytest.fixture
    def game(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
        monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "forge"))
        install = tmp_path / "Factorio"
        (install / "data" / "base").mkdir(parents=True)
        (install / "data" / "base" / "info.json").write_text("{}", encoding="utf-8")
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        monkeypatch.setenv(paths.ENV_USER, str(user_dir))
        return install, user_dir

    def test_log_facts_are_read(self, tmp_path: Path) -> None:
        log = write_log(tmp_path, Path("/games/Factorio"), Path("/data/Factorio"))
        facts = paths.read_log(log)
        assert facts["version"] == "2.0.77"
        assert facts["write_data"] == "/data/Factorio"
        assert facts["binaries"] == "/games/Factorio/bin"

    def test_each_root_names_its_source(self, game: tuple[Path, Path]) -> None:
        install, user_dir = game
        write_log(user_dir, install, user_dir)

        assert paths.factorio_install_dir() == install
        sources = paths.resolve_sources()
        assert sources["forge_home"] == f"set by {paths.ENV_HOME}"
        assert sources["factorio_user_dir"] == f"set by {paths.ENV_USER}"
        assert sources["factorio_install_dir"].startswith(
            f"Binaries path from {paths.LOG_FILENAME} of "
        )
        assert sources["factorio_version"].startswith(f"from {paths.LOG_FILENAME} of ")

    def test_default_user_dir_says_it_is_the_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "forge"))
        default = tmp_path / "Factorio"
        (default / "saves").mkdir(parents=True)
        monkeypatch.setattr(
            paths, "_default_user_dirs", lambda: iter([(default, "default location, test")])
        )
        assert paths.factorio_user_dir() == default
        assert paths.resolve_sources()["factorio_user_dir"] == "default location, test"

    def test_a_log_pointing_elsewhere_is_reported(
        self, game: tuple[Path, Path], tmp_path: Path
    ) -> None:
        install, user_dir = game
        elsewhere = tmp_path / "other-drive" / "Factorio"
        elsewhere.mkdir(parents=True)
        write_log(user_dir, install, elsewhere)
        assert paths.write_data_elsewhere() == elsewhere

    def test_a_log_pointing_here_is_not(self, game: tuple[Path, Path]) -> None:
        install, user_dir = game
        write_log(user_dir, install, user_dir)
        assert paths.write_data_elsewhere() is None


class TestUserDirIsTheOneTheGameUses:
    """Two folders with game data: the one the game ran from last wins."""

    @pytest.fixture
    def two_dirs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path, Path]:
        monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "forge"))
        install = tmp_path / "Factorio"
        stale = tmp_path / "stale"
        fresh = tmp_path / "fresh"
        for folder in (stale, fresh):
            (folder / "saves").mkdir(parents=True)
        monkeypatch.setattr(
            paths,
            "_default_user_dirs",
            lambda: iter([(stale, "default location, test")]),
        )
        monkeypatch.setattr(
            paths,
            "_relocated_user_dirs",
            lambda: iter([(fresh, "the same folder on the game's drive (test)")]),
        )
        return install, stale, fresh

    @staticmethod
    def age(log: Path, days: int) -> None:
        moment = time.time() - days * 86400
        os.utime(log, (moment, moment))

    def test_newer_log_beats_the_default(self, two_dirs: tuple[Path, Path, Path]) -> None:
        install, stale, fresh = two_dirs
        self.age(write_log(stale, install, stale), 47)
        write_log(fresh, install, fresh)

        assert paths.factorio_user_dir() == fresh
        source = paths.resolve_sources()["factorio_user_dir"]
        assert "game's drive" in source and str(stale) in source

    def test_default_stays_when_it_is_newer(self, two_dirs: tuple[Path, Path, Path]) -> None:
        install, stale, fresh = two_dirs
        write_log(stale, install, stale)
        self.age(write_log(fresh, install, fresh), 5)

        assert paths.factorio_user_dir() == stale
        assert paths.resolve_sources()["factorio_user_dir"] == "default location, test"

    def test_a_folder_with_a_log_beats_one_without(self, two_dirs: tuple[Path, Path, Path]) -> None:
        install, _, fresh = two_dirs
        write_log(fresh, install, fresh)
        assert paths.factorio_user_dir() == fresh

    def test_without_any_log_the_default_stays(self, two_dirs: tuple[Path, Path, Path]) -> None:
        _, stale, _ = two_dirs
        assert paths.factorio_user_dir() == stale

    def test_the_log_can_point_to_the_real_folder(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """No drive guessing needed: the default's own log may name the real one."""
        monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "forge"))
        install = tmp_path / "Factorio"
        old, real = tmp_path / "old", tmp_path / "real"
        (old / "saves").mkdir(parents=True)
        (real / "saves").mkdir(parents=True)
        monkeypatch.setattr(paths, "_default_user_dirs", lambda: iter([(old, "default location, test")]))
        monkeypatch.setattr(paths, "_relocated_user_dirs", lambda: iter([]))
        self.age(write_log(old, install, real), 40)
        write_log(real, install, real)

        assert paths.factorio_user_dir() == real

    def test_paths_calls_an_old_log_stale(self, two_dirs: tuple[Path, Path, Path]) -> None:
        install, stale, _ = two_dirs
        log = write_log(stale, install, stale)
        self.age(log, 47)
        assert "STALE" not in paths._log_label(log)
        assert "47 days old" in paths._log_label(log)

    def test_a_recent_log_is_not_called_stale(self, two_dirs: tuple[Path, Path, Path]) -> None:
        install, stale, _ = two_dirs
        assert "days old" not in paths._log_label(write_log(stale, install, stale))

    def test_warning_for_an_old_log(
        self, monkeypatch: pytest.MonkeyPatch, two_dirs: tuple[Path, Path, Path]
    ) -> None:
        install, stale, _ = two_dirs
        self.age(write_log(stale, install, stale), 47)
        monkeypatch.setenv(paths.ENV_USER, str(stale))
        assert any("47 days ago" in line for line in paths.user_dir_warnings())

    def test_pinned_folder_with_a_fresher_neighbour_is_warned_about(
        self, monkeypatch: pytest.MonkeyPatch, two_dirs: tuple[Path, Path, Path]
    ) -> None:
        install, stale, fresh = two_dirs
        self.age(write_log(stale, install, stale), 3)
        write_log(fresh, install, fresh)
        monkeypatch.setenv(paths.ENV_USER, str(stale))
        warnings = paths.user_dir_warnings()
        assert any(str(fresh) in line and paths.ENV_USER in line for line in warnings)

    def test_no_warning_when_all_is_fresh(self, two_dirs: tuple[Path, Path, Path]) -> None:
        install, _, fresh = two_dirs
        write_log(fresh, install, fresh)
        assert paths.user_dir_warnings() == []

    @pytest.mark.skipif(sys.platform != "win32", reason="drive roots are a Windows idea")
    def test_other_drive_comes_from_where_the_game_is_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APPDATA", "C:\\Users\\someone\\AppData\\Roaming")
        monkeypatch.setattr(paths, "_install_hints", lambda: iter([Path("E:/Games/Factorio")]))
        relocated = [folder for folder, _ in paths._relocated_user_dirs()]
        assert relocated == [Path("E:/Users/someone/AppData/Roaming/Factorio")]

    @pytest.mark.skipif(sys.platform != "win32", reason="drive roots are a Windows idea")
    def test_same_drive_gives_no_second_candidate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APPDATA", "C:\\Users\\someone\\AppData\\Roaming")
        monkeypatch.setattr(paths, "_install_hints", lambda: iter([Path("C:/Games/Factorio")]))
        assert list(paths._relocated_user_dirs()) == []


class TestOutputAndChain:
    @pytest.mark.skipif(sys.platform != "win32", reason="drive roots are a Windows idea")
    def test_stale_default_log_leads_to_the_games_other_drive(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Old log in the default folder -> Binaries path on another drive -> same AppData path there."""
        appdata = tmp_path / "Users" / "someone" / "AppData" / "Roaming"
        default = appdata / "Factorio"
        default.mkdir(parents=True)
        (default / paths.LOG_FILENAME).write_text(
            "   0.001 Binaries path: Q:/Games/Factorio/bin\n", encoding="utf-8"
        )
        monkeypatch.setenv("APPDATA", str(appdata))
        monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "forge"))
        monkeypatch.setattr(paths, "_probe_install_dirs", lambda: iter([]))
        relocated = [folder for folder, _ in paths._relocated_user_dirs()]
        expected = Path("Q:\\") / default.relative_to(default.anchor)
        assert relocated == [expected]

    def test_folder_without_log_is_named_in_the_source(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        install = tmp_path / "Factorio"
        first, other = tmp_path / "first", tmp_path / "other"
        for folder in (first, other):
            (folder / "saves").mkdir(parents=True)
        monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "forge"))
        monkeypatch.setattr(paths, "_default_user_dirs", lambda: iter([(first, "default, test")]))
        monkeypatch.setattr(paths, "_relocated_user_dirs", lambda: iter([(other, "elsewhere, test")]))
        write_log(other, install, other)
        source = paths.resolve_sources()["factorio_user_dir"]
        assert "has no log" in source and "newer" not in source

    def test_main_prints_the_old_log_warning_to_stderr(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        folder = tmp_path / "user"
        (folder / "saves").mkdir(parents=True)
        log = write_log(folder, tmp_path / "Factorio", folder)
        moment = time.time() - 47 * 86400
        os.utime(log, (moment, moment))
        monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "forge"))
        monkeypatch.setenv(paths.ENV_USER, str(folder))
        assert paths.main() == 0
        err = capsys.readouterr().err
        assert "47 days ago" in err and paths.ENV_USER in err

    def test_list_profiles_without_profiles_says_what_to_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import argparse

        from factorio_forge import cli
        from factorio_forge.profile import Profile

        monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "forge"))
        monkeypatch.setattr(Profile, "active_profile_name", staticmethod(lambda: "vulkan-start"))
        assert cli._cmd_list_profiles(argparse.Namespace()) == 0
        out = capsys.readouterr().out
        assert str(paths.profiles_root()) in out
        assert "create-profile" in out
        assert "vulkan-start" in out
