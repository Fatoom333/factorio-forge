"""Block ids, the edit operations, and a build that reuses what an edit left alone.

The plan is a chain of the same shape built from whatever the active data has
(`test_plan.two_ingredient_chain`), twice, far apart, so an edit to one chain can
be told from the other's untouched blocks and routes.
"""

from __future__ import annotations

import copy
import json
import os
import pickle
import random

import pytest
from draftsman.data import recipes

import prototypes
from factorio_forge import cli, edit, plan, route, rows
from test_plan import two_ingredient_chain
from test_route import block_spec, chests, point


def chain_blocks(prefix: str, offset: tuple[int, int]):
    """Two blocks (maker, user) with the connections between them, named by id, shifted by `offset`."""
    maker, user = two_ingredient_chain()
    first = block_spec(*maker, [2], id=f"{prefix}-maker", at=list(offset))
    second = block_spec(*user, [2], id=f"{prefix}-user")
    alone = plan.build({"blocks": [{k: v for k, v in first.items() if k != "id"}]}).blocks[0]
    out = next(p for p in alone.ports if p["io"] == "out")
    (product,) = {i for lane in out["items"] for i in lane.split("+") if i}
    other = next(i["name"] for i in recipes.raw[user[0]]["ingredients"] if i["name"] != product)
    second["at"] = [offset[0] + alone.width + 14, offset[1]]
    probe = plan.build({"blocks": [{k: v for k, v in second.items() if k != "id"}]}).blocks[0]
    port = next(p for p in probe.ports if p["io"] == "in" and p["kind"] == "belt")
    connections = [
        {"id": f"{prefix}-share", "from": {"block": f"{prefix}-maker", "port": out["index"]},
         "to": [{"block": f"{prefix}-user", "port": port["index"]}, point(out["x"] + 8, out["y"] + 8)]},
        {"id": f"{prefix}-other", "from": point(port["x"] - 4, port["y"] - 8, 8, items=[other]),
         "onto": {"block": f"{prefix}-user", "port": port["index"]}},
    ]
    return [first, second], connections


@pytest.fixture(scope="module")
def two_chains() -> dict:
    a_blocks, a_conns = chain_blocks("a", (0, 0))
    b_blocks, b_conns = chain_blocks("b", (0, 120))
    return {"label": "edit test", "blocks": a_blocks + b_blocks, "connections": a_conns + b_conns}


def fresh(the_plan: dict) -> dict:
    return copy.deepcopy(the_plan)


def outcome(the_plan: dict, cache=None):
    """What a build gives, or the one-line reason it does not; the same shape either way."""
    try:
        result = plan.build(fresh(the_plan), None, cache)
    except plan.PlanError as exc:
        return ("error", str(exc))
    return ("ok", result.blueprint.to_string(), json.dumps(result.to_dict(), sort_keys=True, default=str))


def by_index(the_plan: dict) -> dict:
    """The same plan with every block named by index, as plans were before ids."""
    out = copy.deepcopy(plan.resolve_block_ids(the_plan))
    for block in out["blocks"]:
        block.pop("id", None)
    return out


# --------------------------------------------------------------------------
# ids
# --------------------------------------------------------------------------


class TestBlockIds:
    def test_a_block_named_by_id_is_the_block_at_that_index(self, two_chains: dict) -> None:
        assert outcome(two_chains) == outcome(by_index(two_chains))
        assert outcome(two_chains)[0] == "ok"

    def test_connections_and_sources_resolve_ids_inside_lists(self, two_chains: dict) -> None:
        resolved = plan.resolve_block_ids(two_chains)
        assert resolved["connections"][0]["to"][0]["block"] == 1
        assert resolved["connections"][0]["from"]["block"] == 0
        assert two_chains["connections"][0]["from"]["block"] == "a-maker", "the plan itself is left as written"
        with_source = {**two_chains, "sources": [{"id": "feed", "block": "b-user", "port": 0, "items": ["x"]}]}
        assert plan.resolve_block_ids(with_source)["sources"][0]["block"] == 3

    @pytest.mark.parametrize("bad, said", [
        ("has space", "without spaces"), ("", "without spaces"), ("12", "not a number"), (7, "without spaces"),
    ])
    def test_a_malformed_id_is_one_line(self, two_chains: dict, bad, said: str) -> None:
        the_plan = fresh(two_chains)
        the_plan["blocks"][0]["id"] = bad
        with pytest.raises(plan.PlanError, match=said):
            plan.build(the_plan)

    def test_ids_are_unique(self, two_chains: dict) -> None:
        the_plan = fresh(two_chains)
        the_plan["blocks"][1]["id"] = the_plan["blocks"][0]["id"]
        with pytest.raises(plan.PlanError, match="already the id of block 0"):
            plan.build(the_plan)

    def test_an_unknown_id_in_a_connection_names_the_known_ones(self, two_chains: dict) -> None:
        the_plan = fresh(two_chains)
        the_plan["connections"][0]["to"][0]["block"] = "nope"
        with pytest.raises(plan.PlanError, match=r"connection 0: block 'nope' is not a block id \(the ids are: a-maker"):
            plan.build(the_plan)


