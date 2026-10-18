"""Tests for what a build reports beyond the routes: gap columns, planned rates, a whole line.

Prototypes and recipes come from the active data by shape, as in the rest of
the suite. The one fixture naming real prototypes -- the red-circuit line a
clean install built on another PC, now joined by the router -- runs only when
the active data has them; a chain of the same shape built from whatever the
data has stands in for it everywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from draftsman.data import entities, items, recipes

import prototypes
from factorio_forge import cli, environment, inspection, layout, plan, rows
from factorio_forge.categories import crafts, primary_category, recipe_categories
from test_route import block_spec, codes, point, ports_of

FIXTURES = Path(__file__).parent / "fixtures"


def raw_input_unknown(f) -> bool:
    """A block's raw inputs are the player's supply; these plans declare none."""
    return f.code == "lanes-unknown" and f.summary.startswith("block 0 port")


def serious(result) -> list[str]:
    return [str(f) for f in result.findings
            if f.severity is not inspection.Severity.NOTE and not raw_input_unknown(f)]


# --------------------------------------------------------------------------
# gap columns
# --------------------------------------------------------------------------


class TestGapNote:
    def test_a_block_with_gap_columns_says_what_it_built(self) -> None:
        poles = sorted(n for n, d in entities.raw.items() if d.get("type") == "electric-pole"
                       and n in prototypes._buildable())
        checked = 0
        for shape in ((1, 0, 1, 0), (2, 0, 1, 0), (1, 1, 0, 1)):
            try:
                recipe, machine = prototypes.crafting_setup(*shape)
            except pytest.skip.Exception:
                continue
            for pole in poles:
                pw = layout.machine_size(pole)[0]
                for n in range(1, 9):
                    try:
                        block = rows.build_block(rows.RowBlockSpec(**{**block_spec(recipe, machine, [n]), "pole": pole}))
                    except layout.LayoutError:
                        continue
                    (note,) = [t for t in block.notes if "poles needed gap columns" in t] or [None]
                    if note is None:
                        continue
                    checked += 1
                    spans = sorted((p.x, p.x + p.width) for p in block.placements if p.name == machine)
                    first, last = spans[0][0], spans[-1][1]
                    px = sorted({p.x for p in block.placements if p.name == pole})
                    left = [x for x in px if x + pw <= first]
                    right = [x for x in px if x >= last]
                    between = [x for x in px if first <= x < last and not any(a <= x < b for a, b in spans)]
                    where = note.split("; poles stand ")[-1]; assert "; poles stand " in note, note
                    if left and right:
                        assert where.startswith("at every end"), (pole, n, note)
                    elif not left and not right:
                        assert where.startswith("at no end"), (pole, n, note)
                    else:
                        assert where.startswith("in 1 of 2 end columns"), (pole, n, note)
                    if between:
                        assert f"{len(between)} of " in where.split(", ")[1], (pole, n, note)
                    else:
                        assert "none between machines" in where or "0 of" in where, (pole, n, note)
        if not checked:
            pytest.skip("no block from the active data needs gap columns for its poles")


# --------------------------------------------------------------------------
# planned rates
# --------------------------------------------------------------------------


def one_block():
    recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
    block = block_spec(recipe, machine, [2])
    return block, plan.build({"blocks": [block]}).blocks[0]


class TestPlannedRates:
    def test_a_blocks_own_planned_rate(self, tmp_path, capsys: pytest.CaptureFixture) -> None:
        block, alone = one_block()
        half = alone.crafts_per_second / 2
        result = plan.build({"blocks": [{**block, "planned": half}]})
        (built,) = result.blocks
        assert built.planned_crafts_per_second == pytest.approx(half)
        for port in built.ports:
            assert port["planned_rate"] == pytest.approx(port["rate"] / 2)
        path = tmp_path / "plan.json"
        path.write_text(json.dumps({"label": "planned", "blocks": [{**block, "planned": half}]}), encoding="utf-8")
        assert cli.main(["build", str(path), "--no-render"]) == 0
        out = capsys.readouterr().out
        assert "planned" in out and "at full load" in out

    def request_for(self, tmp_path, recipe, machine, per_second, monkeypatch) -> dict:
        monkeypatch.setattr(environment, "for_active_profile", lambda: (None, "no export in this test"))
        entry = recipes.raw[recipe]
        product = next(r["name"] for r in entry["results"] if r.get("type") != "fluid")
        (tmp_path / "request.json").write_text(json.dumps({
            "targets": [{"item": product, "per_second": per_second}],
            "boundary": [i["name"] for i in entry["ingredients"]],
            "recipe_choices": {product: recipe},
            "machine_choices": {primary_category(entry): machine},
        }), encoding="utf-8")
        return {"label": "requested", "request": "request.json"}

    def test_a_request_plans_the_block(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        block, alone = one_block()
        amount = next(r["amount"] for r in recipes.raw[block["recipe"]]["results"] if r.get("type") != "fluid")
        the_plan = self.request_for(tmp_path, block["recipe"], block["machine"],
                                    alone.crafts_per_second * amount / 2, monkeypatch)
        the_plan["blocks"] = [block]
        planned, warned = plan.planned_from_request(the_plan, tmp_path / "plan.json")
        assert warned == [] and planned[block["recipe"]] == pytest.approx(alone.crafts_per_second / 2)
        result = plan.build(the_plan, planned)
        assert result.blocks[0].planned_crafts_per_second == pytest.approx(alone.crafts_per_second / 2)
        assert "block-short" not in codes(result)

        the_plan = self.request_for(tmp_path, block["recipe"], block["machine"],
                                    alone.crafts_per_second * amount * 2, monkeypatch)
        the_plan["blocks"] = [block]
        planned, _ = plan.planned_from_request(the_plan, tmp_path / "plan.json")
        assert "block-short" in codes(plan.build(the_plan, planned))

    def test_targets_in_a_plan_take_the_requests_choices(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        block, alone = one_block()
        amount = next(r["amount"] for r in recipes.raw[block["recipe"]]["results"] if r.get("type") != "fluid")
        by_request = self.request_for(tmp_path, block["recipe"], block["machine"],
                                      alone.crafts_per_second * amount / 2, monkeypatch)
        spec = json.loads((tmp_path / "request.json").read_text(encoding="utf-8"))
        by_targets = {"label": "ordered", **{k: v for k, v in spec.items() if k != "said"}}
        wanted, warned = plan.planned_from_request(by_request, tmp_path / "plan.json")
        got, said = plan.planned_from_targets(by_targets)
        assert warned == [] and said == []
        assert got == pytest.approx(wanted)
        by_targets["blocks"] = [block]
        built = plan.build(by_targets)
        assert built.blocks[0].planned_crafts_per_second == pytest.approx(alone.crafts_per_second / 2)

    def test_a_blocks_own_planned_rate_is_its_share(self) -> None:
        block, alone = one_block()
        cps = alone.crafts_per_second
        other = {**block, "at": [alone.width + 10, 0]}
        result = plan.build({"blocks": [{**block, "planned": cps / 4}, other]}, {block["recipe"]: cps})
        first, second = result.blocks
        assert first.planned_crafts_per_second == pytest.approx(cps / 4)
        assert second.planned_crafts_per_second == pytest.approx(cps * 3 / 4)
        assert "block-short" not in codes(result)

    def test_a_block_the_bill_does_not_list(self) -> None:
        block, _ = one_block()
        result = plan.build({"blocks": [block]}, {"not-this-recipe": 1.0})
        assert "not in the request's bill of materials" in result.blocks[0].notes
        assert result.blocks[0].planned_crafts_per_second is None

    def test_routes_are_sized_for_the_planned_flow(self) -> None:
        recipe, machine = prototypes.crafting_setup(1, 0, 1, 0)
        placeable = prototypes._buildable()
        slow = min((n for n, d in entities.raw.items() if d.get("type") == "transport-belt" and d.get("speed")
                    and n in placeable), key=lambda n: (entities.raw[n]["speed"], n))
        for count in (2, 4, 8, 16, 32):
            block = block_spec(recipe, machine, [count])
            alone = plan.build({"blocks": [block]}).blocks[0]
            out = next(p for p in alone.ports if p["io"] == "out")
            # The route checks a one-lane load against one lane.
            carries = (layout.lane_throughput(slow) if sum(1 for lane in out["items"] if lane) == 1
                       else layout.belt_throughput(slow))
            if out["rate"] > carries * 1.2:
                break
        else:
            pytest.skip(f"no row of {recipe} outruns {slow}")
        dx, dy = inspection.STEP[out["direction"]]
        connection = {"belt": slow, "from": {"block": 0, "port": out["index"]},
                      "to": point(out["x"] + 6 * dx, out["y"] + 6 * dy, out["direction"])}
        full = plan.build({"blocks": [block], "connections": [connection]})
        assert "route-belt-slow" in codes(full)
        share = carries / 2 / out["rate"]
        sized = plan.build({"blocks": [{**block, "planned": alone.crafts_per_second * share}],
                            "connections": [connection]})
        assert "route-belt-slow" not in codes(sized)


# --------------------------------------------------------------------------
# a whole line: split, merge, three blocks
# --------------------------------------------------------------------------


class TestWholeLine:
    def test_the_pc_red_circuit_line(self) -> None:
        the_plan = json.loads((FIXTURES / "red-circuits-routed.json").read_text(encoding="utf-8"))
        names = {b["machine"] for b in the_plan["blocks"]} | {e["name"] for e in the_plan["entities"]}
        names |= {b[k] for b in the_plan["blocks"] for k in ("belt", "inserter", "long_inserter", "pole")}
        wanted = {b["recipe"] for b in the_plan["blocks"]}
        if not names <= set(entities.raw) or not wanted <= set(recipes.raw):
            pytest.skip("the active data lacks the fixture's prototypes; test_a_chain_of_the_same_shape covers it")
        try:
            result = plan.build(the_plan)
        except plan.PlanError as exc:
            if "the plan expected" not in str(exc):
                raise
            pytest.skip(f"the active data's recipes differ from the fixture's: {exc}")
        assert all(r.ok for r in result.routes), [r.reason for r in result.routes]
        # Pole reach comes from the active data (mods change it), so the fixture's
        # poles may not join up elsewhere; this test is about routes and lanes.
        power = ("power-split", "pole-isolated")
        assert [str(f) for f in result.findings
                if f.severity is not inspection.Severity.NOTE and f.code not in power
                and not raw_input_unknown(f)] == []
        assert [f.code for f in result.findings
                if f.code.startswith("lanes-") and not raw_input_unknown(f)] == ["lanes-swapped"]
        green_in, red_in, red_cable = (result.blocks[1].ports[0], result.blocks[2].ports[0],
                                       result.blocks[2].ports[1])
        assert green_in["arrives"] == list(green_in["items"])
        assert set(red_in["arrives"]) == set(red_in["items"])
        assert red_cable["items"][0] in red_cable["arrives"]
        assert sum(1 for e in result.blueprint.entities if e.type == "splitter") == 1

    def test_a_chain_of_the_same_shape(self) -> None:
        maker, user = two_ingredient_chain()
        first = block_spec(*maker, [2])
        second = block_spec(*user, [2])
        alone = plan.build({"blocks": [first]}).blocks[0]
        out = next(p for p in alone.ports if p["io"] == "out")
        (product,) = {i for lane in out["items"] for i in lane.split("+") if i}
        other = next(i["name"] for i in recipes.raw[user[0]]["ingredients"] if i["name"] != product)
        second_at = [alone.width + 14, 0]
        port = next(p for p in plan.build({"blocks": [{**second, "at": second_at}]}).blocks[0].ports
                    if p["io"] == "in" and p["kind"] == "belt")
        assert out["direction"] == port["direction"] == 4  # unturned blocks flow east
        result = plan.build({"blocks": [first, {**second, "at": second_at}], "connections": [
            {"id": "share", "from": {"block": 0, "port": out["index"]},
             "to": [{"block": 1, "port": port["index"]}, point(out["x"] + 8, out["y"] + 8)]},
            {"id": "other", "from": point(port["x"] - 4, port["y"] - 8, 8, items=[other]),
             "onto": {"block": 1, "port": port["index"]}},
        ]})
        assert all(r.ok for r in result.routes), [r.reason for r in result.routes]
        assert [s for s in serious(result) if "power" not in s and "pole" not in s] == []
        arrives = result.blocks[1].ports[port["index"]]["arrives"]
        assert set(arrives) == {product, other}
        assert not [f for f in result.findings if f.code.startswith("lanes-") and f.code != "lanes-swapped"
                    and not raw_input_unknown(f)]


def two_ingredient_chain() -> tuple[tuple[str, str], tuple[str, str]]:
    """A one-item recipe and a two-item recipe eating its product, each with a 3x3 machine, items only."""
    placeable = prototypes._buildable()
    machines = sorted(
        n for n, d in entities.raw.items()
        if n in placeable and d.get("crafting_speed") and d.get("crafting_categories")
        and prototypes._footprint(d) == (3, 3) and not d.get("fluid_boxes")
    )

    def solid(recipe, ins: int) -> str | None:
        if not recipe or "recycling" in recipe_categories(recipe):
            return None
        parts = (recipe.get("ingredients") or []) + (recipe.get("results") or [])
        if any(p.get("type") == "fluid" for p in parts) or len(recipe.get("results") or []) != 1:
            return None
        if len(recipe.get("ingredients") or []) != ins or "amount" not in recipe["results"][0]:
            return None
        if any(p.get("name") not in items.raw for p in parts):
            return None
        return recipe["results"][0]["name"]

    def machine_for(recipe) -> str | None:
        return next((m for m in machines if crafts(entities.raw[m], recipe)), None)

    makers = {}
    for name in sorted(recipes.raw):
        product = solid(recipes.raw[name], 1)
        if product and machine_for(recipes.raw[name]):
            makers.setdefault(product, name)
    for name in sorted(recipes.raw):
        data = recipes.raw[name]
        if solid(data, 2) is None or not machine_for(data):
            continue
        for ingredient in data["ingredients"]:
            first = makers.get(ingredient["name"])
            if first and first != name:
                return (first, machine_for(recipes.raw[first])), (name, machine_for(data))
    pytest.skip("the active data has no two-item recipe eating a one-item recipe's product")
