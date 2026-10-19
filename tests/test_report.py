"""The report in two levels: errors, then facts.

The mapping from every finding code to one of the two is the thing that must not
rot, so one test reads the source for codes and fails on any without a rule.
"""

from __future__ import annotations

import ast
import json
import warnings
from types import SimpleNamespace
from pathlib import Path

import pytest
from draftsman.blueprintable import Blueprint

import prototypes
from factorio_forge import cli, inspection, report
from factorio_forge.inspection import Finding, Severity
from test_route import block_spec

SRC = Path(inspection.__file__).parent


def codes_in_source() -> dict[str, set[str]]:
    """Every code any `Finding(...)` in the source is built with, by file."""
    found: dict[str, set[str]] = {}
    for path in sorted(SRC.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name != "Finding":
                continue
            assert len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str), (
                f"{path.name}:{node.lineno}: a Finding's code must be a string literal, so the report can map it"
            )
            found.setdefault(node.args[1].value, set()).add(path.name)
    return found


def finding(code: str, severity: Severity, detail: str = "", position=None) -> Finding:
    return Finding(severity, code, f"{code} happened", detail, position)


class TestEveryCodeIsMapped:
    def test_no_code_without_a_rule(self) -> None:
        missing = sorted(set(codes_in_source()) - set(report.RULES))
        assert not missing, (
            f"finding codes with no error/fact rule in report.RULES: {missing}. "
            "Add each, and a row in docs/checking.md."
        )

    def test_no_rule_for_a_code_that_is_gone(self) -> None:
        stale = sorted(set(report.RULES) - set(codes_in_source()))
        assert not stale, f"report.RULES names codes nothing produces: {stale}"

    def test_rules_are_one_of_three(self) -> None:
        assert set(report.RULES.values()) <= {report.ALWAYS_ERROR, report.ALWAYS_FACT, report.ERROR_UNLESS_NOTE}

    def test_every_code_is_in_the_docs(self) -> None:
        text = (SRC.parent.parent / "docs" / "checking.md").read_text(encoding="utf-8")
        assert [c for c in report.RULES if f"`{c}`" not in text] == []


class TestTheTwoLevels:
    @pytest.mark.parametrize("severity", list(Severity))
    def test_a_collision_is_an_error_whatever_its_grade(self, severity: Severity) -> None:
        assert report.level(finding("overlap", severity)) == report.ERROR

    @pytest.mark.parametrize("severity", list(Severity))
    def test_a_stated_limit_is_a_fact_whatever_its_grade(self, severity: Severity) -> None:
        assert report.level(finding("route-tiles-unchecked", severity)) == report.FACT

    def test_a_note_is_a_fact_where_the_code_allows_both(self) -> None:
        assert report.level(finding("lanes-rate", Severity.NOTE)) == report.FACT
        assert report.level(finding("lanes-rate", Severity.PROBLEM)) == report.ERROR

    def test_a_doubtful_one_is_an_error(self) -> None:
        # a suspect: the check could not be sure it breaks the build
        assert report.level(finding("unpowered", Severity.SUSPECT)) == report.ERROR
        assert report.level(finding("lanes-unknown", Severity.SUSPECT)) == report.ERROR

    def test_a_short_supply_is_an_error_though_the_check_called_it_a_note(self) -> None:
        assert report.level(finding("route-short-supply", Severity.NOTE)) == report.ERROR

    def test_a_code_nobody_mapped_is_an_error_rather_than_a_crash(self) -> None:
        assert report.level(finding("made-up-code", Severity.NOTE)) == report.ERROR

    def test_an_error_from_a_doubtful_check_says_what_to_check(self) -> None:
        out = report.from_findings([finding("route-short-supply", Severity.NOTE)])
        assert out.errors[0].detail
        out = report.from_findings([finding("made-up-code", Severity.SUSPECT)])
        assert out.errors[0].detail

    def test_the_checks_own_detail_is_kept(self) -> None:
        out = report.from_findings([finding("unpowered", Severity.SUSPECT, "look at the poles")])
        assert out.errors[0].detail == "look at the poles"

    def test_the_old_grade_is_kept_on_the_line(self) -> None:
        out = report.from_findings([finding("unpowered", Severity.SUSPECT, "x")])
        assert out.errors[0].severity == "suspect"


