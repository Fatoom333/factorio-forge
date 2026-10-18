"""Commands that take a file report a missing or unreadable one in a single line."""

from __future__ import annotations

from pathlib import Path

import pytest

from factorio_forge import cli


def run(command: str, target: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, list[str]]:
    code = cli.main([command, str(target)])
    return code, capsys.readouterr().err.strip().splitlines()


@pytest.mark.parametrize("command", ["review", "build"])
def test_missing_file_is_one_line(
    command: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, err = run(command, tmp_path / "nonexistent.json", capsys)
    assert code == 2
    assert len(err) == 1
    assert "nonexistent.json" in err[0]
    assert "does not exist" in err[0]


@pytest.mark.parametrize("command", ["review", "build"])
def test_folder_instead_of_file(
    command: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, err = run(command, tmp_path, capsys)
    assert code == 2
    assert len(err) == 1
    assert "folder" in err[0]


@pytest.mark.parametrize("command", ["review", "build"])
def test_invalid_json_is_one_line(
    command: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{bad", encoding="utf-8")
    code, err = run(command, bad, capsys)
    assert code == 2
    assert len(err) == 1
    assert "not valid JSON" in err[0]


def test_readable_file_has_no_problem(tmp_path: Path) -> None:
    """Only a missing path is a problem; a readable file passes."""
    assert cli._input_file_problem(tmp_path / "ok.json") is not None
    ok = tmp_path / "ok.json"
    ok.write_text("{}", encoding="utf-8")
    assert cli._input_file_problem(ok) is None
