# JedAI handoff (17 Nov 2026, 14:00)

Paste this into a new chat to carry on. It replaces the chat history.
Fuller background: `docs/ROADMAP.md`, `docs/REORGANISATION.md`, `README.md`.

## 1. What this is

JedAI: a bot that plays SWGEmu (Star Wars Galaxies emulator) from a fresh
character to Jedi, by looking at the screen and pressing keys like a player.

- Repo: `SWGAI` (GitHub `Sumane/SWGAI`). On Marc's PC: `~/dev/SWGAI`.
- Marc's PC (`marc-Agentic`, Ubuntu): game client on Xwayland display `:2`,
  2560x1440. Python venv `~/venvs/jedai`. GPUs: RTX 3060 (bot vision) and
  RTX 5070 Ti (game + local Qwen 27B via llama-server, which also holds ~8 GB
  of the 3060). VRAM is tight: close Thunderbird/Telegram/Sunshine if the game lags.
- Game server: Marc's own Core3 on MAGNUS (192.168.0.40).
- Game install: `/mnt/storage/Swgemu`. Models: `/mnt/storage/SWGEMU_bot/jedai/models`.
  Raw navmesh dumps: `/mnt/storage/SWGEMU_bot/navmeshes` (`paths.navmesh_dir`).
- Map database: `~/jedai-data/jedai_nav_v2.db` (set in `config/settings.local.yaml`).
- Character on 17 Nov: Naboo, Theed (missions taken at the terminal at (-4865, 4182)).
- Marc has Claude Code installed on the PC (`cd ~/dev/SWGAI && claude`).

## 2. Rules Marc has set

- **Runtime knowledge:** only what a player could know (wiki-level). No server
  telemetry, spawn tables or enemy positions. Exception: the navmesh is allowed
  as the base map. Reading Core3 source to learn how a *command* works is fine.
- **Stay in cities. Avoid combat completely** until combat is built (last,
  with the LLMs). The bot only takes Deliver missions.
- Every in-game command becomes a macro key or a harness tool call.
- Failures are data: ESC out, log an anomaly with screenshots
  (`data/live/anomalies/`), review later. Don't stop silently.
- Store the speeder after every dismount. Mount only beyond 150 m; get off
  within 50 m of a target that needs a close stop.
- Start of every run: drop held missions, then delete ALL active waypoints
  ("a fresh start on whatever is the current objective"). If that fails after
  4 tries, stop and escalate (`data/live/escalations/`) for the 27B to fix.
- Dead check only when actually dead (clone window).
- Can't get into a cell = blocked at once; repeated failures in a place make it
  dearer and dearer (no mindless loops). Can't reach a target = revert to D*.
- **String the systems together when testing**; Marc steps in when needed.
- Git: never rewrite the authorship of Marc's older commits. Weights stay out
  of git. Never handle Marc's passwords/tokens; don't read `.env` files.
- Marc prefers blunt, technical answers; keep replies short. Diagnose from
  evidence and say when something is a guess.
- Architecture (Marc): harness for linked commands (System 1), a small decision
  model for quick changes of decision (System 2), a 27B on llama-server to run
  the harness and manage long-horizon tasks (System 3).

## 3. How the code is laid out

| Path | What |
|---|---|
| `jedai/navigation/` | `runner.py` (run loop, mount/dismount, arrival), `navigator.py` (state machine, stuck handling, penalties), `dstar_lite.py`, `grid_db.py`, `navmesh.py` (Core3 navmesh: flat priors AND level-aware polygon routing, `route()`, `MeshIndex`), `route.py` (RouteFollower), `compass.py`, `places.py` |
| `jedai/perception/` | `hud.py` (coords incl. height, waypoint panel YOLO), `chat.py`, `chatlog.py`, `text.py` |
| `jedai/control/` | key controller + client (`keys.py`) |
| `jedai/harness/` | `chain.py` (start-of-run clean-up, find, mission, deliver legs, escalate), `state.py`, `skill.py`, `system2.py`, `supervisor.py` |
| `jedai/harness/skills/` | `navigation.py` (navigate_to, follow_waypoint, mount, dismount), `datapad.py` (clean_waypoints, abort_missions), `recover.py`, `travel.py`, `mission.py` (take_mission), `find.py` (find_nearest), `deliver.py` (mission_npc, close_in, level_route), `interact.py` |
| `config/settings.yaml` | every tunable; `settings.local.yaml` = Marc's machine |
| `scripts/` | `go.py` (chains), `linux/1_game.sh` `2_controller.sh` `3_deliver.sh`, `level_route.py` (offline navmesh check), `check_waypoints.py`, `capture_session.py`, `route_from_capture.py` |
| `tests/` | ~180 pass, 1 xfail (`python -m pytest -q tests`, ~2 min). `test_run_harness_help` fails only in Claude's sandbox |

