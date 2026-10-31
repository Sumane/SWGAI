# SWGAI
A project to create a an algorithm that can learn to play the MMO game Star Wars Galaxies

Star Wars Galaxies is an MMO that was released in 2003 and has since been emulated (SWGemu) due to its popularity. The purpose of this project is to experiment with the idea of creating a machine learning algorythm that can play and become proffficient at the game, through a use of both supervised and unsupervised learning.

The below steps will outline my ambitions for the project, in both near term and long term goals. I believe it is possible to create a bot that can interact with the game with minimal supervision, and achieve post-human levels of achievment, however the ambitions and realisations of the project will be determined by firstly my enthusiasm for the project, my ability to improve my own skills in machine learning, and any potential future collaboration with others in the project.

Step 1) Making sense of its environment.
The first step in creating a program that can comfortably traverse the world of Star Wars Galaxies, is to allow it to "see" what is going on, and process that information into usable data. Currently, I feel that the best way to achieve this is to use a "You Only Look Once" algorythm, to identify key features of the games display. Features such as the direction a player is moving, the current health status of the player, potential visible opportunities and risks, other players, and ability to interact with the games user interface. This particular part of the project will continue as the program learns more and more about the game, and will never be perfect, however once a certain level of recognition is learned, then we can proceed onto the next phase of development.

Step 2) Path Finding
Once the program is able to indentify key features in its environment, it now needs to learn to move and navigate that environment. To do this, a path finding algorythm will be needed. Similar to the previous step, once a basic level of understanding is met, we can move onto the next part of development process and this path finding can continue to be refined in the future. I do believe that developing a certain amount of immagination will be required, as there are certain paths that require an amount of problem solving, however this is long term.

Step 3) Interacting with the environment
Once a basic level of the first 2 steps are accomplished, the program will need to learn to have some form of interaction with its environment, firstly in terms of creatures and NPC's in the game. This will require 2 different learning processes, to develop. Firstly the program will need to learn basic combat mechanics of the game, and secondly to maximise productivity by gaining skills in the in-game skill tree, through purchase with experience. Both aspects of this level of interaction would now need to be further enhanced through trial and error over time, but once a basic understanding is met, further progress can be made.

Step 4) Enhancing current Levels of skill
At this point I cant decide on how to best proceed and feel that the only way to decide on the next step, would be to see how the project is going and revisit the progress at that point.

---

# 2026 Update: nine years later

*The text above is the original 2017 plan, left exactly as written. Everything below was added in October 2026.*

The project went dormant after that README, then was rebuilt from scratch in 2026 as **JedAI**: an agent that plays SWGEmu using only what a human player has (the rendered screen, the chat window and the combat log). No database access, no packet sniffing, no memory reading.

## Plan vs reality

| 2017 step | What actually happened |
|---|---|
| **1. Making sense of its environment** (YOLO on the game display) | Built, and the 2017 instinct was right. Eight YOLO detectors (YOLOv8n/s and YOLO11n) read the HUD: waypoint group, panel and components, waypoint arrow, distance digits, minimap, coordinate field and coordinate digits. All score 0.99+ mAP50 (table below). EasyOCR reads waypoint names. The unglamorous part was building the training data: capture, frame extraction, clustering, auto-labelling. |
| **2. Path finding** | Built, and it became the bulk of the work. The navigator went through many versions (header v0.02 to v0.20) and three architectures: speed-based obstacle handling, then wall-hugging with "frustration" exploration, then a D* Lite planner over a persistent SQLite grid map. **v1.0 (Oct 2026): the bot reaches its waypoint, cities included** (Theed, 302 m in 193 s). The "imagination" problem from 2017 is real, and is what the vision work in 1.1+ is for. |
| **3. Interacting with the environment** (combat and skill trees) | Not built yet. It's designed as a council of specialist agents (below) rather than one learning process. |
| **4. Enhancing current levels of skill** ("revisit at that point") | Revisited. This became a five-tier completion target and a plan for the bot to commission its own tools. |

The biggest change in approach: the 2017 plan assumed one machine-learning algorithm learning everything. In 2026 the plan is **perception models for seeing, classic algorithms for moving, and local LLMs for deciding**.

## What's in this repo

```
config/settings.yaml   one file for paths, monitor, planet, keys and tuning
                       (put your own values in config/settings.local.yaml)
jedai/                 the bot
  perception/          screen capture + HUD reading (the 8 YOLO models, OCR)
  control/             command channel, key client, master controller
  navigation/          navigator, D* Lite planner, SQLite grid map, target
                       trilateration, reading filters, run recorder, navmesh reader
scripts/               run_navigator.py, review_run.py, build_navmesh_priors.py,
                       depth_check.py, calibrate_speed.py, check_turn_direction.py
tools/                 capture -> crop -> auto-label -> train (see tools/README.md)
models/                training records for each model; weights go here (not in git)
tests/                 offline tests, including a simulated drive
archive/navigation/    every earlier navigator version, untouched
docs/                  ROADMAP.md, reorganisation notes, September 2026 status review
```

