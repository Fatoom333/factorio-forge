# Checking a blueprint

[Русская версия](checking.ru.md)

```bash
factorio-forge check <blueprint string or file>
factorio-forge render <blueprint> --check -o layout.html
```

The first prints what looks wrong. The second draws the blueprint with the
findings marked where they are, because a check can say *what* and only the
picture can say *where*.

## Why this exists

A player's own blueprints are the best source of their style — the grid they
build on, the belt tier they use, how long their trains are. They are not a
source of correctness. A real blueprint can have a belt someone rotated by
accident, a wire never run, a filter set on an inserter that ignores filters.

Learning style from such a blueprint is the whole point of this project.
Learning its mistakes is not. So before any of that, there has to be a way to
tell the two apart.

## Three things, treated differently

| | What it is | What happens |
| --- | --- | --- |
| **Style** | grid size, belt tier, pole type, train length | copied — the blueprint is authoritative |
| **A defect** | a belt the wrong way, a missing wire, an idle inserter | reported, never learned as style |
| **Inefficiency** | unbalanced ratios, wasted space | left alone |

The third row is deliberate. Not everyone wants maximum optimisation; some
build for looks, some leave headroom, some simply do not care. Suspected
inefficiency is not something this reports, and production ratios are not
checked at all unless asked for.

## Nothing is ever changed

Every check reports. None of them edits. The line between a mistake and an
intentional oddity is usually not the tool's to draw, and a tool that quietly
"fixes" someone's layout is worse than one that points and asks.

## Three strengths, because crying wolf is fatal

A checker that fires on ordinary blueprints teaches its reader to skip the
output, and then the real findings go unread too. So findings are graded:

- **problem** — almost certainly wrong. An underground belt with no other end,
  an inserter with empty tiles on both sides, filters set on an inserter with
  filtering switched off, a combinator no wire reaches, two belts head on.
- **worth a look** — has honest reasons to be deliberate. A machine outside
  every pole's supply area, a pole out of wire reach of the rest, an assembler
  with no recipe, a constant combinator holding nothing.
- **note** — context, not criticism. An assembler without a recipe in a
  *parameterised* blueprint is a note, because that is exactly what
  parameterising means.

The test suite has as many tests for what must **not** be reported as for what
must.

## Edges

Most blueprints are fragments meant to join onto something else. A belt running
off the edge is normal; so is a machine with no power in a piece that carries
none, and an inserter at the boundary whose neighbour is in the next blueprint
along.

Every check that could otherwise fire on half of all blueprints is therefore
edge-aware: anything reaching past the boundary is presumed to meet whatever is
out there. Where silencing a check entirely would make it useless — a rail
signal, which sits at a blueprint's edge by its nature — the finding is softened
to *worth a look* instead of suppressed.

## What is checked

One idiom looks exactly like a mistake and is not. A blueprint meant to be
parameterised needs a free variable to exist before it can be turned into a
parameter, and the way to make one is a constant combinator holding a value
that appears nowhere else, wired to nothing. That is noted rather than
complained about; a constant combinator holding nothing at all is still
reported, by its own check.

Two entities are reported as overlapping only when the game itself would
refuse them: their collision masks must share a layer, and their real collision
boxes must intersect. Sharing a tile is neither necessary nor sufficient — a
rail signal is a fifth of a tile across, and a diagonal rail is a slanted shape
inside a square of four — and tile arithmetic reported thirty-one collisions on
a blueprint taken straight off a working map, where by definition there were
none. Rails are excluded from the check altogether: their true shapes are
curved, the geometry available here approximates each with a rectangle, and at
a junction those rectangles overlap while the rails do not.

Connectivity and geometry: underground belts and pipes without a matching end,
inserters reaching nothing or shuffling one container into itself, belts facing
each other, entities sharing a tile, rail signals not beside a rail.

### Inserters turned the wrong way round

An inserter's `direction` names the side it takes *from*: direction north takes
from the tile to its north and drops to the south. A row drawn by eye is easily
mirrored, and an inserter between two things looks fine to the idle/loop check
either way round. So `inserter-backwards` asks each side what it carries, using
the recipes of the machines on either side and what the lane tracer knows about
the belts (the pickup and drop tiles come from the inserter's reach, so a
long-handed one is judged the same way):

