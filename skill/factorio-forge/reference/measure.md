# Checking in the game: measure and the check before delivery

Read when: the player wants proof that a plan makes its rate, or before
handing over a finished blueprint.

## Measure

The companion mod's `/forge-measure` builds the blueprint on an empty surface
of its own, feeds every declared source, empties the output belt ends, runs
for a warm-up and a measured stretch, and writes `measure.json`. It finds
what no reading can: a ratio that starves a machine, an output that backs up,
a supply the belt cannot carry.

```
factorio-forge measure spec plan.json --surface vulcanus -o spec.json
tools/headless-run.ps1 -Save my-save.zip -Out out -Measure spec.json     (companion repo)
factorio-forge measure result out/measure.json --spec spec.json
```

A target counts as met from 95% of the order; a short target may only be a
line still filling: `measure result` says so, and advises a longer warm-up.
Full description and how to read the result: `docs/checking.md`, section
"Measuring what a build makes".

## In-game check before delivery

Before a finished blueprint is handed over it is placed in the game without a
window (an abstract blueprint on an empty site, a concrete place on a copy of
the save). If something the tools called fine does not stand, the blueprint is
not handed over. If Factorio is not installed, the check is skipped and the
report says so.

```
factorio-forge deliver work/<design>/plan.json
```

`deliver` builds the plan; with any error it stops. Otherwise it places the
blueprint in the game without a window, on a copy of the profile's save
(`--save` for another one), and writes `<name>.txt` only when the game placed
everything forge expected. If not, the blueprint is written as
`<name>.unverified.txt`, the exit code is non-zero, and a ready test stub for
the case is printed: hand it to whoever maintains the tool. The companion mod
is found through `FACTORIO_FORGE_COMPANION` (its folder or a built zip) or
`--companion`. Without Windows, Factorio, the runner or the companion the
verdict says "not checked in the game: <reason>" and the blueprint is written
as before; `--no-game-check` asks for that on purpose. Tell the player when a
blueprint was not checked in the game.
