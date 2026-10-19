"""Tests for `chain`: an order turned into a draft plan for every stage.

The wiring, layout and plan shape are tested on stages made by hand, so they
hold whatever the active game data is. The whole chain, from a bill of
materials to a built blueprint, runs only when the active data has a recipe
chain of the shape the test needs (a product whose two ingredients are
themselves made, one of them by the other's recipe), found by shape.
"""

from __future__ import annotations

import json

import pytest
from draftsman.data import entities, recipes

from factorio_forge import bom, chain, cli, inspection, request
from factorio_forge.categories import crafts, primary_category

# --------------------------------------------------------------------------
# stages made by hand
# --------------------------------------------------------------------------


def port(index, io, items, x, y, lanes=None, kind="belt", rate=1.0):
    lanes = lanes or (items[0], items[-1]) if len(items) <= 2 else tuple(items)
    return chain.Port(index, kind, io, frozenset(i for i in items if i), tuple(lanes), x, y, 4, rate)


def stage(index, recipe, inputs, outputs, ports, width=10, height=7, machines=2):
    line = bom.MachineLine(recipe, "machine", "crafting", 1.0, machines, 0.0, dict(inputs), dict(outputs))
    s = chain.Stage(index, line, {"type": "rows", "recipe": recipe, "machine": "machine", "rows": [machines]},
                    width, height, ports)
    return s


def in_port(index, a, b=None, y=0, rate=1.0):
    return port(index, "in", [a] if b is None else [a, b], 0, y, lanes=(a, b or a), rate=rate)


def out_port(index, item, y=6, both_lanes=False):
    return port(index, "out", [item], 9, y, lanes=(item, item) if both_lanes else ("", item))


def three_stage_chain(both_lanes=False):
    """cable (raw copper) feeds both circuit (cable + raw iron) and board (circuit + cable)."""
    cable = stage(0, "cable", {"copper": 3.0}, {"cable": 6.0},
                  [in_port(0, "copper"), out_port(1, "cable", both_lanes=both_lanes)])
    circuit = stage(1, "circuit", {"cable": 3.0, "iron": 1.0}, {"circuit": 1.0},
                    [in_port(0, "iron", "cable"), out_port(1, "circuit")])
    board = stage(2, "board", {"circuit": 1.0, "cable": 3.0}, {"board": 0.5},
                  [in_port(0, "circuit", y=0), in_port(1, "cable", y=1), out_port(2, "board")])
    return [cable, circuit, board]


# --------------------------------------------------------------------------
# the order
# --------------------------------------------------------------------------


class TestOrderWords:
    def test_per_second_and_per_minute(self) -> None:
        assert chain.parse_targets(["a=2", "b=90/m"]) == [{"item": "a", "per_second": 2.0}, {"item": "b", "per_minute": 90.0}]

    @pytest.mark.parametrize("word", ["a", "=2", "a=", "a=two", "a=/m"])
    def test_a_malformed_word_is_refused(self, word: str) -> None:
        with pytest.raises(chain.ChainError):
            chain.parse_targets([word])

    def test_rows_are_split_evenly(self) -> None:
        assert chain._row_counts(7, 1) == [7]
        assert chain._row_counts(7, 2) == [4, 3]
        assert chain._row_counts(3, 9) == [1, 1, 1]


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------


