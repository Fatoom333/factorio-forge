# Connecting blocks: connections, splits, merges, joins

Read when: the blocks are placed and belts or pipes must run between them
(step 6), or a `route-failed` / `lanes-*` finding names a connection.

Write `connections` from the port indices the build report prints (`[j]`
under each block): `{"from": {"block": 0, "port": 3, "items": [...]}, "to":
{"block": 1, "port": 0}}`. A point (`{"at": [x, y], "direction": d, "items":
[...], "rate": r}`) stands for a blueprint edge or anything outside the plan.
Put `items` on port references: if a block change renumbers its ports, the
build refuses instead of joining the wrong ones. Belt, underground,
pipe and pipe-to-ground default to the blocks' own; name them to override,
`"underground": false` to stay on the surface.

The router finds the pieces and obeys what the game would: no side-loading,
nothing in an inserter's reach, no pipe touching another fluid (it goes
underground to squeeze past), no hop that steals another pair. Underground
reach comes from the active profile's prototypes, so mods that change it are
followed. It joins ports straight and keeps lanes as the source has them.
Routes go in plan order and each blocks the next, so put the hardest
connection first. On `route-failed`, read the reason and the tile
it names, then steer: a `via` tile, a larger `routing.margin`, a moved or
rotated block, a different order, undergrounds allowed. Keep doors and
future corridors free with `routing.reserve`.

Blocks laid apart share no wire: see power.md for `power-split` and
`power.link`.

Three shapes go beyond one port to one port (belts only; details in
docs/layout.md):

- **Split** -- one source, several destinations: `"to": [{"block": 1, "port":
  0}, {"block": 2, "port": 1}]`. A splitter goes in place of a straight belt
  of the first route for each extra destination; sub-routes are `<id>/0`,
  `<id>/1`. Pin it with `"split": [{"at": [x, y], "side": "right"}]` if the
  place matters. All or nothing.
- **Merge** -- a second item side-loaded onto one lane: `{"id": "iron",
  "from": {"at": [x, y], "direction": 8, "items": ["iron-plate"]}, "onto":
  {"block": 1, "port": 0}}` (the route into that port), or `"onto": {"route":
  "cable/0"}`, or `"onto": {"at": [x, y], "lane": "left"}` for a hand-placed
  belt. `lane` defaults to `auto`: the lane the port names the item on, else
  the free one. List it after the route it merges onto.
- **Lane join** -- two sources a lane each into one destination: `"from":
  [source A, source B], "to": ...`. A joins from its side at a turn of A's
  route, so the path needs a turn (add a `via` if it runs straight).

Typical two-ingredient block: route the main ingredient into the port, merge
the second onto it. If the second lands on the "wrong" lane the build notes
`lanes-swapped` -- harmless, the inserters take from both lanes.

## Hand-placed entities

Hand-place in `entities` only balancers, splitters with a priority or filter,
and lane swaps of belts already laid. Declare what enters a hand-placed belt
head with `"inputs": [{"at": [x, y], "items": ["<left>", "<right>"]}]` so its
lanes are checked like the router's; the same checks run over everything.
An undeclared hand-placed head carries something unknown, so the ports it
reaches only get `lanes-unknown`. A belt that ends against a pole, a machine
or a chest is a dead end: what nothing takes before it fills the lane.

Lane findings and what to do about them: lanes.md.
