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

### 0.1 Where the three systems stand (16 Nov 2026)

Marc's wording on the day: a harness for linked commands, a small decision model
for quick changes of decision, and the 27B to run the harness and manage
long-horizon tasks. Same three systems as the table above.

| System | State on 16 Nov |
|---|---|
| 1: skills + chains | Mostly built. Skills in `jedai/harness/skills/`, strung together by `jedai/harness/chain.py`, run with `scripts/go.py` (`travel`, `find`, `mission`, `deliver`, `abort`, `recover`). |
| 2: small decision model | Not built. Its job is done by hard-coded rules in `chain.py` (retry the waypoint clean-up four times, prefer a place we know over `/find`, stop after four lost reads). Those rules are kept in the chain, not buried in skills, so they can be handed over. `system2.py` is still only the badge list. |
| 3: the 27B (llama-server on the 5070 Ti) | Not built. Nothing calls the model yet. |

**The tool list the 27B will call** (proposed, not yet agreed): what `go.py`
already exposes (`travel`, `find`, `deliver`, `abort`, `recover`), plus a manual
mode for escalations: look at the screen, click, press a key, done, give up.

**Escalations (built 16 Nov).** When the scripts can't fix something, the run
stops and `Chain.escalate()` leaves a request in
`data/live/escalations/<time>_<reason>/`: `screen.jpg`, and `request.json` with
the reason, a plain-language task, the last steps and `status: open`. One line
per request goes in `escalations/index.jsonl`. First user: the waypoint
clean-up (`waypoints_not_cleaned`). Nothing reads these yet; until the operator
exists an escalation means the run waits for Marc.

**The operator loop, when it's built:** read an open request, send the
screenshot and task to the model, get one action back, do it through the same
key controller, take a new screenshot, repeat until done or a step limit.

**Open question.** The rule above says System 3 never touches keys and a
failure goes up one level only. A 27B fixing the datapad by hand breaks both.
Either the manual mode is an agreed exception for escalations, or the fixing is
done by a separate vision operator that System 3 only asks. Not decided.

Still needed before building it: the llama-server address and port, the exact
model, and whether its vision projector is loaded.

---

## 1. Versions

### 1.0 Outdoor navigation (done, tag v1.0)
D* Lite over 5 m cells, trilateration of the waypoint, stuck recovery, run
recorder.

### 1.1 Navmesh verifier + data capture (in progress)

**Project rule: what the bot may know at runtime.** Anything a player could
know (starport and badge locations, quest steps, NPC names: wiki knowledge) is
fine. Server internals more precise than a player can see (spawn tables, enemy
positions, live server telemetry) are not.

**The navmesh is the exception (Marc, 8 Nov 2026).** The navmesh-free bot walled
itself in too often and cost too much time, so the navmesh is now the base map
at runtime (`planner.navmesh_prior: true`): unexplored cells are priced from it,
and the bot's own map changes only where it actually gets stuck (2 m cells, a
first bump makes a cell "suspect", a second makes it a wall). It covers cities
and points of interest only; open ground is still learned by bumping.
The navmesh-free variant stays behind the same switch for comparison, and
replacing the navmesh with the bot's own vision (1.2) is still the long-term aim.

- [x] Parse Core3 navmeshes for all planets (`jedai/navigation/navmesh.py`).
- [x] Navmesh-prior variant behind the switch (on by default since 8 Nov).
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
- [x] **Harness v0 (6 Nov 2026):** world state (SQLite), skill interface, skill log,
      rule-based badge collector (System 2 v0), supervisor. `python scripts/run_harness.py`.
      Travel points + badge areas in `jedai/data/world_points.json`.
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

**Agreed 10 Nov 2026 (replaces the earlier order):** stay inside cities and
avoid combat until the very end.

1. Die and recover: detect death, clone, exit the cloning facility (first
   indoor, multi-level navigation).
2. City skills: mission, bazaar, bank and shuttleport/starport terminals
   (planet travel, 1.5), city points of interest. One shared window module.
3. Every in-game command becomes a macro key or a harness tool call, as met.
4. Test the client chat log (`/logChat`): stays on, and what it records.
5. Combat last, together with the LLMs (fast decision model, slow strategy model).

Exploration badges outside cities (3.1) wait until the bot can survive there.
See `docs/HANDOFF.md` for the current state.
