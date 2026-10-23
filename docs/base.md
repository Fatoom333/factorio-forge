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

A number is read from the snapshot or computed from the game's own recipes and
the machine speeds in the snapshot. Where the snapshot cannot know, the report
says so instead of filling it in. Machine counts are first estimates, not a
design: they assume the makers could be copied as they stand.

Every recipe group and every short item is shown once. Where the same group or
item comes up again under another line, the report says "already shown above"
instead of repeating the subtree, so the report is as long as the base has
groups, not as long as it has paths. What is short of an item is added up over
everyone who waits for it, and what its makers could still add is taken off
once, at the item.

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

**What holds the base back.** One numbered entry per problem, the one that
explains the most idle machine time first (see Order below). Under it, indented, the chain down to the first cause: each line is the
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
- *power is short by N MW* -- only where a network's machines want more watts
  than it makes. Otherwise the line says the machines lose a share of their
  time to low or no power, gives the network's satisfaction, and says that the
  shortfall in watts is not in the snapshot (satisfaction is a share, not a
  watt ratio). The snapshot does not say which network a machine is on; with
  several short networks the line lists them all.
- *no fuel*, *switched off*, *nowhere for it to go*, *no recipe* -- as said.
- *the recipes feed each other here* -- a real loop in the recipes (A needs
  what B makes and B needs what A makes); the walk stops there instead of
  going round.
- *already shown above* -- the group or item was expanded on an earlier line;
  it is not a loop, only not repeated.
- A machine that waits is followed by what the snapshot says was missing,
  whatever its state is called (items, fluids, science packs).

- *possible is a lower bound* -- the group made more than the rate counted as
  possible. Modules and researched productivity are not in the snapshot, so
  the possible rate is a floor; the made rate is what was seen.
- *labs ... fed*, *no research is in progress*, *survey has no labs* -- a
  science pack held at a full output is judged by the labs, when the survey
  has them. Labs mostly working: research takes what it needs, a full output
  is normal and the group is not a problem (it is listed under works well as
  "research"). Labs waiting for packs: the pack held back is not the one they
  lack; the packs whose makers are not held back by a full output are listed
  under "labs wait for science packs" and traced like any other shortage only when exactly one pack is a candidate, as an estimate no larger than double what the labs take now; with several, none is ordered (the
  survey does not say which pack a lab misses). No research in progress: said
  so. No labs in the survey (an older file): said so in the limits, no cause.
- *nearest patch ... enemies* -- a resource that nothing at the base mines, with no field of it at
  the base: the nearest patch the survey found, its distance and amount, and
  the enemies counted near it (spawners and worms: clear them first; none
  found; or not looked at). A resource in no patch the survey searched is said
  to be outside its search radius. Fields with enemies near them are listed in
  the limits.

**Order.** Entries are ranked by the machine time they explain, in machines,
not by units per second: a fluid made in thousands per second counts for
what its machines leave idle, not for its rate. Each group's idle time for a
shortage (idle for a full output is not counted: that is the symptom, the
cause is further down) is credited once, in the chain that shows the group,
and divided among the things it waited for in step with the output lost to
each; only what ends in a fault (too few makers, no ore, no field, power, fuel,
a switched-off machine) is credited, and the part for something shown in
another chain goes to that chain. An entry that is only a full output, with no
fault under it, comes after the ones that have one. The figure is in each
entry's `numbers` (`impact_idle_machines`, and `explains_idle_machines` on
every line) and in its text ("explains N machine(s) of idle time"). A user
that does not take a full output is an entry of its own, not hidden under the
maker whose output is full.

**Machines that make buildings** are not demand. A machine whose product is
a building (a foundry making foundries, a drill, a belt) waits and fills its
output whenever the base has enough of it; that raises no shortfall and adds
nothing to what its ingredients are wanted at. They are listed once in the
limits. A building that another machine, outside the buildings, takes as an
ingredient (a belt into a science pack, a pipe into an engine) is not left
out this way: its makers are ordinary producers and the chain goes on to what
they lack.

Problems already explained by a chain are not listed again as separate
entries. With `--target item=rate`, a rate wanted (`=60/m` for per minute),
the gap between it and what the snapshot shows made is an entry of its own,
first in the list, followed down the same way.

**Build.** Each leaf that only building fixes becomes an order: the item, the
rate short, and a request exactly as `review` and `chain` take it. What the
base already makes beyond what it uses (an item whose flow made more than it
used) goes into the request's `boundary`, so the bill does not go back to ore
for it; the line says how much of it there is and how much the order draws.
Items that other orders in the list build are also in the boundary, and the
orders are sized together: what one order's bill draws from another order's
item is added to that order's rate, and the bills are made again until the
sizes settle (if they do not, the order says so). The order pins the recipe and
the machine the base itself uses for the item, so the bill speaks of the
machines the base has; whether the player can build that machine is the
review's to say. The order carries the bill of materials with machine counts
for the whole of what it builds, which can be more machines than the "about N
more" on the line (that counts only the makers that already stand). With `--drafts -o folder`,
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
- Which power network a machine is on, and how many watts are missing when
  the networks' wanted watts do not exceed what they make.
- With an area surveyed (`area` set in the file; a whole-base survey has it
  null and gets rates and hours), production flows still cover the whole surface, so no
  mining rate, drill count per field or hours left is given for fields.

`-o folder` writes `base.json` (the same structure as `--json`) and the drafts.
