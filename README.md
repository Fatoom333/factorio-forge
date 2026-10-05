# factorio-forge

Generate Factorio blueprints from a description — tailored to your own save, your
mod set and the way *you* build.

[Русская версия](README.ru.md)

> **Status: early development.** Reading saves, building profiles, extracting
> game data, drawing and checking a blueprint, reviewing a request against your
> game, counting machines, laying out rows of them and routing belts and pipes
> from one block's port to another's all work, and a companion mod exports what
> only the running game knows. Generators for whole blocks, splitters and
> balancers between blocks, and trains are not written yet; those parts are done
> by hand with the tool's checks.

## What it is

Most blueprint tools give you a generic, correct-in-a-vacuum design. This one aims
to give you a blueprint that drops straight into *your* base: the same city-block
grid, the same rail spacing, the same belt tier, the same train length, using the
recipes that actually exist in your modpack.

It does that by building a **profile** for each of your saves — the mod set, the
game data as that mod set resolves it, and a building style measured from
blueprints you already use — and generating inside that profile's conventions.

## How it works

- **The mod set of a save** is read from the header of the save file itself, with
  no need to launch the game. Nothing beyond that header is parsed: the rest is
  Factorio's internal format and not worth fighting.
- **Blueprint strings, entity data and collision boxes** come from
  [`factorio-draftsman`](https://github.com/redruin1/factorio-draftsman), which
  runs Factorio's real Lua data lifecycle over your installed mods. Mod
  interactions are resolved by the game's own rules rather than guessed.
- **Your base and its research state** come from a small companion mod that
  exports them from inside the game, where the game itself acts as the parser.
- **Your building style** is measured from reference blueprints rather than
  asked for in a questionnaire, and kept with the profile.
- **Your preferences and corrections** accumulate in the profile's `notes.md`,
  in your own words, and are read back when designing.

## Requirements

- Factorio 2.0 (any mod set, Space Age optional)
- Python 3.10 or newer

## Setup

```bash
git clone https://github.com/Fatoom333/factorio-forge
cd factorio-forge
python -m venv .venv
```

Activate the environment — `.venv\Scripts\activate` on Windows (`Activate.ps1`
from PowerShell), `source .venv/bin/activate` on Linux and macOS — then:

```bash
pip install -e ".[dev]"
```

> **Note.** This installs `factorio-draftsman` from
> [a fork](https://github.com/Fatoom333/factorio-draftsman/tree/main-forge)
> rather than from PyPI. No released version can extract game data for the mod
> sets this project targets; the fork is upstream `main` plus three fixes
> reported upstream and the extraction of resources, asteroid chunks and
> surfaces. See [docs/draftsman-notes.md](docs/draftsman-notes.md) and
> [FORK.md](https://github.com/Fatoom333/factorio-draftsman/blob/main-forge/FORK.md).

Then check that the game was found:

```bash
factorio-forge paths
```

Every path is detected automatically, primarily by reading the paths Factorio
itself recorded in its last log — which works for Steam libraries on any drive,
standalone installs and all three platforms. Nothing needs configuring unless
detection fails.

## Drawing a blueprint

Anything that is already a blueprint string can be looked at:

```bash
factorio-forge render "0eNqlk...=" -o layout.html
```

Entities are drawn at their real footprint, coloured by family and marked with
their direction; hovering shows recipes, priorities and the rest. The page is a
single self-contained file with nothing to fetch. See
[docs/rendering.md](docs/rendering.md).

## Checking a blueprint

```bash
factorio-forge check "0eNqlk...="
```

Reports what looks wrong — an underground belt with no other end, an inserter
reaching nothing, filters set on an inserter with filtering switched off —
without changing anything. Add `--check` to `render` to see the findings marked
on the drawing. See [docs/checking.md](docs/checking.md).

## Documentation

- [Profiles](docs/profiles.md) — what a profile is, how mod sets are resolved,
  and why extraction happens once
- [The request](docs/request.md) — step 1: what is asked for, and what the game
  knows
- [Layout](docs/layout.md) — from a decision to a checked blueprint
- [Rendering](docs/rendering.md) — how a blueprint is drawn, and what is
  deliberately left out
- [Checking](docs/checking.md) — what is reported, why nothing is corrected,
  and how false alarms are kept down
- [Notes on factorio-draftsman](docs/draftsman-notes.md) — what we rely on, what
  was verified by hand, and why the dependency points at a fork

## Where things are stored

Code lives in the repository. Your data does not.

| What | Where |
| --- | --- |
| Profiles, extracted game data, local config | `%APPDATA%\factorio-forge\` (Windows) |
| | `~/.local/share/factorio-forge/` (Linux) |
| | `~/Library/Application Support/factorio-forge/` (macOS) |

Override the location with the `FACTORIO_FORGE_HOME` environment variable. The
game's own location can be overridden with `FACTORIO_PATH` and `FACTORIO_USER_DIR`,
though it is normally detected.

Extracted game data is generated on your machine and never committed: it depends
on your game version and mod set, and it is not ours to redistribute.

## Licence

MIT for this project's own code. Factorio is a trademark of Wube Software; this
is an unofficial community tool and ships no game assets or game data.