- **problem** — a machine's product is put on a belt that carries one of the
  machine's own ingredients while nothing takes from that belt into the machine
  (an input inserter turned about); two machines joined by an inserter where the
  product of the first is no ingredient of the second; an item taken from a
  belt into a machine that does not use it.
- **problem** (in a plan build, where the sources are declared) — a machine
  that no inserter brings an ingredient to, and one that takes from a belt
  nothing is put on.
- **worth a look** — a machine whose product nothing takes out, while its
  inserters all bring things to it from belts (an output inserter turned
  about); the mirror of that, a machine whose inserters all only take from
  it and none brings it anything (an input inserter turned about), when the
  sources are not declared; the same finding in a plan build when the machine
  is near the edge of the blueprint, where the supply may lie outside.

A belt that serves as input and output of the same machine is fine, even with
the output inserter upstream of the input one. With both inserters turned
about, only the input side is reported.

Nothing is said where the answer rests on what the picture does not hold: a
belt of unknown content, a machine without a recipe, a chest, a burner
machine that may be handed fuel, a machine beside a loader. A belt whose head
nobody declared is not called empty. A blueprint alone can be judged only
as far as it makes its own items; in a plan build the declared `sources` give
the belts their content.

Power: machines outside every supply area, poles isolated from the rest of the
network.

Settings that silently do nothing: filters stored on an entity that has
filtering off, a circuit or logistic condition on an entity no wire reaches, a
combinator with no wires at all, a constant combinator holding no signals, an
assembler with no recipe.

## Lanes (in a build)

`factorio-forge build` follows every belt lane of the build -- block belts,
hand-placed belts and routed pieces alike (`lanes.py`) -- and checks what
each lane carries against what takes from it. Left and right are seen in the
direction of travel. Not yet run by `factorio-forge check`.

**How a belt takes what is pushed into it** (Factorio 2.0):

| Receiver | from behind | head-on | from a side |
|---|---|---|---|
| transport belt | joins straight | dead end | see below |
| underground entrance | joins straight | into the hood: dead end | hood rule |
| underground exit | into the hood: dead end | dead end | not tracked |
| splitter half | joins that half | dead end | dead end |
| loader, linked belt | exit: takes anything | | |
| anything else (pole, machine, chest, inserter ...) | dead end | dead end | dead end |
| nothing | open end, except the tail of a block's input line | | |

- A transport belt fed from behind, or from both sides, is straight: each
  side feeder side-loads, both its lanes onto the near lane. Fed from one
  side only it is a curve and keeps the lanes, whatever feeds it: a plain
  belt, an underground exit or a splitter output (checked in the game).
- **Hood rule**: onto the side of an underground entrance only the feeder's
  lane on the entrance's open side gets through, onto the near lane; the
  other lane is a dead end (`lanes.hood_passes`, checked in the game).
- An underground pair keeps the lanes. A splitter sends each input's left
  lane to the left lane of both outputs, and the same for the right; filter
  and priority are not modelled (noted).
- Items come on from a declared start (a route's point, or the plan's
  `inputs`), from an inserter's drop (what the machine it takes from makes,
  through its filter; what it takes off another belt; else unknown), a
  drill's drop (unknown), at a belt head at the edge of the build or behind
  a loader (unknown), and at the head of a hand-placed belt anywhere that
  `inputs` does not name (unknown, and a port it reaches gets
  `lanes-unknown`). The head of a block's or a route's belt elsewhere carries
  nothing. What an inserter takes from a belt is the recipe of the machine
  it feeds, or anything for a chest, a lab or a furnace without a recipe.

**Open and closed ends.** A belt pointing into an empty tile is open: it may
go on in the world, as everywhere in the checker. Dead ends are closed (a
belt pointing into a pole, a machine or a chest is one), and
so is the tail of a block's input line, which is meant to end there. At a
closed end every item on the lane must be taken by something upstream on
its way there, or it fills the lane.

**The rules.**

