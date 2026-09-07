"""Tests for failure diagnosis.

Each sample below is a real failure seen while building this project, kept
verbatim so that a change to the rules cannot quietly stop recognising a case
that has actually happened. The last group matters as much as the rest: an
unrecognised failure must say it is unrecognised rather than be forced into the
nearest category, because a confident wrong diagnosis sends the reader off in
the wrong direction.
"""

from __future__ import annotations

from factorio_forge import diagnostics


MOD_ARCHIVE_LAYOUT = """\
Traceback (most recent call last):
  File "<string>", line 6, in <module>
  File ".../draftsman/environment/mod_list.py", line 269, in register_mod
    raise IncorrectModFormatError(
draftsman.error.IncorrectModFormatError: Mod archive 'Early-3x3-Electric-Furnaces' has more \
than one internal folder, and none of the internal folders match it's external name
"""

MISSING_DEFINES = """\
Traceback (most recent call last):
  File ".../draftsman/environment/update.py", line 632, in run_data_lifecycle
    file_to_string(
FileNotFoundError: [Errno 2] No such file or directory: \
'C:\\\\venv\\\\Lib\\\\site-packages\\\\draftsman\\\\compatibility\\\\defines\\\\2.0.lua'
"""

WRONG_GAME_PATH = """\
Traceback (most recent call last):
  File ".../draftsman/environment/update.py", line 1976, in update_draftsman_data
    with open(os.path.join(game_path, "base", "info.json")) as base_info_file:
FileNotFoundError: [Errno 2] No such file or directory: \
'D:\\\\Steam\\\\steamapps\\\\common\\\\Factorio\\\\base\\\\info.json'
"""

MOD_ENCODING = """\
Traceback (most recent call last):
  File ".../draftsman/environment/mod_list.py", line 172, in file_to_string
    return file.read()
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xcf in position 41: invalid continuation byte
"""

MISSING_MOD = """\
draftsman.error.MissingModError: Unrecognized mod name 'Krastorio2'
"""


class TestRecognisedFailures:
    def test_mod_packaged_with_junk_folder(self) -> None:
        result = diagnostics.diagnose(MOD_ARCHIVE_LAYOUT)
        assert result.category == "mod-archive-layout"
        assert "Early-3x3-Electric-Furnaces" in result.summary
        assert "__MACOSX" in result.detail
        assert result.remedy

    def test_library_missing_version_constants(self) -> None:
        result = diagnostics.diagnose(MISSING_DEFINES)
        assert result.category == "library-game-version-mismatch"
        assert "2.0" in result.detail
        assert "3.3.1" in result.remedy

    def test_game_path_pointing_at_the_wrong_directory(self) -> None:
        result = diagnostics.diagnose(WRONG_GAME_PATH)
        assert result.category == "game-path-wrong"
        assert "data folder" in result.detail

    def test_mod_file_in_a_legacy_encoding(self) -> None:
        result = diagnostics.diagnose(MOD_ENCODING)
        assert result.category == "mod-encoding"
        assert "UTF-8" in result.summary

    def test_mod_absent_from_the_folder(self) -> None:
        result = diagnostics.diagnose(MISSING_MOD)
        assert result.category == "missing-mod"
        assert "Krastorio2" in result.summary

    def test_every_recognised_case_offers_a_remedy(self) -> None:
        for sample in (
            MOD_ARCHIVE_LAYOUT,
            MISSING_DEFINES,
            WRONG_GAME_PATH,
            MOD_ENCODING,
            MISSING_MOD,
        ):
            result = diagnostics.diagnose(sample)
            assert result.recognised
            assert result.remedy, f"no remedy offered for {result.category}"


class TestHonestyAboutTheUnknown:
    def test_unrecognised_failure_says_so(self) -> None:
        result = diagnostics.diagnose("ValueError: something nobody anticipated\n")
        assert result.category == "unknown"
        assert not result.recognised
        assert "not recognised" in result.summary

    def test_unrecognised_failure_still_surfaces_the_error(self) -> None:
        result = diagnostics.diagnose("RuntimeError: the roof is on fire\n")
        assert "the roof is on fire" in result.detail

    def test_empty_output_does_not_crash(self) -> None:
        result = diagnostics.diagnose("", "", returncode=3)
        assert result.category == "unknown"
        assert "3" in result.detail

    def test_success_is_not_a_failure(self) -> None:
        result = diagnostics.diagnose("", "", returncode=0)
        assert result.category == "none"
        assert not result.remedy


class TestReport:
    def test_report_includes_summary_detail_and_remedy(self) -> None:
        text = diagnostics.diagnose(MISSING_MOD).report()
        assert "Krastorio2" in text
        assert "What to do:" in text

    def test_raw_output_is_opt_in(self) -> None:
        result = diagnostics.diagnose(MISSING_MOD)
        assert "MissingModError" not in result.report()
        assert "MissingModError" in result.report(include_raw=True)
