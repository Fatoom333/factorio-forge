"""factorio-forge: generate Factorio blueprints from a description.

The package is deliberately layered. ``paths`` resolves machine-specific
locations and is the only module that touches them. Everything above it works
with a profile: a description of one of the player's saves, its mod set, the
game data extracted for it, and the building style of that particular base.
"""

__version__ = "0.1.0"
