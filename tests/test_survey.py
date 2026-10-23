import json

import pytest

from factorio_forge import survey


def _minimal(**extra):
    data = {"kind": "survey", "surface": "nauvis", "tick": 600, "window_s": 60,
            "machines": [{"unit": 1, "name": "assembling-machine-2", "position": [0.5, 0.5],
                          "recipe": "iron-gear-wheel",
                          "states": {"working": 0.7, "no_ingredients": 0.3},
                          "missing": {"iron-plate": 0.3}, "crafts_per_s": 1.1}],
            "miners": [{"unit": 2, "name": "electric-mining-drill", "position": [10, 10],
                        "resource": "iron-ore", "field": 1, "states": {"working": 1.0}}],
            "fields": [{"id": 1, "resource": "iron-ore", "amount": 5000, "tiles": 20,
                        "drills": 1, "center": [10, 10]}],
            "flows": {"items": {"iron-plate": {"made": 2.0, "used": 2.5}},
                      "fluids": {"water": {"made": 10}}},
            "power": [{"network": 1, "made_w": 1e6, "used_w": 9e5, "satisfaction": 1,
                       "machines": 3}],
            "blueprint_origin": [-4, -4]}
    data.update(extra)
    return data


def test_reads_every_part():
    read = survey.from_dict(_minimal())
    machine = read.machines[0]
    assert machine.working_share == pytest.approx(0.7)
    assert machine.missing == {"iron-plate": pytest.approx(0.3)}
    assert read.miners[0].field == 1
    assert read.fields[0].resource == "iron-ore"
    assert read.flow("iron-plate").used == pytest.approx(2.5)
    assert read.flow("water").made == pytest.approx(10)
    assert read.flow("copper-plate").made == 0
    assert read.power[0].satisfaction == 1
    assert read.blueprint_origin == (-4.0, -4.0)
    assert read.area is None


def test_refuses_what_is_not_a_survey():
    with pytest.raises(survey.SurveyError, match="not a survey"):
        survey.from_dict({"kind": "environment"})


def test_bad_entry_is_one_line():
    data = _minimal(machines=[{"unit": 1, "name": "x", "position": "here"}])
    with pytest.raises(survey.SurveyError, match="position"):
        survey.from_dict(data)


def test_load_accepts_a_byte_order_mark(tmp_path):
    path = tmp_path / "survey.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(_minimal()).encode())
    assert survey.load(path).surface == "nauvis"


def test_load_missing_file(tmp_path):
    with pytest.raises(survey.SurveyError, match="no survey"):
        survey.load(tmp_path / "nope.json")


def test_reads_labs_bounds_and_unmined_patches():
    read = survey.from_dict(_minimal(
        bounds=[[-10, -10], [20, 20]], research="automation",
        labs=[{"unit": 9, "name": "lab", "position": [3.5, 3.5],
               "states": {"working": 0.25, "missing_science_packs": 0.75}}],
        unmined=[{"resource": "tungsten-ore", "position": [300, 5], "distance": 280,
                  "amount": 1000, "enemies": {"worms": 4}}]))
    assert read.whole_base and read.bounds == ((-10.0, -10.0), (20.0, 20.0))
    assert read.labs[0].working_share == pytest.approx(0.25)
    assert read.research == "automation"
    assert read.unmined[0].enemies == {"worms": 4}
    assert read.fields[0].enemies is None


def test_area_without_bounds_is_read_as_asked_for():
    read = survey.from_dict(_minimal(area=[[0, 0], [5, 5]]))
    assert not read.whole_base and read.bounds == read.area


def test_zero_enemy_counts_mean_none_found():
    data = _minimal(unmined=[{"resource": "stone", "position": [1, 1], "distance": 0,
                              "enemies": {"spawners": 0, "worms": 0, "demolishers": 0}}])
    assert survey.from_dict(data).unmined[0].enemies == {}
