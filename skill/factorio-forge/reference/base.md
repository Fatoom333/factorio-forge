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

- A cause is a chain, biggest loss first. Each indented line is the cause of
  the line above: a group makes less than its machines could -> it waits for an
  item -> who makes that -> ... down to the first cause. Read the last line of
  each chain: that is what to act on. The numbers on it are rates per second,
  shares of the window and unit numbers.
- The last lines of chains tell different things. "makers work all the time yet the
  users starve" and "N drills work all the time" mean build more. "Drills sit
  with a full output" means the ore is not carried off: a belt problem, not a
  mining one. "Field ... hours left" means a new field is due. "nobody makes it"
  with "made elsewhere" means the item comes from outside: ask the player.
- `build` entries carry a ready request and a bill of materials. The boundary
  of each is the base's surplus and the other orders' goods. If a line says the
  order draws more of an item than the surplus gives, say so: that supply
  needs raising too.
- `fix` entries need no building; they carry unit numbers and positions.

What to build first, and where, is for the player and you to decide; the tool
gives the numbers. Drafts stand on an empty site and say nothing about terrain.
Say the surplus and the gaps in plain words, and name the numbers.

## What it cannot see

Belts, trains, robots, hand-delivered items, modules, beacons, researched
productivity, anything outside the surveyed area, which network a machine is
on. A "waits for X" whose makers are fine may be a belt problem. Never present
a cause as certain where the report's `limits` say the snapshot cannot tell.
Details: `docs/base.md`.
