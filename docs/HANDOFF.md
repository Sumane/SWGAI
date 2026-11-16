# JedAI handoff (16 Nov 2026, 14:35)

Paste this into a new chat to carry on. It replaces the chat history.
Fuller background: `docs/ROADMAP.md`, `docs/REORGANISATION.md`, `README.md`.

## 1. What this is

JedAI: a bot that plays SWGEmu (Star Wars Galaxies emulator) from a fresh
character to Jedi, by looking at the screen and pressing keys like a player.

- Repo: `SWGAI` (GitHub `Sumane/SWGAI`). On Marc's PC: `~/dev/SWGAI`.
- Marc's PC (`marc-Agentic`, Ubuntu): game client on Xwayland display `:2`,
  2560x1440. Python venv `~/venvs/jedai`. GPUs: RTX 3060 (bot vision) and
  RTX 5070 Ti (local Qwen).
- Game server: Marc's own Core3 on MAGNUS (192.168.0.40).
- Game install: `/mnt/storage/Swgemu` (start with `bash scripts/linux/start_swg.sh`).
  Models: `/mnt/storage/SWGEMU_bot/jedai/models`.
- Map database: `~/jedai-data/jedai_nav_v2.db` (set in `config/settings.local.yaml`).
- Character on 16 Nov: Naboo, at the Lake Retreat shuttleport (-5491, -22).

## 2. Rules Marc has set

