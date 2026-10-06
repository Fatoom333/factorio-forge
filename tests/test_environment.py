"""Tests for reading the companion mod's environment.json export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factorio_forge import paths
from factorio_forge.environment import Environment, EnvironmentError, read_environment
from factorio_forge.save import ModRef


def write_export(output_dir: Path, payload: dict) -> Path:
    target = output_dir / "factorio-forge" / "environment.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def make_payload(**overrides) -> dict:
    payload = {
        "exported_by": "factorio-forge-companion",
        "game_version": "2.0.28",
        "tick": 123456,
        "force": "player",
        "mods": {"base": "2.0.28", "Krastorio2": "2.0.19"},
        "startup_settings": {"kr-some-setting": True},
        "researched": ["automation"],
        "available_to_research": ["logistics"],
        "recipes_enabled": ["iron-plate"],
        "counts": {"mods": 2, "researched": 1, "recipes_enabled": 1},
    }
    payload.update(overrides)
    return payload


class TestReadEnvironment:
    def test_no_export_is_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(paths, "script_output_dir", lambda: tmp_path)
        assert read_environment() is None

    def test_no_user_dir_is_not_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(paths, "script_output_dir", lambda: None)
        assert read_environment() is None

    def test_reads_every_field(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(paths, "script_output_dir", lambda: tmp_path)
        write_export(tmp_path, make_payload())

        env = read_environment()
        assert env == Environment(
            game_version="2.0.28",
            tick=123456,
            force="player",
            mods={"base": "2.0.28", "Krastorio2": "2.0.19"},
            startup_settings={"kr-some-setting": True},
            researched=("automation",),
            available_to_research=("logistics",),
            recipes_enabled=("iron-plate",),
        )

    def test_malformed_json_is_a_named_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(paths, "script_output_dir", lambda: tmp_path)
        target = tmp_path / "factorio-forge" / "environment.json"
        target.parent.mkdir(parents=True)
        target.write_text("not json", encoding="utf-8")

        with pytest.raises(EnvironmentError):
            read_environment()

    def test_missing_required_field_is_a_named_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(paths, "script_output_dir", lambda: tmp_path)
        payload = make_payload()
        del payload["tick"]
        write_export(tmp_path, payload)

        with pytest.raises(EnvironmentError):
            read_environment()


class TestMatchesMods:
    def test_exact_set_matches(self) -> None:
        env = Environment(
            game_version="2.0.28",
            tick=1,
            force="player",
            mods={"base": "2.0.28", "Krastorio2": "2.0.19"},
        )
        mods = (ModRef("base", (2, 0, 28)), ModRef("Krastorio2", (2, 0, 19)))
        assert env.matches_mods(mods)

    def test_different_version_does_not_match(self) -> None:
        env = Environment(
            game_version="2.0.28", tick=1, force="player", mods={"base": "2.0.28"}
        )
        assert not env.matches_mods((ModRef("base", (2, 0, 27)),))

    def test_extra_or_missing_mod_does_not_match(self) -> None:
        env = Environment(
            game_version="2.0.28",
            tick=1,
            force="player",
            mods={"base": "2.0.28", "Krastorio2": "2.0.19"},
        )
        assert not env.matches_mods((ModRef("base", (2, 0, 28)),))

    def test_empty_export_never_matches(self) -> None:
        env = Environment(game_version="2.0.28", tick=1, force="player", mods={})
        assert not env.matches_mods((ModRef("base", (2, 0, 28)),))


class TestMismatchSaysWhat:
    """A refused export must say which one it was, how it differs, and what to do."""

    @pytest.fixture
    def active(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        from factorio_forge import profile as profile_module

        class FakeProfile:
            name = "my-save"
            source_save = "My save"
            mods = (ModRef("base", (2, 0, 28)), ModRef("only-here", (1, 0, 0)), ModRef("shared", (1, 2, 0)))

        monkeypatch.setattr(paths, "script_output_dir", lambda: tmp_path)
        monkeypatch.setattr(profile_module.Profile, "active_profile_name", staticmethod(lambda: "my-save"))
        monkeypatch.setattr(profile_module.Profile, "load", classmethod(lambda cls, name: FakeProfile()))
        return tmp_path

    def test_names_each_difference_and_the_export(self, active: Path) -> None:
        from factorio_forge.environment import for_active_profile

        tick = 216000 * 3 + 3600 * 5  # 3 hours 5 minutes at 60 ticks a second
        write_export(active, make_payload(
            tick=tick, mods={"base": "2.0.28", "only-there": "3.0.0", "shared": "1.1.0"}
        ))
        found, why = for_active_profile()
        assert found is None
        assert "only in the export: only-there" in why
        assert "only in the profile: only-here" in why
        assert "shared 1.1.0 -> 1.2.0" in why
        assert f"tick {tick}" in why and "3:05 played" in why
        assert "written 20" in why  # the file's own timestamp
        assert "/forge-export" in why and "My save" in why
        assert "create-profile" in why and "--force" in why

    def test_a_match_still_says_which_export(self, active: Path) -> None:
        from factorio_forge.environment import for_active_profile

        write_export(active, make_payload(mods={"base": "2.0.28", "only-here": "1.0.0", "shared": "1.2.0"}))
        found, why = for_active_profile()
        assert found is not None
        assert "tick 123456" in why and "matching profile 'my-save'" in why


def test_long_differences_are_cut_short() -> None:
    from factorio_forge.environment import ModDifferences

    text = ModDifferences(only_export=tuple(f"mod-{i}" for i in range(10))).describe(limit=3)
    assert text == "only in the export: mod-0, mod-1, mod-2 and 7 more"


def test_export_with_bom_loads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths, "script_output_dir", lambda: tmp_path)
    target = write_export(tmp_path, make_payload())
    target.write_text("﻿" + target.read_text(encoding="utf-8"), encoding="utf-8")
    assert read_environment().tick == 123456
