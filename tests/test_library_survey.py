"""Blocks that ran evenly in a survey of the base are cut out and kept in the library.

The blueprint of the surveyed area is built here from the active game data: a short row of machines with an
input belt, inserters and an output belt, as plan.build lays it out. A temporary profile holds the library.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import prototypes
from factorio_forge import cli, library, paths, plan, survey
from factorio_forge.profile import Profile
from factorio_forge.save import ModRef, SaveInfo
from test_route import block_spec

ORIGIN = (1000.0, -500.0)


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "forge"))
    return tmp_path


@pytest.fixture
def profile(monkeypatch: pytest.MonkeyPatch) -> Profile:
    save = SaveInfo(path=Path("Survey.zip"), name="Survey", game_version=(2, 0, 77), mods=(ModRef("base", (2, 0, 77)),))
    made = Profile.from_save(save)
    made.write()
    monkeypatch.setattr(Profile, "active_profile_name", staticmethod(lambda: made.name))
    return made


def row_blueprint(machines: int = 2):
    recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
    result = plan.build({"blocks": [block_spec(recipe, machine, [machines])]})
    return recipe, machine, result.blueprint


def survey_of(blueprint, recipe: str, states: dict[str, float], origin=ORIGIN, extra_machines: list[dict] | None = None,
              strip: bool = False, labels=None) -> survey.Survey:
    """A survey whose machines stand where the blueprint's machines do, all in the given states."""
    if strip:  # the same blueprint, with an unrelated belt far from the block, to show the cut leaves it out
        blueprint.entities.append(prototypes.belt(), tile_position=(60, 60), direction=0)
    machines = []
    for number, entity in enumerate(e for e in blueprint.entities if getattr(e, "recipe", None)):
        machines.append({
            "unit": number + 1, "name": entity.name,
            "position": [entity.position.x + origin[0], entity.position.y + origin[1]],
            "direction": int(entity.direction), "recipe": labels(entity) if labels else recipe, "crafting_speed": 1.0,
            "states": dict(states), "crafts_per_s": 0.5,
        })
    return survey.from_dict({
        "kind": "survey", "surface": "nauvis", "tick": 123456, "window_s": 60,
        "machines": machines + (extra_machines or []),
        "blueprint": blueprint.to_string(), "blueprint_origin": list(origin),
    })


