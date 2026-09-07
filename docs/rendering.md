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

Colour is by family — belts, inserters, production, storage, fluids, power,
circuits, rails, military — grouped by what someone looks for when reading a
layout rather than by the game's own prototype taxonomy. A pump and a pipe
belong together here; the game considers them unrelated.

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
