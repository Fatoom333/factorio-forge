# Lessons that hold for any base

Read when: before handing a design over. Mistakes that already happened once;
check against them.

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
- **While iterating, look at a text tile map; render once, at the end** (map.md).