- R1, at each closed end: an item nothing upstream takes is *stranded*.
  `lanes-not-taken` (problem) when it shares the lane with an item that is
  used -- the lane fills and the used one stops arriving; (suspect) when it
  is alone on the lane. `lanes-overflow` (note) instead when a splitter on
  the way sends it all to another output where it is taken.
  `lanes-mixed` (suspect) when two used items share the lane: unless they
  arrive in the ratio they are used, one blocks the other.
- R2, at each in port and each routed point goal that names items:
  `lanes-missing` (problem) when an ingredient never arrives;
  `lanes-wrong-item` (problem) naming every lane that brings an item the
  block does not use while an ingredient is missing; `lanes-nothing` when
  nothing at all reaches the port (problem if a connection ends there,
  suspect otherwise); `lanes-unknown` (suspect at a block's in port, note at
  a point) when it cannot be told because something undeclared comes on --
  declare it in the plan's `sources`; `lanes-rate` when the declared sources
  bring less than the port needs, totals compared (problem when the plan
  carries the order, note at full load); `lanes-swapped` (note) when a block's two ingredients arrive on
  each other's lanes -- its inserters take from both, so it runs as built.
  A point is ordered: `lanes-wrong` (problem) when its lanes do not match,
  saying so when they are only swapped. `route-lanes-disagree` (problem)
  when the router's belief about a route's lanes differs from the trace.
- R3: `lanes-starved` (problem) when machines whose inserters all take from
  belts carrying only known items never get an ingredient; not said again
  where a port already reports it missing.
- `lanes-unchecked` (note) for every spot past which lanes are not tracked.

**Silence.** Unknown content never makes a finding on its own. An in port
where nothing known arrives and no route ends is an unconnected fragment and
is not judged, and a machine whose belts carry nothing known is not called
starved.

## Nothing about an entity is hardcoded

Mods change these numbers, often by multiples, so every one of them is read from
the profile's own game data at the moment of checking. Under Krastorio 2 an
express underground reaches **twenty** tiles where vanilla gives it nine, and
the mod adds undergrounds reaching thirty and forty. An underground pipe spans
twenty rather than ten. A constant in the code would be right for vanilla and
badly wrong for the player.

Belts state their reach plainly; pipes bury the same idea in their fluid box, so
both places are read. Where neither yields an answer the check is **skipped**
rather than run on an assumed number: not knowing is a fact, not a licence to
guess.

An underground run's direction comes from the prototype too. A pipe to ground
shows its open end above ground and buries the run *behind* itself: the
prototype lists the two connections separately, the visible one at the entity's
direction and the underground one at the opposite. Assuming the run follows the
facing reported eighteen unpaired pipes in one real city block, every one of
which had its partner in the other direction.

An inserter's reach comes from the `pickup_position` and `drop_position` in the
blueprint when it carries them, and from the ones the entity reports otherwise.
A blueprint that states them is describing what the game wrote down, already
oriented; mods that let an inserter reach sideways or diagonally express
themselves entirely through those numbers. Neither comes from the direction. That avoids having to reason about which way
`direction` points — it points the opposite of the obvious way, at the side the
inserter takes *from* — and, more importantly, a mod can change the relationship
between direction and reach entirely. Bob's Inserters lets inserters pick up at
ninety degrees. Reading the reported positions keeps that correct for free; a
formula would not.

## The report says which data it used

The same blueprint legitimately gives different answers under different mod
sets. A fifteen-tile gap between express undergrounds is fine under Krastorio 2
and broken under vanilla, and both verdicts are right. So every report names the
profile it was produced against.

If the blueprint contains entities the loaded data has never heard of, that is
said **first and as a problem**, because it almost always means the wrong
profile is active — and every other finding in the report depends on that data,
so the rest of it cannot be trusted until it is fixed:

```
2 entity type(s) are missing from the game data in use
    Not found: kr-advanced-underground-belt, kr-superior-inserter.
    The data being read is space-ageeeeeeeee. Everything else in this report
    depends on that data, so activate the profile this blueprint belongs to
    and check again.
```

## Measuring what a build makes

