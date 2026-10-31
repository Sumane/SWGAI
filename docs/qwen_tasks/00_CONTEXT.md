# Context for every task (read first)

You are working on JedAI: a bot that plays Star Wars Galaxies Emulator (SWGEmu)
through the screen and keyboard, like a player. Repo root: `~/dev/SWGAI`.

## Architecture you are building towards
- **System 1 (skills):** functions that do one thing in-game (`navigate_to`,
  `mount`). Each returns a `Result` (ok / failed / stuck).
- **System 2 (decisions):** picks the next skill from the world state.
- **System 3 (LLM council):** sets long-term objectives. Not part of these tasks.
- **Harness:** runs it all, logs everything. Folder: `jedai/harness/`.

## Hard rules
1. **Only touch the files your task lists.** If you think another file needs
   changing, write that in your final message instead of changing it.
2. **No new dependencies.** Python 3.13 standard library + what's already used
   (numpy, pyyaml, pytest). SQLite via `sqlite3`.
3. **Tests run without the game.** No screen capture, no key presses in tests.
   Use fakes (small classes written in the test file).
4. **All tests must pass:** `python -m pytest -q tests`. Don't edit or delete
   existing tests to make them pass.
5. **Never hard-code paths.** Use `jedai.config.settings`:
   - `settings.data_path("harness", "x.db")` → a path inside `data/` (git-ignored)
   - `settings.raw["harness"]["some_key"]` for new settings (your task says which)
   - `settings.planet` → current planet name, e.g. `"naboo"`
6. **Runtime knowledge rule:** the bot may use anything a player could know
   (wiki knowledge: starport and badge locations, quest steps). It may NOT use
   server internals (navmesh geometry, spawn tables, enemy positions).
7. Type hints on public functions, a docstring on each module saying what it is for.

## Coordinates (important, easy to get wrong)
SWG's ground plane is **X (east) / Z (north)**. In Core3's Lua files the
north coordinate is called `y` and the height is called `z`. In this codebase:
- `x` = east, `z` = north, `height` = up.
- So Lua `y` → our `z`, Lua `z` → our `height`.

## Existing code you may call (don't modify it)
- `jedai.navigation.runner.NavigationRunner(settings)`:
  `.navigate(target_name) -> str` drives to the in-game waypoint with that name
  and returns `"arrived"`, `"no_waypoint"`, `"stopped"`, another stop reason,
  or `"crashed: ..."`. `.keys` is the key client, `.read_position()` returns
  `(x, z)` or `None`, `.mounted` is a bool, `.dismount()` gets off the vehicle.
- `runner.keys.type_text(text)` types into the game (chat commands like
  `/waypoint ...`). `runner.keys.press(key)`, `runner.keys.tap(key, secs)`.
- `jedai.control.keys.kill_pressed(settings.kill_key, settings)` → True when
  the user pressed the kill key.

## Style
Plain, readable code over clever code. Small functions. Comments say *why*.
