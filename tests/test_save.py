"""Tests for reading a save's header.

Real saves cannot be committed -- they are large and they are the player's own
data -- so these build headers byte by byte instead. That is not a weaker test:
the layout is the thing under test, and constructing it deliberately lets us
cover cases a particular player's folder happens not to contain, such as a
version component large enough to need the escape encoding.
"""

from __future__ import annotations

import struct
import zipfile
import zlib
from pathlib import Path

import pytest

from factorio_forge import save


# --------------------------------------------------------------------------
# building synthetic headers
# --------------------------------------------------------------------------


def optimised(value: int) -> bytes:
    """Factorio's small-number encoding: one byte, or 0xFF plus a u16."""
    if value < 0xFF:
        return bytes([value])
    return b"\xff" + struct.pack("<H", value)


def pack_string(text: str) -> bytes:
    raw = text.encode("utf-8")
    return optimised(len(raw)) + raw


def pack_mod(name: str, version: tuple[int, int, int], crc: int = 0) -> bytes:
    return (
        pack_string(name)
        + b"".join(optimised(part) for part in version)
        + struct.pack("<I", crc)
    )


def build_header(
    game_version: tuple[int, int, int] = (2, 0, 77),
    mods: list[tuple[str, tuple[int, int, int]]] | None = None,
    filler: bytes = b"\x01\x00\x00\x00\x00\x00\x00\x01",
) -> bytes:
    """A level-init.dat header shaped like the ones the game writes."""
    if mods is None:
        mods = [("base", (2, 0, 77))]
    parts = [
        struct.pack("<HHHH", *game_version, 0),
        b"\x00\x00",
        pack_string("freeplay"),
        pack_string("base"),
        filler,
        optimised(len(mods)),
    ]
    parts.extend(pack_mod(name, version) for name, version in mods)
    return b"".join(parts)


def write_save(tmp_path: Path, header: bytes, name: str = "TestSave") -> Path:
    path = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}/level-init.dat", header + b"\x00" * 512)
        archive.writestr(f"{name}/level.dat0", b"\x00" * 64)
    return path


# --------------------------------------------------------------------------


class TestReadSaveInfo:
    def test_reads_version_and_mods(self, tmp_path: Path) -> None:
        header = build_header(
            (2, 0, 77),
            [("base", (2, 0, 77)), ("Krastorio2", (2, 0, 19)), ("flib", (0, 16, 0))],
        )
        info = save.read_save_info(write_save(tmp_path, header))

        assert info.game_version == (2, 0, 77)
        assert info.game_version_string == "2.0.77"
        assert len(info.mods) == 3
        assert info.mod_names == {"base", "Krastorio2", "flib"}
        assert save.ModRef("Krastorio2", (2, 0, 19)) in info.mods

    def test_mods_added_after_creation_come_from_level_dat0(self, tmp_path: Path) -> None:
        """level-init.dat keeps the mods the map was created with.

        A mod added to the save later is only in the compressed level.dat0, so
        a player who added Factorissimo to their save got a profile without it.
        """
        created = build_header(mods=[("base", (2, 0, 77))])
        current = build_header(mods=[("base", (2, 0, 77)), ("factorissimo", (3, 11, 19))])
        path = tmp_path / "Grown.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Grown/level-init.dat", created + b"\x00" * 512)
            archive.writestr("Grown/level.dat0", zlib.compress(current + b"\x00" * 512))
        assert save.read_save_info(path).mod_names == {"base", "factorissimo"}

    def test_name_comes_from_the_filename(self, tmp_path: Path) -> None:
        path = write_save(tmp_path, build_header(), name="Мой сейв")
        assert save.read_save_info(path).name == "Мой сейв"

    def test_finds_the_table_despite_unknown_leading_fields(self, tmp_path: Path) -> None:
        """The bytes before the mod count are not fully understood.

        Their size could change between game versions, so the parser searches
        rather than trusting an offset. An unusually long filler stands in for
        such a change.
        """
        header = build_header(filler=b"\x02\x11\x00\x00\x7f\x00\x01\x00\x00\xa0\x00\x33\x07")
        info = save.read_save_info(write_save(tmp_path, header))
        assert info.mod_names == {"base"}

    def test_large_version_component_uses_the_escape(self, tmp_path: Path) -> None:
        header = build_header(mods=[("base", (2, 0, 77)), ("bigmod", (1, 300, 0))])
        info = save.read_save_info(write_save(tmp_path, header))
        assert save.ModRef("bigmod", (1, 300, 0)) in info.mods

    def test_version_string_of_a_mod(self) -> None:
        assert save.ModRef("flib", (0, 16, 0)).version_string == "0.16.0"
        assert str(save.ModRef("flib", (0, 16, 0))) == "flib 0.16.0"


