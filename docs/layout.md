# Layout: from a decision to a checked blueprint

[Русская версия](layout.ru.md)

The layout stage turns a bill of materials into buildable entities. It is
split along one line: **decisions** are made by whoever designs the layout --
Claude, through the skill in `skill/factorio-forge/SKILL.md` -- and
**expansion and checking** are done by code, from the active profile's
prototypes.

That split is deliberate. Closed generators (a program that decides
everything) exist and each hits its own ceiling: one layout form, one style,
or a constraint solver that stalls past a hundred tiles. A language model
placing every tile itself is the opposite failure: the Factorio Learning
Environment benchmark found models struggling to coordinate more than a few
machines. What works is what SpatialGrammar (2026) measured for room layouts:
the model writes a compact plan, a deterministic compiler expands it into
exact geometry, and compiler and checker feedback comes back to the model.

## Modules

| Module | What it answers |
|---|---|
| `layout.py` | How many machines one row keeps running, and what binds it: lanes, output lane, inserter throughput |
| `rows.py` | Where every machine, inserter, belt, pipe and pole of a block of rows goes |
| `fluids.py` | Which fluid a recipe puts in which fluid box; which pipes form one network |
| `plan.py` | A plan of blocks and hand-placed entities in, a blueprint and a report out |
| `route.py` | The belts and pipes between ports: a search that obeys the game's rules, or the reason it cannot |

## Capacity: lanes, not belts

- An inserter drops only onto the **far lane**. A row fills one lane of its
  output belt, shared or not.
- An inserter picks from both lanes, so a single ingredient has the whole
  belt; **two ingredients on one belt get a lane each**.
- A belt shared by two mirrored rows is split between them.
- Inserter throughput is `60 x rotation_speed x stack size` per second, the
  chest-to-chest ceiling; from a belt it is lower.

The first version of the layout module checked every flow against a whole
belt, which doubled the row for two-ingredient recipes (battery: 120 machines
instead of 60).

## Geometry is searched, not assumed

Mods change machine sizes, fluid box positions, inserter reach and direction,
pole supply areas and underground reach. `rows.py` reads every one of them
and searches:

- **inserters**: the rotation whose own `pickup_position` lands on the belt
  line and `insert_position` inside the machine (or the reverse, for output).
  The belt is placed exactly as far out as the pickup reaches;
- **machines**: the rotation that puts a connection of every fluid box the
  recipe fills on a long side of the row, and stacks tightest;
- **poles**: free tiles covering every electric entity, then extra poles
  until the network is one piece; gap columns between machines only when no
  free tile will do.

A prototype whose geometry does not fit this row form is refused with the
reason.

## The cross-section

```
outer   track      a pipe carrying one fluid along the row
        partner    pipe-to-ground surfacing from under the belts
        belts      each as far out as its inserter reaches
inner   arm line   inserters, pipe-to-ground dives, poles
        MACHINES
```

A side with no belts and one fluid lays its pipe straight along the arm line.
`mirror` stacking shares output belts, input belts, such a pipe, and the
outermost track between neighbours; two different fluids' pipes never touch.

Against the player's measured base: an item row comes out at 6 tiles per row
(measured: 6) and a fluid-only row of 3-tile plants at 4 (measured: 4). A row
with items and one fluid comes out at about 8 where the base has 6 -- how the
base fits the fluid in is still to be read from its blueprint.

## Fluid boxes

A recipe ingredient can name its box with `fluidbox_index`. When none does,
the engine spreads each fluid over the machine's input boxes in equal
consecutive groups (a Factorio developer, forums.factorio.com p=701829: four
boxes and two fluids -- the first two boxes get the first fluid). An uneven
split is refused rather than guessed. Results are assumed to follow the same
rule.

## Connecting blocks

A plan lists `connections`, each joining an out port (or a point) to an in
port (or a point). `route.py` finds the pieces; nothing is drawn by hand.

```json
"connections": [
  {"id": "feed", "from": {"block": 0, "port": 3, "items": ["<item>"]},
   "to": {"block": 1, "port": 0},
   "belt": "<belt>", "underground": "<underground belt>",
   "via": [[x, y]], "turn_cost": 1.0, "hop_cost": 2.0},
  {"kind": "pipe", "from": {"at": [x, y], "direction": 4, "items": ["<fluid>"], "rate": 20},
   "to": {"block": 1, "port": 2}, "pipe": "<pipe>", "pipe_to_ground": "<pipe-to-ground>"}
],
"routing": {"margin": 3, "max_nodes": 200000, "reserve": [[x0, y0, x1, y1]]}
```

- **Ports** are named by their index in the block's report (`[j]` in the
  build output). An optional `items` on a port reference guards against
  renumbering: the build refuses if the port carries something else.
