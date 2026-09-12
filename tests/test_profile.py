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
from draftsman.environment.mod_settings import read_mod_settings

from factorio_forge import paths
from factorio_forge.profile import (
    OFFICIAL_MODS,
    Profile,
    ProfileError,
    available_versions,
    needs_repacking,
    parse_archive_name,
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


class TestArchiveNames:
    @pytest.mark.parametrize(
        "filename, name, version",
        [
            ("Krastorio2_2.0.19.zip", "Krastorio2", (2, 0, 19)),
            ("AAI_Language_Pack_0.1.0.zip", "AAI_Language_Pack", (0, 1, 0)),
            ("belt-balancer-3_1.2.10.zip", "belt-balancer-3", (1, 2, 10)),
        ],
    )
    def test_underscores_in_mod_names_survive(
        self, filename: str, name: str, version: tuple[int, int, int]
    ) -> None:
        assert parse_archive_name(Path(filename)) == (name, version)

    def test_something_that_is_not_a_mod_archive(self) -> None:
        assert parse_archive_name(Path("mod-list.json")) is None
        assert parse_archive_name(Path("Krastorio2.zip")) is None

    def test_versions_come_back_newest_first(self, tmp_path: Path) -> None:
        for version in ("1.2.3", "1.10.0", "1.3.0"):
            (tmp_path / f"helmod_{version}.zip").touch()
        (tmp_path / "other_9.9.9.zip").touch()
        assert available_versions(tmp_path, "helmod") == [(1, 10, 0), (1, 3, 0), (1, 2, 3)]


class TestModVersionPolicy:
    """The ladder mirrors what Factorio itself does with a save whose mods have
    moved on: exact wins, newer is loaded and migrated, older is refused."""

    def prepare(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        installed: list[str],
        wanted: tuple[int, int, int] = (2, 0, 19),
    ):
        mods = tmp_path / "mods"
        mods.mkdir()
        for version in installed:
            with zipfile.ZipFile(mods / f"Krastorio2_{version}.zip", "w") as zf:
                zf.writestr(f"Krastorio2_{version}/info.json", '{"name": "Krastorio2"}')
        monkeypatch.setattr(paths, "mods_dir", lambda: mods)

        profile = Profile.from_save(
            make_save(mods=[ModRef("base", (2, 0, 77)), ModRef("Krastorio2", wanted)])
        )
        profile.write()
        return profile, profile.prepare_mods()

    def test_exact_version_is_used_as_is(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        profile, report = self.prepare(monkeypatch, tmp_path, ["2.0.19"])
        assert report.ok
        assert report.linked == ["Krastorio2"]
        assert report.substituted == []
        assert (profile.mods_dir / "Krastorio2_2.0.19.zip").is_file()

    def test_newer_version_is_used_and_recorded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        profile, report = self.prepare(monkeypatch, tmp_path, ["2.0.25"])
        assert report.ok
        assert [str(s) for s in report.substituted] == ["Krastorio2 2.0.19 -> 2.0.25"]
        assert (profile.mods_dir / "Krastorio2_2.0.25.zip").is_file()

    def test_newest_is_chosen_when_several_are_newer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        profile, report = self.prepare(monkeypatch, tmp_path, ["2.0.20", "2.0.25", "2.0.22"])
        assert [s.used for s in report.substituted] == [(2, 0, 25)]

    def test_only_older_installed_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Migrations run forwards only, so the game will not load this either."""
        _, report = self.prepare(monkeypatch, tmp_path, ["2.0.10", "2.0.15"])
        assert not report.ok
        assert [str(s) for s in report.outdated] == ["Krastorio2 2.0.19 -> 2.0.15"]
        assert report.substituted == []

    def test_absent_mod_is_missing_not_substituted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _, report = self.prepare(monkeypatch, tmp_path, [])
        assert not report.ok
        assert [m.name for m in report.missing] == ["Krastorio2"]

    def test_substitutions_are_persisted_to_the_profile(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        profile, _ = self.prepare(monkeypatch, tmp_path, ["2.0.25"])
        reloaded = Profile.load(profile.name)
        assert reloaded.mod_substitutions == [
            {"name": "Krastorio2", "wanted": [2, 0, 19], "used": [2, 0, 25]}
        ]


class TestModLinking:

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


def write_environment_export(script_output_dir: Path, mods: dict, startup_settings: dict) -> None:
    target = script_output_dir / "factorio-forge" / "environment.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "exported_by": "factorio-forge-companion",
                "game_version": "2.0.77",
                "tick": 1,
                "force": "player",
                "mods": mods,
                "startup_settings": startup_settings,
                "researched": [],
                "available_to_research": [],
                "recipes_enabled": [],
                "counts": {"mods": len(mods), "researched": 0, "recipes_enabled": 0},
            }
        ),
        encoding="utf-8",
    )


class TestModSettings:
    def prepare(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Profile:
        mods = tmp_path / "mods"
        mods.mkdir()
        with zipfile.ZipFile(mods / "Krastorio2_2.0.19.zip", "w") as zf:
            zf.writestr("Krastorio2_2.0.19/info.json", '{"name": "Krastorio2"}')
        (mods / "mod-settings.dat").write_bytes(b"global settings, byte for byte")
        monkeypatch.setattr(paths, "mods_dir", lambda: mods)

        profile = Profile.from_save(make_save())
        profile.write()
        return profile

    def test_matching_export_is_used_instead_of_the_approximation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        script_output = tmp_path / "script-output"
        write_environment_export(
            script_output,
            mods={"base": "2.0.77", "Krastorio2": "2.0.19"},
            startup_settings={"kr-flag": True, "kr-count": 3},
        )
        monkeypatch.setattr(paths, "script_output_dir", lambda: script_output)

        profile = self.prepare(monkeypatch, tmp_path)
        profile.prepare_mods()

        assert profile.mod_settings_source == "environment"
        written = read_mod_settings(str(profile.mods_dir))
        assert written["startup"] == {
            "kr-flag": {"value": True},
            "kr-count": {"value": 3},
        }
        assert Profile.load(profile.name).mod_settings_source == "environment"

    def test_mismatched_export_falls_back_to_the_approximation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        script_output = tmp_path / "script-output"
        write_environment_export(
            script_output,
            mods={"base": "2.0.77", "Krastorio2": "2.0.10"},  # wrong version
            startup_settings={"kr-flag": True},
        )
        monkeypatch.setattr(paths, "script_output_dir", lambda: script_output)

        profile = self.prepare(monkeypatch, tmp_path)
        profile.prepare_mods()

        assert profile.mod_settings_source == "global-approximation"
        copied = (profile.mods_dir / "mod-settings.dat").read_bytes()
        assert copied == b"global settings, byte for byte"

    def test_no_export_falls_back_to_the_approximation(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(paths, "script_output_dir", lambda: None)

        profile = self.prepare(monkeypatch, tmp_path)
        profile.prepare_mods()

        assert profile.mod_settings_source == "global-approximation"


class TestStyle:
    """The Profile-level wiring; the algorithm itself is tests/test_style.py."""

    def write_reference_blueprint(self, profile: Profile, filename: str = "region-1.txt") -> None:
        import warnings

        from draftsman.blueprintable import Blueprint

        from prototypes import pole

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bp = Blueprint()
            bp.entities.append(pole(), tile_position=(0, 0))
            bp.entities.append(pole(), tile_position=(3, 0))
        (profile.blueprints_dir / filename).write_text(bp.to_string(), encoding="utf-8")

    def test_no_reference_blueprints_is_a_named_error(self) -> None:
        from factorio_forge.style import StyleError

        profile = Profile.from_save(make_save())
        profile.write()
        with pytest.raises(StyleError, match="no reference blueprints"):
            profile.measure_style()

    def test_measuring_writes_the_cache_next_to_the_profile(self) -> None:
        profile = Profile.from_save(make_save())
        profile.write()
        self.write_reference_blueprint(profile)

        assert not profile.has_measured_style
        profile.measure_style()

        assert profile.has_measured_style
        assert profile.style_path == profile.directory / "style.json"
        assert profile.load_style().source_files == ("region-1.txt",)

    def test_new_reference_blueprints_before_any_measurement(self) -> None:
        profile = Profile.from_save(make_save())
        profile.write()
        self.write_reference_blueprint(profile)

        assert profile.new_reference_blueprints() == ["region-1.txt"]

    def test_new_reference_blueprints_after_measurement(self) -> None:
        profile = Profile.from_save(make_save())
        profile.write()
        self.write_reference_blueprint(profile, "region-1.txt")
        profile.measure_style()

        self.write_reference_blueprint(profile, "region-2.txt")
        assert profile.new_reference_blueprints() == ["region-2.txt"]
