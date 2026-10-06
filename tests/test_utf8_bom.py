"""Files people write by hand load with a UTF-8 byte order mark.

Windows PowerShell 5.1 writes one with `Set-Content -Encoding utf8`, and that
is how a request or plan often gets written on Windows. Python's plain utf-8
codec refuses it ("Unexpected UTF-8 BOM"), which says nothing useful to the
person who saved the file; every reader of a user-written file takes it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factorio_forge import cli, paths, plan, request

BOM = "﻿"


def write_with_bom(path: Path, text: str) -> Path:
    path.write_text(BOM + text, encoding="utf-8")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    return path


def test_request_with_bom_loads(tmp_path: Path) -> None:
    path = write_with_bom(tmp_path / "request.json", json.dumps({"targets": [{"item": "x", "per_second": 1}]}))
    assert request.load(path).targets == [("x", 1.0)]


def test_plan_with_bom_loads(tmp_path: Path) -> None:
    path = write_with_bom(tmp_path / "plan.json", json.dumps({"blocks": []}))
    assert plan.load(path) == {"blocks": []}


def test_config_with_bom_loads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path))
    write_with_bom(paths.config_path(), json.dumps({"note": "кириллица"}))
    assert paths.load_config() == {"note": "кириллица"}


def test_blueprint_file_with_bom_loads(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from draftsman.blueprintable import Blueprint

    path = write_with_bom(tmp_path / "bp.txt", Blueprint().to_string())
    assert cli.main(["map", str(path)]) == 0
    assert "no entities" in capsys.readouterr().out
