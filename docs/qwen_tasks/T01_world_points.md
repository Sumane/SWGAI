# T01: travel points + badge locations → JSON

## GOAL
Turn Core3's public planet data into a JSON file the bot can use: every
starport/shuttleport and every exploration badge area, per planet.

## SOURCE
Core3 is on GitHub. Download this file (it's ~60 KB):
```bash
mkdir -p ~/dev/SWGAI/data/core3
curl -L -o ~/dev/SWGAI/data/core3/planet_manager.lua \
  https://raw.githubusercontent.com/swgemu/Core3/unstable/MMOCoreORB/bin/scripts/managers/planet/planet_manager.lua
```
Inside, each planet is a block like:
```lua
corellia = {
	planetTravelPoints = {
		{name = "Coronet Starport", x = -66.760902, z = 28, y = -4711.3281, interplanetaryTravelAllowed = 1, incomingTravelAllowed = 1, landingRange = 6},
		...
	},
	badgeAreas = {
		{"badge_bela_vistal_fountain",6767,-5617,0,30,81},   -- {key, x, y, z, radius, badge_id}
		...
	},
	...
},
```
Read the file to confirm the planet list and the exact layout before writing the parser.

## FILES TO WRITE
- `scripts/extract_world_points.py`: `python scripts/extract_world_points.py [--src PATH] [--out PATH]`.
  Defaults: `--src data/core3/planet_manager.lua`, `--out jedai/data/world_points.json`.
- `jedai/data/world_points.json`: the generated output (commit it: it's small and public).
- `jedai/data/__init__.py`: empty.
- `tests/test_world_points.py`

## FORMAT
```json
{
  "corellia": {
    "travel": [{"name": "Coronet Starport", "x": -66.76, "z": -4711.33, "height": 28.0,
                "kind": "starport", "interplanetary": true}],
    "badges": [{"key": "badge_bela_vistal_fountain", "badge_id": 81,
                "x": 6767.0, "z": -5617.0, "radius": 30.0}]
  }
}
```
- Lua `x` → `x`, Lua `y` → `z`, Lua `z` → `height` (see 00_CONTEXT.md).
- `kind` = `"starport"` if `interplanetaryTravelAllowed == 1`, else `"shuttleport"`.
- Round coordinates to 2 decimals. Planets in alphabetical order.
- Skip planets with no travel points and no badges (test/tutorial zones).

Also add `load_world_points() -> dict` in `jedai/data/__init__.py` that reads
the JSON next to it (use `pathlib.Path(__file__).with_name(...)`).

## DONE WHEN
`python -m pytest -q tests` passes, including new tests that check:
- `corellia` has "Coronet Starport" at x=-66.76, z=-4711.33, kind "starport", interplanetary true
- `corellia` has "Coronet Shuttle A", kind "shuttleport"
- `corellia` has badge "badge_bela_vistal_fountain", x=6767, z=-5617, badge_id 81, radius 30
- naboo, tatooine, corellia, talus, rori, dantooine, lok, yavin4, endor, dathomir are all present,
  each with at least one starport
- every badge has a positive radius and an integer badge_id
- the script is re-runnable: running it twice gives an identical JSON file

## DO NOT
Touch any file not listed above.
