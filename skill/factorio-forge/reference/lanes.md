# Lanes: what rides which lane

Read when: a `lanes-*` or `block-short` or `route-lanes-disagree` finding
appears, or a block takes two ingredients on one belt.

A belt has two lanes (left, right in the direction of travel). An inserter
drops on one lane; inserters take from both. Ports list their lane contents,
and the tracker follows them through every route, split and merge, and over
hand-placed belts whose heads you declared (routes.md, "Hand-placed
entities").

| Finding | Do |
|---|---|
| `lanes-missing`: an ingredient never arrives | merge it onto the route into that port, or declare what the player feeds there in the plan's `sources` |
| `lanes-wrong-item`: a lane brings an item the block does not use | the wrong belt reaches this port; fix the connection or the source |
| `lanes-nothing`: nothing reaches an in port | connect it, or declare the outside supply in `sources` |
| `lanes-unknown` (at a block's in port) | something undeclared rides the lane: declare it in `sources`, then the port can be checked |
| `lanes-rate`: declared sources bring less than the port needs | raise the source, or add a belt; totals are compared, not each item |
| `lanes-not-taken`: nothing takes an item on a lane | a wrong item, or the wrong port; an error when it shares the lane with a used item (it will block it) |
| `lanes-mixed`: two used items on one lane | give each its own lane (merge onto the other lane, or a lane join) |
| `lanes-starved`: machines never get an ingredient | something upstream is missing it; follow the belts their inserters take from |
| `lanes-swapped` | nothing to do: inserters take from both lanes |
| `block-short`: the request needs more than the block makes | more machines or rows, or a faster machine |
| `route-lanes-disagree` | the router and the lane tracker differ about the game; report it, check that spot in game |

Not modelled: lane rates. `lanes-mixed` warns of a shared lane, but whether
the ratio deadlocks, belt compression and which lane an inserter prefers are
not computed. Checked in game: an underground belt pairing at exactly its
reach, the underground hood rule (`lanes.hood_passes`), and that a belt fed from one side only by an
underground exit or a splitter output is a curve keeping both lanes. Not yet
confirmed: side-loading onto an underground exit (reported as not tracked)
and a fast-tier turn after one straight belt behind an exit.
