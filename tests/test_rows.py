"""Tests for expanding a row layout into entities, against the active data.

The point of `rows.py` is that its output is right for whatever mod set is
active, so these tests do not name vanilla prototypes: they ask the data for a
recipe of a given shape and a machine that crafts it (`tests/prototypes.py`),
build a block, and hold the result to what the game would hold it to -- the
checker, the fluid networks, where every inserter actually takes from and
puts to.
"""

from __future__ import annotations

import pytest

import prototypes
from factorio_forge import fluids, inspection, layout, plan, rows

MACHINES = {"assembling-machine", "furnace"}


def spec(recipe: str, machine: str, counts: list[int], **extra) -> rows.RowBlockSpec:
    fields = dict(
        recipe=recipe,
        machine=machine,
        rows=counts,
        belt=prototypes.fastest_belt(),
        inserter=prototypes.inserter_reaching(1),
        pole=prototypes.small_pole(),
        stack_size=12,
    )
    fields.update(extra)
    return rows.RowBlockSpec(**fields)


def built(block_spec: rows.RowBlockSpec, rotate: int = 0) -> plan.BuildResult:
    fields = {k: v for k, v in block_spec.__dict__.items() if v is not None}
    fields["rotate"] = rotate
    return plan.build({"label": "test", "blocks": [fields]})


def wrong_inserters(blueprint) -> list:
    """Inserters that do not take from a belt into a machine, or the reverse."""
    layout_view = inspection.Layout(blueprint)
    wrong = []
    for entity in blueprint.entities:
        if entity.type != "inserter":
            continue
        pickup, drop = inspection.inserter_reach(entity)
        source = {e.type for e in layout_view.at(*pickup)}
        target = {e.type for e in layout_view.at(*drop)}
        feeds = "transport-belt" in source and target & MACHINES
        empties = source & MACHINES and "transport-belt" in target
        if not (feeds or empties):
            wrong.append((entity.name, tuple(entity.tile_position), source, target))
    return wrong


def assert_sound(result: plan.BuildResult) -> None:
    serious = [f for f in result.findings if f.severity is not inspection.Severity.NOTE]
    assert serious == [], [str(f) for f in serious]
    assert wrong_inserters(result.blueprint) == []
    assert result.power_networks == 1


# --------------------------------------------------------------------------


class TestItemRows:
    def test_a_plain_row_builds_clean(self) -> None:
        recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
        assert_sound(built(spec(recipe, machine, [4, 4, 3])))

    def test_mirrored_rows_share_belts_at_the_measured_pitch(self) -> None:
        # The measured base: belt, inserters, machines, inserters, shared belt.
        # Two mirrored 3-tile rows take 3 + 1 + 1 + 1 per row plus the belts
        # shared between them.
        recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
        block = rows.build_block(spec(recipe, machine, [4, 4]))
        size = layout.machine_size(machine)[1]
        assert block.height == 2 * size + 4 + 3  # arms x4, belts: in, shared out, in

    def test_repeat_stacking_shares_nothing(self) -> None:
        recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
        mirror = rows.build_block(spec(recipe, machine, [4, 4]))
        repeat = rows.build_block(spec(recipe, machine, [4, 4], stack="repeat"))
        assert repeat.height > mirror.height
        assert_sound(built(spec(recipe, machine, [4, 4], stack="repeat")))

    def test_two_ingredients_ride_one_belt_a_lane_each(self) -> None:
        recipe, machine = prototypes.crafting_setup(2, 0, 1, 0)
        block = rows.build_block(spec(recipe, machine, [3]))
        inputs = [p for p in block.ports if p.io == "in" and p.kind == "belt"]
        assert len(inputs) == 1
        assert len(set(inputs[0].items)) == 2
        assert_sound(built(spec(recipe, machine, [3, 3])))

    def test_three_ingredients_use_a_long_handed_inserter(self) -> None:
        recipe, machine = prototypes.crafting_setup(3, 0, 1, 0)
        with pytest.raises(layout.LayoutError, match="long_inserter"):
            rows.build_block(spec(recipe, machine, [3]))
        long_arm = prototypes.inserter_reaching(2)
        result = built(spec(recipe, machine, [3, 3, 3], long_inserter=long_arm))
        assert_sound(result)
        assert any(e.name == long_arm for e in result.blueprint.entities)

    def test_rows_can_differ_in_length_and_be_centred(self) -> None:
        recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
        result = built(spec(recipe, machine, [2, 5, 5, 2], align="center"))
        assert_sound(result)

    def test_turning_the_block_keeps_it_sound_and_swaps_its_size(self) -> None:
        recipe, machine = prototypes.crafting_setup(2, 0, 1, 0)
        straight = built(spec(recipe, machine, [4, 3]))
        turned = built(spec(recipe, machine, [4, 3]), rotate=90)
        assert_sound(turned)
        block, other = straight.blocks[0], turned.blocks[0]
        assert (other.width, other.height) == (block.height, block.width)

    def test_overfull_rows_are_reported_not_refused(self) -> None:
        recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
        capacity = layout.row_capacity(recipe, machine, prototypes.belt())
        if not capacity.per_row or capacity.per_row > 40:
            pytest.skip("the slowest belt still feeds a very long row of this recipe")
        block = rows.build_block(spec(recipe, machine, [capacity.per_row + 1], belt=prototypes.belt()))
        assert any("keep" in note for note in block.notes)