class TestOrder:
    def test_errors_go_worst_grade_first_then_as_found(self) -> None:
        found = [
            finding("unpowered", Severity.SUSPECT, "a"),
            finding("route-short-supply", Severity.NOTE),
            finding("overlap", Severity.PROBLEM, "b"),
            finding("lanes-missing", Severity.PROBLEM, "c"),
        ]
        out = report.from_findings(found)
        assert [e.code for e in out.errors] == ["overlap", "lanes-missing", "unpowered", "route-short-supply"]
        assert out.facts == []

    def test_facts_are_kept_apart(self) -> None:
        out = report.from_findings([finding("lanes-swapped", Severity.NOTE), finding("overlap", Severity.PROBLEM, "x")])
        assert [e.code for e in out.errors] == ["overlap"]
        assert [f.code for f in out.facts] == ["lanes-swapped"]

    def test_the_verdict_comes_first_then_errors_then_facts(self) -> None:
        out = report.from_findings([finding("lanes-swapped", Severity.NOTE), finding("overlap", Severity.PROBLEM, "x")])
        lines = report.format_lines(out, "plan")
        assert lines[0] == "plan: 1 error"
        assert lines.index("errors:") < lines.index("facts:")

    def test_no_errors_says_so_and_has_no_error_section(self) -> None:
        lines = report.format_lines(report.from_findings([finding("lanes-swapped", Severity.NOTE)]))
        assert lines[0] == "no errors"
        assert "errors:" not in lines and "facts:" in lines

    @pytest.mark.parametrize(("count", "text"), [(0, "no errors"), (1, "1 error"), (3, "3 errors")])
    def test_verdict_wording(self, count: int, text: str) -> None:
        assert report.verdict(count) == text


@pytest.fixture
def plan_file(tmp_path: Path) -> Path:
    recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"label": "one block", "blocks": [block_spec(recipe, machine, [2])]}), encoding="utf-8")
    return path


