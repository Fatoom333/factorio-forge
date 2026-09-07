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

## Why the dependency points at a fork

No released version can build game data for the mod sets this project exists to
serve, so the dependency is temporarily a fork of upstream 3.3.1 carrying two
fixes. Three separate defects are involved.

**4.0.0 cannot run `update` at all.** It loads
`compatibility/defines/<major>.<minor>.lua`, and the published wheel does not
contain that directory, so every run dies on a missing file regardless of game
version or mods. Reported as upstream issue #227. This rules out the 4.x line,
which is also why the fork branches from the `3.3.1` tag rather than `main`.

**`require` does not return the cached module.** `compatibility/interface.lua`
clears `package.loaded` after every call, so requiring the same file twice
returns two different values. Mods routinely have one file require a shared
table and add to it while another requires the same table and uses what was
added; the second file gets a fresh copy and the additions are gone. The
clearing exists for a real reason — `package.loaded` is keyed by the name as
written, so one mod's `utils` would satisfy another mod's `require("utils")` —
but it also destroys caching within a mod. Keying a cache on the resolved path,
which `require` already computes, satisfies both.

**A Space Age prototype is read without checking it exists.** `get_items`
extracts `space-platform-starter-pack` whenever the game version is 2.0 or
newer, but that prototype belongs to the expansion and is absent without it, so
extraction fails for every mod set that does not include Space Age.
`get_signals` in the same file already tests for presence; the fix makes
`get_items` consistent with it.

Both patched defects are reported upstream with reproductions and diffs. When
they are released, the dependency goes back to a plain version specifier and the
fork is abandoned — nothing in this project depends on the fork existing beyond
that.

## The bundled dataset is not ground truth

Draftsman ships a copy of `factorio-data`, and on the 4.x line that copy tracks
2.1.x — which comes from Factorio's **public test branch**, not a released
version. The stable game is 2.0.x.

This matters little in practice, because every profile regenerates its data from
the player's own installation rather than using the bundle. But the bundle
should never be treated as authoritative, and anything unfamiliar in it wants
checking against the actual game first.