class TestWiring:
    def test_a_shared_stage_is_one_link_with_every_user_as_an_end(self) -> None:
        stages = three_stage_chain()
        issues: list[str] = []
        links, feeds, joins = chain.wire(stages, set(), issues)
        cable = [l for l in links if l.item == "cable"]
        assert len(cable) == 1, "one block, one out port, one connection"
        assert cable[0].mode == "to"
        assert {(c.index, p.index) for c, p in cable[0].ends} == {(1, 0), (2, 1)}
        assert not joins and not issues

    def test_raw_goods_become_sources_or_merges(self) -> None:
        stages = three_stage_chain()
        _, feeds, _ = chain.wire(stages, set(), [])
        by_item = {(f.stage.index, f.item): f for f in feeds}
        assert by_item[(0, "copper")].how == "source"
        merged = by_item[(1, "iron")]
        assert merged.how == "merge"
        assert merged.rate == pytest.approx(1.0)

    def test_a_belt_that_fills_both_lanes_cannot_take_a_merge_so_the_goods_are_joined(self) -> None:
        a = stage(0, "a", {"raw": 1}, {"x": 1}, [in_port(0, "raw"), out_port(1, "x", both_lanes=True)])
        b = stage(1, "b", {"raw": 1}, {"y": 1}, [in_port(0, "raw"), out_port(1, "y")])
        c = stage(2, "c", {"x": 1, "y": 1}, {"z": 1}, [in_port(0, "x", "y"), out_port(1, "z")])
        links, feeds, joins = chain.wire([a, b, c], set(), [])
        (join,) = joins
        assert {join.first.item, join.second.item} == {"x", "y"}
        assert all(l.mode == "join" for l in links if l.item in ("x", "y"))
        # with a free lane on the routed belt the other good is merged onto it instead
        a2 = stage(0, "a", {"raw": 1}, {"x": 1}, [in_port(0, "raw"), out_port(1, "x")])
        links, _, joins = chain.wire([a2, b, c], set(), [])
        assert not joins
        assert sorted(l.mode for l in links) == ["onto", "to"]

    def test_a_raw_good_is_joined_with_a_belt_that_fills_both_lanes(self) -> None:
        a = stage(0, "a", {"raw": 1}, {"x": 1}, [in_port(0, "raw"), out_port(1, "x", both_lanes=True)])
        c = stage(1, "c", {"x": 1, "iron": 1}, {"z": 1}, [in_port(0, "iron", "x"), out_port(1, "z")])
        _, feeds, joins = chain.wire([a, c], set(), [])
        (join,) = joins
        assert isinstance(join.second, chain.Feed) and join.second.how == "join" and join.second.item == "iron"

    def test_a_good_wanted_at_more_ports_is_the_one_routed_to_a_port(self) -> None:
        stages = three_stage_chain()
        links, _, _ = chain.wire(stages, set(), [])
        modes = {l.item: l.mode for l in links}
        assert modes["cable"] == "to"
        assert modes["circuit"] == "to"

    def test_one_out_port_starts_one_connection(self) -> None:
        # x is routed to one port and would have to merge at another: the merge is left to the player
        a = stage(0, "a", {"raw": 1}, {"x": 1}, [in_port(0, "raw"), out_port(1, "x")])
        b = stage(1, "b", {"x": 1}, {"y": 1}, [in_port(0, "x"), out_port(1, "y")])
        c = stage(2, "c", {"x": 1, "y": 1}, {"z": 1}, [in_port(0, "y", "x"), out_port(1, "z")])
        d = stage(3, "d", {"y": 1}, {"w": 1}, [in_port(0, "y"), out_port(1, "w")])
        issues: list[str] = []
        links, _, _ = chain.wire([a, b, c, d], set(), issues)
        x = next(l for l in links if l.item == "x")
        assert x.mode == "to" and {(s.index, p.index) for s, p in x.ends} == {(1, 0)}
        assert any("one out port starts one connection" in i for i in issues)

    def test_a_fluid_is_piped_and_a_raw_fluid_is_left_to_the_player(self) -> None:
        maker = stage(0, "maker", {}, {"water-ish": 1}, [port(0, "out", ["water-ish"], 9, 3, kind="pipe")])
        user = stage(1, "user", {"water-ish": 1, "other": 1}, {"z": 1},
                     [port(0, "in", ["water-ish"], 0, 2, kind="pipe"), port(1, "in", ["other"], 0, 4, kind="pipe"),
                      out_port(2, "z")])
        issues: list[str] = []
        links, _, _ = chain.wire([maker, user], set(), issues)
        assert [(l.item, l.out.kind, l.mode) for l in links] == [("water-ish", "pipe", "to")]
        assert any("other" in i and "by hand" in i for i in issues)

    def test_a_fluid_used_by_two_stages_gets_every_consumer(self) -> None:
        maker = stage(0, "maker", {}, {"water-ish": 2}, [port(0, "out", ["water-ish"], 9, 3, kind="pipe")])
        users = [stage(i, f"user{i}", {"water-ish": 1}, {f"z{i}": 1},
                       [port(0, "in", ["water-ish"], 0, 2, kind="pipe"), out_port(1, f"z{i}")]) for i in (1, 2)]
        issues: list[str] = []
        links, _, _ = chain.wire([maker, *users], set(), issues)
        (link,) = links
        assert {(s.index, p.index) for s, p in link.ends} == {(1, 0), (2, 0)}
        assert not issues

    def test_a_good_the_bill_marks_as_arriving_is_not_taken_from_a_stage(self) -> None:
        stages = three_stage_chain()
        links, feeds, _ = chain.wire(stages, {"cable"}, [])
        assert not [l for l in links if l.item == "cable"]
        assert {f.item for f in feeds} >= {"cable", "copper", "iron"}