class TestSaveFromSurvey:
    def test_an_even_row_is_cut_out_and_saved_with_its_ports(self, profile: Profile) -> None:
        recipe, machine, blueprint = row_blueprint()
        found = survey_of(blueprint, recipe, {"working": 0.97, "no_ingredients": 0.03}, strip=True)
        report = library.save_from_survey(found, profile.name)
        assert len(report) == 1 and report[0]["saved"], report
        row = report[0]
        assert row["recipe"] == recipe and row["machines"] == 2
        assert row["why"].startswith("97% working over 60 s, 2 machines, ")
        entry = library.load(profile.library_dir, row["name"])
        assert entry["source"] == "forge"
        assert entry["found_from"].startswith("survey tick 123456, area ")
        assert entry["why"] == row["why"]
        assert entry["size"] == row["size"]
        assert entry["shape"]["recipe"] == recipe and entry["shape"]["machines"] == 2
        assert [(p["kind"], p["dir"]) for p in entry["ports"]] == [("belt", "in"), ("belt", "out")]
        # the belt far away is not part of the block
        from factorio_forge.library import read_blueprint

        assert len(read_blueprint(entry["blueprint"]).entities) < len(blueprint.entities)

    def test_the_name_is_the_recipe_and_the_centre(self) -> None:
        assert library._survey_name("iron-gear-wheel", 12.4, 30.6) == "iron-gear-wheel-12-31"
        assert library._survey_name("iron-gear-wheel", -12.2, 30) == "iron-gear-wheel-m12-30"
        library.check_name(library._survey_name("a" * 80, -1, -1))

    def test_a_cluster_at_sixty_percent_is_not_saved(self, profile: Profile) -> None:
        recipe, _, blueprint = row_blueprint()
        found = survey_of(blueprint, recipe, {"working": 0.6, "no_ingredients": 0.4})
        report = library.save_from_survey(found, profile.name)
        assert [r["saved"] for r in report] == [False]
        assert "60%" in report[0]["reason"]
        assert library.entries(profile.library_dir)[0] == []

    def test_one_machine_that_waits_keeps_the_block_out_even_when_the_mean_is_high(self, profile: Profile) -> None:
        recipe, _, blueprint = row_blueprint(3)
        found = survey_of(blueprint, recipe, {"working": 1.0})
        found.machines[0].states = {"working": 0.8, "full_output": 0.2}
        report = library.save_from_survey(found, profile.name)
        assert report[0]["saved"] is False and "waited" in report[0]["reason"]

    @pytest.mark.parametrize("state", ["full_output", "item_ingredient_shortage", "fluid_ingredient_shortage",
                                       "waiting_for_space_in_destination"])
    def test_the_games_own_waiting_statuses_count(self, profile: Profile, state: str) -> None:
        recipe, _, blueprint = row_blueprint(3)
        found = survey_of(blueprint, recipe, {"working": 1.0})
        found.machines[1].states = {"working": 0.85, state: 0.15}
        report = library.save_from_survey(found, profile.name)
        assert report[0]["saved"] is False and "waited" in report[0]["reason"]

    def test_a_lower_bar_takes_what_a_higher_one_refuses(self, profile: Profile) -> None:
        recipe, _, blueprint = row_blueprint()
        found = survey_of(blueprint, recipe, {"working": 0.95, "no_ingredients": 0.05})
        assert library.save_from_survey(found, profile.name, min_share=0.99, dry_run=True)[0]["saved"] is False
        assert library.save_from_survey(found, profile.name, min_share=0.9, dry_run=True)[0]["saved"] is True

    def test_a_single_machine_is_not_a_block(self, profile: Profile) -> None:
        recipe, _, blueprint = row_blueprint(1)
        found = survey_of(blueprint, recipe, {"working": 1.0})
        assert library.save_from_survey(found, profile.name) == []

    def test_the_same_block_a_second_time_is_skipped(self, profile: Profile) -> None:
        recipe, _, blueprint = row_blueprint()
        found = survey_of(blueprint, recipe, {"working": 1.0})
        assert library.save_from_survey(found, profile.name)[0]["saved"] is True
        again = library.save_from_survey(found, profile.name)
        assert again[0]["saved"] is False and "already" in again[0]["reason"]
        # the same shape standing somewhere else in the world
        moved = survey_of(blueprint, recipe, {"working": 1.0}, origin=(2000.0, 700.0))
        twin = library.save_from_survey(moved, profile.name)
        assert twin[0]["saved"] is False and "shape and recipe" in twin[0]["reason"]
        assert len(library.entries(profile.library_dir)[0]) == 1

    def test_dry_run_writes_nothing(self, profile: Profile) -> None:
        recipe, _, blueprint = row_blueprint()
        found = survey_of(blueprint, recipe, {"working": 1.0})
        report = library.save_from_survey(found, profile.name, dry_run=True)
        assert report[0]["saved"] is True
        assert not profile.library_dir.exists() or not list(profile.library_dir.iterdir())

    def test_other_machines_inside_the_cut_stop_it(self, profile: Profile) -> None:
        recipe, machine, blueprint = row_blueprint()
        near = [e for e in blueprint.entities if getattr(e, "recipe", None)][0]
        stranger = {"unit": 99, "name": machine, "position": [near.position.x + ORIGIN[0], near.position.y + ORIGIN[1] + 4],
                    "recipe": "some-other-recipe", "states": {"working": 1.0}}
        found = survey_of(blueprint, recipe, {"working": 1.0}, extra_machines=[stranger])
        report = library.save_from_survey(found, profile.name)
        assert report[0]["saved"] is False and "other machines" in report[0]["reason"]

    def test_two_rows_of_different_recipes_across_one_belt_are_cut_apart(self, profile: Profile) -> None:
        recipe, blueprint = merged_facing_rows()
        found = survey_of(blueprint, recipe, {"working": 1.0},
                          labels=lambda e: recipe if e.position.y < 6 else "second-" + recipe)
        report = library.save_from_survey(found, profile.name)
        assert [r["saved"] for r in report] == [True, True], report
        upper, lower = (library.load(profile.library_dir, r["name"]) for r in report)
        rows = {}
        for key, entry in (("upper", upper), ("lower", lower)):
            rows[key] = list(library.read_blueprint(entry["blueprint"]).entities)
            assert [(p["kind"], p["dir"]) for p in entry["ports"]] == [("belt", "in"), ("belt", "out")], entry["ports"]
        # machines stand at y 2..4 and 8..10, the shared belt is y 6: neither block holds the other row's inserters
        assert {e.position.y for e in rows["upper"] if e.type == "inserter"} == {1.5, 5.5}
        assert {e.position.y for e in rows["lower"] if e.type == "inserter"} == {7.5, 11.5}

    def test_a_fluid_row_keeps_its_pipe_track_as_one_line(self, profile: Profile) -> None:
        recipe, machine = prototypes.crafting_setup(1, 1, 1, 0)
        built = plan.build({"blocks": [block_spec(recipe, machine, [3])]}).blueprint
        found = survey_of(built, recipe, {"working": 1.0})
        report = library.save_from_survey(found, profile.name)
        assert report[0]["saved"] is True, report
        entry = library.load(profile.library_dir, report[0]["name"])
        assert len(library.read_blueprint(entry["blueprint"]).entities) == len(built.entities)
        assert [p["dir"] for p in entry["ports"] if p["kind"] == "pipe"] == ["in"]

    def test_the_measured_rate_names_the_products(self, profile: Profile) -> None:
        recipe, _, blueprint = row_blueprint()
        found = survey_of(blueprint, recipe, {"working": 1.0})
        library.save_from_survey(found, profile.name)
        entry = library.entries(profile.library_dir)[0][0][1]
        results = [part["name"] for part in library.recipe_data.raw[recipe]["results"]]
        assert set(entry["measured"]["rate"]) == set(results)
        assert all(v > 0 for v in entry["measured"]["rate"].values())

    def test_a_blueprint_field_that_names_a_file_is_not_read(self, profile: Profile, tmp_path: Path) -> None:
        recipe, _, blueprint = row_blueprint()
        found = survey_of(blueprint, recipe, {"working": 1.0})
        elsewhere = tmp_path / "somewhere.txt"
        elsewhere.write_text(blueprint.to_string(), encoding="utf-8")
        found.blueprint = str(elsewhere)
        report = library.save_from_survey(found, profile.name)
        assert report[0]["saved"] is False and "not a blueprint string" in report[0]["reason"]
        assert library.entries(profile.library_dir)[0] == []

    def test_a_recipe_with_path_tricks_writes_only_inside_the_library(self, profile: Profile, tmp_path: Path) -> None:
        recipe, _, blueprint = row_blueprint()
        found = survey_of(blueprint, recipe, {"working": 1.0}, labels=lambda e: "../../evil")
        before = set(tmp_path.rglob("*"))
        report = library.save_from_survey(found, profile.name)
        assert report and "/" not in report[0]["name"] and ".." not in report[0]["name"]
        created = set(tmp_path.rglob("*")) - before
        assert created and all(profile.library_dir in p.parents or p == profile.library_dir for p in created), created

    def test_the_grid_finds_the_same_clusters_as_comparing_every_pair(self) -> None:
        import random

        rng = random.Random(7)

        class M:
            def __init__(self, recipe: str) -> None:
                self.recipe = recipe

        machines = [M(rng.choice("ab")) for _ in range(150)]
        boxes = []
        for _ in machines:
            x, y = rng.randrange(0, 90), rng.randrange(-40, 40)
            boxes.append(library.Box(x, y, x + 2, y + 2))
        parent = list(range(len(machines)))

        def find(i: int) -> int:
            while parent[i] != i:
                i = parent[i]
            return i

        for i in range(len(machines)):
            for j in range(i + 1, len(machines)):
                if machines[i].recipe == machines[j].recipe and library._gap(boxes[i], boxes[j]) <= library.CLUSTER_GAP:
                    parent[find(i)] = find(j)
        expected = {frozenset(i for i in range(len(machines)) if find(i) == root) for root in {find(i) for i in range(len(machines))}}
        assert {frozenset(g) for g in library._clusters(machines, boxes)} == expected

    def test_a_survey_without_a_blueprint_says_so(self, profile: Profile) -> None:
        recipe, _, blueprint = row_blueprint()
        found = survey_of(blueprint, recipe, {"working": 1.0})
        found.blueprint = None
        report = library.save_from_survey(found, profile.name)
        assert report[0]["saved"] is False and "no blueprint" in report[0]["reason"]

    def test_a_cut_that_fails_the_check_is_skipped_with_the_reason(self, profile: Profile, monkeypatch: pytest.MonkeyPatch) -> None:
        recipe, _, blueprint = row_blueprint()
        found = survey_of(blueprint, recipe, {"working": 1.0})
        monkeypatch.setattr(library, "problems_in", lambda bp: _one_problem())
        report = library.save_from_survey(found, profile.name)
        assert report[0]["saved"] is False and "error" in report[0]["reason"]
        assert library.entries(profile.library_dir)[0] == []

    def test_a_name_with_path_tricks_is_refused(self, profile: Profile) -> None:
        recipe, _, blueprint = row_blueprint()
        found = survey_of(blueprint, recipe, {"working": 1.0})
        for machine in found.machines:
            machine.recipe = "../../evil"
        # the recipe is turned into a safe name; nothing can leave the library folder
        name = library._survey_name("../../evil", 1, 2)
        assert library.check_name(name) == name and "/" not in name and ".." not in name
        with pytest.raises(library.LibraryError):
            library.make_entry("../x", blueprint, source="forge", found_from="t")
        report = library.save_from_survey(found, profile.name, dry_run=True)
        assert all(r["name"] == library.check_name(r["name"]) for r in report)


