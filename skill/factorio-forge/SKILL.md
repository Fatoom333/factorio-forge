---
name: factorio-forge
description: Design Factorio blueprints for a player's own save -- their mod set, recipes, belt tier and building style -- using the factorio-forge toolkit. Use when the user asks for a Factorio blueprint, production block, smelting or assembly array, city block contents, or wants a layout planned, checked or drawn.
---

# factorio-forge

You are the designer; the toolkit is your hands and your checker. You decide
the layout -- which blocks, how many rows, which belt and inserter, how the
blocks fit the plot and the player's style. The tools give you the numbers to
decide with, expand your decisions into exact entities from the active mod
set's own prototypes, and check the result the way the game would.

Do not place machines, inserters and belts tile by tile from your head. Write
a plan of blocks, build it, read the report, look at the drawing, revise.
Placing entities by hand is for the short connections between blocks, and
those are checked too.

All commands run from the factorio-forge checkout with its environment:

```
cd <factorio-forge>
.\.venv\Scripts\python.exe -m factorio_forge.cli <command>
```

## 0. Know which game you are designing for

```
factorio-forge list-profiles        # which save profiles exist, which is active
factorio-forge activate-profile NAME
factorio-forge show-style NAME      # measured spacing, alignment, symmetry, orientation
```

Every number below comes from the active profile. A blueprint designed against
the wrong profile is wrong in ways nothing will flag. If the player's save has
no profile yet: `factorio-forge create-profile <save>`.

## 1. Pin down the request

Before any layout, have answers to:

- **Target**: which item, at what rate (per second).
- **Boundary**: what arrives from outside (train, bus, another block) and is
  not to be made here. In a city block this is usually plates and fluids.
- **Plot**: free area, and its shape. The player's base may use hexagonal or
  rectangular city blocks on a rail grid; a row that runs into a diagonal edge
  is shorter than the one in the middle.
- **Tiers**: belt, inserters, pole the player has researched, and the
  inserter hand size (stack bonus). If you do not know the stack bonus, ask or
  say what you assumed -- with a hand of 1, fast inserters often cannot keep
  up with fast recipes, and the tools will tell you so.

Ask when something that changes the design is missing. Do not ask about what
the tools can compute.

## 2. How many machines

Bill of materials (Python, no CLI yet):

```python
from factorio_forge import bom
result = bom.compute(bom.Request(
    targets=(bom.Target("battery", 2.0),),
    boundary=frozenset({"iron-plate", "copper-plate", "sulfuric-acid"}),
))
for line in result.lines:
    print(line.recipe, line.machine, line.machines, line.inputs, line.outputs)
print(result.ambiguities)   # choices it made for you
```

It picks the fastest machine for each crafting category and says so in
`ambiguities`. The player may not have that machine; pin it with
`machine_choices={"chemistry": "chemical-plant"}` (keyed by category), and a
recipe with `recipe_choices={"item": "recipe"}`.

## 3. The numbers for each block

```
factorio-forge options <recipe> <machine> --belt B --inserter I --long-inserter L --pole P [--stack-size N] [--machines M]
```

Without `--belt`/`--inserter`/`--pole` it lists the choices with their
throughput, reach and supply area. With them it reports:

- flows per machine;
- **how many machines one row keeps running**, and the reason in words --
  on its own input belts, and when a mirrored neighbour shares them;
- input lane assignment and inserters needed per machine;
- tiles per row for `mirror` and `repeat` stacking;
- with `--machines`, the block size for 1..8 rows and which rows would be
  overfed.

## 4. Decide, and write the plan

```json
{
  "label": "Batteries 2/s",
  "blocks": [
    {"type": "rows", "recipe": "battery", "machine": "chemical-plant",
     "rows": [10, 10, 10, 10],
     "belt": "fast-transport-belt", "inserter": "fast-inserter",
     "long_inserter": "long-handed-inserter", "pole": "medium-electric-pole",
     "stack_size": 2, "stack": "mirror", "align": "start",
     "at": [0, 0], "rotate": 0}
  ],
  "entities": [
    {"name": "fast-transport-belt", "position": [-1, 2], "direction": 4}
  ]
}
```