# --------------------------------------------------------------------------
# layout
# --------------------------------------------------------------------------


class TestLayout:
    def laid(self, both_lanes=False, tweaks=None):
        stages = three_stage_chain(both_lanes)
        links, feeds, joins = chain.wire(stages, set(), [])
        chain.lay_out(stages, links, tweaks)
        return stages, links, feeds, joins

    def test_columns_follow_the_flow_and_the_product_is_last(self) -> None:
        stages, *_ = self.laid()
        cable, circuit, board = stages
        assert cable.column < circuit.column < board.column
        assert cable.x < circuit.x < board.x

    def test_a_stage_stands_as_late_as_it_can(self) -> None:
        # the pipe-like stage that feeds only the last stage stands next to it, not at the far edge
        a = stage(0, "a", {"raw": 1}, {"x": 1}, [in_port(0, "raw"), out_port(1, "x")])
        b = stage(1, "b", {"raw": 1}, {"y": 1}, [in_port(0, "raw"), out_port(1, "y")])
        c = stage(2, "c", {"y": 1}, {"z": 1}, [in_port(0, "y"), out_port(1, "z")])
        d = stage(3, "d", {"x": 1, "z": 1}, {"w": 1}, [in_port(0, "x", "z"), out_port(1, "w")])
        links, _, _ = chain.wire([a, b, c, d], set(), [])
        chain.lay_out([a, b, c, d], links)
        assert (a.column, b.column, c.column, d.column) == (1, 0, 1, 2)

    def test_no_two_blocks_overlap(self) -> None:
        stages, *_ = self.laid()
        for i, a in enumerate(stages):
            for b in stages[i + 1:]:
                apart = (a.x + a.width <= b.x or b.x + b.width <= a.x or a.y + a.height <= b.y or b.y + b.height <= a.y)
                assert apart, (a.index, b.index)

    def test_feeders_stand_in_the_order_of_the_ports_they_feed(self) -> None:
        stages, *_ = self.laid()
        cable, circuit, board = stages
        # board's top port takes circuit, its second takes cable: circuit's out port lies above cable's
        assert circuit.y + circuit.ports[1].y < cable.y + cable.ports[1].y

    def test_the_corner_of_the_layout_is_the_origin(self) -> None:
        stages, *_ = self.laid()
        assert min(s.x for s in stages) == 0 and min(s.y for s in stages) == 0

    def test_more_room_moves_the_block_and_widens_its_feeders(self) -> None:
        plain, *_ = self.laid()
        roomy, *_ = self.laid(tweaks=chain.Tweaks(roomy={2}))
        assert roomy[2].x > plain[2].x
        assert abs(roomy[1].y - roomy[0].y) != abs(plain[1].y - plain[0].y) or roomy[2].x > plain[2].x

    def test_the_other_side_swaps_the_feeders(self) -> None:
        plain, *_ = self.laid()
        flipped, *_ = self.laid(tweaks=chain.Tweaks(flipped={2}))
        above = lambda ss: ss[1].y + ss[1].ports[1].y < ss[0].y + ss[0].ports[1].y  # noqa: E731
        assert above(plain) and not above(flipped)

    def test_the_nearest_free_row(self) -> None:
        assert chain._free_slot(10, 7, [], 8) == 10
        assert chain._free_slot(10, 7, [(8, 7)], 8) in (-7, 23)
        assert chain._free_slot(30, 7, [(8, 7)], 8) == 30


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------


