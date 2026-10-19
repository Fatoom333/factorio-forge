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
| `chain.py` | A draft plan for every stage of an order: blocks in flow order, every connection, every source |

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

- **Ids**: `id` names a connection (else `c<index>`); each is used once, and
  none has a `/`, which names a split's or a lane join's sub-routes
  (`<id>/0`, `<id>/1`). A plan with an id twice is refused.
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
"inputs": [{"at": [19, -3], "items": ["<item>"]}],
"sources": [
  {"id": "iron", "at": [19, -3], "direction": 4, "items": ["<item>"], "rate": 7.5},
  {"block": 2, "port": 1, "items": ["<item>", "<item>"], "rate": 3}
],
"targets": [{"item": "<item>", "per_second": 1}]
```

- **Split**: `to` is a list of two or more ports or points, each with its own
  optional `via`. The route to the first is laid first (sub-route `<id>/0`);
  every further one (`<id>/1`, ...) branches off a splitter put in place of a
  belt of a route already laid. That belt must be a *straight piece*: a plain
  belt fed straight from behind (by the belt or underground exit before it,
  or the port or point it starts from), so the splitter's input makes no
  curve; a curve right after it is allowed (a curve fed by a splitter output
  keeps both lanes, checked in the game). Its other half and
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
  A source whose items are all on the blocked lane is refused. Checked in
  the game.
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

**Inputs.** A hand-placed belt head (nothing feeding it from behind or from
a side) is declared with `inputs`: `items` is `[left, right]`, one item for
both lanes. An input on a tile fed from behind or from a side is refused.
Without it, the head of a hand-placed belt carries something unknown,
wherever it is, and a port it reaches gets `lanes-unknown`.

**Sources.** `sources` says what the player feeds in from outside, and is
read like `connections`: each entry may have an `id` (unique; a default
`source N` names it in messages), and is either a belt head -- `at` and the
`direction` the belt there flows -- or a block's in port -- `block` and
`port` (the port's own direction is used; a `direction` that disagrees is
refused). `items` is `[left, right]`, one item for both lanes; `rate`
(items/s, both lanes) is optional. A source on a tile with no belt, with the
wrong direction, fed from behind or from a side, twice on one tile, at an out
port, at a port a connection ends at, or on the tile a connection's point starts
at (give the items and rate on the connection's `from`) is refused. A `rate` must
be a finite number of at least 0. The tracer treats a
source as known supply (`inputs` is the same without the rest); a connection's
point start with a `rate` does too. A belt start nobody declared stays `?`,
and a block in port it reaches gets `lanes-unknown` (suspect), naming the port
and saying to declare it in `sources`. That holds in a plan that declares
connections or sources, whether the port reads `? | ?` or `? | item`, and also
when every wanted item arrives but something unknown rides the lane too (it
can clog it). A lone block with no connections or sources is a fragment.

What the trace finds against a block in port is raised as findings
(`docs/checking.md`, "Lanes (in a build)"):

- `lanes-missing` (problem): an ingredient never arrives. With it, every lane
  that carries something the block does not use gets `lanes-wrong-item`
  (problem), naming the lane and the item; which lane it is does not matter
  at a block port.
- `lanes-swapped` (note): both ingredients arrive, on the other lanes. An
  inserter takes from both lanes, so for a block it does not matter. A
  point's lanes are ordered: there it is `lanes-wrong` (problem).
- `lanes-nothing`: nothing arrives at all (problem where a connection ends
  there; suspect for a port nothing reaches in a plan that declares
  connections or sources -- without either, a lone block is a fragment and
  is not reported).
- `lanes-rate`: the supply declared behind the port is below what it takes.
  The rate is known only when every belt behind the port starts at a
  declared source or point with a `rate` and no splitter is in between;
  otherwise it is not compared. The comparison is of totals: not per item, and
  not shared between several ports on one belt. A port a connection ends at is
  left to `route-short-supply`. Below the ordered rate (`planned_rate`) it is
  a problem; below full load only (no order), a note.

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
with the same recipe); a block's own `"planned"` (crafts/s) is its share,
and the rest of the recipe's rate goes to the other blocks. Each port
then has `planned_rate` next to `rate`, and per-item `item_rates`; routes are
sized by the planned flow of the items they actually carry. A block planned
above its capacity is `block-short`; a block whose recipe the bill does not
list gets a note. A plan with no request file can say what is ordered itself,
`"targets": [{"item": "electronic-circuit", "per_second": 1}]` (or
`per_minute`), and gets the same bill and the same `planned_rate`. With
neither, the report keeps only the rates at full load. A plan names its order
once: `request` together with `targets` is refused. Next to `targets` the plan
takes the request's own `recipe_choices`, `machine_choices`, `tiers`, `effects`, `surface` and `boundary`; without
`recipe_choices` a target that several recipes make is planned with whichever the bill picks, and
the blocks of the recipe actually built are "not in the bill".

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
- side-loading onto an underground exit is not tracked (not tried in the
  game); a belt fed from one side only by an underground exit or a splitter
  output is a curve and keeps its lanes (checked in the game); a fast-tier
  turn after one straight belt behind an exit is not tried in the game;
  a curve fed by anything else is not tracked; loaders, linked belts and drills are unknown sources
  or exits; an inserter dropping onto a curve is unknown;
- lane rates and ratio deadlocks beyond `lanes-mixed`; compression, which
  lane an inserter prefers and side-load priority;
- splitting or merging pipes; ripping up one route for another; running the
  lane tracker in `factorio-forge check` on any blueprint; bill lines with no
  block in the plan;
- map terrain (water, cliffs, ore, an existing base -- `reserve` stands in);
  pipe throughput and pumps; pieces larger than one tile besides a two-tile
  splitter; rails and robots.

## Power between blocks

Blocks carry their own poles; blocks laid apart share no wire. The report's
`power` lists every pole network: its poles (centre positions, the block each
stands in, whether the tool added it), the machines it powers per block and per
route (belts and pipes need no power, so a route shows only if it holds a
powered piece), and for separate networks the `gaps`: the closest pair of poles
between them in a spanning order, their distance, the wire reach (the shorter
of the two poles' `maximum_wire_distance`) and how far short it falls.

```json
"power": {"link": true, "pole": "<pole>"}
```

With `link`, the tool adds the fewest poles it can that join every network, on
free tiles: not under a machine, belt or route piece, not on a `reserve`d tile,
not where an inserter picks up or drops, not on a pipe connection and not on
the tile in front of a port, nor on the tile of a source or of a connection's `at` point and the
tiles before and after it along its direction. It grows the largest network by the
shortest chain of free tiles to any other (a search over tile positions, the
wire rule being the one the blueprint's wires follow), so a gap blocked by a
route or a reserved area gets a pole on each side of it, or a chain round it.
Bridge poles are chosen by wire reach alone; the supply area only decides what each
network powers in the report. `pole` defaults to the pole the build uses most; the added poles are listed in
`power.added`. If a network cannot be reached, nothing is added for it and
`power.link.failures` and the `power-split` finding say which one and why (no
free chain within the wire reach inside the search area -- the build's extent
plus `routing.margin`, at least four tiles). Poles are wired by the same
generator as the rest, which holds a pole to five wires; if that leaves the
networks apart the report says so.

## A whole chain from an order

`factorio-forge chain` turns an order into a draft plan for every stage in one
command, then builds it once:

```
factorio-forge chain advanced-circuit=1 --boundary iron-plate,copper-plate,plastic-bar -o plan.json
factorio-forge chain request.json -o plan.json        # a request file, as for `review`
```

`item=rate` is per second; `item=60/m` is per minute. With a request file the
tiers, machines, recipes and boundary come from it; with words they come from
`review`'s defaults (the best the player has unlocked, else a fast belt,
fast inserters and a medium pole, said as an assumption). Beside the plan it
writes the blueprint, report and drawing, as `build` does, and lists what it
left to the player.

What the draft contains:

- **One block per recipe** of the bill of materials. A stage gets as few rows
  as keep its machines fed; several rows are mirrored, so its input ports
  repeat. Blocks are named by index in the bill's order.
- **Columns in flow order.** A stage stands as late as it can: stages that
  take only raw goods at the left edge, the ordered product at the right,
  each stage in the column before its first user. Within a column, a block is
  lined up with the port it feeds (a straight belt); where a block's feeders
  compete, they are spread in the order of the ports they feed, so the feeder
  of the top port stands highest and no two belts cross.
- **Shared stages.** An intermediate the bill counts once for several users
  stays one block. Its out port is one connection with a list of
  destinations, which the router splits with one splitter per branch.
- **Ports with two goods.** The good wanted at more ports is routed to the
  port; the other joins it: a raw good as a belt head beside the port
  (`onto`, lane `auto`), a product from its own out port (`onto`). If the
  routed belt fills both lanes, the two are joined a lane each instead, with a
  `via` tile off the straight line so the belt has the turn a join needs.
- **Raw goods.** A port that takes only raw goods is a `sources` entry at the
  port, with the rate the bill needs there. Every other raw good starts a belt
  head beside its port, as a connection with a `rate`. `targets` and the
  request's choices go into the plan, and `power` has `link` on.
- **Fluids.** Pipe connections from the out port of the stage that makes the
  fluid. A pipe cannot split, so a fluid used by several stages is piped to
  the first and the rest is listed. A fluid nobody in the chain makes is
  listed too.

**Mending, at most twice.** If the build has problems at the end of some
connection, each such block gets two other placements, one after the other:
more room (moved right, its feeders twice as far apart, its raw belts start
further out), then the other side (its feeders swapped above and below, join
`via` on the other side). The first clean build stops it; of a set of
failed ones the one with the fewest problems is kept, the earliest on a tie.
A connection the plan check refuses outright is left out and listed, so the
rest of the chain still builds.

**Left to the player**, as `left to you:` lines: a raw fluid, a fluid piped to
more than one stage, a good that has to reach two ports in different ways
(one out port starts one connection), a port that takes three or more goods
on one belt, a stage whose row holds more machines than its belt keeps fed.
A draft with a problem left is not wrong output: it is the starting point of
the next edit, and the build's own findings say where.

## Library blocks

A profile can keep blocks of its own in a `library/` folder next to its notes,
one `<name>.json` per block (a lowercase name of letters, digits and dashes; a
name is never read as a path). An entry holds the blueprint of the block alone,
its size, its ports (`kind`, `dir`, `at` relative to the block's top-left tile,
`direction`, `items`, `rate`) and its `shape`: the machine, the recipe, how many
solid and fluid goods go in and out, and the tiers it was built with.

A plan block pastes one:

```json
{"library": "gears", "at": [0, 0], "rotate": 90, "mirror": true, "id": "gears", "recipe": "other-recipe"}
```

The block is mirrored left to right first, then turned clockwise, then put with
its corner at `at`. Its entities are copied one by one (circuit wires between
them are not kept) and go through the same checks as every other entity. Its
ports are numbered as in the entry and transformed with it: a turn adds to
every direction, a mirror reflects east and west and swaps the two lanes of a
belt port; so `connections` and `sources` use them exactly as they use the
ports of a rows block. `recipe` swaps the recipe of the block's machines when
the new recipe takes and makes the same number of solid and fluid goods and the
machine can craft it; the goods on the ports follow by position and the port
rates are scaled by how much the new goods flow. An unknown name, a bad entry
or a recipe that does not fit is a one-line plan error.

`chain` looks in the library before it lays out a rows block for a stage: the
same machine, the same number of goods in and out, tiers that agree with the
stage's, and at least as many machines as the stage needs. A block that already
makes the stage's recipe goes first, then one whose recipe can be swapped; the
smallest big-enough block wins. A stage that has a speed bonus keeps its rows.
The output says `stage N: library block NAME` (with `recipe swapped (A -> B)`
when it was), and why a block of the right shape was passed over. Only the
block is reused; its links to the neighbours are routed fresh like any other.

## Small edits, and a rebuild that keeps what did not change

A block may carry an `id` (a text without spaces, not a number, unique in the
plan). Connections and sources may name a block by that id as well as by index:
`{"block": "red", "port": 1}`. Ids are turned into indices before anything else
reads the plan, so a plan without ids is read as before.

`factorio-forge edit plan.json <operation> [<operation> ...]` changes the plan
file (or a copy, with `-o`) and builds it again. An operation is a verb and a few
words, and a block is its index or its id:

| Operation | Does |
|---|---|
| `move <block> <dx> <dy>` | shifts the block by whole tiles |
| `set <block> <field>=<value>` | sets `recipe`, `machine`, `belt`, `inserter`, `long_inserter`, `pole`, `pipe`, `pipe_to_ground`, `stack`, `align`, `speed_bonus`, `stack_size`, `input_belts`, `planned` or `rotate` (degrees: 0, 90, 180, 270); `<field>=none` clears an optional one |
| `rows <block> +1` / `-1` / `per=<n>` | one row more (it copies the last) or fewer, or `n` machines in every row |
| `remove <block>` | drops the block and every connection and source that names it, and every merge left without its route; indices after it are renumbered |

Several operations may follow one another: `edit plan.json move red 0 5 set red
recipe=advanced-circuit`. An unknown block, field or verb is one line on the error
output and nothing is written. `edit` does not touch what the plan holds as tiles
(`via`, `at` points, `entities`, `reserve` rectangles): moving a block does not
move them. The plan is built before it is written: if the edited plan no longer builds -- a
connection that expected the old recipe's items, say, or a recipe name that does not
exist -- the build error is printed, the file on disk stays as it was, and with `-o`
into another folder a relative `request` is rewritten to still find its file. The
drawing is written as `build` writes it (`--no-render` skips it). The plan is written back as indented JSON, so hand formatting is lost. `edit plan.json` with no operation only rebuilds. `speed_bonus` is the
nearest thing to modules a block has; there is no `modules=` field yet.

The rebuild keeps a cache in `.<plan name>.cache/` beside the plan, as one file
(never in the repository; `--no-cache` skips it). Two kinds of entry:

- **a block's expansion**, under a hash of the block's own definition (`id` and
  `planned` left out) and of what the expansion reads: the package's source, the
  active profile and the exported research. Moving or changing one block
  expands that block only.
- **a route**, under its connection, the routing options and, for a merge, the
  route it merges onto. The search is watched while it runs: every tile of the
  grid it looked at (occupied, reserved, fed, in reach of an inserter, fluid
  connections, belt entries) is recorded with what it held. A cached route is
  read back only if every one of those tiles holds the same now, the same
  underground pairs exist, and the tiles the search treated as outside the search
  area are still outside it; the cached grid changes and the merges it made are
  applied as the search would have made them. A route that touches an edited block
  or crosses its area saw a different tile and is searched again. A search that
  walks a whole table rather than asking about tiles is not kept at all.

The result is the same blueprint and report as a build without the cache (the
tests build random edit sequences both ways and compare). Entries a build did not
use are deleted, so the cache holds the last build. Cache files are read back with
a restricted loader that accepts only this package's data classes, so a
cache folder from another machine cannot run code.

Placing the entities in the blueprint and the checks that read the whole of it
(inspection, lane tracing, power) run on every build; the saving is in the
expansion and the route searches.

## Checks in a build

`factorio-forge build` runs the general checker (`docs/checking.md`) and
adds, for generated layouts:

- `fluids-mixed`: one pipe network joins two fluids, traced through every
  connection and underground pair from the prototypes;
- `fluid-box-unconnected`: a machine gets no pipe for a fluid it needs;
- `power-split`: the poles form more than one network; the finding names the closest
  poles between them and the `power.link` option joins them.

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
