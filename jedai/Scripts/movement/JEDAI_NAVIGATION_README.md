# JedAI Navigation System
**Version 0.19 — Vehicle Navigation with D\* Lite Spatial Learning**

---

## What This Is

An autonomous vehicle navigation system for SWGEmu. The bot reads the game screen using YOLO vision models and EasyOCR, determines its position and the direction/distance to a target waypoint, and drives there while navigating around obstacles.

It gets smarter over time. Every run writes spatial data to a SQLite database. By the second run the bot already knows which areas are clear and which are blocked, and plans a path that avoids the hard spots it hit last time.

---

## Files

| File | Purpose |
|------|---------|
| `navigation_v0_11.py` | Main script — run this |
| `nav_database.py` | SQLite spatial cell store |
| `dstar_lite.py` | D\* Lite incremental path planner |
| `jedai_nav.db` | Auto-created on first run — the bot's spatial memory |
| `speed_config.json` | Vehicle max speed overrides (optional) |

All three `.py` files must be in the same directory.

---

## Setup

### 1. Dependencies
```
pip install ultralytics easyocr opencv-python numpy mss keyboard
```

### 2. Configure paths
At the top of `navigation_v0_11.py`, update `MODELS` to point to your trained YOLO weights:
```python
MODELS = {
    "group":    r"path\to\wp_group\weights\best.pt",
    "panel":    r"path\to\wp_panel\weights\best.pt",
    # ... etc
}
```

### 3. Set your planet
```python
PLANET = "naboo"   # lowercase — used as the DB key
```
Change this to match whichever planet your server is on. Each planet has its own spatial map in the database.

### 4. Set monitor index
```python
MONITOR_INDEX = 2   # 1 = primary, 2 = secondary, etc.
```

### 5. Key bindings
The script sends commands via `nav_state.json` to a separate key-sending process. Make sure your key executor is running and watching that file. The logical key names (`AUTORUN`, `FORWARD`, `LEFT`, `RIGHT`, `BACK`) map to whatever physical keys your executor is configured for.

---

## Running

```bash
python navigation_v0_11.py
```

1. Select vehicle (default: swoop)
2. The bot scans for waypoints by rotating the camera
3. Select waypoint ID from the list
4. Tab back into the game — the bot starts after a 4-second countdown

Press **F8** to kill at any time.

---

## How It All Works Together

### The Three-Layer Stack

```
┌─────────────────────────────────────────────────┐
│  LAYER 3 — D* Lite Path Planner                 │
│  Knows where it's been. Plans multi-step routes. │
│  Gets smarter every run.                         │
├─────────────────────────────────────────────────┤
│  LAYER 2 — Hug Controller + Frustration System  │
│  Handles obstacle edges in real time.            │
│  Escalates when stuck.                           │
├─────────────────────────────────────────────────┤
│  LAYER 1 — Vision (YOLO + OCR)                  │
│  Reads screen: coordinates, arrow, distance.     │
│  Ground truth for everything above.             │
└─────────────────────────────────────────────────┘
```

### Layer 1 — Vision

Each tick the bot captures a screenshot and runs it through several YOLO models in sequence:

- **Minimap model** → finds the minimap region
- **Coords model** → finds X/Y/Z coordinate fields within the minimap
- **Digit model** → reads the actual coordinate numbers (white-on-teal digits)
- **Group/Panel models** → finds waypoint panels in the HUD
- **Component model** → separates name vs distance within each panel
- **Arrow model** → reads the direction arrow (N, NE, E, SE, S, SW, W, NW)
- **WP digit model** → reads the distance number from the panel

This gives the bot: `current_pos (x, z)`, `arrow direction`, `distance to target`.

### Layer 2 — Hug Controller (`SimpleHugController`)

The hug controller is a three-state machine that handles real-time obstacle avoidance:

#### States

**CLEAR** — Normal navigation. Steers based on arrow direction (or D\* Lite waypoint if available). If speed drops below 30% of max while pointing north — obstacle detected — transitions to HUG.

