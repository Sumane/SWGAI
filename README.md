# SWGAI → JedAI

An autonomous agent that plays **Star Wars Galaxies** (via the [SWGEmu](https://www.swgemu.com/) emulator) using only what a human player has: the rendered screen, the chat window and the combat log. No database access, no packet sniffing, no memory reading.

> **Repo history.** SWGAI started in 2017 as a README of intentions — "make it see, make it move, make it fight". The project went dormant and was rebuilt from scratch in 2026 as **JedAI**. The original 2017 plan is preserved at the bottom of this file for context.

---

## Goal

Fully complete SWGEmu, in tiers:

| Tier | Name | Milestones |
|---|---|---|
| T1 | Survival | Stay alive, move, fight, heal, earn credits |
| T2 | Establishment | All four themeparks, unlock Force Sensitive |
| T3 | Power | Jedi Knight, heroics, PvP |
| T4 | Mastery | All 32 professions, Jedi Master, all badges, space |
| T5 | Transcendence | Open-ended self-improvement loop |

Current status: **T1, navigation**.

---

## Architecture

```
Perception ──► Council ──► Orchestrator + SanityCheck ──► Action Executor
 (vision,       (agents)                                    (pydirectinput)
  OCR, chat,       ▲                                              │
  combat log)      └──────────── Memory ◄──── Self-improve ◄──────┘
```

- **Perception** – YOLO detectors for HUD regions (minimap, coordinate readout, waypoint panel), YOLO digit readers, EasyOCR for free text, chat and combat-log parsing.
- **Council** – specialist agents (`CombatAgent`, `ProgressAgent`, `EconomyAgent`, `NavigationAgent`, `RiskAgent`, `ErrorAgent`, `MetaAgent`) each propose actions; an orchestrator resolves them and a sanity check vetoes nonsense.
- **LLM backend** – local models via Ollama / llama.cpp, swappable. Runs on consumer hardware (RTX 5070 Ti 16 GB + RTX 3060 12 GB).
- **Action Executor** – keyboard/mouse via `pydirectinput`.
- **Memory** – SQLite stores (navigation grid, observations) persisted across sessions.

### Tool-factory direction (2026)
Rather than hand-writing every capability, the local council identifies capability gaps and emits structured tool requests; frontier models build the requested tools, which are then tested against in-game goals. The bot is meant to grow its own toolset.

---

## Navigation stack (v0.03 – v0.12)

| Component | Approach | Result |
|---|---|---|
| Waypoint distance reader | YOLO digit model | 0.995 mAP50 |
| Player coordinate reader | YOLO field detector + digit model | 0.995 mAP50 |
| Training data | Auto-label factory (see below), ~3,000 images + similar-sized held-out validation set | – |
| Target localisation | `target_estimator.py` – trilateration from waypoint distances via least squares | – |
| Map / planner | `grid_planner.py` – SQLite sparse 5 m grid, A* with string-pulling (replaced Bug2 → D* Lite) | – |

**Hard-won invariants**
- The waypoint arrow is a **relative bearing**, never a compass direction.
- SWGEmu's ground plane is **X/Z**, not X/Y.

**Bootstrapping loop for vision models**
Small hand-labelled batch → rough model → auto-label ~500 samples → human correction → **full retrain** (not fine-tune, to avoid catastrophic forgetting) → repeat.

### What works / what doesn't
- ✅ Open-world navigation with no obstacles: 100% arrival in testing.
- ❌ Cities: the bot gets close, then loops while error-correcting back to the same spot.
  - Swoop inertia means logged coordinates lag the true position by metres, poisoning the grid map. Conclusion: **dismount and walk in cities**.
  - Planned trigger: SWG prints *"You have entered…"* ~200 m before a city boundary. A system-message region filter plus a per-letter YOLO text reader will let any system message fire a function, with dismount as the first.
  - Earlier attempt — steering towards the deepest region (left/centre/right) of a depth heatmap — failed on small obstacles.
- A code review identified seven bugs in the D* Lite version (unreachable align condition, wrong CLEAR marking, cache miss, an unexplored-cell cost that caused backward routing, a malformed f-string, an unnormalised cross product, node loss). This drove the rewrite to the A* sparse-grid planner.

### Where it's heading
The YOLO HUD readers are SWG-specific. The longer-term aim is a general navigation module that works with limited data in any environment, using coordinates as the one reliable signal.

---

## Repository layout

_To be filled in when the code is uploaded._

## Running it

_To be filled in. Requires Windows (for `pydirectinput`), a SWGEmu client, Python 3.x, CUDA GPU, and Ollama._

Model weights are expected at `models/<model_name>/weights/best.pt`.

---

## Disclaimer

Developed and tested on a **private** SWGEmu server. Automation is against the rules of public SWGEmu servers — do not run this on them. Not affiliated with SWGEmu, Lucasfilm, Disney or Daybreak.

---

<details>
<summary>Original 2017 plan</summary>

1. **Making sense of the environment** – use YOLO to recognise key features of the display: movement direction, health, opportunities and risks, other players, UI elements.
2. **Path finding** – learn to move and navigate, eventually with some "imagination" for paths that need problem solving.
3. **Interacting with the environment** – learn combat, and spend experience on the skill tree efficiently.
4. **Enhancing skill** – decide once the first three are working.

Nine years later, steps 1 and 2 are where the real work turned out to be.
</details>
