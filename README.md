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
| **2. Path finding** | Built, and it became the bulk of the work. The navigator went through many versions (header v0.02 to v0.20) and three architectures: speed-based obstacle handling, then wall-hugging with "frustration" exploration, then a D* Lite planner over a persistent SQLite grid map. The bot can reach a waypoint in open terrain. Cities still beat it. The "imagination" problem from 2017 is real. |
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
  navigation/          navigator state machine, D* Lite planner, SQLite grid map
scripts/               run_controller.py, run_navigator.py,
                       calibrate_speed.py, check_turn_direction.py
tools/                 capture -> crop -> auto-label -> train (see tools/README.md)
models/                training records for each model; weights go here (not in git)
tests/                 offline tests, including a simulated drive
archive/navigation/    every earlier navigator version, untouched
docs/                  reorganisation notes and the September 2026 status review
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

**Results**
- ✅ In my own testing, the bot reaches waypoints in open terrain with no obstacles.
- 🗺️ The nav database has mapped 15,192 grid cells on Naboo: 2,523 clear, 444 blocked and the rest unexplored.
- ⚠️ None of the 13 logged runs is marked complete, so arrival isn't being recorded properly yet. There's no automated measure of success rate.
- ❌ Cities: the bot gets close, then loops, error-correcting back to the same spot.
  - The cause: swoop inertia makes logged coordinates lag the true position by metres, which poisons the grid map. So the bot needs to **dismount and walk in cities**.
  - Planned trigger: SWG prints *"You have entered…"* about 200 m before a city boundary. A system-message region filter plus a per-letter YOLO text reader would let any system message fire a function, and dismounting would be the first.
  - Tried and failed: steering towards the deepest region (left, centre or right) of a Depth Anything V2 heatmap. It couldn't handle small obstacles.

**What the simulator showed.** Driving the real navigator code through a
simulated world (`tests/test_simulated_drive.py`) turned up two things:
- **The steering may turn the wrong way.** If SWG's X is east and Z is north,
  the original code taps LEFT when D*'s next cell is to the right. In the
  simulator that mismatch never arrives; with the sign matched, it arrives
  every time. That may be why none of the 13 logged v0.20 runs completed. It's
  now a setting, and `scripts/check_turn_direction.py` measures it in-game.
- **Obstacles are handled very inefficiently.** Every "try this cell" drives
  forward for a fixed 3–4 seconds, which is 48–64 m on a swoop for a 5–20 m
  target. A 400 m trip past a wall took 2–3 km of driving, and a 400 m long
  wall wasn't solved at all. This is the strongest lead for the city looping.

**Mounted vs on foot.** On a vehicle the map is now read-only, because lag
puts marks in the wrong cells. If the bot gets stuck twice in the same place
while mounted, it dismounts and carries on on foot with mapping on. The
settings, and a simulator comparison of the options, are in
[docs/REORGANISATION.md](docs/REORGANISATION.md).

**Planned rebuild (not in this repo yet).** A code review of the D* Lite version found seven bugs:
- an alignment condition that could never be met
- wrong CLEAR marking
- a cache miss
- an unexplored-cell cost that caused backward routing
- a malformed f-string
- an unnormalised cross product
- node loss

The replacement design has two parts:
- trilateration of the target from several waypoint-distance readings, by least squares
- an A* planner with string-pulling over the sparse SQLite grid

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

Current status: **T1, navigation**.

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
