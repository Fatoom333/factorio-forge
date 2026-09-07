# factorio-forge

Generate Factorio blueprints from a description — tailored to your own save, your
mod set and the way *you* build.

[Русская версия](README.ru.md)

> **Status: early development.** The path layer and project skeleton exist; the
> profile system, companion mod, renderer and generators are being built. Nothing
> here is usable yet.

## What it is

Most blueprint tools give you a generic, correct-in-a-vacuum design. This one aims
to give you a blueprint that drops straight into *your* base: the same city-block
grid, the same rail spacing, the same belt tier, the same train length, using the
recipes that actually exist in your modpack.

It does that by building a **profile** for each of your saves — the mod set, the
game data as that mod set resolves it, and a building style measured from
blueprints you already use — and generating inside that profile's conventions.

## How it works

- **Blueprint strings, entity data and collision boxes** come from
  [`factorio-draftsman`](https://github.com/redruin1/factorio-draftsman), which
  runs Factorio's real Lua data lifecycle over your installed mods. Mod
  interactions are resolved by the game's own rules rather than guessed.
- **Your base and its research state** come from a small companion mod that
  exports them from inside the game. Save files are not parsed directly — their
  format is internal and version-specific, so the game itself acts as the parser.
- **Your building style** is measured from reference blueprints rather than
  asked for in a questionnaire.

## Requirements

- Factorio 2.0 (any mod set, Space Age optional)
- Python 3.10 or newer

## Setup

```bash
git clone <this-repo>
cd factorio-forge
python -m venv .venv
.venv/Scripts/activate      # Windows
source .venv/bin/activate   # Linux / macOS
pip install -e ".[dev]"
```

Then check that the game was found:

```bash
python -m factorio_forge.paths
```

Every path is detected automatically, primarily by reading the paths Factorio
itself recorded in its last log — which works for Steam libraries on any drive,
standalone installs and all three platforms. Nothing needs configuring unless
detection fails.

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
