# When the tools refuse

Read when: a command refuses or a finding names something you do not know how
to fix. Lane findings are in lanes.md, fluid ones in fluids.md, power ones in
power.md.

| Message says | Do |
|---|---|
| `already outruns its belt` | faster belt, or a slower machine |
| `needs input belt 2 ... choose a long_inserter` | add `long_inserter` |
| `N solid ingredients needing 3 input belt(s)` | this row form holds 4 lanes; split the recipe's supply differently or hand-build |
| `arm-line tiles and needs` / `no arm-line tile ... lets` | faster inserter, larger `stack_size`, or fewer machines per belt |
| `connections point into the neighbouring machine` | see fluids.md |
| `split is only known when it divides evenly` | see fluids.md |
| `pole network is in N pieces` | `power.link` in the plan, or a pole with a longer wire (power.md) |
| `lanes-*`, `block-short`, `route-lanes-disagree` | see lanes.md |
| `route-failed` | read the reason and the tile it names; steer as in routes.md |
