# Build and what it prints

Read when: running `build`, or when a line of its output is unclear.

```
factorio-forge build work/<design>/plan.json
```

Writes, beside the plan, `<label>.txt` (blueprint string), `<label>.html`
(drawing with findings marked) and `<label>.report.json`.

## Reading the output

The output has two levels (core, "Reading the build output"): **errors** --
the build will not stand or will not work -- and **facts** -- rates, belt
loads, lane fill, entity counts. Errors are fixed before anything is handed
over; facts are for you to weigh.

> FIELD NAMES AND PRINTED FORM TO FILL AFTER MERGE (the two-level output).

What the output contains:

- **findings** with severity `problem` must be fixed before handing anything
  over: overlaps, unpowered machines, `fluids-mixed`,
  `fluid-box-unconnected`, `power-split`;
- **rows**: machines against capacity;
- **ports**: where each belt and pipe enters or leaves, its lane contents
  (left, right in the direction of travel) and rate -- this is what you
  connect blocks with. Rates are at full machine load; with the plan's
  `"request"` set (plans.md) ports read `5/s planned (6/s at full load)`.
  In ports also say what `arrives`, lane by lane, once connected;
- **notes**: overfed rows, extra inserters, gap columns for poles (said as
  built: which end columns hold a pole, how many between machines, and why).

Port indices (`[j]` under each block) are what `connections` refer to
(routes.md).

## Looking at the result

Look at the drawing (render the HTML in a browser, or publish it) and ask
whether a player would build it this way. Revise the plan, not the output.
While iterating, use the text tile map (map.md) and render once at the end.

## Two strikes

When the same spot fails twice, do not make a third fix of the same kind:
redesign that spot -- another layout, another route, another arrangement of
the blocks. "The same spot" is the same place in the drawing (the same few
tiles, the same port or the same connection) or the same finding repeating
after you changed something around it.
