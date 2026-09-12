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
- Routing between blocks, train stations, bus composition.
