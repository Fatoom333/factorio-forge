"""Tests for the notes index: topic files, the index built from them, its cache and the lookup."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from factorio_forge import cli, names, notes, paths, slang
from factorio_forge.profile import Profile
from factorio_forge.save import ModRef, SaveInfo


FAKE_ITEMS = {
    name: {}
    for name in (
        "inserter", "fast-transport-belt", "electronic-circuit", "advanced-circuit", "steam-engine", "pipe",
    )
}


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "forge"))
    # the item lookup reads the game data of the active profile: use a small fixed set instead
    monkeypatch.setattr(names, "_raw", lambda kind: FAKE_ITEMS if kind == "item" else {})
    monkeypatch.setattr(names, "find", lambda *a, **k: [])
    return tmp_path


def make_profile(name: str = "Notes") -> Profile:
    save = SaveInfo(path=Path(f"{name}.zip"), name=name, game_version=(2, 0, 77), mods=(ModRef("base", (2, 0, 77)),))
    profile = Profile.from_save(save)
    profile.write()
    return profile


def write(profile: Profile, relative: str, text: str) -> Path:
    path = profile.directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def many_notes(profile: Profile) -> None:
    write(
        profile,
        "notes/blocks.md",
        "# Расстановка блоков\n\nРяды сборщиков идут слева направо.\n\n"
        "## Красные схемы\n\nСобираем в два ряда, лента меди сверху.\n\n"
        "## Зелёные схемы\n\ntags: electronic-circuit\n\nТри ряда по бокам.\n",
    )
    write(
        profile,
        "notes/style.md",
        "---\ntags: inserter, подземка\nsummary: Какие манипуляторы и ленты ставить\n---\n\n"
        "# Стиль\n\nПредпочитаю быстрые манипуляторы; fast-transport-belt везде.\n",
    )
    write(profile, "notes/power.md", "# Питание\n\nСтолбы через каждые девять клеток.\n")


class TestIndex:
    def test_lines_for_every_file_and_for_sections(self) -> None:
        profile = make_profile()
        many_notes(profile)
        entries, rebuilt = notes.load_index(profile.directory)
        assert rebuilt
        by_place = {(e.file, e.heading): e for e in entries}
        assert ("notes/blocks.md", "") in by_place
        assert ("notes/blocks.md", "Красные схемы") in by_place
        assert ("notes/blocks.md", "Зелёные схемы") in by_place
        assert ("notes/style.md", "") in by_place
        assert ("notes/power.md", "") in by_place
        assert by_place[("notes/style.md", "")].summary == "Какие манипуляторы и ленты ставить"
        assert "inserter" in by_place[("notes/style.md", "")].items
        assert "fast-transport-belt" in by_place[("notes/style.md", "")].items
        assert "electronic-circuit" in by_place[("notes/blocks.md", "Зелёные схемы")].items

    def test_a_slang_word_finds_the_note_through_its_internal_name(self) -> None:
        profile = make_profile()
        many_notes(profile)
        entries, _ = notes.load_index(profile.directory)
        red = next(e for e in entries if e.heading == "Красные схемы")
        assert "advanced-circuit" in red.items
        found = notes.lookup(profile.directory, entries, "красная схема")
        assert [e.heading for e, _ in found] == ["Красные схемы"]
        assert "лента меди" in found[0][1]
        assert "Три ряда" not in found[0][1]

    def test_lookup_by_internal_name_file_name_and_tag(self) -> None:
        profile = make_profile()
        many_notes(profile)
        entries, _ = notes.load_index(profile.directory)
        assert [e.file for e, _ in notes.lookup(profile.directory, entries, "advanced-circuit")] == ["notes/blocks.md"]
        assert [e.file for e, _ in notes.lookup(profile.directory, entries, "power")] == ["notes/power.md"]
        assert [e.file for e, _ in notes.lookup(profile.directory, entries, "подземка")] == ["notes/style.md"]
        assert notes.lookup(profile.directory, entries, "zzzz nothing") == []

    def test_cache_is_reused_until_a_note_changes(self) -> None:
        profile = make_profile()
        many_notes(profile)
        assert notes.load_index(profile.directory)[1] is True
        assert (profile.directory / notes.INDEX_FILE).is_file()
        assert notes.load_index(profile.directory)[1] is False

        path = profile.directory / "notes" / "power.md"
        path.write_text("# Питание\n\nТеперь про steam-engine.\n", encoding="utf-8")
        entries, rebuilt = notes.load_index(profile.directory)
        assert rebuilt
        assert "steam-engine" in next(e for e in entries if e.file == "notes/power.md").items
        assert notes.load_index(profile.directory)[1] is False

        # same size, only the time differs: still a change
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        assert notes.load_index(profile.directory)[1] is True

        write(profile, "notes/new.md", "# Новая\n\nПро pipe.\n")
        assert notes.load_index(profile.directory)[1] is True
        (profile.directory / "notes" / "new.md").unlink()
        assert notes.load_index(profile.directory)[1] is True

    def test_every_note_file_has_an_index_line(self) -> None:
        profile = make_profile()
        many_notes(profile)
        write(profile, "notes/deep/empty.md", "")
        write(profile, "notes/deep/only-heading.md", "## Одна секция\n")
        entries, _ = notes.load_index(profile.directory)
        assert notes.unindexed(profile.directory, entries) == []
        lines = notes.format_index(entries).splitlines()
        for path in notes.note_files(profile.directory):
            relative = path.relative_to(profile.directory).as_posix()
            assert any(line.startswith(relative) for line in lines), relative

    def test_an_old_single_notes_file_still_works(self) -> None:
        profile = make_profile()
        profile.notes_path.write_text(
            "# Старый\n\n## Красные схемы\n\nВсё в одну линию.\n\n## Прочее\n\nМелочи.\n", encoding="utf-8"
        )
        entries, _ = notes.load_index(profile.directory)
        assert {e.file for e in entries} == {"notes.md"}
        found = notes.lookup(profile.directory, entries, "красные схемы")
        assert len(found) == 1 and "в одну линию" in found[0][1]
        assert notes.unindexed(profile.directory, entries) == []

    def test_a_fresh_profile_indexes_its_starter_note(self) -> None:
        profile = make_profile()
        entries, _ = notes.load_index(profile.directory)
        assert [e.file for e in entries] == ["notes.md"]

    def test_no_notes_at_all_is_an_empty_index(self) -> None:
        profile = make_profile()
        profile.notes_path.unlink()
        assert notes.load_index(profile.directory)[0] == []


class TestCommand:
    def test_index_then_topic(self, capsys: pytest.CaptureFixture[str]) -> None:
        profile = make_profile()
        many_notes(profile)
        assert cli.main(["notes", profile.name]) == 0
        out = capsys.readouterr().out
        assert "notes/blocks.md#Красные схемы" in out
        assert "advanced-circuit" in out

        assert cli.main(["notes", profile.name, "красная", "схема"]) == 0
        out = capsys.readouterr().out
        assert "лента меди" in out and "Три ряда" not in out

    def test_a_topic_alone_uses_the_active_profile(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = make_profile()
        many_notes(profile)
        monkeypatch.setattr(Profile, "active_profile_name", staticmethod(lambda: profile.name))
        assert cli.main(["notes", "power"]) == 0
        assert "через каждые девять" in capsys.readouterr().out

    def test_an_unmatched_topic_lists_the_index(self, capsys: pytest.CaptureFixture[str]) -> None:
        profile = make_profile()
        many_notes(profile)
        assert cli.main(["notes", profile.name, "zzzz"]) == 1
        assert "notes/power.md" in capsys.readouterr().err


class TestCacheKey:
    def test_the_salt_and_the_slang_table_are_part_of_the_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        profile = make_profile()
        many_notes(profile)
        assert notes.load_index(profile.directory, salt="a")[1] is True
        assert notes.load_index(profile.directory, salt="a")[1] is False
        assert notes.load_index(profile.directory, salt="b")[1] is True  # other data, other index
        assert notes.load_index(profile.directory, salt="b")[1] is False
        monkeypatch.setattr(slang, "TERMS", slang.TERMS[:-1])
        assert notes.load_index(profile.directory, salt="b")[1] is True

    def test_without_the_cache_nothing_is_written(self) -> None:
        profile = make_profile()
        many_notes(profile)
        assert notes.load_index(profile.directory, use_cache=False)[1] is True
        assert not (profile.directory / notes.INDEX_FILE).exists()


class TestProfileWord:
    def test_the_profile_name_is_taken_in_any_case(self, capsys: pytest.CaptureFixture[str]) -> None:
        profile = make_profile("alpha-beta")
        many_notes(profile)
        assert cli.main(["notes", "Alpha-Beta", "power"]) == 0
        out = capsys.readouterr().out
        assert "через каждые девять" in out and "Расстановка" not in out

    def test_a_mistyped_profile_name_says_so(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = make_profile("alpha-beta")
        many_notes(profile)
        monkeypatch.setattr(Profile, "active_profile_name", staticmethod(lambda: profile.name))
        assert cli.main(["notes", "alpha-bata"]) == 1
        err = capsys.readouterr().err
        assert "no profile named 'alpha-bata'" in err and "alpha-beta" in err

    def test_an_item_name_with_a_hyphen_is_still_a_topic(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = make_profile("alpha-beta")
        many_notes(profile)
        monkeypatch.setattr(Profile, "active_profile_name", staticmethod(lambda: profile.name))
        assert cli.main(["notes", "advanced-circuit"]) == 0
        assert "лента меди" in capsys.readouterr().out

    def test_the_active_profile_caches_and_another_does_not(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = make_profile("alpha-beta")
        many_notes(profile)
        monkeypatch.setattr(Profile, "active_profile_name", staticmethod(lambda: "other"))
        assert cli.main(["notes", "alpha-beta"]) == 0
        assert not (profile.directory / notes.INDEX_FILE).exists()
        assert "not the active profile" in capsys.readouterr().err
        monkeypatch.setattr(Profile, "active_profile_name", staticmethod(lambda: profile.name))
        assert cli.main(["notes", "alpha-beta"]) == 0
        assert (profile.directory / notes.INDEX_FILE).is_file()
