# Grid-snapped sections (walls, lines of modules)

Read when: the player wants a book of sections to place side by side, to
deploy quickly.

- Blueprint keys `snap-to-grid: {x, y}` and `absolute-snapping: true`; the grid
  cell is (0,0)..(x,y) in blueprint coordinates (confirmed in game).
- Draw every section for one side only (e.g. outside to the north) and let the
  player turn it with R. Rotation keeps a clockwise belt ring clockwise;
  mirroring reverses it -- say "rotate, do not flip".
- Make the cell a whole number of modules and of any repeating pattern's
  period along the line, so modules and pattern continue across joints.
- A corner meets two differently turned neighbours: anything that runs across
  the joints (a repeating pattern, belts, a pipe chain) must continue both. Split a
  pattern along the diagonal: one side's pattern above it, the other side's
  below. A corner drawn for one position fails in the other three.
- Check by assembling a test ring from turned copies, not each section alone:
  belts closed and each feeder reaching its ring, one pipe network with only
  the intended inlet, power, roboport coverage, the pattern carried a few tiles
  into each neighbour. Check power by the blueprints' wires, not by distance.
- Poles built from a blueprint do **not** connect to neighbours by themselves,
  even within reach (seen in game), and a blueprint cannot wire to another
  blueprint. Put one pole straddling each joint in *both* neighbouring
  sections at the same spot, wired inside each section: the second paste
  lands on the first one's pole and its wires attach to it.
