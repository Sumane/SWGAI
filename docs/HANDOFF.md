# JedAI handoff (10 Nov 2026)

Paste this into a new project/chat to carry on. It replaces the long chat history.
Fuller background: `docs/ROADMAP.md`, `docs/REORGANISATION.md`, `README.md`.

## 1. What this is

JedAI: a bot that plays SWGEmu (Star Wars Galaxies emulator) from a fresh
character to Jedi, by looking at the screen and pressing keys like a player.

- Repo: `SWGAI` (GitHub `Sumane/SWGAI`). On Marc's PC: `~/dev/SWGAI`.
- Marc's PC (`marc-Agentic`, Ubuntu): game client on Xwayland display `:2`,
  2560x1440. Python venv `~/venvs/jedai`. GPUs: RTX 3060 (bot vision,
  `harness.cuda_visible_devices: "0"`) and RTX 5070 Ti (local Qwen).
- Game server: Marc's own Core3 on MAGNUS (192.168.0.40).
- Game install: `/run/media/marc/New Volume/Swgemu` (external drive, not always mounted).
- Map database: `~/jedai-data/jedai_nav_v2.db` (set in `config/settings.local.yaml`).

## 2. Rules Marc has set

- **Runtime knowledge:** only what a player could know (wiki-level: starports,
  badge locations, quest steps). No server telemetry, spawn tables or enemy
  positions. **Exception (8 Nov): the navmesh is allowed as the base map.**
- **Stay in cities for now. Avoid combat completely.** The character has no
  combat skills and the bot has no combat logic. Combat is the last thing to
  build, together with the LLMs (fast decision model, slow strategy model).
- Every in-game command becomes a **macro key or a harness tool call** as we
  meet it. No blind typing into chat.
- The bot doesn't stop on failure: failures are data. Anything unexpected is
  ESC'd out of, logged as an anomaly with screenshots, and reviewed later (Qwen).
- Store the speeder after every dismount. Mount = F1, `/tar speederbike swoop`, `/mount`.
- Walk when the target is close; mount only beyond 150 m.
- Git: never rewrite the authorship of Marc's older commits. Weights stay out
  of git. Never handle Marc's passwords/tokens; don't read `.env` files.
- Marc prefers blunt, technical answers and will push back on generic ones.

## 3. How the code is laid out

| Path | What |
|---|---|
| `jedai/navigation/` | `runner.py` (the run loop, mount, chat, layers), `navigator.py` (steering, stuck handling, marking), `dstar_lite.py` (planner), `grid_db.py` (SQLite cell map), `navmesh.py` (Core3 navmesh reader), `recorder.py` (per-run logs) |
| `jedai/perception/` | `hud.py` (coords/waypoint OCR + YOLO), `chat.py` (chat OCR, death/combat words), `chatlog.py` (client chat log file), `tiles.py` (readable screenshot tiles) |
| `jedai/control/` | key controller process + client (`keys.py`: press, tap, click, combo, chat) |
| `jedai/harness/` | `state.py` (world state, SQLite), `skill.py`, `log.py`, `system2.py` (badge collector), `supervisor.py`, `skills/navigation.py`, `skills/datapad.py` (delete waypoints) |
| `jedai/data/world_points.json` | 45 badges + travel points (from Core3 scripts) |
| `config/settings.yaml` | every tunable; `settings.local.yaml` = Marc's machine overrides |
| `scripts/` | `harness_live.py` (the one to run), `run_harness.py`, `clean_waypoints.py`, `capture_session.py`, review tools |
| `tests/` | 150+ tests incl. a driving simulator (`test_simulated_drive.py`) |
| `docs/qwen_tasks/` | job specs for local Qwen; T07 = review anomalies |

## 4. How to run

```bash
cd ~/dev/SWGAI && source ~/venvs/jedai/bin/activate
python scripts/harness_live.py          # controller + harness loop; F8 or Ctrl+C stops
python scripts/run_harness.py --list    # badges and what's earned
python scripts/clean_waypoints.py --dry-run
python -m pytest -q tests               # ~2.5 min
```

