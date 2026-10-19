# The text tile map

Read when: you want to look at a blueprint while iterating.

While iterating, look at a text tile map; render once, at the end.
`factorio-forge map work/<design>/<label>.txt` prints the blueprint one
character per tile, with coordinates and a legend: belt arrows, inserter
arrows pointing where they drop, `U`/`u` underground entrance and exit,
anything larger than a tile (machines, splitters `Ss`, big poles) as a
capital at the centre of a lower-case footprint (a splitter's direction is
not drawn), `?` for what the data does not know, `!` for overlaps
(`--index N` for a book entry). Each drawing costs a screenshot; one render of
the final version is enough.