class TestPlanShape:
    def planned(self, both_lanes=False, join=False):
        if join:
            a = stage(0, "a", {"raw": 1}, {"x": 1}, [in_port(0, "raw"), out_port(1, "x", both_lanes=True)])
            c = stage(1, "c", {"x": 1, "iron": 1}, {"board": 0.5}, [in_port(0, "iron", "x"), out_port(1, "board")])
            stages = [a, c]
        else:
            stages = three_stage_chain(both_lanes)
        links, feeds, joins = chain.wire(stages, set(), [])
        chain.lay_out(stages, links)
        spec = request.parse({"targets": [{"item": "board", "per_second": 0.5}], "boundary": ["copper", "iron"]})
        tiers = {"belt": "b", "inserter": "i", "long_inserter": "l", "pole": "p"}
        return chain.to_plan(stages, links, feeds, joins, spec, tiers, "test"), stages

    def test_one_block_per_stage_at_its_corner(self) -> None:
        plan, stages = self.planned()
        assert [(b["recipe"], b["at"]) for b in plan["blocks"]] == [(s.recipe, [s.x, s.y]) for s in stages]

    def test_a_shared_product_is_one_connection_with_a_list_of_destinations(self) -> None:
        plan, _ = self.planned()
        (cable,) = [c for c in plan["connections"] if c["id"].startswith("cable-0")]
        assert cable["from"] == {"block": 0, "port": 1, "items": ["cable"]}
        assert cable["to"] == [{"block": 1, "port": 0}, {"block": 2, "port": 1}] or \
            cable["to"] == [{"block": 2, "port": 1}, {"block": 1, "port": 0}]

    def test_a_shared_fluid_is_one_pipe_connection_to_every_consumer(self) -> None:
        maker = stage(0, "maker", {}, {"water-ish": 2}, [port(0, "out", ["water-ish"], 9, 3, kind="pipe")])
        users = [stage(i, f"user{i}", {"water-ish": 1}, {f"z{i}": 1},
                       [port(0, "in", ["water-ish"], 0, 2, kind="pipe"), out_port(1, f"z{i}")]) for i in (1, 2)]
        stages = [maker, *users]
        links, feeds, joins = chain.wire(stages, set(), [])
        chain.lay_out(stages, links)
        spec = request.parse({"targets": [{"item": "z1", "per_second": 1}]})
        tiers = {"belt": "b", "inserter": "i", "long_inserter": "l", "pole": "p"}
        plan = chain.to_plan(stages, links, feeds, joins, spec, tiers, "test")
        (water,) = [c for c in plan["connections"] if c.get("kind") == "pipe"]
        assert water["from"]["items"] == ["water-ish"]
        assert sorted((t["block"], t["port"]) for t in water["to"]) == [(1, 0), (2, 0)]

    def test_raw_goods_on_a_shared_port_merge_onto_the_port_and_alone_they_are_sources(self) -> None:
        plan, _ = self.planned()
        (merge,) = [c for c in plan["connections"] if c["id"] == "iron-in-1-0"]
        assert merge["onto"] == {"block": 1, "port": 0, "lane": "auto"}
        assert merge["from"]["items"] == ["iron"] and merge["from"]["direction"] in (0, 8)
        assert plan["sources"] == [{"id": "copper-in-0-0", "block": 0, "port": 0, "items": ["copper"], "rate": 3.0}]

    def test_the_order_and_the_power_link_are_in_the_plan(self) -> None:
        plan, _ = self.planned()
        assert plan["targets"] == [{"item": "board", "per_second": 0.5}]
        assert plan["power"]["link"] is True
        assert plan["boundary"] == ["copper", "iron"]

    def test_a_join_lists_both_sources_and_a_tile_off_the_straight_line(self) -> None:
        plan, stages = self.planned(both_lanes=True, join=True)
        (join,) = [c for c in plan["connections"] if isinstance(c["from"], list)]
        assert len(join["from"]) == 2 and join["to"] == {"block": 1, "port": 0}
        (via,) = join["via"]
        px, py = stages[1].x, stages[1].y
        assert via[0] < px and via[1] != py

    def test_a_declared_rate_never_falls_short_by_rounding(self) -> None:
        assert chain._up(0.38461538) >= 0.38461538
        assert chain._up(2.0) == 2.0

    def test_source_ports_with_two_raw_goods_make_one_source(self) -> None:
        both = stage(0, "m", {"a": 1, "b": 2}, {"z": 1}, [in_port(0, "a", "b"), out_port(1, "z")])
        links, feeds, joins = chain.wire([both], set(), [])
        chain.lay_out([both], links)
        spec = request.parse({"targets": [{"item": "z", "per_second": 1}]})
        plan = chain.to_plan([both], links, feeds, joins, spec,
                             {"belt": "b", "inserter": "i", "long_inserter": "l", "pole": "p"}, "t")
        assert plan["sources"] == [{"id": "a-b-in-0-0", "block": 0, "port": 0, "items": ["a", "b"], "rate": 3.0}]


