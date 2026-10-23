"""A whole chain in one strip: two rows of machines facing a shared corridor of belts.

A row block (`rows.py`) holds one recipe. A strip holds several: each stage of
a chain is a *segment* of machines in the top row or the bottom row, the rows
face each other across a corridor of belt lines, and every good travels along
the corridor from the segment that makes it to the segments that use it. Raw
goods enter at the left edge, the product and any surplus leave at the right
edge. Nothing between the stages is routed: the corridor is the connection.

    outer    fluid tracks of the top row (pipes, pipe-to-ground)
    TOP ROW  segments, flush with the corridor
             arm line (inserters, poles)
    corridor belt lines 1..k, running left to right
             arm line
    BOTTOM   segments, flush with the corridor
    outer    fluid tracks of the bottom row

## What decides where

- Order: a producer's segment starts left of every consumer's segment; the
  product's segment is rightmost.
- Reach: which corridor lines a row can take from or drop on follows from the
  inserters' own pickup and insert positions (`rows.fit_arm`), never assumed.
  With a near and a long inserter and two lines, both rows reach both lines.
- Lanes: an inserter drops onto the lane further from it, so a top-row segment
  drops on the right lane (seen along travel, i.e. the south side of an
  eastbound belt) and a bottom-row segment on the left lane. Raw goods may
  start on any lane at the left edge.
- Reuse: each good occupies a lane over an interval of x, from where it is put
  on (0 for raw goods) to its last consumer (the right edge for the product and
  surplus). A lane carries two goods only if their intervals are separated by a
  cut: the belt line ends after the first good's last consumer and a new belt
  starts one tile later, so leftovers never run into the next good.
- Rate: a good needing more than one lane's throughput takes both lanes of a
  line, or two lines; a segment never holds more machines than its lanes feed.
- Fluids: a fluid a stage makes is used by segments in the same row (its outer
  track); raw fluids enter at the left edge on the outer track of the row that
  uses them. Two different fluids never touch: a track is cut between them as
  belts are, and a second track is added only when one is not enough.
- Heights: machines of different heights in one row stand flush with the
  corridor; the outer side follows the tallest, and a shorter machine reaches
  the track straight out from its fluid box.

`build_strip` tries the placements (row of each segment, order inside the
order constraint) and keeps the narrowest strip that satisfies all of the
above. When none does, it raises `StripError` naming the good, lane or fluid
that could not be placed, so the caller can split the chain or fall back to a
block per stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .layout import LayoutError
from .rows import Placement, Port


class StripError(LayoutError):
    """The stages do not fit one strip; the message names what did not fit and why."""


@dataclass
class StripStage:
    recipe: str
    machine: str
    machines: int
    speed_bonus: float = 0.0


@dataclass
class StripSpec:
    stages: list[StripStage]
    belt: str | None = None
    inserter: str | None = None  # the near arm
    long_inserter: str | None = None  # the far arm
    pole: str | None = None
    pipe: str | None = None
    pipe_to_ground: str | None = None
    lines: int | None = None  # corridor belt lines; None: as few as fit
    stack_size: int | None = None  # None: each inserter's hand from the player's research
    outputs: list[str] = field(default_factory=list)  # goods that must leave at the right edge besides the last stage's


@dataclass
class Segment:
    stage: int  # index into StripSpec.stages
    row: str  # "top" or "bottom"
    x: int  # left tile of the first machine
    width: int
    machines: int
    capacity: int  # machines its lanes keep fed (0: nothing limits it)


@dataclass
class LaneUse:
    good: str
    line: int  # corridor line, 1 = nearest the top row
    lane: str  # "left" or "right" along travel
    start: int  # x where the good is put on
    end: int  # x of its last taker (the strip width for what leaves)
    rate: float


@dataclass
class Strip:
    spec: StripSpec
    placements: list[Placement]  # relative to the strip's top-left corner
    ports: list[Port]  # left-edge inputs and right-edge outputs only
    width: int
    height: int
    segments: list[Segment]
    lanes: list[LaneUse]
    notes: list[str] = field(default_factory=list)


def build_strip(spec: StripSpec) -> Strip:
    """Every entity of the strip, from the prototypes of the active profile, or `StripError`."""
    raise NotImplementedError
