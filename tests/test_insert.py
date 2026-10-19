"""Tests for library blocks: pasted into a plan, and offered to `chain` for a stage of their shape.

A library entry is written by the test into a temporary folder from a rows block built on the active data, so
the entry holds a real blueprint and ports that fit it; the active profile's own library is never read. Recipes
and machines are found by shape in the active data.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from draftsman.data import entities, recipes

import prototypes
from factorio_forge import bom, chain, inspection, insert, plan, rows
from factorio_forge.categories import crafts
from test_route import block_spec

ROTATIONS = (0, 90, 180, 270)


@pytest.fixture
def library(tmp_path, monkeypatch):
    folder = tmp_path / "library"
    folder.mkdir()
    monkeypatch.setattr(insert, "library_dir", lambda: folder)
    return folder


def make_entry(name: str, recipe: str, machine: str, counts=(2,), **spec) -> dict:
    """A library entry made from a rows block built on the active data."""
    fields = block_spec(recipe, machine, list(counts), **spec)
    result = plan.build({"blocks": [fields]})
    block = result.blocks[0]
    shape = insert.recipe_shape(recipe)
    return {
        "name": name, "source": "player", "found_from": "a test",
        "blueprint": result.blueprint.to_string(), "size": [block.width, block.height],
        "ports": [{"id": f"p{p['index']}", "kind": p["kind"], "dir": p["io"], "at": [p["x"], p["y"]],
                   "direction": p["direction"], "items": list(p["items"]), "rate": p["rate"]} for p in block.ports],
        "shape": {"machine": machine, "recipe": recipe, **shape,
                  "tiers": {"belt": fields["belt"], "inserter": fields["inserter"], "pole": fields["pole"]}},
    }


def store(folder, entry: dict) -> dict:
    (folder / f"{entry['name']}.json").write_text(json.dumps(entry), encoding="utf-8")
    return entry


def setup_1011():
    recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
    return recipe, machine


def other_recipe(recipe: str, machine: str, same_shape: bool = True) -> str | None:
    shape = insert.recipe_shape(recipe)
    for name in sorted(recipes.raw):
        data = recipes.raw[name]
        if name == recipe or not data or not crafts(entities.raw[machine], data):
            continue
        if any(p.get("type") == "fluid" for p in (data.get("ingredients") or [])) != bool(shape["fluid_in"]):
            continue
        if any("amount" not in p for p in data.get("results", [])):
            continue
        if (insert.recipe_shape(name) == shape) == same_shape:
            return name
    return None


def entity_boxes(blueprint) -> set[tuple]:
    return {(e.name, int(e.tile_position.x), int(e.tile_position.y), e.tile_width, e.tile_height)
            for e in blueprint.entities}


def corner(blueprint) -> tuple[int, int]:
    boxes = entity_boxes(blueprint)
    return min(b[1] for b in boxes), min(b[2] for b in boxes)


def belts_at(blueprint) -> dict[tuple[int, int], int]:
    return {(int(e.tile_position.x), int(e.tile_position.y)): int(e.direction) for e in blueprint.entities
            if (entities.raw.get(e.name) or {}).get("type") in ("transport-belt", "underground-belt")}


# --------------------------------------------------------------------------
# names and files
# --------------------------------------------------------------------------


class TestNames:
    @pytest.mark.parametrize("name", ["", "A", "a/b", "..", "../x", "a b", "a.json", "-a", "a_b", None, 3])
    def test_a_name_that_is_not_lowercase_letters_digits_and_dashes_is_refused(self, name) -> None:
        with pytest.raises(plan.PlanError, match="lowercase letters, digits and dashes"):
            insert.check_name(name)

    def test_a_name_is_never_used_as_a_path(self, library, tmp_path) -> None:
        (tmp_path / "secret.json").write_text("{}", encoding="utf-8")
        with pytest.raises(plan.PlanError, match="lowercase letters"):
            plan.build({"blocks": [{"library": "../secret"}]})

    def test_an_unknown_name_is_one_line(self, library) -> None:
        with pytest.raises(plan.PlanError) as caught:
            plan.build({"blocks": [{"library": "nothing-here"}]})
        text = str(caught.value)
        assert "\n" not in text and "nothing-here" in text and "does not exist" in text and text.startswith("block 0")

    def test_the_message_lists_what_the_library_has(self, library) -> None:
        store(library, {"name": "alpha", "blueprint": "x"})
        with pytest.raises(plan.PlanError, match="alpha"):
            insert.read_entry("beta")

    @pytest.mark.parametrize("damage", [
        {"blueprint": None}, {"ports": [{"at": [0, 0]}]}, {"size": [0, 3]},
        {"ports": [{"kind": "belt", "dir": "in", "at": [0, 0], "direction": 5}]},
    ])
    def test_a_malformed_entry_is_one_line(self, library, damage) -> None:
        store(library, {"name": "bad", "blueprint": "x", **damage})
        with pytest.raises(plan.PlanError) as caught:
            insert.read_entry("bad")
        assert "\n" not in str(caught.value) and "'bad'" in str(caught.value)

    def test_a_block_takes_only_its_own_fields(self, library) -> None:
        recipe, machine = setup_1011()
        store(library, make_entry("one", recipe, machine))
        with pytest.raises(plan.PlanError, match="rows"):
            plan.build({"blocks": [{"library": "one", "rows": 3}]})
        with pytest.raises(plan.PlanError, match="rotate"):
            plan.build({"blocks": [{"library": "one", "rotate": 45}]})
        with pytest.raises(plan.PlanError, match="mirror"):
            plan.build({"blocks": [{"library": "one", "mirror": "yes"}]})


# --------------------------------------------------------------------------
# pasting: rotation, mirror, ports
# --------------------------------------------------------------------------


class TestPaste:
    @pytest.fixture
    def kept(self, library):
        recipe, machine = setup_1011()
        entry = store(library, make_entry("block-a", recipe, machine, (2, 2)))
        original = plan.build({"blocks": [block_spec(recipe, machine, [2, 2])]})
        return entry, original, recipe, machine

    @pytest.mark.parametrize("rotate", ROTATIONS)
    def test_a_pasted_block_turns_exactly_like_a_rows_block(self, kept, rotate: int) -> None:
        entry, original, recipe, machine = kept
        at = [7, -3]
        oracle = plan.build({"blocks": [{**block_spec(recipe, machine, [2, 2]), "rotate": rotate, "at": at}]})
        pasted = plan.build({"blocks": [{"library": "block-a", "rotate": rotate, "at": at}]})
        want, got = oracle.blocks[0], pasted.blocks[0]
        assert (got.width, got.height) == (want.width, want.height)
        assert [(p["kind"], p["io"], p["items"], p["x"], p["y"], p["direction"]) for p in got.ports] == \
               [(p["kind"], p["io"], p["items"], p["x"], p["y"], p["direction"]) for p in want.ports]
        assert entity_boxes(pasted.blueprint) == entity_boxes(oracle.blueprint)

    @pytest.mark.parametrize("rotate", ROTATIONS)
    def test_the_corner_goes_to_at(self, kept, rotate: int) -> None:
        pasted = plan.build({"blocks": [{"library": "block-a", "rotate": rotate, "mirror": True, "at": [12, 5]}]})
        assert corner(pasted.blueprint) == (12, 5)
        block = pasted.blocks[0]
        xs = [b[1] + b[3] for b in entity_boxes(pasted.blueprint)]
        ys = [b[2] + b[4] for b in entity_boxes(pasted.blueprint)]
        assert (max(xs) - 12, max(ys) - 5) == (block.width, block.height)

    def test_mirroring_twice_is_the_original(self, kept, library) -> None:
        entry, original, recipe, machine = kept
        once = plan.build({"blocks": [{"library": "block-a", "mirror": True}]})
        again = dict(entry, name="mirrored", blueprint=once.blueprint.to_string(), ports=[
            {"id": f"p{p['index']}", "kind": p["kind"], "dir": p["io"], "at": [p["x"], p["y"]],
             "direction": p["direction"], "items": list(p["items"]), "rate": p["rate"]}
            for p in once.blocks[0].ports])
        store(library, again)
        twice = plan.build({"blocks": [{"library": "mirrored", "mirror": True}]})
        assert entity_boxes(twice.blueprint) == entity_boxes(original.blueprint)
        assert [(p["x"], p["y"], p["direction"], p["items"]) for p in twice.blocks[0].ports] == \
               [(p["x"], p["y"], p["direction"], p["items"]) for p in original.blocks[0].ports]

    @pytest.mark.parametrize("rotate", ROTATIONS)
    @pytest.mark.parametrize("mirror", [False, True])
    def test_every_port_still_sits_on_the_belt_it_belonged_to(self, kept, rotate: int, mirror: bool) -> None:
        entry, original, *_ = kept
        before = belts_at(original.blueprint)
        ends = [p["index"] for p in original.blocks[0].ports
                if p["kind"] == "belt" and before.get((p["x"], p["y"])) == p["direction"]]
        if not ends:
            pytest.skip("no belt port of this block ends on a belt facing its direction at its own tile")
        pasted = plan.build({"blocks": [{"library": "block-a", "rotate": rotate, "mirror": mirror, "at": [3, 4]}]})
        after = belts_at(pasted.blueprint)
        for index in ends:
            port = pasted.blocks[0].ports[index]
            assert after.get((port["x"], port["y"])) == port["direction"], port

    def test_a_mirror_swaps_the_lanes_of_a_belt_port(self, kept) -> None:
        entry, original, *_ = kept
        mirrored = plan.build({"blocks": [{"library": "block-a", "mirror": True}]})
        for was, now in zip(original.blocks[0].ports, mirrored.blocks[0].ports):
            if was["kind"] == "belt":
                assert tuple(now["items"]) == tuple(reversed(was["items"]))
            assert now["io"] == was["io"] and now["rate"] == was["rate"]

    def test_a_mirror_reflects_what_faces_east_and_west(self, kept) -> None:
        entry, original, *_ = kept
        mirrored = plan.build({"blocks": [{"library": "block-a", "mirror": True}]})
        for was, now in zip(original.blocks[0].ports, mirrored.blocks[0].ports):
            assert now["direction"] == {4: 12, 12: 4}.get(was["direction"], was["direction"])
            assert now["x"] == original.blocks[0].width - 1 - was["x"] and now["y"] == was["y"]

    def test_the_block_is_reported_like_any_other(self, kept) -> None:
        entry, original, recipe, machine = kept
        result = plan.build({"blocks": [{"library": "block-a", "at": [1, 2], "id": "mine"}]})
        block = result.blocks[0]
        assert (block.recipe, block.machine, block.machines) == (recipe, machine, 4)
        assert block.crafts_per_second == pytest.approx(original.blocks[0].crafts_per_second)
        assert block.prototypes["belt"] == original.blocks[0].prototypes["belt"]
        assert "from the library: block-a" in block.notes
        assert [e.name for e in original.blueprint.entities].count(machine) == \
               [e.name for e in result.blueprint.entities].count(machine)

    def test_its_entities_go_through_the_same_checks(self, library) -> None:
        recipe, machine = setup_1011()
        store(library, make_entry("block-a", recipe, machine))
        crowded = plan.build({"blocks": [{"library": "block-a", "at": [0, 0]}, {"library": "block-a", "at": [1, 1]}]})
        assert "overlap" in [f.code for f in crowded.findings]

    def test_plan_ids_name_a_library_block(self, library) -> None:
        recipe, machine = setup_1011()
        store(library, make_entry("block-a", recipe, machine))
        out = next(i for i, p in enumerate(plan.build({"blocks": [{"library": "block-a"}]}).blocks[0].ports)
                   if p["io"] == "out" and p["kind"] == "belt")
        result = plan.build({"blocks": [{"library": "block-a", "id": "mine"}],
                             "connections": [{"from": {"block": "mine", "port": out},
                                              "to": {"at": [60, 0], "direction": 4}}]})
        assert result.routes and result.routes[0].ok, [r.reason for r in result.routes]


# --------------------------------------------------------------------------
# connections to a pasted block
# --------------------------------------------------------------------------


class TestConnections:
    @pytest.mark.parametrize("rotate", ROTATIONS)
    @pytest.mark.parametrize("mirror", [False, True])
    def test_a_route_leaves_a_pasted_blocks_out_port(self, library, rotate: int, mirror: bool) -> None:
        recipe, machine = setup_1011()
        store(library, make_entry("block-a", recipe, machine))
        block = {"library": "block-a", "rotate": rotate, "mirror": mirror, "at": [0, 0]}
        first = plan.build({"blocks": [block]})
        index, port = next((p["index"], p) for p in first.blocks[0].ports if p["io"] == "out" and p["kind"] == "belt")
        dx, dy = inspection.STEP[port["direction"]]
        goal = {"at": [port["x"] + 8 * dx, port["y"] + 8 * dy], "direction": port["direction"]}
        result = plan.build({"blocks": [block], "connections": [{"from": {"block": 0, "port": index}, "to": goal}]})
        (done,) = result.routes
        assert done.ok, done.reason
        assert (done.pieces[0].x, done.pieces[0].y) == (port["x"] + dx, port["y"] + dy)

    def test_a_pasted_block_feeds_another_through_its_ports(self, library) -> None:
        recipe, machine = setup_1011()
        store(library, make_entry("block-a", recipe, machine))
        blocks = [{"library": "block-a", "at": [0, 0]}, {"library": "block-a", "at": [40, 0], "mirror": True}]
        ports = plan.build({"blocks": blocks}).blocks
        out = next(p for p in ports[0].ports if p["io"] == "out" and p["kind"] == "belt")
        taker = next(p for p in ports[1].ports if p["io"] == "in" and p["kind"] == "belt")
        result = plan.build({"blocks": blocks, "connections": [
            {"from": {"block": 0, "port": out["index"]}, "to": {"block": 1, "port": taker["index"]}}]})
        assert result.routes and all(r.ok for r in result.routes), [r.reason for r in result.routes]

    def test_sources_can_sit_at_a_pasted_blocks_in_port(self, library) -> None:
        recipe, machine = setup_1011()
        store(library, make_entry("block-a", recipe, machine))
        blocks = [{"library": "block-a", "rotate": 90}]
        built = plan.build({"blocks": blocks}).blocks[0]
        taker = next(p for p in built.ports if p["io"] == "in" and p["kind"] == "belt")
        items = list(taker["items"])
        result = plan.build({"blocks": blocks, "sources": [
            {"block": 0, "port": taker["index"], "items": items, "rate": 1.0}]})
        assert result.blocks[0].ports[taker["index"]]["arrives"] == items
        assert not [f for f in result.findings if f.code in ("lanes-missing", "lanes-wrong-item", "lanes-not-taken")]


# --------------------------------------------------------------------------
# recipe swap
# --------------------------------------------------------------------------


class TestSwap:
    @pytest.fixture
    def swap(self, library):
        recipe, machine = setup_1011()
        other = other_recipe(recipe, machine)
        if other is None:
            pytest.skip("the active data has no second recipe of the same shape for that machine")
        store(library, make_entry("block-a", recipe, machine))
        return recipe, machine, other

    def test_the_machines_get_the_new_recipe_and_the_ports_follow(self, swap) -> None:
        recipe, machine, other = swap
        before = plan.build({"blocks": [{"library": "block-a"}]})
        after = plan.build({"blocks": [{"library": "block-a", "recipe": other}]})
        machines = [e for e in after.blueprint.entities if e.name == machine]
        assert machines and all(e.recipe == other for e in machines)
        assert after.blocks[0].recipe == other
        assert any(f"recipe swapped from {recipe} to {other}" in n for n in after.blocks[0].notes)
        goods_in = {g for g in insert._goods(other, "in")}
        goods_out = {g for g in insert._goods(other, "out")}
        for was, now in zip(before.blocks[0].ports, after.blocks[0].ports):
            parts = {part for lane in now["items"] for part in lane.split("+") if part}
            assert parts <= (goods_in if now["io"] == "in" else goods_out), (parts, now)
            assert now["x"] == was["x"] and now["y"] == was["y"] and now["direction"] == was["direction"]
        assert after.blocks[0].crafts_per_second == pytest.approx(
            insert.layout.crafts_per_second(other, machine) * after.blocks[0].machines)

    def test_a_recipe_of_another_shape_is_refused(self, library) -> None:
        recipe, machine = setup_1011()
        store(library, make_entry("block-a", recipe, machine))
        other = other_recipe(recipe, machine, same_shape=False)
        if other is None:
            pytest.skip("the active data has no recipe of another shape for that machine")
        with pytest.raises(plan.PlanError, match="differ in how many goods"):
            plan.build({"blocks": [{"library": "block-a", "recipe": other}]})

    def test_an_entry_that_does_not_say_its_recipe_cannot_swap(self, library) -> None:
        recipe, machine = setup_1011()
        entry = make_entry("block-a", recipe, machine)
        del entry["shape"]
        store(library, entry)
        with pytest.raises(plan.PlanError, match="cannot be swapped"):
            plan.build({"blocks": [{"library": "block-a", "recipe": recipe + "-x"}]})
        built = plan.build({"blocks": [{"library": "block-a"}]})
        assert built.blocks[0].crafts_per_second == 0.0


# --------------------------------------------------------------------------
# chain: a stage takes a block of its shape
# --------------------------------------------------------------------------


def tiers_of(entry: dict) -> dict[str, str]:
    return {"belt": entry["shape"]["tiers"]["belt"], "inserter": entry["shape"]["tiers"]["inserter"],
            "long_inserter": prototypes.inserter_reaching(2), "pole": entry["shape"]["tiers"]["pole"]}


def line_for(recipe: str, machine: str, machines: int) -> bom.MachineLine:
    return bom.MachineLine(recipe, machine, "crafting", 1.0, machines, 0.0, {}, {})


NO_BONUS = SimpleNamespace(effects={})


class TestChain:
    def test_a_stage_of_the_same_recipe_takes_the_library_block(self, library) -> None:
        recipe, machine = setup_1011()
        entry = store(library, make_entry("block-a", recipe, machine, (2,)))
        stage = chain.make_stage(0, line_for(recipe, machine, 2), NO_BONUS, tiers_of(entry))
        assert stage.block == {"library": "block-a", "rotate": 0}
        assert stage.reuse == ["stage 0: library block block-a"]
        assert (stage.width, stage.height) == tuple(entry["size"])
        assert [(p.io, p.kind, p.x, p.y, p.direction) for p in stage.ports] == \
               [(p["dir"], p["kind"], p["at"][0], p["at"][1], p["direction"]) for p in entry["ports"]]

    def test_a_stage_of_a_swappable_recipe_swaps_it_and_says_so(self, library) -> None:
        recipe, machine = setup_1011()
        other = other_recipe(recipe, machine)
        if other is None:
            pytest.skip("the active data has no second recipe of the same shape for that machine")
        entry = store(library, make_entry("block-a", recipe, machine, (2,)))
        stage = chain.make_stage(3, line_for(other, machine, 2), NO_BONUS, tiers_of(entry))
        assert stage.block == {"library": "block-a", "rotate": 0, "recipe": other}
        assert stage.reuse == [f"stage 3: library block block-a, recipe swapped ({recipe} -> {other})"]
        goods = set(insert._goods(other, "in")) | set(insert._goods(other, "out"))
        for p in stage.ports:
            assert p.items <= goods
        # the plan the draft writes builds
        result = plan.build({"blocks": [{**stage.block, "at": [0, 0]}]})
        assert result.blocks[0].recipe == other

    def test_the_exact_recipe_wins_over_a_swap(self, library) -> None:
        recipe, machine = setup_1011()
        other = other_recipe(recipe, machine)
        if other is None:
            pytest.skip("the active data has no second recipe of the same shape for that machine")
        store(library, make_entry("a-first", other, machine, (2,)))
        entry = store(library, make_entry("b-second", recipe, machine, (2,)))
        stage = chain.make_stage(0, line_for(recipe, machine, 2), NO_BONUS, tiers_of(entry))
        assert stage.block["library"] == "b-second" and "recipe" not in stage.block

    def test_a_different_shape_is_not_used(self, library) -> None:
        recipe, machine = setup_1011()
        entry = store(library, make_entry("block-a", recipe, machine, (2,)))
        other = other_recipe(recipe, machine, same_shape=False)
        if other is None:
            pytest.skip("the active data has no recipe of another shape for that machine")
        stage = chain.make_stage(0, line_for(other, machine, 1), NO_BONUS, tiers_of(entry))
        assert stage.block.get("type") == "rows" and not stage.reuse

    def test_another_machine_is_not_used(self, library) -> None:
        recipe, machine = setup_1011()
        entry = store(library, make_entry("block-a", recipe, machine, (2,)))
        elsewhere = next((n for n, d in entities.raw.items() if n != machine and d.get("crafting_speed")
                          and crafts(d, recipes.raw[recipe]) and n in prototypes._buildable()
                          and prototypes._footprint(d) == (3, 3)), None)
        if elsewhere is None:
            pytest.skip("the active data has no second three-tile machine for that recipe")
        choice, _ = insert.choose(recipe, elsewhere, tiers_of(entry), 1)
        assert choice is None

    def test_other_tiers_are_not_used(self, library) -> None:
        recipe, machine = setup_1011()
        entry = store(library, make_entry("block-a", recipe, machine, (2,)))
        slower = {**tiers_of(entry), "belt": "some-other-belt"}
        choice, _ = insert.choose(recipe, machine, slower, 1)
        assert choice is None

    def test_a_block_too_small_is_passed_over_and_said(self, library) -> None:
        recipe, machine = setup_1011()
        entry = store(library, make_entry("block-a", recipe, machine, (2,)))
        stage = chain.make_stage(1, line_for(recipe, machine, 5), NO_BONUS, tiers_of(entry))
        assert stage.block.get("type") == "rows"
        assert any("has 2 machine(s), the stage needs 5" in t for t in stage.reuse)

    def test_the_smallest_block_that_is_big_enough_wins(self, library) -> None:
        recipe, machine = setup_1011()
        store(library, make_entry("big", recipe, machine, (3, 3)))
        entry = store(library, make_entry("mid", recipe, machine, (3,)))
        store(library, make_entry("small", recipe, machine, (1,)))
        stage = chain.make_stage(0, line_for(recipe, machine, 2), NO_BONUS, tiers_of(entry))
        assert stage.block["library"] == "mid"

    def test_a_stage_with_a_speed_bonus_keeps_its_rows(self, library) -> None:
        recipe, machine = setup_1011()
        entry = store(library, make_entry("block-a", recipe, machine, (2,)))
        boosted = SimpleNamespace(effects={"*": SimpleNamespace(speed=0.5)})
        stage = chain.make_stage(0, line_for(recipe, machine, 2), boosted, tiers_of(entry))
        assert stage.block.get("type") == "rows"

    def test_without_a_library_nothing_changes(self, monkeypatch, tmp_path) -> None:
        recipe, machine = setup_1011()
        monkeypatch.setattr(insert, "library_dir", lambda: tmp_path / "no-such-folder")
        tiers = {"belt": prototypes.fastest_belt(), "inserter": prototypes.inserter_reaching(1),
                 "long_inserter": prototypes.inserter_reaching(2), "pole": prototypes.small_pole()}
        stage = chain.make_stage(0, line_for(recipe, machine, 2), NO_BONUS, tiers)
        assert stage.block["type"] == "rows" and stage.reuse == []

    def test_no_active_profile_is_no_library(self, monkeypatch) -> None:
        def none():
            raise plan.PlanError("no profile is active")
        monkeypatch.setattr(insert, "library_dir", none)
        recipe, machine = setup_1011()
        tiers = {"belt": prototypes.fastest_belt(), "inserter": prototypes.inserter_reaching(1),
                 "long_inserter": prototypes.inserter_reaching(2), "pole": prototypes.small_pole()}
        stage = chain.make_stage(0, line_for(recipe, machine, 2), NO_BONUS, tiers)
        assert stage.block["type"] == "rows"

    def test_links_to_neighbours_are_routed_fresh(self, library) -> None:
        recipe, machine = setup_1011()
        entry = store(library, make_entry("block-a", recipe, machine, (2,)))
        stage = chain.make_stage(0, line_for(recipe, machine, 2), NO_BONUS, tiers_of(entry))
        # only the block is reused: its stored entry carries no route, and the plan block names no connection
        assert set(stage.block) <= {"library", "rotate", "recipe"}


class TestRowsBlockUnchanged:
    def test_rows_blocks_still_build_without_a_library(self) -> None:
        recipe, machine = setup_1011()
        result = plan.build({"blocks": [block_spec(recipe, machine, [2])]})
        assert result.blocks[0].rows and result.blocks[0].machines == 2
        assert rows is not None


class TestFluidBlocks:
    @pytest.fixture
    def fluid_entry(self, library):
        recipe, machine = prototypes.crafting_setup(1, 1, 1, 0)
        try:
            return store(library, make_entry("wet", recipe, machine, (2,))), machine
        except plan.PlanError as exc:
            pytest.skip(f"the active data's block for a fluid recipe cannot be built: {exc}")

    @pytest.mark.parametrize("rotate", ROTATIONS)
    def test_a_mirrored_machine_is_flagged_and_pipe_ports_keep_their_fluid(self, fluid_entry, rotate: int) -> None:
        entry, machine = fluid_entry
        plain = plan.build({"blocks": [{"library": "wet", "rotate": rotate}]})
        mirrored = plan.build({"blocks": [{"library": "wet", "rotate": rotate, "mirror": True}]})
        flagged = [bool(getattr(e, "mirror", None)) for e in mirrored.blueprint.entities if e.name == machine]
        if getattr(next(e for e in plain.blueprint.entities if e.name == machine), "mirror", None) is None:
            pytest.skip("this machine has no mirror flag in the active data")
        assert flagged and all(flagged)
        assert not any(getattr(e, "mirror", False) for e in plain.blueprint.entities if e.name == machine)
        for was, now in zip(plain.blocks[0].ports, mirrored.blocks[0].ports):
            if was["kind"] == "pipe":
                assert now["items"] == was["items"]
        problems = lambda r: sorted(f.code for f in r.findings if f.severity is inspection.Severity.PROBLEM)  # noqa: E731
        assert problems(mirrored) == problems(plain)


class TestMalformedShape:
    @pytest.mark.parametrize("shape_damage", [
        {"solid_in": "one"}, {"tiers": ["x"]}, {"recipe": ["a"]}, {"fluid_out": -1}, {"machine": 3}, {"solid_out": True},
    ])
    def test_a_bad_shape_is_left_out_and_said_and_chain_still_runs(self, library, shape_damage) -> None:
        recipe, machine = setup_1011()
        good = store(library, make_entry("good", recipe, machine, (2,)))
        bad = make_entry("bad-one", recipe, machine, (2,))
        bad["shape"].update(shape_damage)
        store(library, bad)
        skipped: list[str] = []
        assert [e["name"] for e in insert.entries(library, skipped)] == ["good"]
        assert len(skipped) == 1 and skipped[0].startswith("library: bad-one skipped: ")
        choice, said = insert.choose(recipe, machine, tiers_of(good), 1)
        assert choice.name == "good" and said == skipped
        stage = chain.make_stage(0, line_for(recipe, machine, 2), NO_BONUS, tiers_of(good))
        assert stage.block["library"] == "good"
        assert any("bad-one skipped" in t for t in stage.reuse)

    def test_a_name_with_a_trailing_newline_is_refused(self) -> None:
        with pytest.raises(plan.PlanError):
            insert.check_name("abc\n")


class TestChainOrientation:
    def test_a_block_turned_on_its_side_is_turned_back(self, library) -> None:
        recipe, machine = setup_1011()
        entry = make_entry("upright", recipe, machine, (2,))
        store(library, entry)
        turned = plan.build({"blocks": [{"library": "upright", "rotate": 90}]})
        sideways = dict(entry, name="sideways", blueprint=turned.blueprint.to_string(), size=[turned.blocks[0].width, turned.blocks[0].height],
                        ports=[{"id": f"p{p['index']}", "kind": p["kind"], "dir": p["io"], "at": [p["x"], p["y"]],
                                "direction": p["direction"], "items": list(p["items"]), "rate": p["rate"]}
                               for p in turned.blocks[0].ports])
        (library / "upright.json").unlink()
        store(library, sideways)
        stage = chain.make_stage(0, line_for(recipe, machine, 2), NO_BONUS, tiers_of(entry))
        assert stage.block["library"] == "sideways"
        for p in stage.ports:
            if p.kind == "belt":
                assert p.direction == 4 and p.x == (0 if p.io == "in" else stage.width - 1)
        assert plan.build({"blocks": [{**stage.block, "at": [0, 0]}]}).blocks[0].machines == 2

    def test_a_block_whose_ports_cannot_run_west_to_east_is_passed_over(self, library) -> None:
        recipe, machine = setup_1011()
        entry = make_entry("odd", recipe, machine, (2,))
        for p in entry["ports"]:
            if p["kind"] == "belt":
                p["at"] = [1, 1]  # neither edge
        store(library, entry)
        stage = chain.make_stage(0, line_for(recipe, machine, 2), NO_BONUS, tiers_of(entry))
        assert stage.block.get("type") == "rows"
        assert any("ports do not run west to east" in t for t in stage.reuse)

    def test_a_block_far_bigger_than_the_stage_is_passed_over(self, library) -> None:
        recipe, machine = setup_1011()
        entry = store(library, make_entry("huge", recipe, machine, (4, 4)))
        stage = chain.make_stage(0, line_for(recipe, machine, 1), NO_BONUS, tiers_of(entry))
        assert stage.block.get("type") == "rows"
        assert any("far more than" in t for t in stage.reuse)


class TestSplitterPriority:
    @pytest.mark.parametrize("rotate", ROTATIONS)
    def test_a_mirror_swaps_the_sides_of_a_priority(self, library, rotate: int) -> None:
        from draftsman.blueprintable import Blueprint

        name = next((n for n, d in entities.raw.items() if d.get("type") == "splitter"), None)
        if name is None:
            pytest.skip("the active data has no splitter")
        bp = Blueprint()
        bp.entities.append(name, tile_position=(0, 0), direction=4, input_priority="left", output_priority="right")
        entry = {"name": "split", "blueprint": bp.to_string(), "ports": []}
        for mirror, want in ((False, ("left", "right")), (True, ("right", "left"))):
            got = insert.paste(entry, (0, 0), rotate, mirror).objects[0]
            assert (got.input_priority, got.output_priority) == want
