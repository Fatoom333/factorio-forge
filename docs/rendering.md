# Rendering

[Русская версия](rendering.ru.md)

Everything upstream of this is invisible. A blueprint is a list of names and
coordinates, and a mistake in one looks exactly like correctness: a belt facing
the wrong way, a machine overlapping its neighbour and an inserter reaching into
nothing all read as perfectly ordinary rows of JSON.

So before anything generates a blueprint, something has to draw one.

## Using it

```bash
factorio-forge render <blueprint string or file> -o layout.html
```

The argument is a blueprint string, or the path of a file containing one. The
result is a single HTML file: open it, or send it to someone.

A blueprint book is recognised and its contents listed, since a book is what
people usually have to hand:

```
That is a blueprint book holding 2 entries. Choose one with --index:
  --index 0   Belt run  [Blueprint, 4 entities]
  --index 1   Smelter   [Blueprint, 1 entity]
```

From Python:

```python
from factorio_forge import render
render.write_html(blueprint, "layout.html")
```

`render_svg()` and `render_html()` return strings if you would rather embed the
drawing somewhere else.

## What it draws, and what it deliberately does not

Entities appear at their **real footprint**, taken from the entity itself rather
than assumed, so an assembler is three tiles square and a splitter facing east
is one wide and two tall. Overlaps are therefore visible as overlaps, which is
most of the point.

Direction becomes an arrow. Factorio 2.0 counts direction in sixteenths of a
turn from north, which is the same convention SVG rotates in, so the arrow is
the direction with no translation to get wrong.

An inserter is the one entity that does not get that arrow. Its `direction`
names the side it picks up from, not the side it drops onto — the opposite of
what an arrow means on every belt around it — so drawing it the same way would
read backwards for exactly the entity where backwards matters most. Instead an
inserter draws the real pickup and drop tiles, read the same way the checker
reads them: a hollow ring where it reaches to take an item, a solid dot where
it puts it, joined by a thin line.

Colour is by family — belts, inserters, production, storage, fluids, power,
circuits, rails, military — grouped by what someone looks for when reading a
layout rather than by the game's own prototype taxonomy. A pump and a pipe
belong together here; the game considers them unrelated.

The family table has ten entries and is meant to: it is a hand-picked grouping
of what a reader looks for, not a catalogue of every prototype a mod set might
contain, and was never going to reproduce the game's own colours. A fast belt
is red in the game and, drawn purely by family, would come out the same
amber as every other belt — recognisably "a belt," but not recognisably
*that* belt.

So each entity is drawn, where possible, in **the colour read out of its own
icon** — the same PNG the game itself points the prototype at, decoded once
and reduced to the single colour a person would call its colour. A plain
pixel average is not that colour: most of an icon's area is a dark steel
frame or a drop shadow, and averaging a red belt's icon gives a muddy brown.
Weighting each pixel by how saturated and how bright it is before averaging
its hue instead recovers the accent colour — the belt's red, not its frame —
the way a favicon "dominant colour" picker would. The icon comes from
wherever the active profile says it lives: the game's own data directory for
`__base__` and the other packages it ships with, or the matching mod's zip
archive in the profile's mod folder otherwise. Nothing is looked up by name —
a mod's own tier gets its own real colour automatically, because the mod's
own icon is what is being read, not a name this tool would have to already
know.

When no icon resolves — a mod that supplies no icon, an inactive profile, a
file this reader cannot decode — each specific prototype instead gets its own
shade of the family's colour, nudged away from the family's base hue and
lightness by a hash of the prototype's own name. This keeps the same promise
the icon lookup makes (one particular prototype, one particular colour,
determined without a table of known tiers) with a weaker one attached: the
result is merely *distinct*, not *correct*. Two entities that share a
prototype always match either way, because the name is the only input; two
different prototypes in the same family almost always end up visibly apart,
and a hash-derived colour always stays close enough to the family's own hue
to still read as that family at a glance.

Colour alone still cannot tell two members of the same family apart with
certainty — two icons can coincidentally share an accent colour, and a hash
can coincidentally place two fallback colours close together — so two places
where that used to matter get their own treatment on top of it:

- **Rails are drawn as track, not as a filled block.** A rail's bounding box
  is usually bigger than the rail — a curve's box covers tiles the curve
  never touches — so filling it solid overstates the footprint and makes a
  chain of pieces read as an undifferentiated grey mass. Instead each piece
  draws a pair of rails and ties through its own footprint, oriented by its
  direction; a curved piece bends, a straight one does not, and `curved-rail-a`
  bends the opposite way from `curved-rail-b` so the two are not confused with
  each other either. This is a schematic, not the game's actual curve
  geometry — reproducing that exactly, per rail type and per one of sixteen
  directions, was not worth it for a diagram whose job is "track or not track,
  straight or bent." A rail signal draws as a circle and a chain signal as a
  diamond, since both used to sit on the same small grey square and were
  otherwise only told apart by hovering.
- **A buried connection — an underground belt, a pipe-to-ground — gets a
  diagonal hatch and a dashed outline**, and keeps its label even at one tile,
  below the size a label would normally get room for. In flat colour alone an
  underground belt is a transport-belt-coloured square with an arrow, which is
  also exactly what a transport belt is; the one thing that told them apart
  was the entity name in the hover tooltip. The hatch means the difference no
  longer needs a hover to see. Hovering either end of a paired run also draws
  a dashed line to the other end, found the same way the checker finds it —
  walking the direction the run leaves the entity, as far as the prototype
  states it can reach, looking for a same-named partner. An end with no
  partner simply gets no line, which is itself the same fact the checker
  reports as a finding.
