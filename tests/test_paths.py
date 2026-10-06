"""Tests for path resolution.

These deliberately avoid depending on a real Factorio installation: they must
pass on a contributor's machine and in CI, where no game is present. What is
tested is the resolution *policy* — that an explicit override wins, that the
platform default is used otherwise, and that nothing ever resolves into the
repository itself.
"""

from __future__ import annotations

import sys
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