class TestFluidRows:
    def test_a_fluid_only_row_lays_its_pipe_in_the_arm_line(self) -> None:
        recipe, machine = prototypes.crafting_setup(0, 1, 0, 1)
        block = rows.build_block(spec(recipe, machine, [4, 4, 4]))
        size = layout.machine_size(machine)[1]
        # pipe, machines, shared pipe, machines, shared pipe, machines, pipe
        assert block.height == 3 * size + 4
        assert_sound(built(spec(recipe, machine, [4, 4, 4])))

    def test_items_and_a_fluid_dive_under_the_belts(self) -> None:
        recipe, machine = prototypes.crafting_setup(2, 1, 1, 0)
        result = built(spec(recipe, machine, [3, 3, 2]))
        assert_sound(result)
        kinds = {e.type for e in result.blueprint.entities}
        assert "pipe-to-ground" in kinds

    def test_pipe_ports_name_their_fluid_and_direction(self) -> None:
        recipe, machine = prototypes.crafting_setup(0, 1, 0, 1)
        block = rows.build_block(spec(recipe, machine, [2]))
        from draftsman.data import recipes

        entry = recipes.raw[recipe]
        wanted_in = {p["name"] for p in entry["ingredients"]}
        wanted_out = {p["name"] for p in entry["results"]}
        assert {p.items[0] for p in block.ports if p.io == "in"} == wanted_in
        assert {p.items[0] for p in block.ports if p.io == "out"} == wanted_out


class TestArms:
    def test_a_sideways_inserter_is_fitted_by_its_own_geometry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Mods can turn an inserter's pickup ninety degrees. Nothing assumes
        # north-to-south: the rotation is searched from the prototype.
        from draftsman.data import entities

        sideways = dict(entities.raw[prototypes.inserter_reaching(1)])
        sideways["pickup_position"] = [-1, 0]
        sideways["insert_position"] = [1.2, 0]
        patched = dict(entities.raw)
        patched["sideways-arm"] = sideways
        monkeypatch.setattr(rows.entity_data, "raw", patched)
        arm = rows.fit_arm("sideways-arm", "input", 3)
        assert arm.distance == 1

    def test_an_arm_that_cannot_reach_across_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from draftsman.data import entities

        odd = dict(entities.raw[prototypes.inserter_reaching(1)])
        odd["pickup_position"] = [-1, 0]
        odd["insert_position"] = [0, -1.2]  # picks and drops on the same side
        patched = dict(entities.raw)
        patched["odd-arm"] = odd
        monkeypatch.setattr(rows.entity_data, "raw", patched)
        with pytest.raises(layout.LayoutError, match="no rotation"):
            rows.fit_arm("odd-arm", "input", 3)


class TestPlan:
    def test_unknown_fields_are_refused(self) -> None:
        with pytest.raises(plan.PlanError, match="unknown"):
            plan.build({"blocks": [{"type": "rows", "recipe": "x", "machine": "y", "rows": [1], "colour": "red"}]})

    def test_a_refusal_names_the_block(self) -> None:
        recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
        with pytest.raises(plan.PlanError, match="block 0"):
            plan.build({"blocks": [{"recipe": recipe, "machine": machine, "rows": [2]}]})

    def test_the_blueprint_string_round_trips(self) -> None:
        from draftsman.blueprintable import get_blueprintable_from_string

        recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
        result = built(spec(recipe, machine, [3, 3]))
        again = get_blueprintable_from_string(result.to_dict()["blueprint"])
        assert len(again.entities) == len(result.blueprint.entities)

    def test_extra_entities_are_checked_with_the_blocks(self) -> None:
        recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
        fields = {k: v for k, v in spec(recipe, machine, [3]).__dict__.items() if v is not None}
        result = plan.build({
            "blocks": [fields],
            "entities": [{"name": machine, "position": [0, 0]}],  # right on top of the block
        })
        assert any(f.code == "overlap" for f in result.findings)


class TestFluidCheck:
    def test_two_fluids_meeting_in_a_pipe_are_found(self) -> None:
        recipe, machine = prototypes.crafting_setup(0, 1, 0, 1)
        result = built(spec(recipe, machine, [2]))
        entities = list(result.blueprint.entities)
        # Every pipe line of the row starts at the block's west edge, so one
        # pipe column just outside it joins the input and output fluids.
        ports = [p for p in result.blocks[0].ports if p["kind"] == "pipe"]
        from draftsman.blueprintable import Blueprint

        bridge = Blueprint()
        for entity in entities:
            bridge.entities.append(entity)
        column = min(e.tile_position.x for e in entities) - 1
        pipe = next(e.name for e in entities if e.type == "pipe")
        for y in range(min(p["y"] for p in ports), max(p["y"] for p in ports) + 1):
            bridge.entities.append(pipe, tile_position=(column, y))
        codes = {f.code for f in fluids.check(list(bridge.entities))}
        assert "fluids-mixed" in codes

    def test_a_machine_with_no_pipe_is_found(self) -> None:
        from draftsman.blueprintable import Blueprint

        recipe, machine = prototypes.crafting_setup(0, 1, 0, 1)
        lonely = Blueprint()
        lonely.entities.append(machine, tile_position=(0, 0), recipe=recipe)
        codes = {f.code for f in fluids.check(list(lonely.entities))}
        assert "fluid-box-unconnected" in codes
