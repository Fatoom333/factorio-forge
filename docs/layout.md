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
"routing": {"margin": 3, "max_nodes": 200000, "reserve": [[x0, y0, x1, y1]], "candidates": 12}
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

**Splits, merges and lane joins** are belt connections with more than two
ends:

```json
"connections": [
  {"id": "cable", "from": {"block": 0, "port": 1, "items": ["<item>"]},
   "to": [{"block": 1, "port": 0}, {"block": 2, "port": 1, "via": [[30, 12]]}],
   "split": [{"at": [14, 18], "side": "right"}], "splitter": "<splitter>"},
  {"id": "iron", "from": {"at": [19, -3], "direction": 8, "items": ["<item>"], "rate": 2},
   "onto": {"block": 1, "port": 0, "lane": "auto"}},
  {"id": "plastic", "from": {"at": [31, -3], "direction": 8, "items": ["<item>"]},
   "onto": {"route": "green-out", "lane": "left"}},
  {"id": "hand", "from": {"at": [5, 5], "direction": 4, "items": ["<item>"]},
   "onto": {"at": [9, 7], "lane": "right"}},
  {"id": "mix", "from": [{"block": 1, "port": 1}, {"at": [33, -2], "direction": 8, "items": ["<item>"]}],
   "to": {"block": 2, "port": 0}}
],
"inputs": [{"at": [19, -3], "items": ["<item>"]}]
```

- **Split**: `to` is a list of two or more ports or points, each with its own
  optional `via`. The route to the first is laid first (sub-route `<id>/0`);
  every further one (`<id>/1`, ...) branches off a splitter put in place of a
  belt of a route already laid. That belt must be a *straight piece*: a plain
  belt fed straight from behind (by the belt or underground exit before it,
  or the port or point it starts from) and carrying on straight, so neither
  the splitter's input nor either output makes a curve. Its other half and
  the tile past it must be free. The splitter is the only one of the belt's
  speed unless `splitter` names one; only two-tile splitters split in v1.
  `split` (optional, aligned with `to[1:]`) pins a branch to a tile of the
  first route and the side the other half goes on. All or nothing: if any
  destination fails, nothing of the connection is placed. With three or more
  destinations the splitters cascade (`route-split-cascade`).
- **Merge**: `onto` instead of `to` side-loads the source onto one lane of an
  earlier route (`{"route": id}`, a sub-route `id/k` too), of the route into
  an in port (`{"block": b, "port": j}`), or of a belt standing at a tile
  (`{"at": [x, y]}`, hand-placed or routed). The source's last piece points
  into the side of a straight piece past the route's last splitter, on the
  side of the chosen `lane` -- both of its lanes go onto that one. A target
  belt must be fed from behind: fed only from the side it is a curve, and the
  side-load would turn it (refused). `lane` is `left`, `right` or `auto` (the
  default; not for `at`): the lane the destination names the source's item
  on, else the free one; both taken everywhere is refused. A merge must come
  after the route it merges onto in the plan.
- **Hood rule**: onto an underground entrance (only through `onto.at`) only
  the source lane on the entrance's open side gets through
  (`lanes.hood_passes`: a feeder from the left passes its right lane, one
  from the right its left lane), onto the near lane; the other lane stops.
  A source whose items are all on the blocked lane is refused. Still to be
  confirmed in the game.
- **Lane join**: `from` is a list of two sources and `to` one destination.
  The first source is routed to the destination; the second points into the
  other side of one of its curves (`curve_pieces`: a turn fed by a plain
  belt), which makes the curve a T-junction, so each source fills the lane on
  the side it comes from. If the destination names the two items on
  different lanes, only turns that give that order are used. A path with no
  turn is refused: add a `via` tile that makes it turn.
- **Choosing a place**: every candidate place is estimated by distance, the
  nearest `routing.candidates` (12) are routed in full, and the cheapest
  wins (length, turns, hops). Ties go by plan order, then position: the same
  plan always builds the same blueprint.
- **Ports**: an out port may be a `from` once and an in port a `to` once; a
  port inside `onto` is not a use. Pipes do not split or merge: route to a
  point beside the pipe instead.

**Inputs.** A hand-placed belt head (nothing feeding it from behind) is
declared with `inputs`: `items` is `[left, right]`, one item for both lanes.
Without it, a head at the edge of the build carries something unknown.

**Lanes.** After routing, `lanes.py` traces every belt of the build -- block
belts, hand-placed belts and routes alike -- and is the one place lane
findings come from (`docs/checking.md`, "Lanes (in a build)"). Each in port
in the report gets `arrives` (what reaches its first tile, lane by lane) and
each route `delivered`. A route's own belief about its lanes is checked
against the trace: `route-lanes-disagree` means one of them is wrong about
the game.

**Planned rates.** Port rates and `crafts_per_second` are capacity at full
load. With `"request": "request.json"` in the plan (a path from the plan's
folder), `build` runs the request's bill of materials and gives each block
its share of its recipe's rate (in proportion to capacity, across blocks
with the same recipe); a block's own `"planned"` (crafts/s) wins. Each port
then has `planned_rate` next to `rate`, and per-item `item_rates`; routes are
sized by the planned flow of the items they actually carry. A block planned
above its capacity is `block-short`; a block whose recipe the bill does not
list gets a note.

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
crossed itself). Splits, merges and joins try their candidate places on a
copy of the grid and change nothing until they succeed. After routing, the
general checks run over everything, so `belts-head-on`,
`underground-unpaired`, `overlap` and `fluids-mixed` independently catch a
router mistake.

**Findings.** `route-failed` (nothing of that route is placed; the reason
names the blocked start, goal or via tile, the tile the search came closest
from and what blocked it, or the exhausted budget; a split's or join's
sub-routes all fail together), `route-lanes-disagree`, `route-belt-slow`
(also a merge that overfills one lane), and notes `route-short-supply`,
`route-split-cascade`, `route-no-underground`, `route-underground-speed`,
`route-tiles-unchecked`; from the build, `block-short`. Lane findings
(`lanes-*`) are listed in `docs/checking.md`. The report has a `routes` list
with each route's connection, pieces, length, turns, underground pairs,
lanes, junction, merges, delivered lanes and reason.

**Out of scope** in v1:

- splitter priority, filters and balancers -- the router never sets them; the
  lane tracker passes such a splitter on as unfiltered and notes it;
- swapping the lanes of a belt already laid -- the one lane choice is a lane
  join, which builds a new head;
- the router does not create underground entrances to filter lanes; an
  entrance can be a merge target only through `onto.at`;
- side-loading onto an underground exit and a curve fed by anything but a
  belt are not tracked; loaders, linked belts and drills are unknown sources
  or exits; an inserter dropping onto a curve is unknown;
- lane rates and ratio deadlocks beyond `lanes-mixed`; compression, which
  lane an inserter prefers and side-load priority;
- splitting or merging pipes; ripping up one route for another; running the
  lane tracker in `factorio-forge check` on any blueprint; bill lines with no
  block in the plan;
- map terrain (water, cliffs, ore, an existing base -- `reserve` stands in);
  pipe throughput and pumps; pieces larger than one tile besides a two-tile
  splitter; rails and robots.

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
- Train stations, bus composition.