Block fields: `recipe`, `machine`, `rows` (list of machine counts, or `rows` +
`per_row`), `belt`, `inserter` (nearer belt and output), `long_inserter`
(second input belt), `pole`, optional `pipe`, `pipe_to_ground`,
`input_belts`, `stack_size`, `speed_bonus`, `stack` (`mirror`|`repeat`),
`align` (`start`|`center`), `at` [x, y] (top-left tile), `rotate`
(0/90/180/270, clockwise). In a block before rotation, rows run west to east,
inputs enter at the west end, outputs leave at the east end.

Design rules, with the reason for each:

- **Never plan a row longer than its capacity.** An overfed row looks built
  and runs at a fraction; the player's own base does this twenty times.
  More machines than one row holds means more rows.
- **Fill rows toward capacity** when the plot allows: a short row wastes a
  belt's worth of corridor.
- **Mirror by default.** Neighbouring rows share their output belt (each fills
  one lane) and their input belts (each gets half), and fluid-only rows share
  their pipe. It is also how the player builds.
- **An inserter drops on one lane.** A lone row's output belt is half full at
  best; two mirrored rows fill it.
- **Two ingredients on one belt get one lane each.** If one of them is much
  hungrier, `input_belts: 2` gives it a belt.
- **Plot shape**: for a non-rectangular plot, give each row the length the
  plot allows at that row and `align: center`.
- **Machines 7 tiles and wider**, or rows kept far apart by fluid tracks, need
  a pole with a long wire (substation class). The report says when.
- **Style**: copy what the player does -- flush machines, mirrored rows,
  orientation, grid alignment from `show-style`. Do not copy their mistakes:
  the tools compute capacity; the base is evidence of style, not of
  correctness.

## 5. Build, read, look, revise

```
factorio-forge build plan.json -o out/
```

Writes `out/<label>.txt` (blueprint string), `out/<label>.html` (drawing with
findings marked) and `out/<label>.report.json`. Read the report:

- **findings** with severity `problem` must be fixed before handing anything
  over: overlaps, unpowered machines, `fluids-mixed`,
  `fluid-box-unconnected`, `power-split`;
- **rows**: machines against capacity;
- **ports**: where each belt and pipe enters or leaves, its lane contents
  (left, right in the direction of travel) and rate -- this is what you
  connect blocks with;
- **notes**: overfed rows, extra inserters, gap columns for poles.

Then look at the drawing (render the HTML in a browser, or publish it) and ask
whether a player would build it this way. Revise the plan, not the output.

## 6. Connect blocks

Use port coordinates to lay the connecting belts and pipes in `entities`:
straight runs, underground belts or pipes to cross a line, a splitter to
share. Rebuild; the same checks run over hand-placed entities. Keep two
different fluids' pipes from touching -- `fluids-mixed` catches it.

## 7. Hand over

- Give the blueprint as a **file**, not pasted into chat: long strings pasted
  through chat have come back corrupted.
- Say what you decided and why, with the numbers: machines, rows against
  capacity, belts in and out with rates, what arrives from outside.
- Say what was assumed (stack bonus, recipe choices from `ambiguities`) and
  what is not checked (below).

## When the tools refuse

| Message says | Do |
|---|---|
| `already outruns its belt` | faster belt, or a slower machine |
| `needs input belt 2 ... choose a long_inserter` | add `long_inserter` |
| `N solid ingredients needing 3 input belt(s)` | this row form holds 4 lanes; split the recipe's supply differently or hand-build |
| `arm-line tiles and needs` / `no arm-line tile ... lets` | faster inserter, larger `stack_size`, or fewer machines per belt |
| `connections point into the neighbouring machine` | this machine's fluid boxes do not fit a flush row; another machine, or hand-build |
| `split is only known when it divides evenly` | the game's fluid-box assignment is uncertain here; confirm in game before building |
| `pole network is in N pieces` | pole with a longer wire |

## Not checked yet

- Inserter throughput is a chest-to-chest upper bound; from a belt it is
  lower.
- Pipe throughput over long runs is not modelled.
- Modules and beacons are not placed; `speed_bonus` only changes the numbers.
- No automatic routing between blocks, no train stations; connect by hand.
