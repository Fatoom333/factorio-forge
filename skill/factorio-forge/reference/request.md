# Pinning down the request

Read when: turning the player's words into a request and reviewing it
(steps 1-2).

The player's words are yours to read; the toolkit checks your reading.

## Names

**Turn every thing they name into a prototype name**, never from memory --
mods rename things (a mod may give red science another name altogether):

```
factorio-forge find красные колбы
factorio-forge find --kind item автоматизационный исследовательский пакет
```

Pass the player's words as they said them. It tries three ways at once and
says under each candidate why it was found:

- **slang** from `src/factorio_forge/slang.py` -- "красные колбы", "синие
  схемы", "мазут", "качалка", "green belt", "prod modules" and about a hundred
  more, in Russian and English;
- **the name shown in this mod set**;
- **the base game's name**, when a mod renamed the thing: "matched the base
  game's name «…»; this mod set shows it as «…»". Tell the player the name
  their game uses.

A vague phrase ("колбы", "ленты", "ассемблеры") lists every option with
"which …?" -- ask. A phrase with a note (like "аккумуляторы": players mean the
accumulator block, the base game's Russian «Аккумулятор» is the battery) is
ambiguous -- ask. "known slang for X, which this mod set does not have" means
the thing is gone (Factorio 2.0 removed the rocket control unit), not that
the player misspoke.

When `find` has nothing and you know what the player meant, search by the
internal name, and **add the phrase to `slang.py`** (a phrase list, the base
game's internal names, a note only if it is ambiguous) so the next request
does not have to work it out again. The test suite checks every name there
against the game's own files.

## The request file

Only `targets` is required:

```json
{
  "said": "the player's words, verbatim",
  "targets": [{"item": "battery", "per_second": 2}],
  "boundary": ["iron-plate", "copper-plate", "sulfuric-acid"],
  "tiers": {"belt": "fast-transport-belt", "inserter": "fast-inserter",
            "long_inserter": "long-handed-inserter", "pole": "medium-electric-pole"},
  "machine_choices": {"chemistry": "chemical-plant"},
  "recipe_choices": {},
  "effects": {"*": {"speed": 0, "productivity": 0, "consumption": 0}},
  "plot": {"width": 120, "height": 40},
  "style": ["mirrored rows like their copper block"],
  "surface": "nauvis"
}
```

- **targets**: rate `per_second` or `per_minute`. "Одна полная лента" is a rate:
  convert with `available belt`.
- **boundary**: what arrives from outside (train, bus, another block) and is
  not made here. In a city block usually plates and fluids.
- **tiers**: leave out what the player did not say; the review fills in the
  best they have unlocked, and lists it.
- **machine_choices** (by crafting category), **recipe_choices** (by item):
  only what the player asked for.
- **effects**: modules and beacons per crafting category, as bonus fractions.
- **plot**: the space and its shape; give the outline of a non-rectangular
  plot, not its bounding box.
- **style**: what they said about looks, in their words.
- **surface**: the planet or platform it is built on. It decides what is free
  to mine, pump or collect there and which recipes and machines work (Space
  Age surface conditions). With one surface in the mod set it is assumed;
  with several and none given, the review asks, and until then anything
  minable anywhere counts as free.

## Review

```
factorio-forge review work/<design>/request.json
```

It reports three lists and the bill of materials:

- **problems** -- unknown names (with suggestions), locked targets, tiers or
  machines. Fix them, usually by asking.
- **ask the player** -- what only they can answer: missing plot, what arrives
  from outside when nothing does, tiers when there is no export, the surface
  when there are several, and anything the bill needs that this surface does
  not have ("calcite cannot be had on nauvis (free on vulcanus)") -- the
  answer is usually "it comes by rocket", which goes into `boundary`.
  Without an export, also **which machine** per category: the bill assumed
  the most basic one and lists the faster ones with their speed and needs;
  put the answer in `machine_choices`. And any machine that **needs more than
  power** ("biochamber needs more than power: burns nutrients fuel") --
  rows do not feed fuel or heat, so either the player supplies it (plan it
  yourself) or another machine is pinned.
- **assumed** -- tiers filled in, the machine picked per category (with an
  export: the fastest they can build), researched productivity applied. Say
  these to the player in one short list; do not make them confirm each.

Ask everything that is missing in **one message**, with a sensible default
beside each question, so the player can answer "yes" or correct one line. Do
not ask what the tools can compute. When the review has no problems and the
answers are in, the bill of materials it printed is the machine count.

## How many machines

`review` already ran it. In Python, for more:

```python
from factorio_forge import bom, environment, request
found, why = environment.for_active_profile()
spec = request.load("work/<design>/request.json")
result = request.review(spec)       # result.bill is a bom.BillOfMaterials
for line in result.bill.lines:
    print(line.recipe, line.machine, line.machines, line.inputs, line.outputs)
```
