"""Commands that take a file report a missing or unreadable one in a single line."""

from __future__ import annotations

from pathlib import Path

import pytest

from factorio_forge import cli


@pytest.mark.parametrize("command", ["review", "build"])
def test_missing_file_is_one_line(
    command: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nonexistent.json"
    assert cli.main([command, str(missing)]) == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert "nonexistent.json" in err[0]
    assert "does not exist" in err[0]


def test_folder_instead_of_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["review", str(tmp_path)]) == 2
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_invalid_json_is_one_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{bad", encoding="utf-8")
    code = cli.main(["review", str(bad)])
    assert code != 0
