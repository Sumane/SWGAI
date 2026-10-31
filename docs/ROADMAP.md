# JedAI roadmap

Goal: a bot that plays SWGEmu from a fresh character to Jedi on Marc's own Core3
server, and gets better by training on its own data.

Built in versions. Each version is a working bot. Nothing gets merged unless it
beats the previous version on the benchmarks in section 6.

---

## 0. The three systems

| | System 1: reflexes | System 2: decisions | System 3: deliberation |
|---|---|---|---|
| What | Skills: `navigate_to`, `use_terminal`, `talk_to`, `fight`, `loot`, `travel_to_planet` | Picks the next skill from the current world state ("jev") | Qwen 3.8 27B council of experts: long-term objectives, votes on what to work on next |
| Speed | 5–20 Hz | ~1 Hz, or on events | Minutes, async |
| How | Code + small vision models + macros | Utility scoring / behaviour tree first; learned policy later | LLM prompts over a written world state; never touches keys |
| Output | Key/mouse input, success or failure + reason | `(skill, args)` | Ordered objective list + current objective |

The rule between them: System 3 writes goals, System 2 writes skill calls,
System 1 writes keys. Nothing skips a level. Every skill returns a result:
`ok`, `failed(reason)` or `stuck`. A failure goes up one level, never straight
to the LLM.

---

## 1. Versions

### 1.0 Outdoor navigation (done, tag v1.0)
D* Lite over 5 m cells, trilateration of the waypoint, stuck recovery, run
recorder.

### 1.1 Navmesh verifier + data capture (in progress)

**Project rule: what the bot may know at runtime.** Anything a player could
know (starport and badge locations, quest steps, NPC names: wiki knowledge) is
fine. Server internals more precise than a player can see (navmesh geometry,
spawn tables, enemy positions) are not. The navmesh is a **teacher**: it grades
the vision model offline and never steers the default bot. The bot builds its
own map from what it sees and bumps into.

Two variants, one switch (`planner.navmesh_prior`, or `run_navigator.py --navmesh-prior`):
- **navmesh-free** (default, the real bot).
- **navmesh prior** (comparison only): unexplored cells are priced from the
  navmesh. Observed cells always win. Use `--fresh-map` for fair A/B runs,
  because both variants write to the same grid map.

- [x] Parse Core3 navmeshes for all planets (`jedai/navigation/navmesh.py`).
- [x] Navmesh-prior variant behind the switch, off by default.
- [ ] Dataset logger: first-person frame (HUD hidden) + pose + heading at 2 Hz,
      on by default during runs.
- [ ] Benchmark: 20 Theed runs, before vs after. Measure success, time, stuck count.

### 1.2 Vision obstacles
- Depth Anything V2 → occupancy fan (`depth_grid.py`), scored against navmesh cells.
- Labels come free: navmesh cells (in cities) + cells the bot actually got stuck in
  (everywhere). Train a small head on DA/DINOv2 features if raw depth isn't good enough.
- Predicted cells are a cost in D*, not walls. Observed cells win.

### 1.5 Planet travel (next big one after 1.1)
- Starport and shuttleport positions come from Core3 scripts (planet manager travel
  points), so no hand mapping.
- New System 1 skills: `use_terminal` (click NPC/terminal, read the list with OCR,
  pick a destination, confirm), `board_shuttle`, `wait_for_zone_load`, `detect_planet`.
- A GUI-interaction module shared by everything later: find a window, read text,
  click a row. Template matching + OCR, no learning needed at first.
- Done when: "go to Mos Eisley cantina" from Theed works unattended.

### 2.0 Confined areas (caves, buildings, dungeons)
- Inside buildings `/loc` gives coordinates relative to the cell. Track
  `(building, cell, x, y, z)`, not world x/z.
- Ground truth: Core3 builds interior floor meshes from the client's building
  files (.pob). Extract them the same way as the outdoor navmeshes.
- Planner: a graph of portals between cells, then D* or A* inside each cell.
  Multiple levels = separate cells joined by stairs/elevator portals.
- Practice order: a starport interior → a cantina → an easy cave → the Warren.

