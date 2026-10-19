---
name: factorio-forge
description: Design Factorio blueprints for a player's own save -- their mod set, recipes, belt tier and building style -- using the factorio-forge toolkit. Use when the user asks for a Factorio blueprint, production block, smelting or assembly array, city block contents, or wants a layout planned, checked or drawn.
---

# factorio-forge

You are the designer; the toolkit is your hands and your checker. You decide
the layout -- which blocks, how many rows, which belt and inserter, how the
blocks fit the plot and the player's style. The tools give you the numbers to
decide with, expand your decisions into exact entities from the active mod
set's own prototypes, and check the result the way the game would. Do not
place machines, inserters and belts tile by tile from your head: write a plan
of blocks, build it, read the output, revise. Hand-placing is for the few
pieces the router cannot do, and those are checked too.

## Topic files

Files below are in `reference/` next to this one. Open one only when its
topic comes up.

- `profile.md` -- profiles, `notes` index, the player's notes, the game export (unlocks, bonuses)
- `request.md` -- `find` (names and slang), the request JSON, `review`, the bill of materials
- `plans.md` -- `options`, plan JSON and block fields, design rules, `chain`, `edit`
- `build.md` -- `build` output, what each part means, looking at the result, two strikes
- `routes.md` -- `connections`, split / merge / lane join, hand-placed entities
- `lanes.md` -- every `lanes-*` finding, `block-short`, lane facts
- `power.md` -- `power-split`, `power.link`, poles, beacons and modules
- `fluids.md` -- pipes, fluid findings, fluid-only refusals
- `measure.md` -- `measure` in the game, in-game check before delivery
- `map.md` -- the text tile map (`map`)
- `grid-sections.md` -- books of snap-to-grid sections (walls, module lines)
- `factory-buildings.md` -- Factorissimo
- `lessons.md` -- mistakes that hold for any base; read before handing over
- `troubleshooting.md` -- what to do on a refusal message
- `not-checked.md` -- what the tools do not cover; say it at hand-over

## Running commands

All commands run with the Python environment inside the player's
factorio-forge checkout (the cloned repository, with a `.venv` made by its
README). Find the checkout once per conversation:

1. The working directory, or a folder above it, if it holds `pyproject.toml`
   with `name = "factorio-forge"` and a `.venv` folder.
2. If this skill's folder is a link into the checkout, follow it: the link
   points at `skill/factorio-forge`, two levels below the checkout.
   PowerShell: `(Get-Item "$env:USERPROFILE\.claude\skills\factorio-forge").Target`;
   bash: `readlink -f ~/.claude/skills/factorio-forge`.
3. Otherwise ask the player where they cloned it. If they have not, point to
   the README's setup; do not install it yourself unasked.

```
# Windows
& "<checkout>\.venv\Scripts\python.exe" -m factorio_forge.cli <command>
# Linux, macOS
"<checkout>/.venv/bin/python" -m factorio_forge.cli <command>
```

`factorio-forge <command>` below is short for this. Start with `paths`: it
shows where the game, its saves and its mods were found.

Work inside the checkout, and keep each design's files -- `request.json`,
`plan.json`, scratch scripts, what `build` writes -- in its own folder under
`work/`, e.g. `work/red-circuits/`. `work/` is ignored by git; never write
them to the checkout's root or into `src/`. Paths below are relative to the
checkout. JSON may be saved with or without a UTF-8 BOM (Windows PowerShell
5.1 `Set-Content -Encoding utf8` adds one).

## Order of work

0. **Profile and notes.** `factorio-forge list-profiles`; activate the
   player's save (`activate-profile NAME`, or `create-profile <save>` if none).
   Every number comes from the active profile; the wrong profile is wrong in
   ways nothing flags. Read the notes **index** (`factorio-forge notes`) and
   open only the sections the request touches. What the notes say beats this
   skill's defaults. Player corrections and standing preferences go into the
   notes, not into this skill. -> `reference/profile.md`
1. **Request.** `find` every thing the player names (never from memory, mods
   rename things), write `request.json`, run `review`. Ask everything missing
   in one message, with a default beside each question. -> `reference/request.md`
2. **Plan.** Machine counts come from `review`; `options` gives the numbers
   per block. Decide the layout and write `plan.json`. A whole multi-stage
   order: `chain` drafts it (one line below). -> `reference/plans.md`
3. **Build.** `factorio-forge build work/<design>/plan.json`. -> `reference/build.md`
4. **Read the output, look, revise.** Fix the plan, never the output. Use the
   text tile map while iterating (`map`), render once at the end.
5. **Connect and check.** `connections` between blocks, `power.link` for
   poles. -> `reference/routes.md`, `reference/power.md`
6. **Hand over.** Read `reference/lessons.md` and `reference/not-checked.md`
   first.

New commands, in one line each:

- `chain` -- draft plan for a whole order at once; see `reference/plans.md`.
- `edit` -- small plan edits without a full rebuild; see `reference/plans.md`.
- `notes` / `notes <topic>` -- the notes index and one section; see `reference/profile.md`.
- In-game check before delivery -- see `reference/measure.md`.

## Reading the build output

The output has two levels. **Errors** -- the blueprint will not stand or will
not work (a collision, an unconnected input, the wrong item on a lane): fix
every one before anything is handed over. **Facts** -- rates, belt loads, lane
fill, counts of entities: they are yours to weigh. A belt of copper at 40 %
may be exactly what the player wants; decide, and mention what matters. There
is no middle level of "warnings" and no rule per kind of fact. Errors come
first; read them first.

## Main rules

- **Plan blocks, not tiles.** You choose; the tool lays out exact entities.
- **Never plan a row longer than its capacity**; more machines than a row
  holds means more rows. An overfed row runs at a fraction.
- **Copy the player's style, not their mistakes.** The base is evidence of
  style, not of correct capacities.
- **Two strikes.** When the same spot fails twice (the same few tiles, port
  or connection, or the same finding repeating after you changed something
  around it), do not make a third fix of the same kind: redesign that spot --
  another layout, route or arrangement of blocks.
- **Hand over a file**, not a string pasted into chat (long strings come back
  corrupted). Say what you decided and why, with numbers: machines, rows
  against capacity, belts in and out with rates, what arrives from outside,
  what was assumed (stack bonus, recipe choices from `ambiguities`), and what
  is not checked.
