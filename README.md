# factorio-forge

Generate Factorio blueprints from a description — tailored to your own save, your
mod set and the way *you* build.

[Русская версия](README.ru.md)

> **Status: early development.** Reading saves, building profiles and extracting
> game data all work: every distinct mod set in a real save folder of a hundred
> saves builds. What is not written yet is the part that produces blueprints —
> the renderer, the companion mod and the generators. Nothing here is usable for
> its stated purpose yet.

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
- **Your base and its research state** will come from a small companion mod that
  exports them from inside the game, where the game itself acts as the parser.
  *(Not written yet.)*
- **Your building style** is measured from reference blueprints rather than
  asked for in a questionnaire. *(Not written yet.)*

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
> [a fork](https://github.com/Fatoom333/factorio-draftsman/tree/3.3.1-forge)
> rather than from PyPI. No released version can extract game data for the mod
> sets this project targets; the fork is upstream 3.3.1 plus five fixes and
> nothing else, all reported upstream. See
> [docs/draftsman-notes.md](docs/draftsman-notes.md) for what each one is and
> [FORK.md](https://github.com/Fatoom333/factorio-draftsman/blob/3.3.1-forge/FORK.md)
> for the diffs. When they are released this goes back to a plain dependency.

Then check that the game was found:

```bash
factorio-forge paths
```

Every path is detected automatically, primarily by reading the paths Factorio
itself recorded in its last log — which works for Steam libraries on any drive,
standalone installs and all three platforms. Nothing needs configuring unless
detection fails.

## Documentation

- [Profiles](docs/profiles.md) — what a profile is, how mod sets are resolved,
  and why extraction happens once
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
