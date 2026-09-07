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

Hovering an entity shows its name, size, position and whatever else it carries:
a recipe, a splitter's priorities, whether an underground belt is the entrance
or the exit, a chest's limit bar. Every entity also has a plain SVG `<title>`,
so the drawing still explains itself if the file is opened somewhere scripts do
not run.

Tiles — concrete and the like — are drawn underneath as ground.

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

Cell size is fixed at 26 pixels per tile. A city block of a hundred tiles a side
is therefore a 2600-pixel image, which a browser pans happily but which is past
the point where a single flat drawing is the right way to look at something.
Large layouts will want zooming, or drawing a region at a time; neither exists
yet.