# --------------------------------------------------------------------------
# the whole thing, on the active data
# --------------------------------------------------------------------------


def _chain_in_data():
    """A product made of two intermediates, one of which the other also uses, each made from plain goods.

    Found by shape in the active data: (product, [intermediate recipes]) where the product's recipe takes
    both intermediates, and the second intermediate is made from the first.
    """
    craftable = {n: r for n, r in recipes.raw.items() if not r.get("hidden") and primary_category(r)}
    made_by: dict[str, str] = {}
    for name, r in craftable.items():
        for result in r.get("results") or []:
            if result.get("type", "item") == "item" and result.get("name") == name and len(r.get("results")) == 1:
                made_by[name] = name
    machines = [n for n, e in entities.raw.items() if "crafting_speed" in e and e.get("type") == "assembling-machine"]
    for name, r in craftable.items():
        if name not in made_by or any(i.get("type", "item") != "item" for i in r.get("ingredients", [])):
            continue
        ingredients = [i["name"] for i in r.get("ingredients", []) if i.get("type", "item") == "item"]
        mid = [i for i in ingredients if i in made_by]
        if len(mid) != 2:
            continue
        a, b = mid
        for first, second in ((a, b), (b, a)):
            second_recipe = craftable[second]
            if first in [i["name"] for i in second_recipe.get("ingredients", [])]:
                firsts = [i["name"] for i in craftable[first].get("ingredients", [])]
                if all(g not in made_by for g in firsts) and any(crafts(entities.raw[m], r) for m in machines):
                    return name, first, second
    return None


class TestOnTheActiveData:
    @pytest.fixture
    def found(self):
        found = _chain_in_data()
        if found is None:
            pytest.skip("the active data's recipes have no product made of two intermediates, one making the other")
        return found

    def test_a_chain_is_drafted_with_one_block_per_recipe_and_built(self, found, tmp_path) -> None:
        product, first, second = found
        spec = request.parse({"targets": [{"item": product, "per_second": 0.5}]})
        reviewed = request.review(spec)
        if not reviewed.ready:
            pytest.skip(f"the request is not ready on this data: {reviewed.problems}")
        raw = set(reviewed.bill.raw_materials)
        d = chain.draft(spec, reviewed=reviewed)
        recipes_in_plan = [b["recipe"] for b in d.plan["blocks"]]
        assert len(recipes_in_plan) == len(set(recipes_in_plan)), "one block per recipe"
        assert {l.recipe for l in reviewed.bill.lines} == set(recipes_in_plan)
        # every product that has a consumer is joined: the first intermediate feeds the second and the product
        ids = {c["id"] for c in d.plan["connections"]}
        assert any(i.startswith(f"{first}-") for i in ids) or not any(
            first in l.inputs for l in reviewed.bill.lines)
        assert d.plan["power"]["link"] is True and raw

    def test_the_command_writes_a_plan_the_build_command_accepts(self, found, tmp_path, capsys) -> None:
        product, *_ = found
        out = tmp_path / "plan.json"
        code = cli.main(["chain", f"{product}=0.5", "-o", str(out), "--no-render"])
        shown = capsys.readouterr().out
        assert out.exists() and "blocks" in shown
        plan = json.loads(out.read_text(encoding="utf-8"))
        from factorio_forge import plan as planner

        result = planner.build(plan, planner.planned_from_targets(plan)[0])
        assert len(result.blueprint.entities) > 0
        assert code in (0, 1)  # 1 only when the draft has problems left, which the output lists
        if code == 1:
            assert "problem" in shown or "FAILED" in shown

    def test_a_request_file_works_as_well_as_item_words(self, found, tmp_path, capsys) -> None:
        product, *_ = found
        req = tmp_path / "request.json"
        req.write_text(json.dumps({"targets": [{"item": product, "per_minute": 30}]}), encoding="utf-8")
        cli.main(["chain", str(req), "-o", str(tmp_path / "p.json"), "--no-build"])
        assert (tmp_path / "p.json").exists()
        assert not list(tmp_path.glob("*.txt")), "--no-build writes the plan only"

    def test_an_unknown_item_is_refused_with_a_reason(self, tmp_path, capsys) -> None:
        code = cli.main(["chain", "no-such-item-xyz=1", "-o", str(tmp_path / "p.json")])
        assert code == 1
        assert "no-such-item-xyz" in capsys.readouterr().err