class TestClassification:
    def test_vanilla_and_space_age(self, tmp_path: Path) -> None:
        header = build_header(mods=[("base", (2, 0, 77)), ("space-age", (2, 0, 77))])
        info = save.read_save_info(write_save(tmp_path, header))
        assert info.is_vanilla
        assert info.has_space_age

    def test_modded_is_not_vanilla(self, tmp_path: Path) -> None:
        header = build_header(mods=[("base", (2, 0, 77)), ("Krastorio2", (2, 0, 19))])
        info = save.read_save_info(write_save(tmp_path, header))
        assert not info.is_vanilla
        assert not info.has_space_age

    def test_supported_only_from_2_0(self, tmp_path: Path) -> None:
        old = save.read_save_info(write_save(tmp_path, build_header((1, 1, 110)), "old"))
        new = save.read_save_info(write_save(tmp_path, build_header((2, 0, 77)), "new"))
        assert not old.is_supported
        assert new.is_supported


class TestFingerprint:
    def test_same_mods_same_fingerprint_regardless_of_order(self, tmp_path: Path) -> None:
        one = build_header(mods=[("base", (2, 0, 77)), ("flib", (0, 16, 0))])
        two = build_header(mods=[("flib", (0, 16, 0)), ("base", (2, 0, 77))])
        a = save.read_save_info(write_save(tmp_path, one, "a"))
        b = save.read_save_info(write_save(tmp_path, two, "b"))
        assert a.fingerprint() == b.fingerprint()

    def test_different_version_changes_fingerprint(self, tmp_path: Path) -> None:
        one = build_header(mods=[("base", (2, 0, 77)), ("flib", (0, 16, 0))])
        two = build_header(mods=[("base", (2, 0, 77)), ("flib", (0, 16, 1))])
        a = save.read_save_info(write_save(tmp_path, one, "a"))
        b = save.read_save_info(write_save(tmp_path, two, "b"))
        assert a.fingerprint() != b.fingerprint()

    def test_game_version_does_not_affect_it(self, tmp_path: Path) -> None:
        """A fingerprint identifies a mod set, so data can be shared between
        saves that differ only in the patch level of the game."""
        mods = [("base", (2, 0, 77)), ("flib", (0, 16, 0))]
        a = save.read_save_info(write_save(tmp_path, build_header((2, 0, 69), mods), "a"))
        b = save.read_save_info(write_save(tmp_path, build_header((2, 0, 77), mods), "b"))
        assert a.fingerprint() == b.fingerprint()


class TestFailures:
    def test_ancient_version_is_reported_as_too_old(self, tmp_path: Path) -> None:
        path = write_save(tmp_path, build_header((0, 12, 29)))
        with pytest.raises(save.UnsupportedSaveVersion) as caught:
            save.read_save_info(path)
        assert caught.value.version == (0, 12, 29)

    def test_not_a_zip(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.zip"
        path.write_bytes(b"this is not a zip file")
        with pytest.raises(save.SaveFormatError):
            save.read_save_info(path)

    def test_zip_without_a_header(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("empty/level.dat0", b"\x00" * 32)
        with pytest.raises(save.SaveFormatError, match="level-init"):
            save.read_save_info(path)

    def test_unparseable_table_is_an_error_not_a_guess(self, tmp_path: Path) -> None:
        """Random bytes must not be mistaken for a mod list."""
        header = struct.pack("<HHHH", 2, 0, 77, 0) + bytes(range(200, 256)) * 4
        with pytest.raises(save.SaveFormatError, match="mod table"):
            save.read_save_info(write_save(tmp_path, header))


class TestSurvey:
    def test_survey_separates_old_from_broken(self, tmp_path: Path, monkeypatch) -> None:
        good = write_save(tmp_path, build_header((2, 0, 77)), "good")
        old = write_save(tmp_path, build_header((0, 13, 0)), "ancient")
        broken = tmp_path / "broken.zip"
        broken.write_bytes(b"nope")
        monkeypatch.setattr(save.paths, "saves_dir", lambda: tmp_path)

        results = save.survey_saves()
        by_name = {
            (r.name if isinstance(r, save.SaveInfo) else r.name): r for r in results
        }

        assert isinstance(by_name["good"], save.SaveInfo)
        assert isinstance(by_name["ancient"], save.SaveProblem)
        assert by_name["ancient"].too_old
        assert isinstance(by_name["broken"], save.SaveProblem)
        assert not by_name["broken"].too_old

    def test_autosaves_are_excluded_by_default(self, tmp_path: Path, monkeypatch) -> None:
        write_save(tmp_path, build_header(), "MyBase")
        write_save(tmp_path, build_header(), "_autosave1")
        monkeypatch.setattr(save.paths, "saves_dir", lambda: tmp_path)

        assert [p.stem for p in save.list_saves()] == ["MyBase"]
        assert len(save.list_saves(include_autosaves=True)) == 2

    def test_no_save_directory_is_not_an_error(self, monkeypatch) -> None:
        monkeypatch.setattr(save.paths, "saves_dir", lambda: None)
        assert save.list_saves() == []
        assert save.survey_saves() == []
