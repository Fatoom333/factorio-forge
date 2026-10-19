# Not checked yet

Read when: you are about to claim that a design "works", and want to know
what the tools do not cover. Say this to the player at hand-over.

- Inserter throughput is a chest-to-chest upper bound; from a belt it is
  lower.
- Pipe throughput over long runs is not modelled.
- Modules and beacons are not placed; `speed_bonus` only changes the numbers.
- Routing splits, merges and joins lanes, but places no balancers, priority
  or filter splitters, and swaps no lanes of a belt already laid; no rip-up
  and reroute across routes (they are greedy in plan order); no map terrain
  -- water, cliffs, ore and an existing base are not obstacles unless
  reserved. Pipes branch and merge for one fluid only; joining pipes inside blocks or hand-placed pipes is not done, and what a long run carries is not judged. No train stations.
- Lane rates are not modelled (lanes.md).
- Confirmed and unconfirmed in the game: lanes.md (underground hood rule,
  curves behind exits), fluids.md (underground pipe at its reach).
