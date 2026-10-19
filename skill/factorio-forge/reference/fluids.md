# Fluids and pipes

Read when: a recipe has a fluid, or a `fluids-mixed`, `fluid-box-unconnected`
or pipe-related refusal appears.

- `fluids-mixed` and `fluid-box-unconnected` are errors that must be fixed
  before handing anything over.
- Fluid-only rows share their pipe when mirrored (plans.md).
- The router lays pipes too: no pipe touching another fluid (it goes
  underground to squeeze past); pipe and pipe-to-ground default to the
  block's own (routes.md). A pipe may join pipes of its own fluid only.
- Branching: `"to": [port, port, ...]` on a pipe connection is one trunk to
  the first, a branch to each further in port (`chain` writes this for a fluid
  several stages use). A second source of the same fluid: `"onto": {"route":
  id}` with `"kind": "pipe"`. A touch with another fluid is still refused.
  The `route-pipe-flow` fact gives demand and run lengths; it does not judge
  what a long run carries.
- A machine that needs more than power (fuel, heat, nutrients) is not fed by
  rows: the review says so (request.md); the player supplies it, or another
  machine is pinned.
- Water is not in a blueprint or a region export (lessons.md).

| Message says | Do |
|---|---|
| `connections point into the neighbouring machine` | this machine's fluid boxes do not fit a flush row; another machine, or hand-build |
| `split is only known when it divides evenly` | the game's fluid-box assignment is uncertain here; confirm in game before building |

Not checked: what a long pipe run carries (only demand and run length are reported). Not yet
confirmed in game: an underground pipe at exactly its reach.
