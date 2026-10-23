# Looking at a running base

Read when: the player asks why the base makes too little, what to build or
what is wrong with it, and a `survey.json` of the base exists (the companion
mod's `/forge-survey`, or `tools/headless-run.ps1 -Survey` without a window).

```
factorio-forge base survey.json [--target item=rate ...] [-o folder] [--drafts] [--json]
```

Add `--target` for what the player wants ("0.5 panels a second"); without it
the report shows only what falls short of its own machines' capacity. `-o`
writes `base.json`; `--drafts` (needs `-o`) runs `chain` on each thing to
build and writes `folder/<item>/plan.json` and the blueprint.

## How to read it

The report has: the verdict line, the causes, `build`, `fix`, `works well`
and `limits`.

- A cause is a chain; causes are ranked by the idle machine time they explain (below). Each indented line is the cause of
  the line above: a group makes less than its machines could -> it waits for an
  item -> who makes that -> ... down to the first cause. Read the last line of
  each chain: that is what to act on. The numbers on it are rates per second,
  shares of the window and unit numbers.
- The last lines of chains tell different things. "makers work all the time yet the
  users starve" and "N drills work all the time" mean build more. "Drills sit
  with a full output" means the ore is not carried off: a belt problem, not a
  mining one. "Field ... hours left" means a new field is due. "nobody makes it"
  with "made elsewhere" means the item comes from outside: ask the player.
- A chain line "already shown above" is not a loop and not a new cause: look
  where it was first shown. Only "the recipes feed each other" is a real loop.
- Order: ranked by idle machine time, in machines: the idle share of the
  machines of every group the cause shows or reaches, a full output not
  counted (`impact_idle_machines` in the JSON, "explains N machine(s) of idle
  time" on the line). Never by units per second: steam in thousands a second
  is not bigger than a missing ore. A group two causes reach counts in both.
- "possible ... is a lower bound": the group made more than the possible rate
  because modules and researched productivity are not counted. Say that, not
  "it does more than it can".
- Science packs at a full output: with labs in the survey, labs working means
  research takes what it needs (not a problem); labs missing packs lists the
  packs whose makers are not held back (the survey does not say which pack is
  missing); no research in progress is said as such. Without labs in the
  survey (an older file) it is not a cause: say the labs were not surveyed.
- Machines that make buildings (a mall) are not demand: their waiting adds no
  shortfall and no order. They are listed once in the limits; idle is normal.
- A mined resource with no drill and no field: the line names the nearest
  patch, its distance, amount and the enemies near it. Spawners or worms
  counted: tell the player to clear them first, not to just connect a field.
  "Not looked at" is not "none".
- Power: a watt figure appears only when the network's machines want more than
  it makes. Otherwise there is a share of lost machine time and a satisfaction
  value, and the watts are unknown: say so, do not compute them.
- `build` entries carry a ready request and a bill of materials. The boundary
  of each is the base's surplus and the other orders' goods; the orders are
  sized together, so an order's rate may be higher than its shortfall by what
  the other orders draw. The request pins the recipe and machine the base uses.
  The machine counts are a first estimate. If a line says the order draws more
  of an item than the surplus gives, say so: that supply needs raising too.
- `fix` entries need no building; they carry unit numbers and positions.

What to build first, and where, is for the player and you to decide; the tool
gives the numbers. Drafts stand on an empty site and say nothing about terrain.
Say the surplus and the gaps in plain words, and name the numbers.

## What it cannot see

Belts, trains, robots, hand-delivered items, modules, beacons, researched
productivity, anything outside the surveyed area (with an area, no mining
rate or hours left for fields), which network a machine is on, power in watts
unless the wanted watts exceed what is made. A "waits for X" whose makers are fine may be a belt problem. Never present
a cause as certain where the report's `limits` say the snapshot cannot tell.
Details: `docs/base.md`.