# --------------------------------------------------------------------------
# operations
# --------------------------------------------------------------------------


class TestOperations:
    def test_move_is_relative_and_defaults_from_the_origin(self, two_chains: dict) -> None:
        the_plan = fresh(two_chains)
        del the_plan["blocks"][0]["at"]
        edit.apply_operations(the_plan, [["move", "a-maker", "3", "-2"], ["move", "0", "1", "1"]])
        assert the_plan["blocks"][0]["at"] == [4, -1]

    def test_set_changes_one_field_and_none_clears_an_optional_one(self, two_chains: dict) -> None:
        the_plan = fresh(two_chains)
        edit.apply_operations(the_plan, [["set", "b-user", "belt=transport-belt"], ["set", "0", "speed_bonus=0.5"]])
        assert the_plan["blocks"][3]["belt"] == "transport-belt"
        assert the_plan["blocks"][0]["speed_bonus"] == 0.5
        edit.apply_operation(the_plan, ["set", "0", "speed_bonus=none"])
        assert "speed_bonus" not in the_plan["blocks"][0]

    def test_rows_in_list_and_counted_form(self, two_chains: dict) -> None:
        the_plan = fresh(two_chains)
        edit.apply_operations(the_plan, [["rows", "0", "+1"], ["rows", "0", "+1"], ["rows", "0", "-1"]])
        assert the_plan["blocks"][0]["rows"] == [2, 2]
        edit.apply_operation(the_plan, ["rows", "0", "per=5"])
        assert the_plan["blocks"][0]["rows"] == [5, 5]
        counted = {"blocks": [{"recipe": "r", "machine": "m", "rows": 3, "per_row": 4}]}
        edit.apply_operations(counted, [["rows", "0", "-1"], ["rows", "0", "per=6"]])
        assert counted["blocks"][0]["rows"] == 2 and counted["blocks"][0]["per_row"] == 6

    def test_rotate_takes_degrees_and_builds_as_written_by_hand(self, two_chains: dict) -> None:
        edited = fresh(two_chains)
        edit.apply_operation(edited, ["set", "a-maker", "rotate=90"])
        assert edited["blocks"][0]["rotate"] == 90
        by_hand = fresh(two_chains)
        by_hand["blocks"][0]["rotate"] = 90
        assert outcome(edited) == outcome(by_hand)

    @pytest.mark.parametrize("words, said", [
        (["wiggle", "0"], "unknown operation 'wiggle'; the operations are move, set, rows, remove"),
        (["move", "0", "1"], r"move <block> <dx> <dy> \(got 'move 0 1'\)"),
        ([], "no operation"),
    ])
    def test_grammar_errors_are_one_line(self, words: list[str], said: str) -> None:
        with pytest.raises(edit.EditError, match=said):
            edit.split_operations(words)

    @pytest.mark.parametrize("op, said", [
        (["move", "9", "1", "1"], "no block 9; the plan has 4"),
        (["move", "ghost", "1", "1"], "no block 'ghost'; the ids are: a-maker, a-user, b-maker, b-user"),
        (["move", "0", "x", "1"], "dx is a whole number"),
        (["set", "0", "colour=red"], "no settable field 'colour'"),
        (["set", "0", "recipe"], r"set <block> <field>=<value>"),
        (["set", "0", "recipe=none"], "cannot go without recipe"),
        (["set", "0", "modules=speed-module"], "no 'modules' yet"),
        (["set", "0", "rotate=3"], "rotate is 0, 90, 180 or 270"),
        (["set", "0", "rotate=4"], "rotate is 0, 90, 180 or 270"),
        (["move", "0", "1", "1"], "at is not"),
        (["rows", "0", "-1"], "keeps at least one"),
        (["rows", "0", "sideways"], r"rows <block> \+1 \| -1 \| per=<n>"),
    ])
    def test_a_bad_operation_is_one_line_and_changes_nothing(self, two_chains: dict, op: list[str], said: str) -> None:
        the_plan = fresh(two_chains)
        the_plan["blocks"][0]["rows"] = [2]
        if "at is not" in said:
            the_plan["blocks"][0]["at"] = "here"
        before = json.dumps(the_plan, sort_keys=True)
        with pytest.raises(edit.EditError, match=said):
            edit.apply_operation(the_plan, op)
        assert json.dumps(the_plan, sort_keys=True) == before

    def test_remove_drops_what_names_the_block_and_renumbers_the_rest(self, two_chains: dict) -> None:
        the_plan = by_index(two_chains)
        said = edit.apply_operation(the_plan, ["remove", "0"])
        assert len(the_plan["blocks"]) == 3
        assert [c["id"] for c in the_plan["connections"]] == ["b-share", "b-other"]
        assert the_plan["connections"][0]["from"]["block"] == 1  # was 2
        assert any("dropped with it" in line and "a-share" in line for line in said)
        assert outcome(the_plan)[0] == "ok" or "a-" not in outcome(the_plan)[1]

    def test_remove_by_id_keeps_ids_working_and_drops_merges_onto_a_gone_route(self, two_chains: dict) -> None:
        the_plan = fresh(two_chains)
        # a merge onto a route that is dropped goes with it
        the_plan["connections"].append({"id": "late", "from": point(0, -30, 8, items=["x"]),
                                        "onto": {"route": "a-share"}})
        edit.apply_operation(the_plan, ["remove", "a-maker"])
        ids = [c["id"] for c in the_plan["connections"]]
        assert "a-share" not in ids and "late" not in ids and "b-share" in ids
        assert the_plan["connections"][0]["from"]["block"] == "b-maker"


