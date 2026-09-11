# Profiles

[Русская версия](profiles.ru.md)

A player has several saves and each is its own world. Krastorio 2 and vanilla do
not share a recipe list, an entity list, or even entity sizes. A blueprint that
is correct in one is nonsense in the other, so nothing about generation can be
global: it all belongs to a profile.

A profile is one base — its save, its exact mod set, the game data that mod set
resolves to, and eventually the building style measured from its blueprints.

## Layout

```
<forge home>/profiles/<name>/
    profile.json     the save it came from, game version, mod set, provenance
    mods/            an isolated mod folder for this set alone: hard-linked
                     archives, a generated mod-list.json, and a copy of
                     mod-settings.dat
    data/            game data extracted for this set, and no other
    blueprints/      reference blueprints, the source of the base's style
    notes.md         accumulated preferences, in the player's own words
```

The `<name>` is the save name reduced to something safe on every platform, so
`Begin Of The Bug - No Way 2` becomes `begin-of-the-bug-no-way-2`.

## Where the mod set comes from

From the save itself. The header of `level-init.dat` lists every mod the save
requires and its exact version, and that is read directly — no need to launch
the game, and no need to trust whatever happens to be enabled in the player's
global `mod-list.json`, which reflects only the last session.

This is what makes profiles automatic: a folder of a hundred saves resolves into
a handful of distinct mod sets without asking the player anything.

## Why each profile gets its own mod folder

Draftsman extracts prototype data into a single fixed directory inside its own
installation, with no setting to redirect it. Extracting a second mod set
overwrites the first. So a profile carries its own mod folder and its own copy
of the extracted data, and `activate()` swaps that copy into place.

The mod archives are **hard linked**, not copied. A real mod folder here holds
283 archives totalling 4.3 GB; a profile needs a few dozen of them, and a hard
link costs nothing because a mod archive of a given version never changes.
Where hard links are unavailable — a different volume, an exotic filesystem —
the file is copied instead and the report says so.

## Mod versions: the same ladder the game uses

A save records the exact version of every mod it was played with. Months later
those mods have been updated, and the exact versions may be gone. Factorio has
an answer for this and we follow it rather than inventing our own:

| Installed | What happens | Why |
| --- | --- | --- |
| the exact version | used as is | nothing to decide |
| a newer version | used, and **recorded** | the game loads it and migrates forwards |
| only older versions | refused | migrations do not run backwards, and neither does the game |
| nothing | refused | there is no mod to load |

When several newer versions are present, the newest is taken — again matching
the game, which loads the newest enabled copy.

Substitutions are never silent. They appear in the report from `prepare_mods()`
and are written into `profile.json` as `mod_substitutions`:

```json
"mod_substitutions": [
  {"name": "RateCalculator", "wanted": [3, 3, 7], "used": [3, 3, 8]},
  {"name": "squeak-through-2", "wanted": [0, 1, 2], "used": [0, 1, 5]}
]
```

That record matters because a different version of a mod can change recipes. If
a generated blueprint ever looks wrong, the first question is whether it was
built against the mods the save actually had, and this answers it.

The alternative — refusing anything but an exact match — was tried first and
rejected. Mods update constantly, so in practice almost no profile would ever
build: the Archie 2 profile here failed on two mods out of thirteen, both of
them interface-only mods that add no prototypes at all.

## Archives the mod loader cannot read

Some mods are zipped on macOS and carry a `__MACOSX` folder beside the real one.
The loader tolerates that only when the inner folder is named `name_version`; a
mod whose inner folder is just `name` fails to load at all.

Rather than lose the mod — and with it every entity and recipe it contributes —
such an archive is repacked into the profile's mod folder without the junk.
Nothing else about it is touched, so the mod that loads is the same mod.

## Extraction and its fingerprint

`extract_data()` runs Factorio's real Lua data lifecycle over the profile's mod
folder. This is the only reliable way to know what a mod set produces: mods
patch each other across load stages, order matters, and settings change recipes.
Reading mod sources cannot answer it.

Extraction is deliberately run **once**. It is not reproducible — see
[the notes on draftsman](draftsman-notes.md) for the measurements and the cause
— so a profile keeps the data it got, and nothing re-extracts on its own.

Each extraction records counts and a digest of every prototype name into
`data_fingerprint`. That is not a correctness check; it answers "is this still
the data my blueprint was built against?", which is the question that matters.

### Startup mod settings

Startup settings feed into the data lifecycle and therefore change recipes — how
long an underground pipe may run, whether a mod's loaders exist at all. A save
carries its own settings in its header, but that section is not parsed, and
copying the player's current global `mod-settings.dat` is only an approximation
that silently rots once those settings move on.

The companion mod closes this gap from the other side: `/forge-export` (or its
button) writes `environment.json`, which includes the exact `startup_settings`
a play session had loaded, alongside the mod set that produced them. When that
mod set matches this profile's exactly — `Environment.matches_mods()` — those
settings are trusted and encoded straight into a fresh `mod-settings.dat` via
Draftsman's `write_mod_settings()`. Only when there is no matching export does
`_copy_mod_settings()` fall back to copying the global file, and either way the
source is recorded rather than left silent: `Profile.mod_settings_source` is
`"environment"` or `"global-approximation"`.

## Sharing a profile

A profile is self-contained and holds no absolute paths, so its folder can be
zipped and given to someone else — a way to pass around a building style rather
than a single blueprint.

Two things should come out first. `data/` is game data extracted from the
sender's own installation and mods, which is not ours to redistribute and would
be wrong for the recipient anyway; they regenerate it from their own copy of the
game. `mods/` holds hard links to mod archives, which is likewise someone else's
work to distribute. What is worth sharing is `profile.json`, `blueprints/` and
`notes.md`.