Live output: `data/live/` (`harness_out.log`, `screen.jpg`, `tiles/`,
`anomalies/`, `datapad/`). Per-run recordings: `data/debug_logs/<time>_<target>/`
(`console.log`, `events.jsonl`, `ticks.jsonl`, `summary.txt`, `report.html`).
Control files in `data/live/`: `PAUSE`, `STOP`, `SET_MOUNTED`, `RESET_BADGES`.

## 5. Where things stand

Working in-game:
- Outdoor navigation. 8 Nov: 4,176 m from a walled-in corner of Theed to 30 m
  from the badge in 390 s (6 stucks).
- Map v2: navmesh as base map (cities and points of interest only), 2 m cells,
  cells written only when the bot gets stuck, two strikes (1st bump =
  "suspect", 2nd = wall). D* searches a bounded box; if no route at all it
  forgets walls within 50 m and replans.
- Mount / dismount / store vehicle; waits out the 15 s vehicle call outside cities.
- Harness: badge collector, world state, skill log, auto-restart on code change.
- Delete the bot's waypoints through the datapad (Ctrl+D), verified once in-game.

Written but NOT yet verified in-game:
- `/logChat` watcher (10 Nov fix: chat window answer wins; file growth = ON).
- Death/combat words matched by short stems (OCR garbles the small chat font).
- Datapad clean-up inside the harness loop (failed twice on 7 Nov, fixed after).
- "Boxed-in" area layers and the remount-after-dismount logic.

Known problems:
- 8 Nov: the character was killed by maulers at Amidala's Beach and the bot
  didn't notice. Leaving cities is unsafe until combat exists.
- Chat OCR at native size is poor ("you were shatm mauler..."). Needs an
  enlarged crop, off the driving loop (each read blocks ~0.6 s).
- Typed commands lose characters sometimes (`/tar speederbike` arrived as
  `peederbike`). Hence the macro rule.
- Heading comes from movement, so it's stale after a turn on the spot. The
  "only blame a faced cell" check exists but is off (`blame_max_angle_deg: 180`).
- Unknown: whether the client chat log records system messages (badges,
  combat) or only spoken chat. Test it (Marc expects it logs everything).
- `navigator.py` (1,300 lines) and `runner.py` (980) carry a lot of patches
  and switched-off options; due a tidy.
- No fixed benchmark yet, although the roadmap requires one before merging.

## 6. Agreed plan (Marc, 10 Nov)

1. **Die and recover.** Detect death, clone, and get out of the cloning
   facility. It's a multi-level indoor building, so this is the first piece of
   indoor navigation and worth real time.
2. **City work.** Stay inside cities. Skills for mission terminals, bazaar
   terminals, bank terminals, shuttleport/starport terminals (planet travel
   lives here), and city points of interest. One shared "window" module: find
   a window, read its text, click a row.
3. **Commands as macros or tool calls**, added as each one comes up
   (mount, store, `/logChat`, waypoint, target...).
4. **Test the chat log**: confirm `/logChat` stays on and what it records.
5. **Combat last**, with the LLMs: fast decision model + slow strategy model.

Ideas noted, not agreed: a screen-state check with the local vision model
(dead? mounted? window open?) every few seconds; a fixed set of benchmark
routes run after each navigation change.

## 7. How Claude and Marc work together

- Marc runs the game and the harness; Claude reads `data/live` and
  `data/debug_logs` from Marc's PC and writes fixes straight into `~/dev/SWGAI`
  (the harness restarts itself on a code change). After writing, check file
  sizes on the PC: writes are occasionally stale.
- **The PC's working tree is the source of truth.** Claude's own copy of the
  repo lives in a temporary workspace and its commits are not on GitHub. As of
  10 Nov the PC's files match Claude's last commit, but they are uncommitted
  there. Marc: commit and push from the PC before starting the new chat.
- Diagnose from evidence (screenshots, tick logs, events), and say when
  something is a guess. Several confident diagnoses in the last week were
  wrong: the water theory, the chat-log "flush" theory (twice), the
  facing-angle statistic.
- Qwen task template and work split: `docs/ROADMAP.md` section 3.