# --------------------------------------------------------------------------
# a rebuild is the same as a clean build
# --------------------------------------------------------------------------


class TestCachedBuild:
    def test_an_unchanged_plan_is_read_back_whole(self, two_chains: dict) -> None:
        cache = edit.BuildCache(None, identity="t")
        first = outcome(two_chains, cache)
        cache.stats = edit.CacheStats()
        again = outcome(two_chains, cache)
        assert first == again == outcome(two_chains)
        assert (cache.stats.blocks_built, cache.stats.routes_built) == (0, 0)
        assert (cache.stats.blocks_reused, cache.stats.routes_reused) == (4, 4)

    def test_moving_a_block_redoes_it_and_its_routes_and_nothing_else(self, two_chains: dict) -> None:
        cache = edit.BuildCache(None, identity="t")
        outcome(two_chains, cache)
        moved = fresh(two_chains)
        edit.apply_operation(moved, ["move", "a-user", "0", "3"])
        cache.stats = edit.CacheStats()
        cache.used = set()
        assert outcome(moved, cache) == outcome(moved)
        assert cache.stats.blocks_built == 1 and cache.stats.blocks_reused == 3
        assert sorted(cache.stats.reused_ids) == ["b-other", "b-share"]
        assert sorted(cache.stats.built_ids) == ["a-other", "a-share"]

    def test_a_route_that_crosses_the_moved_block_is_redone(self, two_chains: dict) -> None:
        """Chain b's maker is moved onto the track chain b's route took: that route changes, the clean build agrees."""
        cache = edit.BuildCache(None, identity="t")
        outcome(two_chains, cache)
        moved = fresh(two_chains)
        edit.apply_operation(moved, ["move", "b-user", "-6", "-4"])
        cache.stats = edit.CacheStats()
        assert outcome(moved, cache) == outcome(moved)
        assert cache.stats.routes_built >= 2

    def test_a_walk_of_edits_matches_clean_builds_every_step(self, two_chains: dict) -> None:
        rng = random.Random(7)
        cache = edit.BuildCache(None, identity="t")
        current = fresh(two_chains)
        outcome(current, cache)
        for step in range(10):
            candidate = fresh(current)
            b = rng.randrange(len(candidate["blocks"]))
            kind = rng.choice(["move", "move", "move", "rows", "belt"])
            try:
                if kind == "move":
                    edit.apply_operation(candidate, ["move", str(b), str(rng.randint(-5, 5)), str(rng.randint(-5, 5))])
                elif kind == "rows":
                    edit.apply_operation(candidate, ["rows", str(b), rng.choice(["+1", "-1", "per=1"])])
                else:
                    edit.apply_operation(candidate, ["set", str(b), "belt=" + candidate["blocks"][b]["belt"]])
            except edit.EditError:
                continue
            cache.used = set()
            cached, clean = outcome(candidate, cache), outcome(candidate)
            assert cached == clean, f"step {step}: {kind} on block {b} differs from a clean build"
            if cached[0] == "ok":
                current = candidate

    def test_the_cache_does_not_hide_a_changed_obstacle(self, two_chains: dict) -> None:
        """A hand-placed entity in a route's way after the first build: the route is searched again."""
        cache = edit.BuildCache(None, identity="t")
        first = plan.build(fresh(two_chains), None, cache)
        tiles = [(p.x, p.y) for r in first.routes if r.id.startswith("a-share") for p in r.pieces]

        blocked = fresh(two_chains)
        blocked["entities"] = chests(tiles[len(tiles) // 2:len(tiles) // 2 + 1])
        cache.used = set()
        assert outcome(blocked, cache) == outcome(blocked)
        assert cache.stats.routes_built >= 1

    def test_the_cache_is_kept_on_disk_and_pruned(self, two_chains: dict, tmp_path) -> None:
        folder = tmp_path / ".cache"
        first = edit.BuildCache(folder, identity="t")
        plan.build(fresh(two_chains), None, first)
        first.save()
        assert [p.name for p in folder.iterdir()] == [edit.ENTRIES_FILE]
        assert len(first.memory) == 4 + 4
        second = edit.BuildCache(folder, identity="t")
        assert outcome(two_chains, second) == outcome(two_chains)
        assert (second.stats.blocks_built, second.stats.routes_built) == (0, 0)
        # another identity (other game data or code) reads none of it, and saving drops what it did not use
        other = edit.BuildCache(folder, identity="other")
        outcome(two_chains, other)
        assert (other.stats.blocks_reused, other.stats.routes_reused) == (0, 0)
        other.save()
        again = edit.BuildCache(folder, identity="other")
        again._load_disk()
        assert len(again.memory) == 8, "pruning keeps only what the last build used"

    def test_a_failed_build_keeps_what_it_had_built(self, two_chains: dict, tmp_path) -> None:
        folder = tmp_path / ".cache"
        broken = fresh(two_chains)
        broken["connections"][0]["to"][0]["port"] = 99
        cache = edit.BuildCache(folder, identity="t")
        with pytest.raises(plan.PlanError):
            edit.rebuild(broken, tmp_path / "plan.json", cache)
        assert (folder / edit.ENTRIES_FILE).is_file()
        later = edit.BuildCache(folder, identity="t")
        assert outcome(two_chains, later) == outcome(two_chains)
        assert later.stats.blocks_reused == 4

    def test_a_cache_file_that_would_run_code_is_a_miss_not_an_execution(self, tmp_path) -> None:
        marker = tmp_path / "ran"

        class Evil:
            def __reduce__(self):
                return (os.system, (f'echo x > "{marker}"',))

        folder = tmp_path / ".cache"
        folder.mkdir()
        (folder / edit.ENTRIES_FILE).write_bytes(pickle.dumps({("blocks", "k"): pickle.dumps(Evil())}))
        cache = edit.BuildCache(folder, identity="t")
        assert cache._read("blocks", "k") is None
        (folder / edit.ENTRIES_FILE).write_bytes(pickle.dumps(Evil()))
        assert edit.BuildCache(folder, identity="t")._read("blocks", "k") is None
        assert not marker.exists()
        with pytest.raises(pickle.UnpicklingError):
            edit._loads(pickle.dumps(Evil()))


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------


class TestCommand:
    def test_edit_rewrites_the_plan_rebuilds_and_keeps_the_rest(self, two_chains: dict, tmp_path, capsys) -> None:
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(two_chains), encoding="utf-8")
        assert cli.main(["edit", str(plan_file)]) == 0
        first = capsys.readouterr().out
        assert "blocks 0 kept / 4 built" in first
        assert (tmp_path / ".plan.cache").is_dir()
        assert cli.main(["edit", str(plan_file), "move", "a-user", "0", "2"]) == 0
        out = capsys.readouterr().out
        assert "block 1 (a-user) moved by (0, 2)" in out
        assert "blocks 3 kept / 1 built, routes 2 kept / 2 built" in out
        edited = json.loads(plan_file.read_text(encoding="utf-8"))
        assert edited["blocks"][1]["at"][1] == two_chains["blocks"][1]["at"][1] + 2
        assert list(tmp_path.glob("*.txt")) and list(tmp_path.glob("*.report.json"))

    def test_edit_to_another_file_leaves_the_original(self, two_chains: dict, tmp_path, capsys) -> None:
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(two_chains), encoding="utf-8")
        copy_file = tmp_path / "wider.json"
        assert cli.main(["edit", str(plan_file), "-o", str(copy_file), "move", "b-maker", "-3", "0", "--no-cache"]) == 0
        capsys.readouterr()
        assert json.loads(plan_file.read_text(encoding="utf-8")) == two_chains
        assert json.loads(copy_file.read_text(encoding="utf-8"))["blocks"][2]["at"][0] == -3
        assert not (tmp_path / ".wider.cache").exists()

    def test_a_bad_operation_writes_nothing(self, two_chains: dict, tmp_path, capsys) -> None:
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(two_chains), encoding="utf-8")
        before = plan_file.read_text(encoding="utf-8")
        assert cli.main(["edit", str(plan_file), "move", "a-user", "0", "2", "set", "a-user", "colour=red"]) == 2
        err = capsys.readouterr().err
        assert err.strip().count("\n") == 0 and "colour" in err
        assert plan_file.read_text(encoding="utf-8") == before

    def test_a_value_the_build_rejects_is_not_written(self, two_chains: dict, tmp_path, capsys) -> None:
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(two_chains), encoding="utf-8")
        before = plan_file.read_text(encoding="utf-8")
        assert cli.main(["edit", str(plan_file), "set", "a-maker", "recipe=no-such-recipe-at-all"]) == 1
        assert "not written" in capsys.readouterr().err
        assert plan_file.read_text(encoding="utf-8") == before

    def test_a_copy_in_another_folder_still_finds_the_request(self, two_chains: dict, tmp_path, capsys) -> None:
        (tmp_path / "req").mkdir()
        (tmp_path / "req" / "request.json").write_text("{}", encoding="utf-8")
        with_request = {**two_chains, "request": "request.json"}
        (tmp_path / "req" / "plan.json").write_text(json.dumps(with_request), encoding="utf-8")
        copy_file = tmp_path / "sub" / "copy.json"
        copy_file.parent.mkdir()
        cli.main(["edit", str(tmp_path / "req" / "plan.json"), "-o", str(copy_file), "move", "b-maker", "1", "0",
                  "--no-cache", "--no-render"])
        capsys.readouterr()
        saved = json.loads(copy_file.read_text(encoding="utf-8"))
        assert (copy_file.parent / saved["request"]).resolve() == (tmp_path / "req" / "request.json").resolve()

    def test_a_missing_plan_is_one_line(self, tmp_path, capsys) -> None:
        assert cli.main(["edit", str(tmp_path / "nope.json"), "move", "0", "1", "1"]) == 2
        assert "no plan at" in capsys.readouterr().err