Every check above reads the blueprint. The last one is to run it: the companion
mod's `/forge-measure` builds the blueprint on an empty surface of its own (with
the surface properties of the planet the plan is for), feeds every declared
source, empties every output belt end, runs for a warm-up and a measured
stretch, and writes `measure.json`. It is the check that finds what no reading
can: a ratio that starves a machine, an output that backs up, a supply the belt
cannot carry.

```
factorio-forge measure spec plan.json --surface vulcanus -o spec.json
tools/headless-run.ps1 -Save my-save.zip -Out out -Measure spec.json     (companion repo)
factorio-forge measure result out/measure.json --spec spec.json
```

`measure spec` builds the plan and writes the request: the blueprint, the sources,
the outputs and the plan's `targets`. A source is each entry of the plan's
`sources` and each connection whose `from` is a point with items and a rate
(`{"at", "direction", "items", "rate"}`): that is a supply starting there, fed as
`connection <id> start`. An output is each out belt port nobody routes from and
each route that really ends at a free point. A route that merges `onto` a belt is
not an output: the belt carries on, and emptying it there would starve what it
feeds. Empty lanes are never written as an item, and a connection whose lanes are
not one item each is left out with a note.
`--output-end X,Y[,ITEM[,DIR]]` adds an output end for a plan that places belts
by hand, `--fluid X,Y,FLUID[,RATE]` a fluid source, `--target ITEM=RATE` replaces
the plan's targets; `--warmup`, `--seconds` and `--speed` set the run (60 s, 120 s,
game speed 10). A source's `rate` is items per second over both lanes, as in the
plan; a source with no rate is fed as fast as the belt takes.

`measure result` prints each target ordered against measured, where measured is
what arrived at the output belt ends (or, with no output end, what the machines
made), then how each kind of machine spent the window, and why the starved ones
waited:

```
targets (ordered -> measured):
  iron-gear-wheel: ordered 0.5/s, measured 0.5/s at the output belts (100%), machines made 0.5/s [ok]
machines:
  1 x assembling-machine-1 (iron-gear-wheel): working 50%, waiting input 50%
starved: 1 x assembling-machine-1 (iron-gear-wheel) waited for input 50%: iron-plate missing 50% of the time
result: every target met
```

A target counts as met from 95% of the order. A machine is called starved when
a quarter of the window or more went on waiting for an ingredient, and blocked
when it went on a full output; any time without power is reported. The exit
code is 0 when every target is met and 1 otherwise. The run also reports
entities placed against expected, prototypes the game refused, a supply the
belt could not take (the rate actually fed against the declared one), and
machines left without a recipe (a recipe whose surface conditions fail on the
planet is dropped by the paste, as in a real game).

A short target does not always mean a faulty plan, and `measure result` tells
the two apart under the machines. If a machine of the last stage lacked an
ingredient that no measured machine made and no source fed in the whole window,
the note says so: an ingredient nothing makes or feeds is a missing source, and
one whose own machines made none was starved upstream; a longer warm-up changes
neither. If every ingredient arrived and the target is still short while above 0
(or its machines waited for input a quarter of the window or more), the line may
still be filling, and the note always advises a run with a warm-up at least twice
as long. Trust a figure when two windows agree: if the longer run measures the
same, it is the plan; if it climbs, it was filling. The note also gives a floor
for the fill time: the longest chain of recipe time over crafting speed from the
active data, plus the belt travel along the longest chain of routes (which
`measure spec` records in the spec as `belt_travel_s`). The floor counts one
craft per stage and no machine buffers, belts filling up or modules, so the real
fill takes longer (a 240 s warm-up was too short for a line whose floor was
72 s); it is an explanation, not a test.

What the numbers are: production and consumption are the force's production
statistics for the measuring surface; deliveries are counted at the sinks.
Power is a test source of one gigawatt, so power is never the limit; fluid
sources are infinity pipes, so a fluid supply is never the limit either. The
rates measured are those of a build fed exactly what the plan declares.

## Still to come

Circuit logic is not yet checked in any meaningful sense. A combinator with no
wires is caught, but whether a circuit does what it was built to do needs the
tick simulator, which is the next piece of this.
