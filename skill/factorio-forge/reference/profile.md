# Profile, notes and the game's export

Read when: starting a session (step 0), the player corrects a design or states
a standing preference, or tiers and unlocks are in question.

## Which game you are designing for

```
factorio-forge list-profiles        # which save profiles exist, which is active
factorio-forge activate-profile NAME
factorio-forge notes [NAME]         # the index of the profile's notes (one line per file or section)
factorio-forge notes [NAME] TOPIC   # the notes on one topic or item, in any words
factorio-forge available            # belts, inserters, poles the player has; bonuses
factorio-forge show-style NAME      # measured spacing, alignment, symmetry, orientation
```

Every number comes from the active profile. A blueprint designed against the
wrong profile is wrong in ways nothing will flag. If the player's save has no
profile yet: `factorio-forge create-profile <save>`.

## Notes

Every profile has notes: a `notes.md` and, as they grow, topic files in
`notes/`. At the start read only the index (`notes`) and open a topic when the
request touches it (`notes красные схемы` finds a note tagged
`advanced-circuit`). They hold the base's accumulated preferences in the
player's own words -- conventions to follow, things to avoid, corrections made
to earlier designs. What the notes say beats the generic defaults in this
skill. When the player corrects a design, or states a standing preference
about their base, append it to the notes (one dated line, in their words), not
to this skill: the skill is for every player, the notes are for this base only.

`factorio-forge notes [NAME]` prints where the notes are, with the profile's
folder and its `reference/` folder if there is one: the player's own
blueprints the notes were measured from. A note tagged with a `reference/`
file holds only for that blueprint; when the player gives a new version,
replace the file and re-measure every note that names it.

Notes index: `notes` shows an index, one line per file or section (what
it covers, which items and blocks, by internal name); `notes <topic>` opens
one. Read the index at step 0 and open only the sections the request touches.

`factorio-forge notes` -- the index of the active profile; `notes NAME` -- another
profile's; `notes [NAME] TOPIC` -- the matching notes, where TOPIC is a file name,
a heading, a tag or an item in any words the name lookup knows. A note file may
start with a `tags:` line (or front matter) naming the items it is about; the
index is rebuilt only when a note file changes.

## The game's export (what is unlocked)

What the player has *unlocked*, and the bonuses research has given them
(inserter hand size, recipe productivity, mining productivity), only the
running game knows. The companion mod (0.7.0 or later) exports it: the player
runs `/forge-export` in that save, or presses Export in its window. The tools
use the export only when its mod set matches the active profile, and say when
it does not: which mods differ, which export it was (tick, play time, when the
file was written) and what to do -- `/forge-export` in the profile's save, and
`create-profile --force` if its mods or startup settings changed. Pass that on
to the player rather than guessing around it. Without it, ask about tiers
instead of guessing, and treat inserter hands as 1.
