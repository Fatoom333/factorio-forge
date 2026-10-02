# Notes on factorio-draftsman

What we rely on from the library, and what was verified by hand rather than
assumed. Everything below was checked on **Factorio 2.0.77**, against
**draftsman 3.3.1** and the fork of it, except where 4.0.0 is named explicitly.
Forge now runs on a fork of upstream `main` (see below); notes not re-checked
on 4.x are still the 3.3.1 findings.

[Русская версия](draftsman-notes.ru.md)

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

**Version targeting works and actually converts, for the versions that are
registered.** `to_string(version=...)` rewrites the output for the target: an
export to `(1, 0)` puts direction values back into the old 8-value scheme.

Which versions exist depends on the line. On 3.3.1, which is what we use,
`draftsman_converters.versions` holds `(1, 0)` and `(2, 0)`; `(2, 1)` was added
in 4.0.0. **An unregistered target does not raise** — passing `(2, 1)` on 3.3.1
returns a string as though it had worked. So the value has to be checked against
`draftsman/serialization.py` rather than assumed from the fact that the call
succeeded.

None of this is pressing, because Factorio 2.1 has **not been released** — it
exists only as a public test branch, and our target is 2.0 stable.

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
serve, so the dependency is a fork: the `main-forge` branch, cut from upstream
`main` (unreleased 4.0.1, Factorio 2.1.17 vanilla data). It carries three fixes
reported upstream and not merged yet -- the `mod-settings.dat` encoder (#235,
PR #236), the global `unpack` (#237), fractional amounts in `parse_energy`
(#238) -- and extracts resources, asteroid chunks and surfaces, which draftsman
leaves out. See its FORK.md.

Until 2026-10-02 the fork was cut from 3.3.1 and carried the fixes described
below. All of them are now in upstream `main` (#227-#233) and the fork takes
upstream's own form; they are kept here for the reasoning. Two differ from
what the fork did: `get_order` has no fallback for a missing item, and an
inserter's unknown pickup or drop position is `None`, not `(0, 0)`.

Upstream's vanilla data is now 2.1, where a recipe states `categories` (a list)
instead of `category`; forge reads both through `factorio_forge.categories`.

Four of them share a shape worth naming, because it predicts where the next one
will be: the code trusts prototype data to take one particular form when
Factorio accepts several, or to reference something that is not guaranteed to
exist. Vanilla data always satisfies those assumptions, so nothing shows up
until a mod does something equally legal and different.

**4.0.0 cannot run `update` at all.** It loads
`compatibility/defines/<major>.<minor>.lua`, and the published wheel does not
contain that directory, so every run dies on a missing file regardless of game
version or mods. Reported as upstream issue #227 and fixed in `main`, which is
why the fork is now cut from `main` rather than from a release.

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

**`feature_flags` never reached mods.** The global was built by formatting a
Python boolean into Lua source, which yields `True` or `False`; Lua's literals
are lowercase, so both were read as undefined globals and every flag came out
`nil` whatever the DLC state. The visible symptom was that `--no-dlc` changed
nothing — it had in fact never worked, having arrived in the same commit as the
formatting mistake. The real cost is quieter: a mod gating content on
`feature_flags.quality` silently contributed the wrong variant, with no error
anywhere. Mods comparing a flag against `false` were affected too, since
`nil == false` is false.

**A bounding box written both ways at once was unreadable.** Factorio accepts a
corner positionally, as `{-1, -1}`, or by name, as `{x = -1, y = -1}`, and the
box itself either as a pair or as `{left_top = ..., right_bottom = ...}`. A
prototype may supply a corner both ways at once, and since a Lua table is
converted to a list only when every key is an integer, such a corner arrives as
a mapping and cannot be indexed positionally. Vanilla `spidertron` is written
plainly and loads; `warptorio-warpspider` is not and took the whole extraction
down. Reading a corner now goes through one helper that accepts every form.

**An entity mined into an unknown item took everything down.** Entities are
sorted by the item they become when mined, and that item was read straight out
of the item table. Nothing guarantees it survived extraction — a mod can name
one that is hidden, or that another mod removed. The fallback branch below it
already tested membership; the first path now does too.

The first three patched defects are reported upstream with reproductions and
diffs; the last two are not yet written up. When they are released, the
dependency goes back to a plain version specifier and the fork is abandoned —
nothing in this project depends on the fork existing beyond that.

## Extraction is not reproducible, and that is expected

Running the same extraction twice over an identical mod set can produce
slightly different data. This was measured, not guessed:

| Profile | Repeats | Recipe counts |
| --- | --- | --- |
| vanilla + Space Age, no other mods | 3 | 658, 657, 658 |
| Krastorio 2, 28 mods | 6 | 1851, 1851, 1833, 1851, 1833, 1833 |

The varying names are always generated ones — barrel recycling recipes in the
vanilla case, Dectorio vegetation and its Krastorio crushing counterparts in the
modded one. The stable core is identical every time.

Note the first row: it contains no third-party mods at all, so this is not some
mod misbehaving.

### Why

Factorio's own data stage extends a table while iterating it. From
`quality/data-updates.lua`:

```lua
for name, recipe in pairs(data.raw.recipe) do
  recycling.generate_recycling_recipe(recipe)   -- adds to data.raw.recipe
end
```

The Lua manual leaves this undefined: you may not assign to a non-existent field
of a table during traversal. Whether a newly added recipe is reached by the loop
still running depends on where it lands internally.

In the real game that lands the same way every time, so nothing is ever noticed.
Under the stock Lua that the extraction runs on, string hashing is seeded per
process — six subprocesses produced six different iteration orders for the same
twelve keys — so the same undefined behaviour resolves differently each run.

Upstream tracks the general form of this as issue #214, "Draftsman should use
Factorio's Lua 5.2 instead of regular Lua 5.2".

### What we do about it

Nothing clever, deliberately. Chasing byte-identical extraction would mean
rebuilding the Lua underneath with a fixed hash seed, and even that would only
buy *reproducibility*, not agreement with the game — matching the game exactly
would require matching its table internals too.

Instead, a profile is extracted **once** and its data kept. Nothing re-extracts
on its own, so within a profile the data a blueprint was generated against stays
put. `Profile.measure_data()` records counts and a digest of every prototype
name at extraction time, stored as `data_fingerprint` in `profile.json`, so that
if the data is ever regenerated the change is visible rather than silent.

The fingerprint is not a correctness check. It answers "is this still the data I
had?", which is the question that actually matters here.

## The bundled dataset is not ground truth

Draftsman ships a copy of `factorio-data`, and on the 4.x line that copy tracks
2.1.x — which comes from Factorio's **public test branch**, not a released
version. The stable game is 2.0.x.

This matters little in practice, because every profile regenerates its data from
the player's own installation rather than using the bundle. But the bundle
should never be treated as authoritative, and anything unfamiliar in it wants
checking against the actual game first.