# --------------------------------------------------------------------------
# what the watched grid reports
# --------------------------------------------------------------------------


class TestWatchedGrid:
    def grid(self) -> edit.WatchedGrid:
        return edit.WatchedGrid((0, 0, 9, 9), {(1, 1): "wall"}, {(2, 2)}, {}, {}, {}, [])

    def test_questions_and_changes_are_logged_with_what_the_tile_held_first(self) -> None:
        g, log = self.grid(), edit.Log()
        g.watch(log)
        assert (1, 1) in g.occupied and (5, 5) not in g.occupied
        g.occupied[(5, 5)] = "belt"  # a change after the question: the log keeps the first answer
        assert (5, 5) in g.occupied
        assert (2, 2) in g.reserved
        assert g.inside((3, 3)) and not g.inside((12, 3))
        assert log.before["occupied"] == {(1, 1): edit.canonical("wall"), (5, 5): edit._MISSING}
        assert log.before["reserved"] == {(2, 2): True}
        assert log.inside == {(3, 3): True, (12, 3): False}
        assert not log.whole and not log.area_read

    def test_a_trial_copy_reports_to_the_same_log(self) -> None:
        g, log = self.grid(), edit.Log()
        g.watch(log)
        trial = g.copy()
        trial.occupied[(7, 7)] = "splitter"
        assert log.before["occupied"][(7, 7)] == edit._MISSING
        assert not log.whole, "copying through copy() is not a walk of the table"
        assert (7, 7) not in dict.keys(g.occupied)

    def test_walking_a_table_or_reading_the_area_is_noticed(self) -> None:
        g, log = self.grid(), edit.Log()
        g.watch(log)
        list(g.occupied)
        assert log.whole
        log2 = edit.Log()
        g.watch(log2)
        assert g.area == (0, 0, 9, 9) and log2.area_read
        g.watch(None)
        g.occupied.get((1, 1))
        assert not any(log2.before.values()), "nothing is logged once the grid is no longer watched"

    def test_sets_read_the_same_whatever_their_order(self) -> None:
        assert edit.canonical(frozenset({"b", "a", "c"})) == edit.canonical(frozenset({"c", "a", "b"}))