- **Runtime knowledge:** only what a player could know (wiki-level). No server
  telemetry, spawn tables or enemy positions. Exception: the navmesh is
  allowed as the base map. Reading Core3 source to learn how a *command*
  works is fine (that's how /purchaseTicket, /boardShuttle, /find were built).
- **Stay in cities. Avoid combat completely** until combat is built (last,
  with the LLMs). Delivery missions are always inside cities; destroy
  missions are always outside, so the bot only takes Deliver.
- Every in-game command becomes a macro key or a harness tool call.
- The bot doesn't stop on failure: failures are data. Unexpected -> ESC out,
  log an anomaly with screenshots (`data/live/anomalies/`), review later.
- Store the speeder after every dismount. Mount = F1, `/tar speederbike swoop`,
  `/mount`. Walk when close; mount only beyond 150 m.
- **String the systems together when testing** (16 Nov): don't ask Marc to
  walk the bot somewhere; use the chains so real bugs show up. Marc steps in
  when needed.
- Git: never rewrite the authorship of Marc's older commits. Weights stay out
  of git. Never handle Marc's passwords/tokens; don't read `.env` files.
- Marc prefers blunt, technical answers. Chat length/usage is a concern: keep
  replies short.

## 3. How the code is laid out

| Path | What |
|---|---|
| `jedai/navigation/` | `runner.py` (run loop, mount, chat), `navigator.py`, `dstar_lite.py`, `grid_db.py`, `navmesh.py`, `route.py` (RouteFollower: walks recorded points from position reads), `places.py` (nearest known place, /find categories, target-bar and datapad-position parsing, `locate()` from distance readings) |
| `jedai/perception/` | `hud.py` (coords/waypoint OCR), `chat.py`, `chatlog.py` |
| `jedai/control/` | key controller + client (`keys.py`: press, tap, click, combo, chat) |
| `jedai/harness/` | `state.py`, `skill.py`, `log.py`, `system2.py` (badge collector), `supervisor.py`, `chain.py` (strings skills together) |
| `jedai/harness/skills/` | `navigation.py` (navigate_to, follow_waypoint, mount, dismount), `datapad.py` (clean_waypoints), `recover.py` (recover_from_death), `travel.py` (travel_to), `mission.py` (take_mission), `find.py` (find_nearest), `interact.py` (`use_target`: /tar + /ui action) |
| `jedai/data/` | `world_points.json` (badges, travel points), `clone_routes.json`, `travel_ports.json`, `terminals.json`, `learned_places.json` (written by the bot) |
| `config/settings.yaml` | every tunable; `settings.local.yaml` = Marc's machine |
| `scripts/` | `go.py` (chains), `harness_live.py`, `capture_session.py`, `route_from_capture.py`, `travel.py`, `recover_from_death.py` |
| `tests/` | 190 pass + 1 xfail (`python -m pytest -q tests`, ~2 min) |

## 4. How to run

```bash
cd ~/dev/SWGAI && source ~/venvs/jedai/bin/activate
python scripts/go.py travel "Keren Starport"      # recover if dead, get to a ticket terminal, buy, board
python scripts/go.py goto -4865 4182 --stop 3
python scripts/go.py recover
python scripts/go.py find mission --dry-run       # /find + datapad read, no walking
python scripts/go.py find bank                    # any KINDS key or /find category
python scripts/go.py mission --dry-run            # find terminal (shuttle if needed), read list, take nothing
python scripts/go.py mission
python scripts/capture_session.py --name X --minutes N    # record Marc playing
python scripts/route_from_capture.py data/captures/<dir>  # -> track.json
```

Step screenshots + OCR: `data/live/{recover,travel,mission,find,datapad}/`.
Skill log: `data/harness/logs/<time>/skills.jsonl`. Navigation runs: `data/debug_logs/`.

## 5. Commands learned (all usable as chat lines)

- `/purchaseTicket <planet> <From_Point> <planet> <To_Point> single` within 8 m
  of a ticket terminal (spaces -> underscores); "Ticket purchase complete" box.
- `/boardShuttle` within 25 m of a landed shuttle, on foot.
- `/tar <start of name>`: only targets what is in the camera's view. The target
  bar (top right) then shows "<Name> (<n>m)".
- `/ui action defaultAction` = double-click (use terminal, talk to NPC). Also
  `radialMenu`, `conversationStart`, `conversationResponseX`, `conversationStop`,
  `untargetOnly`, `cycleTargetOutward` (SWG wiki "UI Commands"; only
  defaultAction is wired, none verified in-game yet).
- `/find <category>` sets an active waypoint on the nearest one on the planet,
  named "<Region> (<Category>)", e.g. "Theed (Terminal)". `/find clear` removes
  it. Categories: terminal_mission, terminal_bank, terminal_bazaar,
  cloningfacility, shuttleport, starport, cantina, capitol, hotel, garage,
  medicalcenter, salon, vendor_junk, guild_*, trainer_* (six novice trainers).
  **Verified in-game 16 Nov** (waypoint made, distance read).
- Datapad (Ctrl+D): details pane shows "Position: x z" for the selected
  waypoint. It can open on the Data tab; click Waypoints first.
- `/logChat` logs everything but in lagging chunks.

## 6. Where things stand

Verified in-game:
- Outdoor navigation (map v2, navmesh base, 2 m cells, two strikes).
- Mount / dismount / store; datapad waypoint clean-up.
- **recover_from_death** at Theed (clone window, walk out of the facility).
- **travel_to** at four Naboo ports: Theed Shuttle B, Keren Starport, Theed
  Spaceport, The Lake Retreat (recorded walks in `travel_ports.json`).
- `go.py travel` chain.
- `/find terminal_mission` + waypoint distance; target-bar read ("mission terminal(51m)").

Written 16 Nov, NOT yet verified in-game:
- **take_mission**: `/tar mission` + defaultAction (double-click fallback),
  Deliver tab, read rows, pick lowest payout (`mission_prefer: short`), open
  details, Accept, check it left the list. Layout offsets come from one
  recorded frame.
- **find_nearest**, in this order: `/find` -> read position from datapad,
  checked against the waypoint's distance (fixes lost minus signs) -> if
  within 40 m, `/tar` looking all round (6 x 60 deg at `standing_turn_deg_s: 42`)
  and trilaterate from three spots -> remember the place. Last run stopped at
  the datapad read because the datapad was on the Data tab; the tab click was
  added after and is untested.
- **chain.find / chain.mission**: if the /find waypoint is over 1.5 km away,
  shuttle to the travel point nearest to it, then /find again, walk, /tar,
  take_mission.

**Next test:** `python scripts/go.py mission --dry-run` from the Lake Retreat
(expected: /find -> datapad -> shuttle to Theed Spaceport -> /find -> walk ->
/tar -> open terminal -> read list).

## 7. To do next

1. Get the test above passing (watch: datapad label pick, Position read,
   port choice, terminal open, list OCR at 16 px text).
2. **Finish a delivery mission** (Marc's goal, then a full POI-badge run):
   read the mission waypoint from the datapad (same reader as /find), go to
   the pickup NPC, talk (`defaultAction` / `conversationStart`), go to the
   destination NPC (travel chain), hand over, confirm from chat. The
   recording is `data/captures/2026-11-16_131000_mission_terminal` (1,239
   frames, `track.json`): Theed terminal (-4865, 4182), pickup NPCs near
   (-5066, 4195), Dee'ja Peak NPC near (4713, -1364), Kaadara NPC near
   (4954, 6799). Marc's advice: payout tracks distance; the destination is
   only known after the pickup; take several missions in one direction.
3. `clean_waypoints` has the same Data-tab problem: make it click Waypoints first.
4. Planet-map reader (Marc's idea) only if /find doesn't cover something.
5. Ticket terminals at ports with no recording (Kaadara, Dee'ja Peak, Moenia,
   the other Theed/Keren shuttles): try `find_nearest` with `/tar ticket`.
6. Smooth the start of the Theed Spaceport `to_terminal` walk (8 bumps near
   (-4849, 4176)); Keren bumps at (1416, 2748) and (1405, 2731).
7. Bank and bazaar terminals; then combat + LLMs (last).

## 8. How Claude and Marc work together

- Marc runs the game; Claude reads `data/live` from Marc's PC and writes fixes
  straight into `~/dev/SWGAI`.
- **After every write, list the folder and compare file sizes.** On 16 Nov a
  write reported success but left the old `find.py`; re-sending from a new
  staging folder fixed it. Use a fresh staging folder each time.
- The PC's working tree is the source of truth. Last push: 16 Nov 13:09
  ("Travel chain verified at all four Naboo ports"). Everything in section 6
  "written 16 Nov" is uncommitted on the PC: commit and push before the new chat.
- Diagnose from evidence and say when something is a guess.