The code was reorganised in October 2026 from two messy drive folders. The
other doc ([docs/REORGANISATION.md](docs/REORGANISATION.md)) maps every old
file to its new home and lists the bugs fixed along the way.

### Vision models

The scores are from the last recorded epoch in each `results.csv` (bounding-box mAP). There's more detail in [models/README.md](models/README.md).

| Model | Reads | Base | mAP50 | mAP50-95 |
|---|---|---|---:|---:|
| `wp_group_v0101` | Waypoint list window | YOLOv8n | 0.995 | 0.958 |
| `wp_panel_v2_production3` | Waypoint row | YOLOv8n | 0.995 | 0.984 |
| `wp_components_v3` | Name / distance in a row | YOLOv8n | 0.995 | 0.948 |
| `wp_direction_v2_final` | Waypoint arrow | YOLO11n | 0.989 | 0.901 |
| `wp_distance` | Distance digits | YOLOv8s | 0.995 | 0.990 |
| `mini_map_v01.00` | Minimap | YOLOv8s | 0.995 | 0.982 |
| `player_coord_v1.01` | Coordinate fields | YOLOv8s | 0.995 | 0.953 |
| `white_on_teal_digits_v01.00` | Coordinate digits | YOLOv8s | 0.993 | 0.989 |

These are detector scores, not proof that the bot navigates. They haven't been checked for near-duplicate frames leaking between the training and validation sets. The arrow and digit models were also trained with horizontal flipping, which mislabels mirrored arrows; the training presets now turn it off.

**Bootstrapping loop used to build the datasets**
1. Hand-label a small batch.
2. Train a rough model.
3. Use it to auto-label ~500 samples.
4. Correct those by hand.
5. Do a **full retrain** rather than a fine-tune, to avoid catastrophic forgetting.
6. Repeat.

## Navigation: what works and what doesn't

**Hard-won invariants**
- The waypoint arrow is a **relative bearing**, never a compass direction.
- SWGEmu's ground plane is **X/Z**, not X/Y.

**Results (v1.0, tagged 27 Oct 2026)**
- ✅ The bot reaches waypoints in open terrain **and in cities**. First city arrival: Theed, 302 m in 193 s, swoop then on foot.
- ✅ Every run is recorded (`data/debug_logs/<time>_<target>/`): ticks, events, screenshots, a path map and an HTML report. `scripts/review_run.py` rebuilds the report for the latest run.
- ✅ On foot it counts as arrived within 10 m; mounted, the stop distance allows for swoop inertia.
- ⚠️ Deep dead ends are still slow: the bot only learns a cell is blocked by bumping into it on foot. 1.1 fixes this with a map from Core3's navmeshes; 1.2 adds vision.
- ⚠️ OCR occasionally misreads the distance (the arrow read as an extra 8). Readings are filtered against how far the bot has actually moved, so misreads no longer send it the wrong way.
- History of how it got here, kept below: the simulator findings, the first in-game drive, and the full bug list in [docs/REORGANISATION.md](docs/REORGANISATION.md).

**What the simulator showed.** Driving the real navigator code through a
simulated world (`tests/test_simulated_drive.py`) turned up two things:
- **The steering turned the wrong way (confirmed in-game).** SWG's X is east
  and Z is north, and the original code tapped LEFT when D*'s next cell was to
  the right. In the simulator that mismatch never arrives; with the sign
  matched, it arrives every time. That's very likely why none of the 13 logged
  v0.20 runs completed. `scripts/check_turn_direction.py` measured it, and the
  default is now fixed.
- **Obstacles were handled very inefficiently.** Every "try this cell" drove
  forward for a fixed 3–4 seconds (48–64 m on a swoop for a 5–20 m target),
  and sliding along a wall counted as reaching a cell inside it. Once the
  simulator stopped letting the bot tunnel through walls, the v0.20 logic
  failed every wall case. Now it gets round a 120 m wall every time, but a
  300 m wall still takes too long: the next thing to work on.

**First in-game drive (27 Oct 2026).** Turning, coordinates and the waypoint
reader all worked, but D\* drove *away* from a waypoint 2.9 km off. The target
was estimated once, from the first moment the arrow read N, as "straight ahead
at that distance". N only means within ±22.5°, which at 3 km is up to 1.1 km
sideways, and the estimate was never refreshed. It's now trilaterated from a
reading every 5 m: the distance is exact to the metre, so the circles meet at
the waypoint. In the simulator, 3 km targets now arrive, with the estimate
within 8–25 m. Fixing it also turned up a D\* heuristic in the wrong units,
which made planned paths loop.

