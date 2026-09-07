"""Tests for profiles.

These cover the parts that do not need a Factorio installation: naming,
persistence, the rules for assembling a mod folder, and the handling of
archives that the mod loader cannot read as they are. Extraction itself needs
the game and is exercised by hand.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from factorio_forge import paths
from factorio_forge.profile import (
    OFFICIAL_MODS,
    Profile,
    ProfileError,
    needs_repacking,
    repack_without_junk,
    slugify,
)
from factorio_forge.save import ModRef, SaveInfo


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "forge"))
    return tmp_path


def make_save(name: str = "Krastorio2", mods: list[ModRef] | None = None) -> SaveInfo:
    if mods is None:
        mods = [ModRef("base", (2, 0, 77)), ModRef("Krastorio2", (2, 0, 19))]
    return SaveInfo(
        path=Path(f"{name}.zip"),
        name=name,
        game_version=(2, 0, 77),
        mods=tuple(mods),
    )


class TestSlugify:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Krastorio2", "krastorio2"),
            ("SPACE AGEEEEEEEEE", "space-ageeeeeeeee"),
            ("warptorio s Gleb", "warptorio-s-gleb"),
            ("Begin Of The Bug - No Way 2", "begin-of-the-bug-no-way-2"),
            ("Archie2ыфвыв", "archie2"),
            ("[item=landfill]", "item-landfill"),
        ],
    )
    def test_produces_safe_directory_names(self, raw: str, expected: str) -> None:
        assert slugify(raw) == expected

    def test_never_returns_empty(self) -> None:
        assert slugify("...") == "profile"
        assert slugify("рпм") == "profile"


class TestPersistence:
    def test_round_trip(self) -> None:
        profile = Profile.from_save(make_save())
        profile.data_fingerprint = {"digest": "abc123", "counts": {"recipes": 10}}
        profile.write()

        loaded = Profile.load(profile.name)
        assert loaded.name == profile.name
        assert loaded.game_version == (2, 0, 77)
        assert loaded.mods == profile.mods
        assert loaded.source_save == "Krastorio2"
        assert loaded.data_fingerprint == {"digest": "abc123", "counts": {"recipes": 10}}

    def test_write_creates_the_directory_tree(self) -> None:
        profile = Profile.from_save(make_save())
        profile.write()
        assert profile.mods_dir.is_dir()
        assert profile.data_dir.is_dir()
        assert profile.blueprints_dir.is_dir()
        assert profile.notes_path.is_file()

    def test_existing_notes_are_never_overwritten(self) -> None:
        profile = Profile.from_save(make_save())
        profile.write()
        profile.notes_path.write_text("my own notes", encoding="utf-8")
        profile.write()
        assert profile.notes_path.read_text(encoding="utf-8") == "my own notes"

    def test_listing(self) -> None:
        Profile.from_save(make_save("Krastorio2")).write()
        Profile.from_save(make_save("Archie2")).write()
        assert Profile.list_all() == ["archie2", "krastorio2"]

    def test_loading_something_absent(self) -> None:
        with pytest.raises(ProfileError, match="no profile"):
            Profile.load("nothing-here")


class TestFromSave:
    def test_carries_the_save_across(self) -> None:
        profile = Profile.from_save(make_save())
        assert profile.source_save == "Krastorio2"
        assert profile.fingerprint == make_save().fingerprint()

    def test_explicit_name_wins(self) -> None:
        assert Profile.from_save(make_save(), name="My Base").name == "my-base"

    def test_old_saves_are_refused(self) -> None:
        old = SaveInfo(
            path=Path("old.zip"), name="old", game_version=(1, 1, 110),
            mods=(ModRef("base", (1, 1, 110)),),
        )
        with pytest.raises(ProfileError, match="2.0"):
            Profile.from_save(old)


class TestModList:
    def test_official_mods_are_listed_explicitly(self) -> None:
        """Anything absent from mod-list.json defaults to enabled, so a profile
        that does not use Space Age must say so rather than stay silent."""
        profile = Profile.from_save(make_save())
        profile.write()
        profile._write_mod_list()

        entries = json.loads((profile.mods_dir / "mod-list.json").read_text())["mods"]
        by_name = {e["name"]: e["enabled"] for e in entries}

        assert by_name["space-age"] is False
        assert by_name["quality"] is False
        assert by_name["elevated-rails"] is False
        assert by_name["base"] is True
        assert by_name["Krastorio2"] is True
        assert OFFICIAL_MODS <= set(by_name)

    def test_space_age_profile_enables_it(self) -> None:
        profile = Profile.from_save(
            make_save(mods=[ModRef("base", (2, 0, 77)), ModRef("space-age", (2, 0, 77))])
        )
        profile.write()
        profile._write_mod_list()

        entries = json.loads((profile.mods_dir / "mod-list.json").read_text())["mods"]
        by_name = {e["name"]: e["enabled"] for e in entries}
        assert by_name["space-age"] is True
        assert profile.has_space_age


class TestArchiveRepacking:
    def make_archive(self, path: Path, folders: list[str]) -> Path:
        with zipfile.ZipFile(path, "w") as zf:
            for folder in folders:
                zf.writestr(f"{folder}/info.json", '{"name": "x"}')
                zf.writestr(f"{folder}/data.lua", "-- nothing")
        return path

    def test_ordinary_archive_is_left_alone(self, tmp_path: Path) -> None:
        archive = self.make_archive(tmp_path / "mod_1.0.0.zip", ["mod_1.0.0"])
        assert not needs_repacking(archive)

    def test_macos_junk_is_detected(self, tmp_path: Path) -> None:
        archive = self.make_archive(tmp_path / "mod_1.0.0.zip", ["mod", "__MACOSX"])
        assert needs_repacking(archive)

    def test_repacking_drops_only_the_junk(self, tmp_path: Path) -> None:
        source = self.make_archive(tmp_path / "mod_1.0.0.zip", ["mod", "__MACOSX"])
        target = tmp_path / "cleaned.zip"
        repack_without_junk(source, target)

        with zipfile.ZipFile(target) as zf:
            names = zf.namelist()
        assert not any(n.startswith("__MACOSX") for n in names)
        assert "mod/info.json" in names
        assert "mod/data.lua" in names

    def test_repacking_preserves_content(self, tmp_path: Path) -> None:
        source = tmp_path / "mod_1.0.0.zip"
        with zipfile.ZipFile(source, "w") as zf:
            zf.writestr("mod/data.lua", "return { marker = 42 }")
            zf.writestr("__MACOSX/mod/._data.lua", b"\x00\x01junk")
        target = tmp_path / "cleaned.zip"
        repack_without_junk(source, target)

        with zipfile.ZipFile(target) as zf:
            assert zf.read("mod/data.lua") == b"return { marker = 42 }"

    def test_a_broken_archive_is_not_reported_as_repackable(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken_1.0.0.zip"
        broken.write_bytes(b"not a zip at all")
        assert not needs_repacking(broken)


class TestModLinking:
    def test_missing_exact_version_is_reported_not_substituted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A different version of a mod changes recipes, so it is never
        silently accepted in place of the one the save recorded."""
        mods = tmp_path / "mods"
        mods.mkdir()
        # The player has a newer version than the save wants.
        with zipfile.ZipFile(mods / "Krastorio2_2.0.20.zip", "w") as zf:
            zf.writestr("Krastorio2_2.0.20/info.json", '{"name": "Krastorio2"}')
        monkeypatch.setattr(paths, "mods_dir", lambda: mods)

        profile = Profile.from_save(make_save())
        profile.write()
        report = profile.prepare_mods()

        assert not report.ok
        assert [m.name for m in report.missing] == ["Krastorio2"]
        assert report.official == ["base"]

    def test_present_mod_is_linked_and_official_ones_are_not(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        mods = tmp_path / "mods"
        mods.mkdir()
        with zipfile.ZipFile(mods / "Krastorio2_2.0.19.zip", "w") as zf:
            zf.writestr("Krastorio2_2.0.19/info.json", '{"name": "Krastorio2"}')
        monkeypatch.setattr(paths, "mods_dir", lambda: mods)

        profile = Profile.from_save(make_save())
        profile.write()
        report = profile.prepare_mods()

        assert report.ok
        assert report.linked == ["Krastorio2"]
        assert report.official == ["base"]
        assert (profile.mods_dir / "Krastorio2_2.0.19.zip").is_file()

    def test_no_mod_folder_is_a_named_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(paths, "mods_dir", lambda: None)
        profile = Profile.from_save(make_save())
        profile.write()
        with pytest.raises(ProfileError, match="mod folder"):
            profile.prepare_mods()
