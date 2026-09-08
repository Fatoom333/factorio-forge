"""Our reading of the rules, held against the game's own answer.

Everything here is checked against a recording made inside a running Factorio:
a city block from a Krastorio 2 save, exported by the companion mod together
with what the game itself says about every underground run in it -- each
entity's partner, named by the engine rather than inferred from directions and
distances.

That inference went wrong twice while this was being written, in opposite
directions, and each time the argument was settled by looking at the picture
again rather than by asking. These tests exist so the question is never
reopened by reasoning: 144 underground entities, and the game's answer for each.

The fixture is Krastorio 2 data. Where the active profile does not know those
prototypes the comparison cannot be made, and the test says so instead of
failing.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from draftsman.data import entities as entity_data

from factorio_forge import inspection

FIXTURES = Path(__file__).parent / "fixtures"
PAIRS = FIXTURES / "underground-pairs-from-the-game.json"


def recorded_runs() -> list[dict]:
    runs = json.loads(PAIRS.read_text(encoding="utf-8"))["runs"]
    missing = {run["name"] for run in runs} - set(entity_data.raw)
    if missing:
        pytest.skip(f"the active game data does not know {sorted(missing)[0]} and others")
    return runs


def underground_step(run: dict) -> tuple[int, int]:
    """Which way we believe the run leaves the entity."""

    class Stub:
        name = run["name"]

    if run["type"] == "pipe-to-ground":
        offset = inspection.underground_direction(Stub()) or 0
        return inspection.STEP[(run["direction"] + offset) % 16]

    step = inspection.STEP[run["direction"]]
    if run.get("io_type") == "output":
        return (-step[0], -step[1])
    return step


def sign(value: float) -> int:
    return 0 if value == 0 else int(math.copysign(1, value))


class TestUndergroundRunsAgainstTheGame:
    def test_the_recording_is_the_one_we_think(self) -> None:
        runs = recorded_runs()
        assert len(runs) == 144
        kinds = {run["type"] for run in runs}
        assert kinds == {"pipe-to-ground", "underground-belt"}

    def test_every_partner_lies_the_way_we_say_it_does(self) -> None:
        """The whole argument, settled once: 142 pairs, and our direction for each."""
        runs = recorded_runs()
        checked = 0
        for run in runs:
            partner = next((p for p in run["partners"] if p["name"] == run["name"]), None)
            if partner is None:
                continue
            step = underground_step(run)
            towards = (sign(partner["x"] - run["x"]), sign(partner["y"] - run["y"]))
            assert towards == step, (
                f"{run['name']} at ({run['x']}, {run['y']}) facing {run['direction']}: "
                f"the game puts its partner {towards}, we look {step}"
            )
            checked += 1
        assert checked == 142

    def test_the_ones_the_game_calls_unpaired_are_the_ones_we_report(self) -> None:
        runs = recorded_runs()
        unpaired = {
            (run["x"], run["y"])
            for run in runs
            if not any(p["name"] == run["name"] for p in run["partners"])
        }
        assert unpaired == {(-545.5, -755.5), (-545.5, -736.5)}

        from draftsman.blueprintable import get_blueprintable_from_string as parse

        blueprint = parse((FIXTURES / "city-block-krastorio.txt").read_text(encoding="utf-8").strip())
        reported = {
            (f.position[0] + 0.5, f.position[1] + 0.5)
            for f in inspection.inspect(blueprint).findings
            if f.code == "underground-unpaired"
        }
        assert reported == unpaired

    def test_an_unpaired_pipe_is_only_a_note(self) -> None:
        """It caps the run rather than carrying anything, which is often the point."""
        from draftsman.blueprintable import get_blueprintable_from_string as parse

        recorded_runs()
        blueprint = parse((FIXTURES / "city-block-krastorio.txt").read_text(encoding="utf-8").strip())
        for finding in inspection.inspect(blueprint).findings:
            if finding.code == "underground-unpaired":
                assert finding.severity is inspection.Severity.NOTE