**Mounted vs on foot.** CLEAR cells are mapped either way: a cell the bot
drove through is clear. BLOCKED cells are only mapped on foot, because a
vehicle can't stop where it hits and can slide straight past the only opening.
If the bot gets stuck twice in the same place while mounted, it dismounts and
carries on on foot, confirming blocked cells for D* to plan around. Details are
in [docs/REORGANISATION.md](docs/REORGANISATION.md).

**What fixed it (summary).** A code review of the old D\* Lite version found seven bugs (alignment condition that could never be met, wrong CLEAR marking, cache miss, an unexplored-cell cost causing backward routing, a malformed f-string, an unnormalised cross product, node loss). All were fixed in place rather than replacing D\* with A\*, together with:
- trilateration of the target from waypoint-distance readings (`jedai/navigation/target_estimator.py`)
- an admissible D\* heuristic and a 300 m "carrot" goal
- reading filters that reject impossible coordinate/distance jumps (`reading_filter.py`)
- stuck detection by grinding, back-off-and-pivot recovery, and dismounting after repeated sticking
- marking only the cell D\* was trying to enter as blocked, never the cells beside it

## The bigger plan

### Goal

Fully complete SWGEmu, in tiers:

| Tier | Name | Milestones |
|---|---|---|
| T1 | Survival | Stay alive, move, fight, heal, earn credits |
| T2 | Establishment | All four themeparks, unlock Force Sensitive |
| T3 | Power | Jedi Knight, heroics, PvP |
| T4 | Mastery | All 32 professions, Jedi Master, all badges, space |
| T5 | Transcendence | Open-ended self-improvement loop |

Current status: **T1, navigation v1.0 done; 1.1 (navmesh map + vision) in progress.** The step-by-step plan to Jedi is in [docs/ROADMAP.md](docs/ROADMAP.md).

### Target architecture (mostly design)

```
Perception ──► Council ──► Orchestrator + SanityCheck ──► Action Executor
 (vision,       (agents)                                    (pydirectinput)
  OCR, chat,       ▲                                              │
  combat log)      └──────────── Memory ◄──── Self-improve ◄──────┘
```

- **Perception** (built, for navigation): the YOLO detectors and OCR above. Chat and combat-log parsing are still to come.
- **Council** (design): specialist agents (`CombatAgent`, `ProgressAgent`, `EconomyAgent`, `NavigationAgent`, `RiskAgent`, `ErrorAgent`, `MetaAgent`) each propose actions. An orchestrator resolves them and a sanity check vetoes nonsense.
- **LLM backend** (design): local models via Ollama and llama.cpp, swappable. The target hardware is consumer GPUs (RTX 5070 Ti 16 GB and RTX 3060 12 GB).
- **Action Executor** (built, basic): `master_controller.py`.
- **Memory** (built, for navigation): the SQLite grid map.

**Tool-factory direction.** Rather than hand-writing every capability, the plan is for the local council to identify gaps and emit structured tool requests. Frontier models build the requested tools, and each one is tested against in-game goals. The idea is that the bot grows its own toolset.

**Beyond SWG.** The YOLO HUD readers only work for this one game. The longer-term aim is a general navigation module that works with limited data in any environment, using coordinates as the one reliable signal.

## Running it

This hasn't been run in-game since the reorganisation, so supervise the first runs.
The code targets **Windows** with the game visible.

1. Install PyTorch with CUDA (`tools/install_cuda_torch.bat`), then run `pip install -r requirements.txt`.
2. Copy `config/settings.local.example.yaml` to `config/settings.local.yaml`. Set `paths.data_dir`, and `paths.nav_db` if you want to keep an existing map.
3. Put the weights in `models/<name>/weights/best.pt`, or point `paths.models_dir` at them.
4. Once, with the controller running, run `python scripts/check_turn_direction.py`. Set `navigation.right_turn_sign` to the value it prints.
5. Run each of these in its own terminal:

```
python scripts/run_controller.py
python scripts/run_navigator.py
```

The navigator asks for a vehicle, scans your waypoints and asks you to pick
one. Switch focus back to the game during the countdown. **F8** stops both.

To calibrate a vehicle's speed: `python scripts/calibrate_speed.py --mode swoop --save`.

**Tests (no game needed):** `python -m pytest tests`

## Disclaimer

Developed and tested on a **private** SWGEmu server. Automation is against the rules of public SWGEmu servers, so don't run this on them. Not affiliated with SWGEmu, Lucasfilm, Disney or Daybreak.
