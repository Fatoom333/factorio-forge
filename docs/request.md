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
| unlocked recipes | which machine the bill picks (the fastest the player can build), which tiers `review` fills in, what `find` marks as locked |

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
  "style": ["mirrored rows, like the copper block"]
}
```

`factorio-forge review request.json` answers with:

- **problems**: unknown names with suggestions, locked targets, tiers and
  machines;
- **questions** for the player: the plot when missing, what arrives from
  outside when nothing does, tiers when there is no export;
- **assumptions**: tiers filled in from what is unlocked, the machine picked
  per category, researched productivity applied;
- the **bill of materials**.

## Commands

| Command | |
|---|---|
| `find <words> [--kind item]` | player's words to prototype names |
| `available [role] [--all]` | belts, inserters, long inserters, poles the player has, and bonuses |
| `review request.json [--json]` | step 1 check and step 2 bill |