**HUG** — Wall-following mode. Locked to one side (default LEFT). Uses two speed thresholds:
- Speed < 30% → nudge away from wall (obstacle contact)
- Speed > 40% → nudge toward wall (drifted too far)
- Speed > 50% while rising from below — **gap detected**, snap back to north

**CONFIRMING** — Candidate exit from HUG. Arrow is north and speed is high. If this holds for 3 seconds continuously, the bot is genuinely free and exits to CLEAR. Any wall contact resets back to HUG.

#### Speed Thresholds (why they work)

SWGEmu vehicle speed is the bot's proximity sensor. Walls slow you down. The hysteresis band (30% escape / 40% approach / 50% gap) creates stable wall-following without oscillation. The 50% rising-edge check is the gap detector — if the bot was pressed against a wall and suddenly accelerates through 50%, the wall ended.

#### Frustration System

When the bot notices it keeps returning to the same coordinates (within 2m, after travelling at least 10m since the last visit), `frustration_index` increments.

| Index | Behaviour |
|-------|-----------|
| 1+ | Force RIGHT side on next HUG entry (try the other side of the obstacle) |
| 6+ | Trigger exploration sweep (stop, run 45° left, run 45° right, pick the direction with more travel distance) |

If the bot makes 75m of progress toward the waypoint while frustrated, the index resets to zero and normal navigation resumes.

### Layer 3 — D\* Lite Spatial Planner

#### The Database (`nav_database.py`)

A sparse SQLite grid covering the planet at 5m resolution. Only visited cells have rows — unexplored space takes no storage.

Each cell has one of three states:
- **CLEAR (1)** — bot has driven through this cell
- **BLOCKED (2)** — obstacle detected here
- **UNEXPLORED (0)** — no data (absence of a row)

Every 5 metres of travel, the current cell is marked CLEAR. When HUG mode is entered (obstacle hit), the cell ahead of the bot is marked BLOCKED and the surrounding 5m cells are subdivided into 1m fine-resolution subcells for more precise replanning near obstacles.

The database persists between runs. The bot's spatial knowledge accumulates indefinitely.

#### The Planner (`dstar_lite.py`)

D\* Lite is an incremental replanning algorithm. Unlike A\* which plans once from scratch, D\* Lite repairs only the parts of the path affected by new information.

**Traversal costs:**
- CLEAR cell: 1
- UNEXPLORED cell: 3 (passable, but bot prefers known routes)
- BLOCKED cell: ∞ (impassable)

The cost of 3 for unexplored means the bot will take a slightly longer known-clear route rather than venturing into unknown territory — but if no known route exists, it will explore.

**How it integrates with steering:**

In the CLEAR state, the bot calls `planner.next_waypoint()` each tick. This returns the world coordinates of the next 5m cell along the planned path. The bot converts this to a compass direction and steers toward it instead of steering purely toward the raw waypoint arrow.

On first run: the planner has no data, all cells are unexplored (cost 3), and it finds a path by exploring. The bot behaves similarly to v0.18.

On second run: the planner loads known-clear cells (cost 1) from the DB. It routes through them, avoiding previously-blocked areas. The bot takes a smoother, faster path.

**Goal coordinate approximation:**

The bot doesn't know the target's exact world coordinates directly. It approximates them by projecting `distance` metres in the `arrow direction` from the current position. This estimate is refreshed every time it shifts by more than 50m. It doesn't need to be perfect — D\* Lite replans continuously as the bot moves and the estimate converges.

---

## Data Flow Per Tick