- **The whole transport family — belts, undergrounds, splitters, loaders —
  keeps its label regardless of size**, for the same reason: a belt is one
  tile, well below the size a label would normally get room for, and a tier
  difference (a yellow belt next to a red one) is invisible in flat colour
  alone. The label is the entity's own name abbreviated, so different tiers
  read as different labels without a hover.

Wires — red and green circuit, copper power — are drawn as coloured lines
between the entities they join, read from the blueprint's own wire list
rather than inferred from adjacency. A wire this picture cannot resolve to two
drawn entities (a bare index a mismatched profile cannot make sense of, say)
is left out rather than guessed at.

Hovering an entity shows its name, size, position and everything it has been
configured to do: a recipe, a splitter's priority side, whether an underground
belt is the entrance or the exit, an inserter's item filters and whether they
are a whitelist or a blacklist, the signals and counts in a combinator or a
requester chest, a decider's conditions, an arithmetic operation and its output,
the circuit condition that enables it, a chest's limit bar. Configuration is
most of what a blueprint actually is, so leaving it out would make the picture
pretty and useless.

Every entity also has a plain SVG `<title>`, so the drawing still explains
itself if the file is opened somewhere scripts do not run.

Tiles — concrete and the like — are drawn underneath as ground.

## Grid snapping

A blueprint can declare the grid cell it occupies, and whether that cell is
pinned to world coordinates. This is what makes city blocks tile instead of
drift, and it is completely invisible in a picture of the entities alone.

The declared cell is drawn as a dashed outline, and **the view widens to hold
it**. That matters more than it sounds: a block is mostly the space it reserves,
so the cell is usually larger than what is in it, and drawing only the entities
would put the boundary off the edge of the picture — precisely the thing worth
looking at. A blueprint larger than its own cell gets the cell tiled across it,
so the overlap is visible, and the panel says so in words.

The panel also reports whether snapping is absolute or relative, the offset
within the cell, and whether the blueprint is double-grid aligned as rails
require.

One caveat: the cell is drawn on the reading that `position-relative-to-grid`
offsets the blueprint's top-left corner within the cell. The numbers in the
panel are read straight from the blueprint and are certainly right; the
*placement* of the outline rests on that reading, and is worth confirming
against a real city-block blueprint from a game.

## Parameters

Factorio 2.0 blueprints can declare parameters, filled in when the blueprint is
pasted. They are listed **in order and numbered**, because the order is not
decoration: a parameter's formula may only refer to parameters declared before
it, and the list order is the order the player is asked. A set would lose that;
a numbered sequence does not.

An id parameter shows what it currently holds and, where it derives from a
recipe, which parameter it is an ingredient of. A number parameter shows its
formula rather than its current value, since the formula is the interesting
part.

Parameters can arrive either as plain dictionaries, if something assigned them,
or as objects, if the blueprint came from a string; both are read, and the
game's hyphenated spellings are accepted alongside the Python ones.

### Where each parameter actually lands

A parameter list tells you what will be asked for. It does not tell you which
entities the answers reach, and that is usually what you want to check.

So the entities that refer to a parameter — in an inserter's filter, a
combinator's signal, anywhere the name `parameter-N` appears in a setting — are
ringed in the drawing, and the panel says how many entities each parameter
reaches. A parameter nothing refers to is reported as **unused**, which is
almost always a mistake and is otherwise completely silent.

A checkbox dims everything that is not parameterised, which turns a dense
blueprint into a map of just the parts that vary.

## Zoom and pan

The drawing zooms with the mouse wheel, pans by dragging, and there are buttons
including a *fit* that returns to the whole blueprint. Zooming moves the SVG
viewBox rather than scaling an image, so the drawing stays sharp at every
magnification instead of turning into blocks.

Browser zoom works too, and for the same reason: this is vector output, so
Ctrl+scroll enlarges it losslessly.

What does not exist is any level-of-detail handling. A city block a hundred
tiles across is drawn as a 2600-pixel image, every entity at full detail,
whether or not you are looking at all of it. That is fine to pan around and
wasteful to hold in memory, and if blueprints get much larger than a block it
will want addressing.

**There are no sprites.** Shipping the game's art would mean redistributing
Wube's work, and it would not help: while building a generator, what matters is
footprint, orientation and type, and those read better as flat colour than as
artwork. This is a diagram, not a screenshot.

## Self-contained on purpose

The page references nothing external — no scripts fetched, no fonts loaded, no
stylesheet to serve. That is checked by a test rather than left to good
intentions, because the moment one CDN link creeps in the file stops working
offline, inside a sandbox, and as an email attachment.

It follows the reader's light or dark preference, and lays out side by side or
stacked depending on the width available.

## An unfamiliar entity is drawn, not dropped

An entity whose prototype type is not in the family table is drawn in the
neutral "other" colour rather than skipped. A mod can invent a prototype type at
any time, and a blueprint that silently loses an entity is worse than one with a
grey box in it — the grey box at least says something is there.

## Scale

A tile is drawn at 26 pixels before any zooming. That is the natural size, not a
limit — see above.
