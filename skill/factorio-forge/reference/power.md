# Power, poles and beacons

Read when: a `power-split` or `pole network is in N pieces` finding appears,
machines are wide, or the player wants modules and beacons.

## Poles between blocks

Blocks laid apart share no wire. When the build says `power-split`, the
report's `power` lists each pole network and the gaps (closest poles,
distance, wire reach). Do not place bridging poles by hand: add `"power":
{"link": true}` (optionally `"pole"`) to the plan and read `power.added`; if
`power.link` reports a network it cannot reach, move a block or free a
corridor with `routing.reserve` (routes.md).

Machines 7 tiles and wider, or rows kept far apart by fluid tracks, need a
pole with a long wire (substation class). The report says when. Gap columns
for poles inside a block are said in the report's notes (which end columns
hold a pole, how many between machines, and why).

Poles built from a blueprint do not connect to neighbouring blueprints by
themselves: see grid-sections.md.

## Beacons and modules

Beacons are placed by hand for now. A beacon's effect is not linear in the
number of beacons on a machine: the prototype's `profile` array scales it
(1/sqrt(n) in the base game; a mod can change it). Its reach is the prototype's
`supply_area_distance`, measured from collision boxes, which are slightly
smaller than the tiles a machine occupies, so a gap of exactly that many whole
tiles is already out of reach (not yet confirmed in game). "Researched" in the
export is not "produced": ask which module tier the player actually makes.

Modules and beacons are not placed by the tools; `speed_bonus` only changes
the numbers.
