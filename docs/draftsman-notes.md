# Notes on factorio-draftsman

What we rely on from the library, and what was verified by hand rather than
assumed. Everything below was checked against **draftsman 4.0.0** on
**Factorio 2.0.77**.

## Why we depend on it at all

Draftsman already solves the mechanical half of this project: the blueprint
string codec, an entity model with collision boxes and footprints, and — most
importantly — a data extraction step that runs Factorio's real Lua data
lifecycle over an installed mod set.

That last point is the decisive one. A mod set cannot be understood by reading
mod sources: mods patch each other across `data.lua`, `data-updates.lua` and
`data-final-fixes.lua`, load order matters, and mod settings change recipes.
Draftsman resolves this the only reliable way, by executing the same lifecycle
the game does (it depends on `lupa`, an embedded Lua runtime, for exactly this).

## API facts, verified

**Construction and import are separate.** This changed in the 3.x line and
matters because the wrong call fails silently in a confusing way:

```python
Blueprint.from_string(s)   # correct: parses a blueprint string
Blueprint(s)               # WRONG: treats s as the first constructor argument
                           # (label), producing a blueprint labelled with the
                           # entire string rather than an error
```

Round-tripping is exact: `Blueprint.from_string(s).to_string() == s`.

**Entities are placed by top-left tile, and the library converts to the centre
position the format requires.** This removes the whole class of half-tile
off-by-one bugs, so `tile_position` should be used everywhere in our code and
raw `position` essentially never:

| Entity | Size | `tile_position=(0,0)` becomes `position` |
| --- | --- | --- |
| `transport-belt` | 1×1 | `(0.5, 0.5)` |
| `assembling-machine-2` | 3×3 | `(1.5, 1.5)` |
| `steam-engine` | 3×5 | `(1.5, 2.5)` |
| `pump` | 1×2 | `(0.5, 1.0)` |

Note the pump: an even dimension lands on an integer coordinate while an odd
one lands on a half. This is why a plain 2D matrix cannot be the canonical
model, and why the entity list stays canonical with the grid derived from it.

**Directions use the 16-value 2.0 scheme** — north 0, east 4, south 8, west 12 —
and the library validates which directions each entity type accepts. Placing a
belt diagonally raises a `DirectionWarning` listing the permitted set. That is
placement-rule validation we would otherwise have had to write.

**Version targeting works and actually converts.** `to_string(version=...)`
accepts `(1, 0)`, `(2, 0)` and `(2, 1)`; exporting to `(1, 0)` rewrites
direction values into the old 8-value scheme. Registered converter versions are
visible in `draftsman/serialization.py`.

Note that Factorio 2.1 has **not been released** — it exists only as a public
test branch. Our target is 2.0 stable, and `(2, 1)` support is forward-looking
groundwork that should not be relied upon until the version actually ships.

## Environment layout, and the constraint it puts on profiles

The CLI takes the game and mod locations as **top-level** options, before the
subcommand:

```bash
draftsman -p <factorio install> -m <mods folder> update
```

`--mods-path` is what makes per-profile mod sets possible, and it was the main
open risk when this project was planned. It is not an option of `update`
itself, which is easy to miss.

The extracted data, however, goes to a single fixed location:
`site-packages/draftsman/data/*.pkl` (roughly 12 MB: entities, recipes, items,
signals, tiles and so on). There is no environment variable or setting to
redirect it — the package was searched for `os.environ` and `DRAFTSMAN_` and
has neither.

**Consequence for us.** Several saves with different mod sets cannot coexist in
one installation's data directory. Running `update` for a second profile
overwrites the first. Our profile layer therefore has to own this: run `update`
against a profile's mod folder, copy the resulting data files into the profile,
and swap them back in when that profile is activated. It is a small amount of
file shuffling and it keeps the profiles genuinely independent.

## Freshness caveat

Version 4.0.0 was published on 2026-09-07 — the same day it was adopted here.
It is a major release, so undiscovered regressions are plausible.

More importantly, it bundles `factorio-data` 2.1.17, which comes from
Factorio's **public test branch**, not a released version. The stable game is
2.0.x. So the library's default bundled dataset is ahead of any released game
and may contain entities, recipes and fields that do not exist in a normal
install.

This is mostly harmless for us, because a profile regenerates its data from the
player's own installation rather than using the bundle — but it means the
bundled dataset must never be treated as ground truth, and anything unfamiliar
in it should be checked against the actual game before being relied upon.