### 3.x System 1: the Jedi skill set
Requirements from Core3's `glowing.lua` (Village progression):
3 Jedi exploration, 2 dangerous exploration, 5 easy exploration,
1 profession master, 5 content badges.
- 3.1 Exploration badges. Pure navigation + travel. 10 of the 15 badges.
- 3.2 Combat via macros (Marc's): pull, fight, heal, retreat. A learned policy later.
- 3.3 Conversation skill: read the NPC dialogue, choose from per-NPC scripts.
  Dialogue trees come from Core3's convo scripts.
- 3.4 Inventory/datapad: loot, check quest items, use an item on a target.
- 3.5 Profession grinding to one master.
- 3.6 Content badges: the Warren (2 badges), then the easiest 3 other content badges.
- 3.7 Village of Aurilia phases.

### 4.x System 2
- 4.1 World state blackboard: one SQLite store everything writes to (position,
  planet, HP, inventory, badges, current objective, last failures).
- 4.2 Rule-based "jev": each skill has preconditions + a utility score; pick the best.
- 4.3 Learned "jev": trained on logged (state, chosen skill, outcome) from 4.2 + Marc's play.

### 5.x System 3
- 5.1 Objective tree: Jedi at the root, then badges/professions, then tasks.
  Stored as YAML; System 3 edits it, never the code.
- 5.2 Council: 3–5 Qwen personas (planner, risk, efficiency, critic) argue in turn,
  then vote. Runs every N minutes or on a big event (death, objective done,
  repeated failure).
- 5.3 Failure review: Qwen reads run recordings and writes up what went wrong and
  what to try next. These are suggestions for Marc/Claude, not auto-applied.

### 6.x Harness
- 6.1 One process supervisor: game, System 1 loop, System 2, System 3, recorder, kill switch.
- 6.2 Rehearsal mode on Marc's server: GM teleport to the start of a test, reset,
  repeat. Real runs never use GM commands.
- 6.3 Overnight unattended runs with summaries in the morning.

---

## 2. Self-improvement loop

```
run → recorder (frames, pose, keys, events, outcomes)
    → auto-labelling (navmesh cells, stuck cells, success/fail per skill)
    → nightly training job (vision head, later jev)
    → benchmark gate: replay set + fixed in-game test routes
    → promote only if better, otherwise keep the old model and log why
```

Guardrails:
- A model is only promoted through the benchmark gate. Never by itself.
- The training data keeps labels from real outcomes separate from the model's
  own predictions. Training on its own predictions makes it confidently wrong.
- Code changes stay human-reviewed. The bot improves its models and its
  objective list, not its own source.

Marc's data collection at first:
- Drive with the recorder on (navigation + vision data).
- Play sessions with key logging (System 1 combat/conversation examples).
- Mark failures by hand in the run report (one key: "this was a bad move").

---

## 3. Work split

| Who | What |
|---|---|
| Marc | In-game testing, data collection, combat macros, server/admin, decisions |
| Claude | Architecture, interfaces between systems, hard debugging (nav, vision), reviewing Qwen's work |
| Local Qwen | Chunky, well-specified, testable jobs (see below) |

Good Qwen jobs (clear input, clear output, tests decide):
- Extract data from Core3 Lua: travel points, dialogue trees, badge locations,
  dungeon spawn lists → JSON.
- Write the dataset logger and labelling scripts against a given interface.
- Write unit tests and sim scenarios for a given module.
- Summarise overnight run logs into a failure list.
- Draft System 3 persona prompts.

Qwen task template (paste this, fill it in):
```
GOAL: one sentence
FILES TO READ: ...
FILES TO WRITE: ...
INTERFACE: function signatures / data format exactly
DONE WHEN: these tests pass: ...
DO NOT: touch other files, change existing interfaces
```
Claude reviews the diff before it's merged.

---

## 4. Order

1.1 finish → 1.5 planet travel → 3.1 exploration badges (first real Jedi progress)
→ 4.1/4.2 simple System 2 → 1.2 vision → 2.0 confined areas → 3.2–3.6 → 5.x → 6.3.
