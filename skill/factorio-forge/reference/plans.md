# Plans: numbers per block, the plan file, design rules

Read when: deciding the layout and writing or changing `plan.json`
(steps 3-4).

## The numbers for each block

```
factorio-forge options <recipe> <machine> --belt B --inserter I --long-inserter L --pole P [--machines M]
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

## The plan file

```json
{
  "label": "Batteries 2/s",
  "blocks": [
    {"type": "rows", "recipe": "battery", "machine": "chemical-plant",
     "rows": [10, 10, 10, 10],
     "belt": "fast-transport-belt", "inserter": "fast-inserter",
     "long_inserter": "long-handed-inserter", "pole": "medium-electric-pole",
     "stack": "mirror", "align": "start",
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
`input_belts`, `stack_size` (inserter hand; default from the export),
`speed_bonus`, `stack` (`mirror`|`repeat`), `align` (`start`|`center`), `at`
[x, y] (top-left tile), `rotate` (0/90/180/270, clockwise). In a block before
rotation, rows run west to east, inputs enter at the west end, outputs leave
at the east end.

Put `"request": "request.json"` (the reviewed request, path from the plan's
folder) in the plan and ports read `5/s planned (6/s at full load)`, routes
are sized for the planned flow, and a block planned above its capacity is
`block-short`. A block's own `"planned"` (crafts/s) is its share of the
request; the other blocks of that recipe share the rest.

## Design rules, with the reason for each

- **Never plan a row longer than its capacity.** An overfed row looks built
  and runs at a fraction, and players' own bases are full of them. More
  machines than one row holds means more rows.
- **Fill rows toward capacity** when the plot allows: a short row wastes a
  belt's worth of corridor.
- **Mirror by default.** Neighbouring rows share their output belt (each fills
  one lane) and their input belts (each gets half), and fluid-only rows share
  their pipe. It is also a common way to build; `show-style` says whether this
  player does.
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

## Chain: a draft plan for a whole order (new)

`chain` turns the bill of materials of a multi-stage order into a draft plan
at once: one block per recipe (a stage shared by several consumers is one
block, its output split by a splitter), blocks in the order of the flow,
belts between each output and the next input, `sources` for raw inputs. You
check and correct the draft; you do not lay the stages out by hand.

> SYNTAX TO FILL AFTER MERGE: exact `chain` command and options.

## Edit: small changes without a rebuild from scratch (new)

`edit` applies short operations to a plan -- move a block, change a recipe,
machine, modules or a belt, add or remove a row. Only the touched block and
its belts are rebuilt; the rest comes from the previous build's cache. Do not
rewrite the whole plan for a small change.

> SYNTAX TO FILL AFTER MERGE: exact `edit` operations.
