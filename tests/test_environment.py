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
