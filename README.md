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

The project went dormant after that README, then was rebuilt from scratch in 2026 as **JedAI**: an autonomous agent that plays SWGEmu using only what a human player has (the rendered screen, the chat window and the combat log). No database access, no packet sniffing, no memory reading.

## Plan vs reality

| 2017 step | What actually happened |
|---|---|
| **1. Making sense of its environment** (YOLO on the game display) | The 2017 instinct was right. Several YOLO models now read the HUD: minimap, coordinate readout, waypoint panel and digit readers, hitting 0.995 mAP50 on the digit and coordinate models. EasyOCR handles free text, and chat and the combat log are parsed. The unglamorous part turned out to be building the training data (see the bootstrapping loop below). |
| **2. Path finding** | This became the bulk of the work. It went through versions v0.03 to v0.12: Bug2, then D* Lite, then an A* planner on a SQLite sparse grid. Open-world navigation now works reliably. Cities still don't. The "imagination" problem from 2017 is real. |
| **3. Interacting with the environment** (combat and skill trees) | Designed but not yet built. It's now split across a council of specialist agents rather than one learning process. |
| **4. Enhancing current levels of skill** ("revisit at that point") | Revisited. This became a five-tier completion target (below) and a plan for the bot to commission its own tools. |

The biggest change in approach: the 2017 plan assumed one machine-learning model learning everything. In 2026 the plan is to use **perception models for seeing, classic algorithms for moving, and local LLMs for deciding**. A model that learns to play end to end from pixels isn't the target any more.

## The new plan

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

### Architecture

```
Perception ──► Council ──► Orchestrator + SanityCheck ──► Action Executor
 (vision,       (agents)                                    (pydirectinput)
  OCR, chat,       ▲                                              │
  combat log)      └──────────── Memory ◄──── Self-improve ◄──────┘
```

- **Perception:** YOLO detectors for HUD regions, YOLO digit readers, EasyOCR, and chat and combat-log parsing.
- **Council:** specialist agents (`CombatAgent`, `ProgressAgent`, `EconomyAgent`, `NavigationAgent`, `RiskAgent`, `ErrorAgent`, `MetaAgent`) each propose actions. An orchestrator resolves them and a sanity check vetoes nonsense.
- **LLM backend:** local models via Ollama and llama.cpp, and they're swappable. Runs on consumer GPUs (RTX 5070 Ti 16 GB and RTX 3060 12 GB).
- **Action Executor:** keyboard and mouse via `pydirectinput`.
- **Memory:** SQLite stores (navigation grid and observations) that persist across sessions.

**Tool-factory direction.** Rather than hand-writing every capability, the plan is for the local council to identify gaps and emit structured tool requests. Frontier models build the requested tools, and each one is tested against in-game goals. The idea is that the bot grows its own toolset.

### Navigation stack

| Component | Approach | Result |
|---|---|---|
| Waypoint distance reader | YOLO digit model | 0.995 mAP50 |
| Player coordinate reader | YOLO field detector plus digit model | 0.995 mAP50 |
| Training data | Auto-label pipeline, ~3,000 images plus a held-out validation set of similar size | n/a |
| Target localisation | `target_estimator.py`: trilateration from waypoint distances via least squares | n/a |
| Map and planner | `grid_planner.py`: SQLite sparse 5 m grid, A* with string-pulling | n/a |

**Hard-won invariants**
- The waypoint arrow is a **relative bearing**, never a compass direction.
- SWGEmu's ground plane is **X/Z**, not X/Y.

**Bootstrapping loop for vision models**
1. Hand-label a small batch.
2. Train a rough model.
3. Use it to auto-label ~500 samples.
4. Correct those by hand.
5. Do a **full retrain** rather than a fine-tune, to avoid catastrophic forgetting.
6. Repeat.

**What works and what doesn't**
- ✅ Open-world navigation with no obstacles: 100% arrival in testing.
- ❌ Cities: the bot gets close, then loops, error-correcting back to the same spot.
  - The cause: swoop inertia makes logged coordinates lag the true position by metres, which poisons the grid map. So the bot needs to **dismount and walk in cities**.
  - Planned trigger: SWG prints *"You have entered…"* about 200 m before a city boundary. A system-message region filter plus a per-letter YOLO text reader would let any system message fire a function, and dismounting would be the first.
  - Tried and failed: steering towards the deepest region (left, centre or right) of a depth heatmap. It couldn't handle small obstacles.
- A code review of the D* Lite version found seven bugs, which drove the rewrite to the A* sparse-grid planner:
  - an alignment condition that could never be met
  - wrong CLEAR marking
  - a cache miss
  - an unexplored-cell cost that caused backward routing
  - a malformed f-string
  - an unnormalised cross product
  - node loss

**Where it's heading.** The YOLO HUD readers only work for SWG. The longer-term aim is a general navigation module that works with limited data in any environment, using coordinates as the one reliable signal.

## Repository layout

*To be filled in when the code is uploaded.*

## Running it

*To be filled in.* It needs Windows (for `pydirectinput`), a SWGEmu client, Python 3, a CUDA GPU and Ollama. Model weights are expected at `models/<model_name>/weights/best.pt`.

## Disclaimer

Developed and tested on a **private** SWGEmu server. Automation is against the rules of public SWGEmu servers, so don't run this on them. Not affiliated with SWGEmu, Lucasfilm, Disney or Daybreak.