# --------------------------------------------------------------------------
# the route cache against the routing code
# --------------------------------------------------------------------------


def test_the_watched_grid_knows_every_table_of_the_grid() -> None:
    """A table added to route.Grid but not watched would let a cached route go stale unseen."""
    import dataclasses

    names = {f.name for f in dataclasses.fields(route.Grid)}
    assert names - {"area", "tunnels"} == set(edit._TABLES)


def routes_only_plan() -> dict:
    """A belt with a hop, a split belt and a pipe, with no blocks: obstacles are what an edit changes."""
    belt, underground, _ = prototypes.belt_set()
    pipe = rows._default_pipe("pipe")
    return {"label": "routes only", "blocks": [], "entities": chests([(6, 0)]), "connections": [
        {"id": "hop", "kind": "belt", "belt": belt, "underground": underground,
         "from": point(0, 0, items=["a"]), "to": point(16, 0)},
        {"id": "fork", "kind": "belt", "belt": belt, "underground": underground,
         "from": point(0, 20, items=["b"]), "to": [point(16, 20), point(16, 26)]},
        {"id": "water", "kind": "pipe", "pipe": pipe, "from": point(0, 40, items=["water"]), "to": point(16, 40)},
    ]}


def test_a_walk_of_obstacles_over_belts_a_split_and_a_pipe_matches_clean_builds() -> None:
    rng = random.Random(11)
    cache = edit.BuildCache(None, identity="t")
    current = routes_only_plan()
    outcome(current, cache)
    for step in range(12):
        candidate = fresh(current)
        y = rng.choice([0, 20, 26, 40, rng.randint(-3, 45)])
        x = rng.randint(1, 15)
        if rng.random() < 0.3 and candidate["entities"]:
            candidate["entities"].pop(rng.randrange(len(candidate["entities"])))
        else:
            candidate["entities"] += chests([(x, y)])
        cache.used = set()
        cached, clean = outcome(candidate, cache), outcome(candidate)
        assert cached == clean, f"step {step}: a chest at ({x}, {y}) differs from a clean build"
        if cached[0] == "ok":
            current = candidate
    assert cache.stats.routes_reused > 0
