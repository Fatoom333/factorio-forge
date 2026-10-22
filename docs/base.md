# Looking at a running base: why it makes too little, what to build

[Русская версия](base.ru.md)

```bash
factorio-forge base survey.json [--target item=rate ...] [-o folder] [--drafts] [--json]
```

`survey.json` is a snapshot of the base written by the companion mod: every
machine and drill with the share of a time window it spent in each state, what
it was missing while it waited, the ore fields, the production flows and the
power networks (the exact format is in `src/factorio_forge/survey.py`). The
command reads it and answers four questions: what works well, what holds the
base back and why, what to build, and what to mend without building.

Nothing is guessed. A number is read from the snapshot or computed from the
game's own recipes and the machine speeds in the snapshot. Where the snapshot
cannot know, the report says so.

## Reading the report

```
2 recipe group(s) work well; 3 problem(s), the biggest: ...

what holds the base back (biggest loss first):
 1. target solar-panel: 0.5/s wanted, 0.112/s made, short 0.388/s
    -> solar-panel: 0.112/s solar-panel made of 0.45/s possible (6 x assembling-machine-2, working 25% of the time)
        -> solar-panel waits for steel-plate: short 1.57/s, 70% of the time (0.315/s of solar-panel lost)
            -> steel-plate: 8 maker(s) work 100% of the time; they work all the time yet the users starve: short 1.57/s, about 13 more electric-furnace
build:
fix (no building needed):
works well:
limits of this reading:
```

**Verdict line.** How many recipe groups work well, how many problems, the
biggest one.

**What holds the base back.** One numbered entry per problem, biggest loss
first. Under it, indented, the chain down to the first cause: each line is the
cause of the line above it.

- *made X of Y possible* -- a group of machines on one recipe. Y is what they
  would make working all the time: recipe output times crafting speed over
  recipe time, with the machine's built-in productivity (a foundry's +50%).
  The loss is `(1 - share of time working) x Y`.
- *waits for ITEM* -- the ingredient the machines lacked, with the rate they
  could not take and the share of time lost. It points at whoever makes ITEM.
- *makers work all the time yet the users starve* -- the makers are too few.
  The line says how many more machines of that kind, from their own rate.
- *even running all the time they would add only N/s* -- the makers are idle
  too, and even fed they would not cover the need: both are reported.
- *nobody makes it* -- no machine in the snapshot makes the item. If the
  production flow shows the item is made on the surface anyway, it arrives from
  outside (another area, a train, by hand) and the line says how much.
- *drills sit with a full output* -- ore is mined but not carried away: the
  limit is the belt or what takes the ore.
- *field F has N left, about H hours left at R/s* -- the field is running out;
  other fields of the same ore with no drills are named.
- *no drills / no field* -- a field with nothing on it, or no field at all.
- *power is short by N MW* -- from the networks' made and wanted watts.
  The snapshot does not say which network a machine is on; with several short
  networks the line lists them all.
- *no fuel*, *switched off*, *nowhere for it to go*, *no recipe* -- as said.
- *loops back to a step shown above* -- the recipes feed each other; the walk
  stops there instead of going round.

Problems already explained by a chain are not listed again as separate
entries. With `--target item=rate`, a rate wanted (`=60/m` for per minute),
the gap between it and what the snapshot shows made is an entry of its own,
first in the list, followed down the same way.

**Build.** Each leaf that only building fixes becomes an order: the item, the
rate short, and a request exactly as `review` and `chain` take it. What the
base already makes beyond what it uses (an item whose flow made more than it
used) goes into the request's `boundary`, so the bill does not go back to ore
for it; the line says how much of it there is and how much the order draws.
Items that other orders in the list build are also in the boundary. The order
carries the bill of materials with machine counts. With `--drafts -o folder`,
`chain` is run on each and the plan and blueprint are written to
`folder/<item>/`. Where to put the blocks is not decided here: drafts are built
on an empty site.

**Fix.** What needs no building, each with unit numbers and positions: no
recipe, no fuel, a disabled machine, power short, an output with nowhere to go,
drills whose ore is not carried off.

**Works well.** Groups working 90% of the time or more.

## What it cannot see

- Belts, trains, robots and items put in by hand. A machine waiting for an
  item its makers make plenty of may be waiting for the belt.
- Modules, beacons and researched productivity: full rates count the crafting
  speed in the snapshot and the machine's built-in productivity only.
- Anything outside the surveyed area: its makers and users count as nobody.
- A cycle longer than the window (oil, rockets) may be seen in part.
- Which power network a machine is on.

`-o folder` writes `base.json` (the same structure as `--json`) and the drafts.
