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
  side only by a plain belt it is a curve and keeps the lanes; fed from one
  side only by an underground exit or a splitter it is not tracked.
- **Hood rule**: onto the side of an underground entrance only the feeder's
  lane on the entrance's open side gets through, onto the near lane; the
  other lane is a dead end (`lanes.hood_passes`, still to be confirmed in
  the game).
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
  `lanes-unknown` (note) when it cannot be told because something undeclared
  comes on; `lanes-swapped` (note) when a block's two ingredients arrive on
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

## Still to come

Circuit logic is not yet checked in any meaningful sense. A combinator with no
wires is caught, but whether a circuit does what it was built to do needs the
tick simulator, which is the next piece of this.