def merged_facing_rows():
    """Two rows of the same shape facing each other across one belt: inserter, belt, inserter."""
    recipe, _, upper = row_blueprint()
    _, _, lower = row_blueprint()
    count = len(upper.entities)
    for entity in list(lower.entities):
        if entity.type == "transport-belt" and entity.position.y < 1:  # its input belt is the upper row's output belt
            continue
        moved = copy.deepcopy(entity)
        moved.position = (entity.position.x, entity.position.y + 6)
        upper.entities.append(moved)
    assert len(upper.entities) > count
    return recipe, upper


def _one_problem():
    class Finding:
        code, summary, position = "overlap", "two things on one tile", None
    return [Finding()]


class TestCommand:
    def write(self, tmp_path: Path, found: survey.Survey, blueprint) -> Path:
        path = tmp_path / "survey.json"
        path.write_text(json.dumps({
            "kind": "survey", "surface": "nauvis", "tick": 5, "window_s": 30,
            "machines": [{"unit": m.unit, "name": m.name, "position": list(m.position), "direction": m.direction,
                          "recipe": m.recipe, "states": m.states, "crafts_per_s": m.crafts_per_s} for m in found.machines],
            "blueprint": blueprint.to_string(), "blueprint_origin": list(ORIGIN)}), encoding="utf-8")
        return path

    def test_it_prints_one_line_per_cluster_and_dry_run_writes_nothing(self, profile: Profile, tmp_path: Path, capsys) -> None:
        recipe, _, blueprint = row_blueprint()
        path = self.write(tmp_path, survey_of(blueprint, recipe, {"working": 1.0}), blueprint)
        assert cli.main(["library", "add-from-survey", str(path), "--dry-run"]) == 0
        out = capsys.readouterr().out.strip().splitlines()
        assert len(out) == 1 and out[0].startswith("would save ")
        assert library.entries(profile.library_dir)[0] == []
        assert cli.main(["library", "add-from-survey", str(path)]) == 0
        assert capsys.readouterr().out.startswith("saved ")
        assert len(library.entries(profile.library_dir)[0]) == 1
        assert cli.main(["library", "add-from-survey", str(path), "--min-share", "0.5"]) == 0
        assert capsys.readouterr().out.startswith("skipped ")

    def test_a_missing_survey_is_one_line(self, profile: Profile, tmp_path: Path, capsys) -> None:
        assert cli.main(["library", "add-from-survey", str(tmp_path / "none.json")]) == 1
        assert "no survey" in capsys.readouterr().err
