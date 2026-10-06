# The request: step 1, and what the game knows

[Русская версия](request.ru.md)

Reading what the player asked for is done by Claude, through the skill. What
this part of the toolkit does is give that reading something to be checked
against, before any layout is designed.

## Three questions a model should not answer from memory

**What is it called?** Players say "red science", "зелёные схемы", "мазут";
tools want `automation-science-pack`, `electronic-circuit`, `heavy-oil` -- and
mods rename things (Krastorio 2's red science is an "automation tech card").
`factorio-forge find <words>` tries three ways at once and says under each
candidate why it was found:

- **slang** (`slang.py`): about a hundred common Russian and English terms
  mapped to the base game's internal names, which mods keep when they rename
  what is shown. Some official Russian names are far from speech -- heavy oil
  is «Мазут», the pumpjack «Насосная станция», the roboport «Дронстанция» --
  and one is a trap: players' «аккумуляторы» are accumulators, while the base
  game's «Аккумулятор» is the battery. Such terms carry a note. A vague phrase
  ("колбы", "ленты") lists every option; slang for something the mod set lacks
  (2.0 removed the rocket control unit) is reported as that;
- **the name shown in this mod set**: the game's translations with the mods'
  on top, `localised_name` with its parameters resolved the way the game does;
- **the base game's name**: the game's packages alone. A match here says both
  names -- "matched the base game's «Автоматизационный исследовательский
  пакет»; this mod set shows it as «Автоматизационная технологическая карта»".

Words are compared without their endings, so "синие", "синяя" and "синий"
meet. The table is words, not game data: whether a name exists, what it is
called and whether it is unlocked always comes from the active profile, and a
test checks every name in the table against the game's own locale files. The
model picks among the candidates, and adds a phrase to the table when it had
to work one out.

**What does the player have?** Unlocked recipes, and from them the belts,
inserters, poles and machines the player can build (an entity counts as
buildable when an item placing it comes out of an enabled recipe; ore and
water, which no recipe makes, are never locked).

**What has research added up to?** The companion mod (0.7.0+) exports the
force's own bonuses: inserter and bulk inserter capacity, belt stack size,
mining and lab productivity, robot bonuses, combat modifiers, and per-recipe
productivity. They are read from the force rather than summed from
technologies, because some do not come from research -- in 2.0 the bulk
inserter technology itself adds to bulk inserter capacity.

Reading these from the save file itself was considered and set aside: past the
mod list in its header, a save is the engine's internal serialisation, which
changes between versions. The game is the parser; the companion mod asks it.

## Where the numbers are used

| Bonus | Used by |
|---|---|
| `inserter_stack_size_bonus`, `bulk_inserter_capacity_bonus` + a prototype's `stack_size_bonus` | inserter hand size, and so inserters needed per machine (`layout.inserter_hand_size`) |
| `recipe_productivity` | the bill of materials: less of every ingredient per product, capped at the recipe's `maximum_productivity` |
| unlocked recipes | which machine the bill picks (the fastest the player can build; see [Which machine](#which-machine)), which tiers `review` fills in, what `find` marks as locked |

An export is used only when its mod set matches the active profile (the
companion mod itself aside), and every tool says when it is not.

## The request file

```json
{
  "said": "нужен блок на 2 батареи в секунду, плиты и кислота приходят поездом",
  "targets": [{"item": "battery", "per_second": 2}],
  "boundary": ["iron-plate", "copper-plate", "sulfuric-acid"],
  "tiers": {"belt": "fast-transport-belt"},
  "machine_choices": {"chemistry": "chemical-plant"},
  "recipe_choices": {},
  "effects": {"*": {"speed": 0, "productivity": 0, "consumption": 0}},
  "plot": {"width": 120, "height": 40},
  "style": ["mirrored rows, like the copper block"],
  "surface": "nauvis"
}
```

The file may be saved with or without a UTF-8 byte order mark (Windows
PowerShell 5.1 `Set-Content -Encoding utf8` writes one); the same goes for a
plan and for a blueprint string saved to a file.

`factorio-forge review request.json` answers with:

- **problems**: unknown names with suggestions, locked targets, tiers and
  machines;
- **questions** for the player: the plot when missing, what arrives from
  outside when nothing does, tiers and machines when there is no export, the
  surface when the mod set has several, each thing the bill needs that the
  surface cannot supply, with where it can be had, and each machine that
  needs more than power to run;
- **assumptions**: tiers filled in from what is unlocked, the machine picked
  per category (with an export), researched productivity applied;
- the **bill of materials**.

## Which machine

Several machines can often run the same recipe, and the bill has to pick one
per crafting category before it can count them.

- **With an export**: the fastest machine the player can build. That is what
  they would reach for, and the export says what they have.
- **Without one**: the slowest -- the most basic. The fastest in the mod set
  is usually late-game: a clean install with Space Age and no export got a
  red-circuit line of foundries, electromagnetic plants and a biochamber for
  oil cracking, none of which a starting base owns. The basic machine errs
  towards more machines rather than ones the player lacks, and the review asks
  about the faster ones by name, speed and what each needs, so the answer
  goes into `machine_choices`.
- Either way, a tie goes to the machine that needs least besides power, and
  an entry in `machine_choices` (by crafting category) wins outright.

**What a machine needs besides power** (`bom.running_needs`, in the bill as
`machine_needs`) is read from the prototype, never from the name: a burner
`energy_source` and its `fuel_categories` with the items that burn there
(the biochamber's nutrients), spent fuel to take out (`burnt_inventory_size`),
a fluid or heat energy source, `surface_conditions` while the surface is
open, and `heating_energy` on a surface whose planet has
`entities_require_heating`. Every such machine the bill uses, picked or
pinned, becomes a question -- rows lay out power poles and nothing else, so
fuel or heat has to be planned by other means -- and a pinned machine whose
surface conditions fail on the chosen surface is a problem.

## Surfaces

What is free depends on where the block stands. A surface offers what is
*placed* there (its `map_gen_settings.autoplace_settings`: resources and
tiles) and what a machine that *works* there can take from it: a drill whose
resource categories reach the resource, an offshore pump on a tile with a
fluid, an asteroid collector for chunks, a boiler for steam once its water is
offered. "Works" is the prototype's `surface_conditions` against the
surface's `surface_properties`, a property left unstated taking its
`default_value`; the same check decides which recipes and machines the bill
may use.

On a surface the bill of materials still prefers local routes, but an item
the surface cannot supply is not simply free: each gets a column at a price
far above any recipe, so it is brought in only when nothing local stands in
(on Nauvis, casting iron with imported calcite no longer beats smelting ore).
What is brought in anyway is listed as `from_elsewhere`, with the surfaces it
is free on and those it can be made on, and becomes a question.

Items that no surface places and no recipe makes -- wood, fish, Krastorio 2's
sand -- stay free: the data does not say where they come from. Gleba's plants
are not extracted yet, so fruit and spoilage count as not offered anywhere.

## Commands

| Command | |
|---|---|
| `find <words> [--kind item]` | player's words to prototype names |
| `available [role] [--all]` | belts, inserters, long inserters, poles the player has, and bonuses |
| `review request.json [--json]` | step 1 check and step 2 bill |
