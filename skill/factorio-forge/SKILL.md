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

All commands run with the Python environment inside the player's
factorio-forge checkout (the cloned repository, with a `.venv` folder made by
its README's setup). Find the checkout once per conversation:

1. The working directory, or a folder above it, if it holds `pyproject.toml`
   with `name = "factorio-forge"` and a `.venv` folder.
2. If this skill's folder is a link into the checkout (the README installs it
   that way), follow it: the link points at `skill/factorio-forge`, two levels
   below the checkout. PowerShell: `(Get-Item "$env:USERPROFILE\.claude\skills\factorio-forge").Target`;
   bash: `readlink -f ~/.claude/skills/factorio-forge`.
3. Otherwise ask the player where they cloned factorio-forge. If they have
   not, point them to the README's setup; do not install it yourself unasked.

Then run every command with that checkout's Python:

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
`work/`, e.g. `work/red-circuits/`. `work/` is ignored by git, so nothing of
the player's ends up in the repository; never write them to the checkout's
root or into `src/`. Paths below (`work/<design>/request.json`) are relative
to the checkout. JSON may be saved with or without a UTF-8 BOM (Windows
PowerShell 5.1 `Set-Content -Encoding utf8` adds one).

## 0. Know which game you are designing for

```
factorio-forge list-profiles        # which save profiles exist, which is active
factorio-forge activate-profile NAME
factorio-forge notes [NAME]         # where the profile's notes.md and reference blueprints are
factorio-forge available            # belts, inserters, poles the player has; bonuses
factorio-forge show-style NAME      # measured spacing, alignment, symmetry, orientation
```

Every number below comes from the active profile. A blueprint designed against
the wrong profile is wrong in ways nothing will flag. If the player's save has
no profile yet: `factorio-forge create-profile <save>`.

**Read the profile's `notes.md` before designing.** Every profile has one: the
base's accumulated preferences in the player's own words -- conventions to
follow, things to avoid, corrections made to earlier designs. What the notes
say beats the generic defaults in this skill. When the player corrects a
design, or states a standing preference about their base, append it to that
file (one dated line, in their words), not to this skill: the skill is for
every player, the notes are for this base only.

`factorio-forge notes [NAME]` prints where it is, with the profile's folder
and its `reference/` folder if there is one: the player's own blueprints the
notes were measured from. A note tagged with a `reference/` file holds only
for that blueprint; when the player gives a new version, replace the file and
re-measure every note that names it.

What the player has *unlocked*, and the bonuses research has given them
(inserter hand size, recipe productivity, mining productivity), only the
running game knows. The companion mod (0.7.0 or later) exports it: the player
runs `/forge-export` in that save, or presses Export in its window. The tools
use the export only when its mod set matches the active profile, and say when
it does not: which mods differ, which export it was (tick, play time, when the
file was written) and what to do -- `/forge-export` in the profile's save, and
`create-profile --force` if its mods or startup settings changed. Pass that on
to the player rather than guessing around it. Without it, ask about tiers instead of guessing, and treat
inserter hands as 1.

## 1. Pin down the request

The player's words are yours to read; the toolkit checks your reading.

**Turn every thing they name into a prototype name**, never from memory --
mods rename things (a mod may give red science another name altogether):

```
factorio-forge find красные колбы
factorio-forge find --kind item автоматизационный исследовательский пакет
```

Pass the player's words as they said them. It tries three ways at once and
says under each candidate why it was found:

- **slang** from `src/factorio_forge/slang.py` -- "красные колбы", "синие
  схемы", "мазут", "качалка", "green belt", "prod modules" and about a hundred
  more, in Russian and English;
- **the name shown in this mod set**;
- **the base game's name**, when a mod renamed the thing: "matched the base
  game's name «…»; this mod set shows it as «…»". Tell the player the name
  their game uses.

