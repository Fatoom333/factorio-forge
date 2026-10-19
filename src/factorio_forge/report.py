"""The report as the player reads it: errors, then facts.

Inside, checks grade what they find three ways (`Severity`: problem, suspect,
note). That grading is kept, because the checks and their tests use it. The
reader gets two levels instead:

* **error** -- the build will not be placed, or will not work: a collision, an
  input nothing feeds, the wrong item on a lane, a supply that falls short, a
  power network in pieces. If it is in doubt whether something breaks the build,
  it is an error, and its text says what to check.
* **fact** -- what the build is: rates, belt load, which items ride which lane,
  how many machines and entities, and what was assumed along the way. Facts
  carry no verdict; the reader decides what they mean.

Nothing in between: a "warning" that may or may not matter is the thing this
replaces.

`RULES` maps every finding code the program can produce. A test reads the
source for codes and fails when one has no rule, so a new check cannot ship
without saying which side it falls on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .inspection import Finding, Severity

ERROR = "error"
FACT = "fact"

# How a code is decided.
#   always error      whatever severity the check gave it
#   always fact       whatever severity the check gave it
#   error unless note a problem or suspect is an error; a note is a fact
ALWAYS_ERROR = "always error"
ALWAYS_FACT = "always fact"
ERROR_UNLESS_NOTE = "error unless note"

RULES: dict[str, str] = {
    # collisions and things the game will refuse
    "overlap": ALWAYS_ERROR,
    "unknown-prototypes": ALWAYS_ERROR,
    "belts-head-on": ALWAYS_ERROR,
    "underground-unpaired": ERROR_UNLESS_NOTE,
    # inserters
    "inserter-idle": ALWAYS_ERROR,
    "inserter-loop": ALWAYS_ERROR,
    "inserter-backwards": ERROR_UNLESS_NOTE,
    "filters-ignored": ALWAYS_ERROR,
    # circuits
    "condition-without-wire": ALWAYS_ERROR,
    "combinator-unwired": ERROR_UNLESS_NOTE,
    "constant-empty": ALWAYS_ERROR,
    # machines and power
    "machine-no-recipe": ERROR_UNLESS_NOTE,
    "unpowered": ALWAYS_ERROR,
    "pole-isolated": ALWAYS_ERROR,
    "power-split": ALWAYS_ERROR,
    # rails
    "signal-without-rail": ERROR_UNLESS_NOTE,
    # fluids
    "fluid-box-unconnected": ALWAYS_ERROR,
    "fluids-mixed": ALWAYS_ERROR,
    # lanes
    "lanes-not-taken": ALWAYS_ERROR,
    "lanes-mixed": ALWAYS_ERROR,
    "lanes-nothing": ERROR_UNLESS_NOTE,
    "lanes-missing": ALWAYS_ERROR,
    "lanes-wrong": ALWAYS_ERROR,
    "lanes-wrong-item": ALWAYS_ERROR,
    "lanes-starved": ALWAYS_ERROR,
    "lanes-rate": ERROR_UNLESS_NOTE,
    "lanes-unknown": ERROR_UNLESS_NOTE,
    "route-lanes-disagree": ALWAYS_ERROR,
    "lanes-overflow": ALWAYS_FACT,
    "lanes-swapped": ALWAYS_FACT,
    "lanes-unchecked": ALWAYS_FACT,
    # routes
    "route-failed": ALWAYS_ERROR,
    "route-belt-slow": ALWAYS_ERROR,
    "route-short-supply": ALWAYS_ERROR,
    "route-split-cascade": ALWAYS_FACT,
    "route-no-underground": ALWAYS_FACT,
    "route-underground-speed": ALWAYS_FACT,
    "route-tiles-unchecked": ALWAYS_FACT,
    # blocks
    "block-short": ALWAYS_ERROR,
}

# What to check, for an error whose check gave no detail of its own.
_CHECK_HINTS: dict[str, str] = {
    "route-short-supply": "Declare more supply at the start of the connection, or lower what the destination is asked for.",
}
_GENERIC_HINT = "Check whether this is intended; if not, part of the build will not work."

_RANK = {Severity.PROBLEM: 0, Severity.SUSPECT: 1, Severity.NOTE: 2}


def level(finding: Finding) -> str:
    """`error` or `fact` for one finding.

    A code with no rule is an error: in doubt, the reader is told. (The test
    suite fails on such a code, so it does not stay that way.)
    """
    rule = RULES.get(finding.code, ALWAYS_ERROR)
    if rule == ALWAYS_ERROR:
        return ERROR
    if rule == ALWAYS_FACT:
        return FACT
    return FACT if finding.severity is Severity.NOTE else ERROR


@dataclass(frozen=True)
class Line:
    """One thing to tell the reader."""

    level: str
    text: str
    kind: str = "finding"  # finding | count | block | route | assumed
    code: str | None = None
    detail: str = ""
    position: tuple[int, int] | None = None
    # the grade the check itself gave it, kept so the old fields stay explainable
    severity: str | None = None

    def to_dict(self) -> dict:
        out: dict = {"text": self.text, "kind": self.kind}
        if self.code:
            out["code"] = self.code
        if self.detail:
            out["detail"] = self.detail
        if self.position is not None:
            out["position"] = list(self.position)
        if self.severity:
            out["severity"] = self.severity
        return out


@dataclass
class Presented:
    errors: list[Line] = field(default_factory=list)
    facts: list[Line] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"errors": [l.to_dict() for l in self.errors], "facts": [l.to_dict() for l in self.facts]}


def verdict(errors: int) -> str:
    if errors == 0:
        return "no errors"
    return "1 error" if errors == 1 else f"{errors} errors"


def _line(finding: Finding) -> Line:
    lvl = level(finding)
    detail = finding.detail
    if lvl == ERROR and not detail:
        detail = _CHECK_HINTS.get(finding.code, _GENERIC_HINT if finding.severity is not Severity.PROBLEM else "")
    return Line(lvl, finding.summary, "finding", finding.code, detail, finding.position, finding.severity.value)


def from_findings(findings) -> Presented:
    """Split findings into errors (worst grade first, then in the order found) and facts."""
    ordered = sorted(enumerate(findings), key=lambda p: (_RANK[p[1].severity], p[0]))
    out = Presented()
    for _, finding in ordered:
        line = _line(finding)
        (out.errors if line.level == ERROR else out.facts).append(line)
    return out


def from_inspection(report) -> Presented:
    """A `check` result: findings, then how much was looked at."""
    out = from_findings(report.findings)
    where = f" against {report.dataset}" if report.dataset else ""
    out.facts.insert(0, Line(FACT, f"{report.entities_checked} entities checked{where}", "count"))
    return out


def _belt_load(result) -> Line | None:
    """`the iron-plate belt is 40% loaded`, when the route knows its rate and belt."""
    from . import layout

    spec = result.spec
    if not result.ok or result.kind != "belt" or result.rate is None or spec is None:
        return None
    try:
        capacity = layout.belt_throughput(spec.surface)
    except layout.LayoutError:
        return None
    items = [i for i in dict.fromkeys(result.lanes or ()) if i]
    what = "/".join(items) if items else "route"
    share = 100 * result.rate / capacity if capacity else 0
    return Line(
        FACT,
        f"route {result.id}: the {what} belt is {share:.0f}% loaded ({result.rate:.4g}/s of {capacity:.4g}/s, {spec.surface})",
        "route",
    )


def _route_line(r) -> Line:
    if not r.ok:
        return Line(FACT, f"route {r.id}: not built ({r.reason})", "route")
    text = f"route {r.id}: {r.kind} {r.length} tile(s)"
    if r.hops:
        text += f", {r.hops} underground pair(s)"
    text += f", {r.turns} turn(s)"
    if r.lanes:
        text += f", {'lanes' if r.kind == 'belt' else 'carries'} " + "|".join(i or "-" for i in r.lanes)
    if r.junction:
        text += f", {r.junction.kind} at {r.junction.tile}"
    if r.delivered:
        text += f", delivered {' | '.join(r.delivered)}"
    return Line(FACT, text, "route")


def _block_lines(block) -> list[Line]:
    if block.planned_crafts_per_second is None:
        rate = f"{block.crafts_per_second:.4g} crafts/s at full load"
    else:
        rate = f"{block.planned_crafts_per_second:.4g}/s planned of {block.crafts_per_second:.4g} crafts/s"
    lines = [Line(FACT, f"block {block.index}: {block.machines} x {block.machine} on {block.recipe}, "
                        f"{block.width}x{block.height} at {tuple(block.at)}, {rate}", "block")]
    for row in block.rows:
        limit = f" of {row['capacity']}" if row["capacity"] else ""
        lines.append(Line(FACT, f"  row {row['index'] + 1}: {row['machines']}{limit}", "block"))
    for port in block.ports:
        items = " | ".join(i or "-" for i in port["items"])
        if port.get("planned_rate") is None:
            flow = f"{port['rate']:.4g}/s at full load"
        else:
            flow = f"{port['planned_rate']:.4g}/s planned ({port['rate']:.4g}/s at full load)"
        arrives = f", arrives {' | '.join(port['arrives'])}" if port.get("arrives") else ""
        lines.append(Line(FACT, f"  [{port['index']}] {port['io']:3s} {port['kind']:4s} ({port['x']}, {port['y']}) "
                                f"flowing {port['direction']}: {items} at {flow}{arrives}", "block"))
    return lines


def from_build(result) -> Presented:
    """A build result: errors first, then facts -- counts, the findings that are facts, blocks, routes, assumptions."""
    out = from_findings(result.findings)
    facts = out.facts
    facts.insert(0, Line(
        FACT,
        f"{len(result.blueprint.entities)} entities, {result.power_networks} power network(s)",
        "count",
    ))
    for block in result.blocks:
        facts.extend(_block_lines(block))
    for r in result.routes:
        facts.append(_route_line(r))
        load = _belt_load(r)
        if load is not None:
            facts.append(load)
    # What was assumed. The same note on every block is said once.
    said: dict[str, list[int]] = {}
    for block in result.blocks:
        for note in block.notes:
            said.setdefault(note, []).append(block.index)
    for note, where in said.items():
        tail = f" (blocks {', '.join(map(str, where))})" if len(result.blocks) > 1 and len(where) > 1 else \
            (f" (block {where[0]})" if len(result.blocks) > 1 else "")
        facts.append(Line(FACT, f"assumed: {note}{tail}", "assumed"))
    for warning in result.warnings:
        facts.append(Line(FACT, f"assumed: {warning}", "assumed"))
    return out


def format_lines(presented: Presented, head: str = "", *, facts: bool = True) -> list[str]:
    """The text the build and check commands print: a verdict, the errors, the facts."""
    lines = [f"{head}: {verdict(len(presented.errors))}" if head else verdict(len(presented.errors))]
    if presented.errors:
        lines.append("")
        lines.append("errors:")
        for e in presented.errors:
            where = f" ({e.position[0]}, {e.position[1]})" if e.position else ""
            lines.append(f"  {e.text}{where}")
            if e.detail:
                lines.append(f"      {e.detail}")
    if facts and presented.facts:
        lines.append("")
        lines.append("facts:")
        for f in presented.facts:
            where = f" ({f.position[0]}, {f.position[1]})" if f.position else ""
            lines.append("  " + f.text + where)
            if f.detail:
                lines.append(f"      {f.detail}")
    return lines


def build_dict(result) -> dict:
    """`result.to_dict()` plus `errors` and `facts`.

    The older `findings` and `warnings` stay for compatibility; they are deprecated
    in favour of the two new arrays.
    """
    data = result.to_dict()
    data.update(from_build(result).to_dict())
    return data
