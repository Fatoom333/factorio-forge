# factorio-forge

Generate Factorio blueprints from a description — tailored to your own save, your
mod set and the way *you* build.

[Русская версия](README.ru.md)

The main way to use it is through [Claude Code](https://claude.com/claude-code):
the [skill](skill/factorio-forge/SKILL.md) in this repository tells Claude how
to turn your request into a plan, build it with these tools, read their checks
and hand you a blueprint. The tools are an ordinary command-line program and
work without Claude too.

## Status

Version 0.1.0, one developer, tested on Windows with Factorio 2.0. Below is
what exists today, not a plan.

**Works and is covered by the test suite** (`python -m pytest`, about 440
tests):

- **Profiles from a save** — the save's mod set is read from the save file's
  header; each profile gets its own mod folder of hard-linked archives; newer
  mod versions are used and recorded, older or missing ones are refused
  (`create-profile`, `activate-profile`, `list-profiles`,
  [docs/profiles.md](docs/profiles.md)).
- **Game data extraction** through
  [a fork of factorio-draftsman](https://github.com/Fatoom333/factorio-draftsman/tree/main-forge),
  which runs the game's own Lua data stage over the profile's mods. The
  extraction needs the installed game, so the automatic tests do not run it;
  it has been used on a Krastorio 2 save and on a Space Age save with 42 mods.
- **Render** — a blueprint string drawn as one self-contained HTML page
  (`render`, [docs/rendering.md](docs/rendering.md)).
- **Check** — what looks wrong in a blueprint, without changing it (`check`,
  [docs/checking.md](docs/checking.md)). Underground pairing is held against
  144 underground entities whose partners were recorded in a running game.
- **Request review** — the player's words turned into prototype names (slang,
  the names this mod set shows, the base game's names), locked items and
  tiers reported, and the questions only the player can answer listed
  (`find`, `review`, [docs/request.md](docs/request.md)).
- **Bill of materials** — machines per recipe for a target rate, with
  researched productivity applied (printed by `review`).
- **Row layout** — rows of machines with their belts, inserters, poles and
  pipes, how many machines a row can keep running, mirrored, repeated and
  rotated blocks (`options`, `build`, [docs/layout.md](docs/layout.md)). Every
  recipe shape in a Krastorio 2 profile (63 shapes) was built this way.
- **Routing between block ports** — belts and pipes from one block's port to
  another's, with underground belts and pipe-to-ground, obeying the game's
  rules (no side-loading, no pipe touching another fluid, no underground
  stealing another pair) ([docs/layout.md](docs/layout.md)).
- **Companion mod export** — what only the running game knows (research,
  bonuses, startup settings) is read from the
  [companion mod](#the-companion-mod)'s export, and used only when its mod set
  matches the profile.
- **Building style** measured from your own reference blueprints
  (`measure-style`, `show-style`).

**Works, but not yet confirmed in the game:**

- A routed underground belt or pipe-to-ground pair at *exactly* its maximum
  reach. The reach comes from the prototype; whether the game pairs the two
  ends at that distance in the same way has not been tried.
- A belt curve right after an underground exit. Whether such a curve keeps
  both lanes is not confirmed, so the router never builds one: it turns only
  after a plain belt ([docs/layout.md](docs/layout.md), "Joins and lanes").

**Does not exist:**

- Splitters, merges and balancers between blocks; one source feeding several
  destinations; lane swaps; joining into the middle of a belt. These are
  placed by hand in the plan, and the same checks run over them.
- Map terrain: water, cliffs, ore and an existing base are not obstacles
  unless you reserve those tiles yourself.
- Trains: no stations, rails or train blocks.
- Generators for whole blocks (a complete smelting column, a science block).
  The tools build rows and route between them; Claude or you decide the
  blocks.
- Modules and beacons as entities: a speed bonus only changes the numbers.

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

- Factorio 2.0 installed (any mod set, Space Age optional)
- Python 3.10 or newer (tested with 3.12). On Windows, install it from
  [python.org](https://www.python.org/downloads/windows/), which also installs
  the `py` launcher used below.
- Git: `pip` fetches the draftsman fork from GitHub
- [Claude Code](https://claude.com/claude-code), to use the skill (optional)

## Setup

**Windows (PowerShell):**

```powershell
git clone https://github.com/Fatoom333/factorio-forge
cd factorio-forge
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\factorio-forge.exe paths
```

The commands call the environment's programs by path, so nothing needs
activating. To type plain `factorio-forge` instead, activate it with
`.\.venv\Scripts\Activate.ps1`; if PowerShell refuses to run scripts, allow
them for your user once with
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

**Linux and macOS:**

```bash
git clone https://github.com/Fatoom333/factorio-forge
cd factorio-forge
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/factorio-forge paths
```

> **Note.** This installs `factorio-draftsman` from
> [a fork](https://github.com/Fatoom333/factorio-draftsman/tree/main-forge)
> rather than from PyPI. No released version can extract game data for the mod
> sets this project targets; the fork is upstream `main` plus three fixes
> reported upstream and the extraction of resources, asteroid chunks and
> surfaces. See [docs/draftsman-notes.md](docs/draftsman-notes.md) and
> [FORK.md](https://github.com/Fatoom333/factorio-draftsman/blob/main-forge/FORK.md).

`paths` shows where the game, its saves and its mods were found. Every path is
detected automatically, primarily by reading the paths Factorio itself recorded
in its last log — which works for Steam libraries on any drive, standalone
installs and all three platforms. Nothing needs configuring unless detection
fails. If it finds no game, start Factorio once so it writes that log, or
set `FACTORIO_PATH` (see [Where things are stored](#where-things-are-stored)).

`render` and `check` work right away against the base game's data bundled with
draftsman. For anything modded, make a profile from your save and activate it
(below, shown as plain `factorio-forge`; on Windows without activation that is
`.\.venv\Scripts\factorio-forge.exe`):

```powershell
factorio-forge create-profile "My save"
factorio-forge activate-profile my-save
```

`create-profile` takes a save's name as the game lists it, or a path to the
`.zip`, and prints the profile's name and the `activate-profile` line to run.
Every mod the save uses must be in the game's mod folder. Activating a profile
replaces the game data of the draftsman installed in this environment; see
[docs/profiles.md](docs/profiles.md).

## The Claude skill

The skill is [skill/factorio-forge/SKILL.md](skill/factorio-forge/SKILL.md).
Claude Code reads personal skills from `~/.claude/skills/` (on Windows
`%USERPROFILE%\.claude\skills\`). Link the skill folder there, so a `git pull`
updates it, or copy it.

**Windows (PowerShell), from the `factorio-forge` folder:**

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills" | Out-Null
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\factorio-forge" -Target "$PWD\skill\factorio-forge"
```

A junction needs no administrator rights. To copy instead:
`Copy-Item -Recurse skill\factorio-forge "$env:USERPROFILE\.claude\skills\"`.

**Linux and macOS, from the `factorio-forge` folder:**

```bash
mkdir -p ~/.claude/skills
ln -s "$PWD/skill/factorio-forge" ~/.claude/skills/factorio-forge
```

To copy instead: `cp -r skill/factorio-forge ~/.claude/skills/`.

The skill runs the tools with this checkout's Python
(`.venv\Scripts\python.exe -m factorio_forge.cli` on Windows,
`.venv/bin/python -m factorio_forge.cli` elsewhere), so Claude has to know
where the checkout is. Start Claude Code in the `factorio-forge` folder, or
tell it the folder's path in the conversation; with the skill linked rather
than copied, Claude can also find the checkout by following the link. Then
ask for a blueprint in your own words, for example "a block making 2
batteries a second for my Krastorio save".

## The companion mod

What only the running game knows — what you have researched, the bonuses it
gave you, the exact startup settings — is exported by a small mod,
[factorio-forge-companion](https://github.com/Fatoom333/factorio-forge-companion).
It is **not on the Factorio mod portal yet**: download the `.zip` from its
[releases](https://github.com/Fatoom333/factorio-forge-companion/releases),
put it into the mod folder that `factorio-forge paths` shows as `mods_dir`,
and enable it in the game. The tools read the research export
from version 0.7.0 on.

In the save, type `/forge-export` in the console or press Export in the mod's
window. Without the export the tools still work, but ask about tiers instead of
knowing them and treat inserter hands as 1.

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
on the drawing. Both also take a file containing the string. See
[docs/checking.md](docs/checking.md).

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