A vague phrase ("колбы", "ленты", "ассемблеры") lists every option with
"which …?" -- ask. A phrase with a note (like "аккумуляторы": players mean the
accumulator block, the base game's Russian «Аккумулятор» is the battery) is
ambiguous -- ask. "known slang for X, which this mod set does not have" means
the thing is gone (Factorio 2.0 removed the rocket control unit), not that
the player misspoke.

When `find` has nothing and you know what the player meant, search by the
internal name, and **add the phrase to `slang.py`** (a phrase list, the base
game's internal names, a note only if it is ambiguous) so the next request
does not have to work it out again. The test suite checks every name there
against the game's own files.

**Write the request** as JSON -- only `targets` is required:

```json
{
  "said": "the player's words, verbatim",
  "targets": [{"item": "battery", "per_second": 2}],
  "boundary": ["iron-plate", "copper-plate", "sulfuric-acid"],
  "tiers": {"belt": "fast-transport-belt", "inserter": "fast-inserter",
            "long_inserter": "long-handed-inserter", "pole": "medium-electric-pole"},
  "machine_choices": {"chemistry": "chemical-plant"},
  "recipe_choices": {},
  "effects": {"*": {"speed": 0, "productivity": 0, "consumption": 0}},
  "plot": {"width": 120, "height": 40},
  "style": ["mirrored rows like their copper block"],
  "surface": "nauvis"
}
```

- **targets**: rate `per_second` or `per_minute`. "Одна полная лента" is a rate:
  convert with `available belt`.
- **boundary**: what arrives from outside (train, bus, another block) and is
  not made here. In a city block usually plates and fluids.
- **tiers**: leave out what the player did not say; the review fills in the
  best they have unlocked, and lists it.
- **machine_choices** (by crafting category), **recipe_choices** (by item):
  only what the player asked for.
- **effects**: modules and beacons per crafting category, as bonus fractions.
- **plot**: the space and its shape; give the outline of a non-rectangular
  plot, not its bounding box.
- **style**: what they said about looks, in their words.
- **surface**: the planet or platform it is built on. It decides what is free
  to mine, pump or collect there and which recipes and machines work (Space
  Age surface conditions). With one surface in the mod set it is assumed;
  with several and none given, the review asks, and until then anything
  minable anywhere counts as free.

**Review it**:

```
factorio-forge review work/<design>/request.json
```

It reports three lists and the bill of materials:

- **problems** -- unknown names (with suggestions), locked targets, tiers or
  machines. Fix them, usually by asking.
- **ask the player** -- what only they can answer: missing plot, what arrives
  from outside when nothing does, tiers when there is no export, the surface
  when there are several, and anything the bill needs that this surface does
  not have ("calcite cannot be had on nauvis (free on vulcanus)") -- the
  answer is usually "it comes by rocket", which goes into `boundary`.
  Without an export, also **which machine** per category: the bill assumed
  the most basic one and lists the faster ones with their speed and needs;
  put the answer in `machine_choices`. And any machine that **needs more than
  power** ("biochamber needs more than power: burns nutrients fuel") --
  rows do not feed fuel or heat, so either the player supplies it (plan it
  yourself) or another machine is pinned.
- **assumed** -- tiers filled in, the machine picked per category (with an
  export: the fastest they can build), researched productivity applied. Say
  these to the player in one short list; do not make them confirm each.

Ask everything that is missing in **one message**, with a sensible default
beside each question, so the player can answer "yes" or correct one line. Do
not ask what the tools can compute. When the review has no problems and the
answers are in, the bill of materials it printed is step 2 done.

## 2. How many machines

`review` already ran it. In Python, for more:

```python
from factorio_forge import bom, environment, request
found, why = environment.for_active_profile()
spec = request.load("work/<design>/request.json")
result = request.review(spec)       # result.bill is a bom.BillOfMaterials
for line in result.bill.lines:
    print(line.recipe, line.machine, line.machines, line.inputs, line.outputs)
```

## 3. The numbers for each block

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

## 4. Decide, and write the plan

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
`input_belts`, `stack_size` (inserter hand; default from the export), `speed_bonus`, `stack` (`mirror`|`repeat`),
`align` (`start`|`center`), `at` [x, y] (top-left tile), `rotate`
(0/90/180/270, clockwise). In a block before rotation, rows run west to east,
inputs enter at the west end, outputs leave at the east end.

Design rules, with the reason for each:

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

## 5. Build, read, look, revise

```
factorio-forge build work/<design>/plan.json
```

Writes, beside the plan, `<label>.txt` (blueprint string), `<label>.html`
(drawing with findings marked) and `<label>.report.json`. Read the report:

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

Write `connections` from the port indices the build report prints (`[j]`
under each block): `{"from": {"block": 0, "port": 3, "items": [...]}, "to":
{"block": 1, "port": 0}}`. A point (`{"at": [x, y], "direction": d, "items":
[...], "rate": r}`) stands for a blueprint edge or anything outside the plan.
Put `items` on port references: if a block change renumbers its ports, the
build refuses instead of joining the wrong ones. Belt, underground,
pipe and pipe-to-ground default to the blocks' own; name them to override,
`"underground": false` to stay on the surface.

The router finds the pieces and obeys what the game would: no side-loading,
nothing in an inserter's reach, no pipe touching another fluid (it goes
underground to squeeze past), no hop that steals another pair. Underground
reach comes from the active profile's prototypes, so mods that change it are
followed. It joins ports straight and keeps lanes as the source has them; it
cannot swap lanes. Routes go in plan order and each blocks the next, so put
the hardest connection first. On `route-failed`, read the reason and the tile
it names, then steer: a `via` tile, a larger `routing.margin`, a moved or
rotated block, a different order, undergrounds allowed. Keep doors and
future corridors free with `routing.reserve`.

Hand-place in `entities` only what the router cannot do: splitters, merges,
one source to several destinations, lane swaps, joins into the middle of a
belt. The same checks run over hand-placed entities.

### Grid-snapped sections (walls, lines of modules)

For a book of sections the player places side by side, to deploy quickly:

- Blueprint keys `snap-to-grid: {x, y}` and `absolute-snapping: true`; the grid
  cell is (0,0)..(x,y) in blueprint coordinates (confirmed in game).
- Draw every section for one side only (e.g. outside to the north) and let the
  player turn it with R. Rotation keeps a clockwise belt ring clockwise;
  mirroring reverses it -- say "rotate, do not flip".
- Make the cell a whole number of modules and of any repeating pattern's
  period along the line, so modules and pattern continue across joints.
- A corner meets two differently turned neighbours: anything that runs across
  the joints (a repeating pattern, belts, a pipe chain) must continue both. Split a
  pattern along the diagonal: one side's pattern above it, the other side's
  below. A corner drawn for one position fails in the other three.
- Check by assembling a test ring from turned copies, not each section alone:
  belts closed and each feeder reaching its ring, one pipe network with only
  the intended inlet, power, roboport coverage, the pattern carried a few tiles
  into each neighbour. Check power by the blueprints' wires, not by distance.
- Poles built from a blueprint do **not** connect to neighbours by themselves,
  even within reach (seen in game), and a blueprint cannot wire to another
  blueprint. Put one pole straddling each joint in *both* neighbouring
  sections at the same spot, wired inside each section: the second paste
  lands on the first one's pole and its wires attach to it.

## 7. Hand over

- Give the blueprint as a **file**, not pasted into chat: long strings pasted
  through chat have come back corrupted.
- Say what you decided and why, with the numbers: machines, rows against
  capacity, belts in and out with rates, what arrives from outside.
- Say what was assumed (stack bonus, recipe choices from `ambiguities`) and
  what is not checked (below).

## Factory buildings (Factorissimo)

When the mod set has Factorissimo 3 (`factory-1/2/3`), a design inside a
building is several blueprints: the exterior (it carries only `tags.id`, not
the floor), the floor inside, and the floor of each nested building. Hand
them over separately with the order to place them.

- Ports sit at fixed positions that depend on the building's size and quality;
  read the mod's own layout code (`script/layout.lua` in its zip) for the
  coordinates and counts instead of guessing. A belt port carries one full
  belt, and the belt on it must face in (input) or out (output).
- Everything that enters or leaves a nested design crosses the outermost
  building's ports, so nesting adds room, not throughput.
- The floor is powered everywhere; the exterior needs a pole whose area
  touches the building (the checker does not flag it).
- Keep the door (middle of the south wall) clear; cross it underground.

Beacons are placed by hand for now. A beacon's effect is not linear in the
number of beacons on a machine: the prototype's `profile` array scales it
(1/sqrt(n) in the base game; a mod can change it). Its reach is the prototype's
`supply_area_distance`, measured from collision boxes, which are slightly
smaller than the tiles a machine occupies, so a gap of exactly that many whole
tiles is already out of reach (not yet confirmed in game). "Researched" in the
export is not "produced": ask which module tier the player actually makes.

## Lessons that hold for any base

Mistakes that already happened once; check against them before handing over.

- **Measure a plot by its true outline.** A bounding box, or "tiles with no
  entity", counts the corners outside a non-rectangular plot as free and
  invents room and split rows. Test every tile against the polygon itself
  (point-in-polygon on the boundary you were given).
- **Verify the numbers the player gives you.** Their base shows their style,
  not correct capacities: recompute belt limits and row lengths for the recipe
  at hand, and copy the grammar, not the figures.
- **Look for the node in the player's own blueprints before inventing it.**
  For a station, balancer, feeder or merge, find the same entities in what
  they have shown you and copy the whole arrangement. If they have none, use a
  standard community technique and say which, or ask. Their blueprints can be
  unfinished too: check wires and power in them as in your own.
- **Connected is not good.** A design can pass every connectivity check and
  still feed one lane of a belt, or drain a chest group unevenly. Add the
  quality checks (both lanes used, even draining) and say aloud any trade-off
  you accept; never make one silently.
- **Pasting over an existing build keeps its old wires.** The paste updates
  entity settings and adds wires but never removes any, so a design that
  removes or reroutes a wire can merge two circuit networks. Diff the wire
  lists of old and new, name each removed wire before handing over, and offer
  a paste-over-safe variant (a new signal on the other wire colour) or "delete
  the old build first", and let the player choose.
- **Water is not in a blueprint or a region export.** Only placeable tiles
  (landfill, concrete) are carried; shoreline is not. A coastline taken from a
  screenshot is a guess with an unknown edge: say so, keep anything that must
  stay on land a couple of tiles from that edge, and remember that landfill in
  a blueprint is harmless where the tile is already land.
- **Check circuits by running them in the game.** What a combinator network does
  lives in the engine, not in the data files; an external simulator can drift
  silently. Use the companion mod to build and run it, and read what it records.
- **Check what an entity really is before assuming its behaviour.** Prototype
  `type` decides it, and mods change it: where a mod makes furnaces assembling
  machines, a furnace needs a recipe, and "pick the recipe from the input"
  does not apply.
- **While iterating, look at a text tile map; render once, at the end.**
  `factorio-forge map work/<design>/<label>.txt` prints the blueprint one
  character per tile, with coordinates and a legend: belt arrows, inserter
  arrows pointing where they drop, `U`/`u` underground entrance and exit,
  anything larger than a tile (machines, splitters `Ss`, big poles) as a
  capital at the centre of a lower-case footprint (a splitter's direction is
  not drawn), `?` for what the
  data does not know, `!` for overlaps (`--index N` for a book entry). Each
  drawing costs a screenshot; one render of the final version is enough.

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
- Routing joins one port to one port only: no splitters, merges, balancers,
  lane swaps or mid-belt joins, no rip-up and reroute across routes (they
  are greedy in plan order), and no map terrain -- water, cliffs, ore and an
  existing base are not obstacles unless reserved. No train stations.
- Not yet confirmed in game: an underground pairing at exactly its reach, a
  curve fed by an underground exit keeping both lanes.
