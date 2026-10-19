# Factory buildings (Factorissimo)

Read when: the mod set has Factorissimo 3 (`factory-1/2/3`) and a design goes
inside a building.

A design inside a building is several blueprints: the exterior (it carries
only `tags.id`, not the floor), the floor inside, and the floor of each nested
building. Hand them over separately with the order to place them.

- Ports sit at fixed positions that depend on the building's size and quality;
  read the mod's own layout code (`script/layout.lua` in its zip) for the
  coordinates and counts instead of guessing. A belt port carries one full
  belt, and the belt on it must face in (input) or out (output).
- Everything that enters or leaves a nested design crosses the outermost
  building's ports, so nesting adds room, not throughput.
- The floor is powered everywhere; the exterior needs a pole whose area
  touches the building (the checker does not flag it).
- Keep the door (middle of the south wall) clear; cross it underground.