## 4. How to run

Three terminals, in order (restart the controller after key changes in settings):
```bash
bash ~/dev/SWGAI/scripts/linux/1_game.sh        # log in, stand in Theed
bash ~/dev/SWGAI/scripts/linux/2_controller.sh  # leave open; F8 stops it
bash ~/dev/SWGAI/scripts/linux/3_deliver.sh     # = python scripts/go.py deliver
```
Other goals: `go.py travel "Keren Starport"`, `goto x z --stop 3`, `recover`,
`find mission`, `mission [--dry-run]`, `abort [--dry-run]`.
Offline navmesh check: `python scripts/level_route.py x height z goal_x goal_z`.

Evidence: `data/live/{anomalies,escalations,datapad,find,mission,travel}/`,
navigation runs `data/debug_logs/<time>_<target>/report.html`.

## 5. Commands learned

- `/find <category>`: waypoint "<City> (<Category>)" on the nearest one; `/find clear`.
- `/tar <start of name>` (in view only) -> target bar "<Name> (<n>m)".
- `/ui action defaultAction` = double-click (use terminal, talk). Terminal use
  range is about 5 m ("too far away to 'List Missions'" otherwise).
- `/follow` (target selected) walks straight at the target, no pathfinding;
  `/stopFollow`. Followed into a wall, it just pushes.
- `/waypoint` with a target selected makes a waypoint named after it at its
  exact x, z (datapad shows "Position: x, 0, z": height is always 0). Not wired in yet.
- Waypoint panel arrow is relative to the CAMERA: from behind, a waypoint you're
  on or past still reads "ahead". Numpad +/- zoom (far, close, 1st person).
- Mission flow: Accept -> "Are you sure..." OK; waypoint named after the
  mission; pickup NPC talk -> waypoint jumps to the destination; destination
  talk -> "Mission complete! Your reward of N...". Abort: Datapad -> Data tab ->
  select icon (pane says "Mission Object") -> right-click -> "Abort Mission" -> OK.

## 6. Where things stand (17 Nov)

Verified in-game: start-of-run abort + waypoint clean (abort by selecting the
icon and reading "Mission Object"); /find + known terminal; terminal walk-in;
mission list read and accept; navigation to the pickup; pickup talk (`/tar` +
`/follow` + defaultAction) - picked up several times; navigation to the destination.

**Not yet one completed delivery.** Last run (13:03) got to the destination NPC,
but he stood on a terrace 2 m above the bot (5 m away on the flat); the bike had
also overshot him. Fixes written after it, none run in-game yet:
- close_in: first person (numpad + held 3 s, numpad - 1.5 s after; tune
  `third_person_zoom_out_s`), "blocked" needs two failed steps, two readings
  of 8 m or less = arrived, talk step accepts targets up to 20 m and /follows.
- runner: dismount within 50 m when asked to stop close (`dismount_near_m`).
- chain: talk fails beside the waypoint -> `level_route` if the navmesh shows the
  NPC on another level, else (or if that walk fails) D* (`navigate_to`, stop 3 m)
  to his spot, then talk again.
- navigator: one bump blocks a cell; growing penalties round repeat failures;
  navmesh-unwalkable cells cost 500; standing pivots at 42 deg/s.
- terminal walk-in ends on any reading within reach; "z" in "(7m)" reads as 7;
  key taps wait as long as the key is held (no false "No acknowledgement").

Known weak spots: waypoint digit model on 1-19 m (drops digits: "13" -> "3");
mission details sometimes read "destination: the"; datapad label OCR.

## 7. To do next

1. Run `3_deliver.sh` (controller restarted) and finish one delivery.
2. Check the navmesh has the Theed terrace and its steps:
   `python scripts/level_route.py -5622 6 4715 -5620 4710`.
3. `/waypoint` on a targeted terminal/NPC for exact coordinates (replaces
   trilateration at terminals).
4. Retrain the waypoint digit model on 1-19 m (Marc's area).
5. Escalation operator for the 27B (needs llama-server address, model, vision projector).
6. Then the POI-badge run; bank and bazaar terminals; combat + LLMs last.

## 8. How Claude and Marc work together

- Marc runs the game; Claude reads `data/live` from Marc's PC and writes fixes
  straight into `~/dev/SWGAI` through the device bridge (no shell there).
- **After every write, list the folder and compare file sizes; use a fresh
  staging folder each time.** The PC's working tree is the source of truth.
- Commit and push at the end of each session (Marc runs git).