- **Points** say where the first piece goes and which way things flow (as
  `from`), or where the last piece goes and which way it faces (as `to`; the
  tile past it is left open, and no piece of the route stands there). If
  something already stands on that tile, the route must continue it
  straight: a belt, underground entrance or splitter carrying the same way
  (not a curve, which the route would straighten into a side-load), or for a
  pipe a fluid connection facing back; anything else is `route-failed`. A
  belt point's `items` are `[left, right]`, one item for both lanes.
- **Prototypes** not named come from the blocks at either end: their belt,
  pipe and pipe-to-ground. The underground belt is the one the belt prototype
  names in `related_underground_belt`, else the only one of its speed;
  `false` forbids hops.
- `via` tiles are covered in order by surface pieces; `reserve` keeps tiles
  free for later (undergrounds may pass beneath); `margin` is how far past
  everything in the plan the search may go.

**Joins and lanes.** A route leaves an out port straight from its last tile
and enters an in port with a straight join into its first tile. A straight
belt, a curve fed by one plain belt and an underground pair all keep the left
lane on the left, and the route never curves right after an underground exit
(still to be confirmed in the game), so it delivers the source's lanes
unchanged. Lanes that do not match the destination are a problem; swapped
lanes are said to be swapped -- a route cannot swap them. A destination
naming one item for both lanes (a block's single-ingredient input) takes it
from either lane, so a source with that item on one lane -- a block's output
-- fits; `route-belt-slow` judges whether one lane is enough.

**Reach** is read per prototype: `max_distance` of an underground belt,
`max_underground_distance` of a pipe-to-ground's underground connection. It
is the largest difference, in tiles along the axis, between the two ends, so
at most reach - 1 tiles lie between them -- the same `range(1, reach + 1)` the
checker walks. Which way a pipe-to-ground faces at each end comes from the
direction of its underground connection, not from an assumption.

**Rules every piece obeys.**

- No piece on an occupied, reserved or out-of-area tile.
- A belt piece never stands where something already pushes items (a belt or
  underground exit pointing at the tile, a splitter, a loader, a drill's drop)
  or where an inserter takes or drops. Each piece points into the next free
  tile, so a route never side-loads into a foreign belt either.
- A pipe piece is refused if one of its connections meets a foreign
  connection pointing back with a category in common -- the rule
  `fluids.networks` joins pieces by. Only the first piece may join what stands
  behind the start, and only the last what stands where it points. A
  pipe-to-ground has no side connections, which is why it is the generic way
  past a pipe of another fluid.
- An underground hop may not pass an end of the same prototype on its line
  and axis, end inside an existing pair of that prototype, or come within
  reach of a lone end of it: any of these would pair with the wrong partner.
  Other prototypes and crossings at right angles are free.

**Order.** Every connection is resolved before any is routed, so a malformed
plan is refused whole. Routes then go one by one in plan order, each an
obstacle for the next; no route is ripped up for another. The one rip-up is
within a route: when `via` tiles make the cheapest path come back over its
own earlier tiles, those tiles are kept out of the later part and the search
runs again (a few times at most; otherwise `route-failed` says where the path
crossed itself). After routing, the
general checks run over everything, so `belts-head-on`,
`underground-unpaired`, `overlap` and `fluids-mixed` independently catch a
router mistake.

**Findings.** `route-failed` (nothing of that route is placed; the reason
names the blocked start, goal or via tile, the tile the search came closest
from and what blocked it, or the exhausted budget), `route-lanes`,
`route-belt-slow`, and notes `route-lanes-unknown`, `route-short-supply`,
`route-no-underground`, `route-underground-speed`, `route-tiles-unchecked`.
The report has a `routes` list with each route's pieces, length, turns,
underground pairs, lanes and reason.

**Out of scope** in v1: splitters, merges, one source to several
destinations, balancers, lane swaps, deliberate side-loading, joining a belt
mid-line; ripping up one route for another; map terrain (water, cliffs, ore, an existing
base -- `reserve` stands in); pipe throughput and pumps; pieces larger than one
tile; rails and robots. A port used twice is refused.

## Checks in a build

`factorio-forge build` runs the general checker (`docs/checking.md`) and
adds, for generated layouts:

- `fluids-mixed`: one pipe network joins two fluids, traced through every
  connection and underground pair from the prototypes;
- `fluid-box-unconnected`: a machine gets no pipe for a fluid it needs;
- `power-split`: the poles form more than one network.

## Verified

Every recipe shape in the Krastorio 2 profile (63 shapes: machine, solid and
fluid inputs and outputs) was built as a mirrored, a repeated and a rotated
block. With a medium pole: 138 builds with no finding, 15 refusals with a
stated reason (machine outruns its belt, more than four solid ingredients,
fluid connections only along the row, uneven fluid box split), and 6 builds
where the largest machines (9x9, 15x15) split the power network -- all clean
with a substation.

## Not modelled

- Inserter pickup from a moving belt (the rate is an upper bound).
- Pipe throughput over long runs.
- Modules and beacons as entities; `speed_bonus` only changes numbers.
- Splitting and merging, train stations, bus composition.