class TestBuildOutput:
    def test_the_printout_gives_a_verdict_then_errors_then_facts(self, plan_file, capsys: pytest.CaptureFixture) -> None:
        assert cli.main(["build", str(plan_file), "--no-render"]) == 0
        lines = capsys.readouterr().out.splitlines()
        assert lines[0].startswith("one block: ") and lines[0].endswith(("no errors", "error", "errors"))
        assert "facts:" in lines
        if "errors:" in lines:
            assert lines.index("errors:") < lines.index("facts:")
        assert any("entities" in line for line in lines)
        assert not any(line.lstrip().startswith(("[problem]", "[suspect]", "[note]", "warning:")) for line in lines)

    def test_report_json_has_errors_and_facts_and_keeps_the_old_fields(
        self, plan_file, tmp_path, capsys: pytest.CaptureFixture
    ) -> None:
        assert cli.main(["build", str(plan_file), "--no-render", "-o", str(tmp_path / "out")]) == 0
        (path,) = (tmp_path / "out").glob("*.report.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data["errors"], list) and isinstance(data["facts"], list)
        for entry in data["errors"] + data["facts"]:
            assert isinstance(entry["text"], str) and entry["text"] and "kind" in entry
        for entry in data["errors"]:
            assert entry["code"] in report.RULES
        # compatibility
        assert {"findings", "warnings", "blocks", "routes", "blueprint"} <= set(data)
        # the same findings are split, not lost: each is an error or a fact
        coded = {e["code"] for e in data["errors"] + data["facts"] if e.get("code")}
        assert {f["code"] for f in data["findings"]} <= coded

    def test_a_block_is_a_fact_not_an_error(self, plan_file, tmp_path, capsys: pytest.CaptureFixture) -> None:
        cli.main(["build", str(plan_file), "--no-render", "-o", str(tmp_path / "out")])
        (path,) = (tmp_path / "out").glob("*.report.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert any(f["kind"] == "block" for f in data["facts"])
        assert not any(e["kind"] == "block" for e in data["errors"])

    def test_a_note_every_block_shares_is_said_once(self) -> None:
        class Block:
            def __init__(self, index: int) -> None:
                self.index, self.notes = index, ["same thing assumed", f"own {index}"]
                self.machines, self.machine, self.recipe = 1, "m", "r"
                self.width = self.height = 1
                self.at = (0, 0)
                self.planned_crafts_per_second, self.crafts_per_second = None, 1.0
                self.rows, self.ports = [], []

        class Result:
            findings, routes, warnings, power_networks = [], [], [], 1
            blueprint = type("B", (), {"entities": []})()
            blocks = [Block(0), Block(1)]

        out = report.from_build(Result())
        said = [f.text for f in out.facts if f.text.startswith("assumed: same thing")]
        assert len(said) == 1 and "blocks 0, 1" in said[0]


class TestCheckOutput:
    def test_a_blueprint_with_an_idle_machine_gives_an_error_first(self, tmp_path, capsys: pytest.CaptureFixture) -> None:
        bp = Blueprint()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bp.entities.append(prototypes.assembler(), tile_position=(0, 0))
        path = tmp_path / "bp.txt"
        path.write_text(bp.to_string(), encoding="utf-8")
        assert cli.main(["check", str(path)]) == 0
        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == "1 error"
        assert lines.index("errors:") < lines.index("facts:")
        assert any("no recipe" in line for line in lines)

    def test_a_clean_blueprint_says_no_errors(self, tmp_path, capsys: pytest.CaptureFixture) -> None:
        bp = Blueprint()
        bp.entities.append(prototypes.chest(), tile_position=(0, 0))
        path = tmp_path / "bp.txt"
        path.write_text(bp.to_string(), encoding="utf-8")
        assert cli.main(["check", str(path)]) == 0
        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == "no errors"
        assert "errors:" not in lines


def _belts_by_speed() -> list[str]:
    from draftsman.data import entities as entity_data

    from factorio_forge import layout

    names = [n for n, e in entity_data.raw.items() if e.get("type") == "transport-belt" and "speed" in e]
    return sorted(names, key=lambda n: layout.belt_throughput(n))


def _route(lanes, rate, surface, underground=None, ok=True):
    ends = SimpleNamespace(items=tuple(lanes))
    spec = SimpleNamespace(surface=surface, underground=underground, start=ends, goal=ends)
    return SimpleNamespace(id="r", kind="belt", ok=ok, rate=rate, lanes=tuple(lanes), spec=spec)


class TestBeltLoad:
    def test_one_lane_is_measured_against_one_lane(self) -> None:
        from factorio_forge import layout

        belt = _belts_by_speed()[0]
        cap = layout.lane_throughput(belt)
        line = report._belt_load(_route(("", "copper-cable"), cap / 2, belt))
        assert line is not None and line.level == report.FACT
        assert "copper-cable lane is 50% loaded" in line.text
        assert f"{cap:.4g}/s" in line.text and belt in line.text

    def test_two_lanes_are_measured_against_the_whole_belt(self) -> None:
        from factorio_forge import layout

        belt = _belts_by_speed()[0]
        cap = layout.belt_throughput(belt)
        line = report._belt_load(_route(("a", "b"), cap / 4, belt))
        assert "belt is 25% loaded" in line.text

    def test_the_slower_underground_sets_the_capacity(self) -> None:
        from factorio_forge import layout

        belts = _belts_by_speed()
        if len({layout.belt_throughput(b) for b in belts}) < 2:
            pytest.skip("the active data has only one belt speed")
        slow, fast = belts[0], belts[-1]
        for surface, underground in ((fast, slow), (slow, fast)):
            line = report._belt_load(_route(("a", "b"), layout.belt_throughput(slow) / 2, surface, underground))
            assert "50% loaded" in line.text and slow in line.text

    def test_a_failed_route_has_no_load_and_no_fact_line(self) -> None:
        belt = _belts_by_speed()[0]
        failed = _route(("a", ""), 1.0, belt, ok=False)
        assert report._belt_load(failed) is None
        assert report._route_line(failed) is None


class _Result:
    """The parts of a build result `from_build` reads."""

    def __init__(self, warnings=(), routes=()) -> None:
        self.findings, self.blocks = [], []
        self.routes, self.warnings, self.power_networks = list(routes), list(warnings), 1
        self.blueprint = type("B", (), {"entities": []})()


class TestBuildWarnings:
    def test_a_build_warning_is_an_error_with_a_hint(self) -> None:
        out = report.from_build(_Result(["request missing.json: no such file"]))
        assert [e.kind for e in out.errors] == ["warning"]
        assert out.errors[0].detail and "missing.json" in out.errors[0].text
        assert not any(f.kind == "assumed" and "missing.json" in f.text for f in out.facts)
        assert report.format_lines(out, "plan")[0] == "plan: 1 error"

    def test_a_plan_naming_a_missing_request_file_counts_as_an_error(
        self, plan_file, tmp_path, capsys: pytest.CaptureFixture
    ) -> None:
        data = json.loads(plan_file.read_text(encoding="utf-8"))
        data["request"] = "no-such-request.json"
        plan_file.write_text(json.dumps(data), encoding="utf-8")
        assert cli.main(["build", str(plan_file), "--no-render", "-o", str(tmp_path / "out")]) == 0
        lines = capsys.readouterr().out.splitlines()
        assert lines[0].endswith("error") or lines[0].endswith("errors")
        assert not lines[0].endswith("no errors")
        assert any("no-such-request.json" in l for l in lines[: lines.index("facts:")])


class TestPosition:
    def test_a_position_already_in_the_text_is_not_repeated(self) -> None:
        f = Finding(Severity.PROBLEM, "overlap", "nothing enters at (0, 12)", "", (0, 12))
        text = chr(10).join(report.format_lines(report.from_findings([f])))
        assert text.count("(0, 12)") == 1

    def test_a_position_not_in_the_text_is_added(self) -> None:
        f = Finding(Severity.PROBLEM, "overlap", "two things overlap", "", (3, 4))
        assert "  two things overlap (3, 4)" in report.format_lines(report.from_findings([f]))


class TestCheckHiddenFacts:
    def _blueprint(self, tmp_path: Path) -> Path:
        bp = Blueprint()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bp.entities.append(prototypes.chest(), tile_position=(0, 0))
        path = tmp_path / "bp.txt"
        path.write_text(bp.to_string(), encoding="utf-8")
        return path

    def test_all_shows_the_facts_without_it_they_are_counted(
        self, tmp_path, monkeypatch, capsys: pytest.CaptureFixture
    ) -> None:
        fact = Finding(Severity.NOTE, "lanes-swapped", "lanes swapped", "", None)
        real = inspection.inspect

        def fake(bp):
            out = real(bp)
            out.findings = list(out.findings) + [fact]
            return out

        monkeypatch.setattr(inspection, "inspect", fake)
        path = self._blueprint(tmp_path)
        cli.main(["check", str(path)])
        short = capsys.readouterr().out
        assert "lanes swapped" not in short and "1 more fact hidden" in short
        cli.main(["check", str(path), "--all"])
        full = capsys.readouterr().out
        assert "lanes swapped" in full and "hidden" not in full