```
Screenshot
    │
    ▼
YOLO pipeline → (current_pos, arrow, dist)
    │
    ├──► _db_tick(pos, dist, arrow)
    │        ├── mark_clear(pos)          → nav_database
    │        ├── planner.update_start()   → dstar_lite (incremental replan)
    │        └── refresh goal estimate
    │
    ├──► hug.notify_dist(dist)            → frustration decay check
    │
    └──► hug.tick(speed, arrow, pos)
             │
             ├── CLEAR state:
             │      planner.next_waypoint() → D* suggested direction
             │      effective_arrow = D* arrow OR panel arrow
             │      steer toward effective_arrow
             │      if speed < 30% → _enter_hug()
             │                           └── mark_blocked(cell ahead)
             │                           └── planner.update_cell(BLOCKED)
             │
             ├── HUG state:
             │      nudge left/right to follow wall
             │      gap detection → REALIGN or EXPLORE
             │      frustration → flip side or exploration sweep
             │
             └── CONFIRMING state:
                    hold N + high speed for 3s → CLEAR
                    any wall contact → HUG
```

---

## Tuning Constants

All tunable values are at the top of `navigation_v0_11.py`:

| Constant | Default | What It Does |
|----------|---------|-------------|
| `PLANET` | `"naboo"` | DB key for current planet |
| `NAV_DB_FILE` | `"jedai_nav.db"` | Path to spatial database |
| `LOW_RATIO` | `0.30` | Speed fraction below which obstacle is detected |
| `HIGH_RATIO` | `0.40` | Speed fraction above which bot hugs back toward wall |
| `GAP_RATIO` | `0.50` | Rising-edge threshold for gap detection |
| `CONFIRM_SECS` | `3.0` | Seconds of clear heading before exiting HUG |
| `FRUSTRATION_FLIP_LVL` | `1` | Revisit count before switching hug side |
| `FRUSTRATION_CHAOS_LVL` | `6` | Revisit count before exploration sweep |
| `FRUSTRATION_DECAY_DIST` | `75.0` | Metres of progress before frustration resets |
| `POS_REVISIT_RADIUS` | `2.0` | Metres — "same spot" for frustration detection |
| `POS_REVISIT_MIN_TRAVEL` | `10.0` | Metres travelled since last visit before frustration counts |
| `STOP_DIST` | `50` | Metres — begin counting arrival confirmations |
| `FINAL_CREEP_DIST` | `20` | Metres — switch to creep pulse mode |
| `MONITOR_INDEX` | `2` | Screen capture monitor |
| `KILL_KEY` | `f8` | Emergency stop |

---

## The Database Over Time

After a few runs to the same destination you can inspect the DB:

```python
from nav_database import NavDatabase
db = NavDatabase("jedai_nav.db")
db.print_stats("naboo")
```

Output example:
```
[NavDB] naboo cell stats:
   res1_blocked: 847
   res1_clear: 4320
   res5_blocked: 156
   res5_clear: 2891
```

The `res1_*` rows are fine-resolution cells around obstacle boundaries. The `res5_*` rows are normal 5m cells. Over many runs, the 5m clear cells build a navigable map of the planet and the blocked cells outline the obstacle geometry.

---

## Known Limitations

**Obstacle cell projection is approximate.** When HUG is entered, the bot marks the cell 5m directly north as BLOCKED. The actual obstacle geometry may be at a different angle depending on approach direction. The DB accumulates enough blocked readings over multiple runs to build an accurate picture, but early runs may have slightly misplaced obstacle cells.

**Target coordinate estimate drifts.** The goal is computed by projecting from current position. On a very long run with many course changes, the accumulated error can be a few cell widths. This is fine — D\* Lite replans continuously and the bot reaches the target regardless.

**The frustration system and D\* Lite are complementary, not redundant.** D\* Lite handles known obstacles from previous runs. The frustration system handles novel obstacles the DB hasn't seen yet. First run on a new route uses mostly frustration. Tenth run uses mostly D\* Lite.

**Planet must be set manually.** There's no auto-detection of which planet the bot is on. Set `PLANET` before running on a new planet.

---

## Roadmap (Not Yet Implemented)

- **Dismount + walk mode** at high frustration (level 11+) for tight geometry a swoop can't navigate
- **On-foot layer** in the DB — inherits all CLEAR data from vehicle layer, adds foot-only paths (indoor areas, narrow gaps)
- **Multi-waypoint chaining** — queue a list of waypoints and run them sequentially
- **Mission loop** — tie navigation into the broader JedAI mission system