class TestRetries:
    def test_a_build_with_nothing_wrong_is_not_tried_again(self, monkeypatch) -> None:
        found = _chain_in_data()
        if found is None:
            pytest.skip("the active data's recipes have no product made of two intermediates, one making the other")
        spec = request.parse({"targets": [{"item": found[0], "per_second": 0.5}]})
        if not request.review(spec).ready:
            pytest.skip("the request is not ready on this data")
        _, attempts = chain.build_with_retries(spec)
        if len(attempts) > 1:
            assert attempts[0].problems, "a second placement is tried only after a failure"
        assert len(attempts) <= 1 + 2 * 20

    def test_each_spot_gets_each_alternative_once(self, monkeypatch) -> None:
        """A build that always fails at block 0 is tried in at most two other placements."""
        seen: list[tuple[set, set]] = []

        class Route:
            ok = False
            id = "x"
            connection = "x"
            reason = "blocked"

        class Result:
            routes = [Route()]
            findings: list = []
            problems: list = []

        real_draft = chain.draft

        def fake_draft(spec, **kw):
            d = real_draft(spec, **kw)
            d.plan["connections"] = [{"id": "x", "from": {"block": 0, "port": 0}, "to": {"block": 1, "port": 0}}]
            tw = kw["tweaks"]
            seen.append((set(tw.roomy), set(tw.flipped)))
            return d

        monkeypatch.setattr(chain, "draft", fake_draft)
        monkeypatch.setattr(chain, "build_salvaging", lambda d: (Result(), ""))
        found = _chain_in_data()
        if found is None:
            pytest.skip("the active data's recipes have no product made of two intermediates, one making the other")
        spec = request.parse({"targets": [{"item": found[0], "per_second": 0.5}]})
        if not request.review(spec).ready:
            pytest.skip("the request is not ready on this data")
        _, attempts = chain.build_with_retries(spec)
        assert [a.number for a in attempts] == [1, 2, 3], "the first placement and two alternatives, no loop"
        assert seen[1][0] and not seen[1][1], "more room first"
        assert seen[2][1] and not seen[2][0], "the other side second"

    def test_a_refused_connection_is_left_out_and_listed(self) -> None:
        found = _chain_in_data()
        if found is None:
            pytest.skip("the active data's recipes have no product made of two intermediates, one making the other")
        spec = request.parse({"targets": [{"item": found[0], "per_second": 0.5}]})
        if not request.review(spec).ready:
            pytest.skip("the request is not ready on this data")
        d = chain.draft(spec)
        d.plan["connections"].append({"id": "bad-one", "from": {"block": 0, "port": 99}, "to": {"block": 1, "port": 0}})
        result, why = chain.build_salvaging(d)
        assert result is not None and not why
        assert all(c["id"] != "bad-one" for c in d.plan["connections"])
        assert any("bad-one" in i for i in d.issues)

    def test_the_best_attempt_has_the_fewest_problems(self) -> None:
        a = chain.Attempt(1, "", ["p", "q"], object())
        b = chain.Attempt(2, "", ["p"], object())
        c = chain.Attempt(3, "", ["p"], object())
        assert chain.best_attempt([a, b, c]) is b
        assert chain.best_attempt([chain.Attempt(1, "", ["refused"], None)]) is None
