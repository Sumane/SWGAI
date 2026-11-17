# JedAI capabilities and process plan (17 Nov 2026)

What the bot can do today, organised so a decision model (System 2) or the 27B
(System 3) can pick the right tool for the job, and a plan for each process the
bot still needs. Companion to `ROADMAP.md` (the three systems, versions) and
`HANDOFF.md` (current state, how to run).

**Milestone, 17 Nov 19:30:** first complete delivery mission (terminal → pickup
NPC → destination NPC → reward), unattended, in Theed.

---

## 1. The layers

| Layer | What it is | Who calls it | Examples |
|---|---|---|---|
| **L0 Primitives** | One input or one read. No judgement. | Skills only | `keys.tap("FORWARD", 0.6)`, `keys.chat("/tar mission")`, `click(x, y)`, read HUD coordinates, read the waypoint panel, OCR a region |
| **L1 Actions** | A few primitives with one check. Seconds. | Skills (and the escalation operator) | face the waypoint arrow, step forward and measure, `/tar` + read target bar, `/follow`, `/ui action defaultAction`, open datapad, zoom to first person |
| **L2 Skills** | One job, start to finish, with its own recovery. Returns `ok / failed(reason) / stuck` + data. | System 2 (today: `chain.py`) | `navigate_to`, `follow_waypoint`, `close_in`, `level_route`, `mount`, `find_nearest`, `take_mission`, `mission_npc`, `travel_to`, `recover_from_death`, `clean_waypoints`, `abort_missions` |
| **L3 Procedures** | Skills in order, with fallbacks. Minutes. | System 3 (today: `go.py`) | `deliver`, `mission`, `find`, `travel`, `go_to`, `recover`, `start` (run hygiene) |
| **L4 Objectives** | Why: badges, credits, professions, Jedi. | System 3 | "earn 5,000 credits in Theed", "visit the 5 Naboo badges" |

Rule (from the roadmap): each layer only calls the one below. A failure goes up
one level with a reason; the level above decides what next.

---

## 2. Choosing how to move

The question a decision model asks most. Read top to bottom; first match wins.

| Situation | Use | Layer | Status |
|---|---|---|---|
| Dead (clone window) | `recover_from_death` (clone, walk out on a recorded route, compass if stuck) | L2 | verified |
| Target on another planet | starport: `travel_to` a starport, buy ticket, board | L2/L3 | not built (1.5) |
| Target > 1.5 km on this planet | shuttle: `port_for_mission` / `port_in` picks the nearest port, `travel_to` | L3 | verified at 4 Naboo ports |
| A recorded route exists (port → ticket terminal, clone exit) | route follower (`travel_ports.json`, `clone_routes.json`) | L1/L2 | verified |
| Target 150 m – 1.5 km | `mount` swoop, then the navigator; it dismounts itself within 50 m when asked to stop close | L2 | verified |
| Target 10 – 150 m, outdoors | navigator on foot: `follow_waypoint` (we have a waypoint) or `navigate_to x z` (we have coordinates) | L2 | verified, still bumps |
| Target on another level (terrace, upper floor) | `level_route` (navmesh polygons with heights), then D* if that walk fails | L2 | written, not proven in-game |
| Last 10 m to a waypoint | `close_in` (first person, arrow, short steps; hands back if > 25 m or blocked 3×) | L2 | verified |
| Last metres to an NPC | `/tar` name + `/follow` (inside `mission_npc`) | L1 | verified |
| Last metres to a terminal (can't follow) | `find_nearest` walk-in: target-bar distance, 3 readings → position, walk | L2 | verified |
| Can't reach a target that is right there | D* to its exact spot (`navigate_to`, stop 3 m), marking what it bumps | L3 rule | verified once |
| Turning, facing, one step | raw keys (`tap LEFT/RIGHT/FORWARD`) | L0 | building block only |

Never for a decision model: raw keys to cross distance. They exist for skills
and the escalation operator.

---

## 3. Knowing where things are

| Need | Sources, best first | Notes |
|---|---|---|
| Where am I | HUD coordinates (x, height, z); compass waypoint for facing | height is the middle number |
| Where is a kind of place | `/find <category>` → waypoint row (city + distance) → known/learned places | known places preferred within 1.5 km |
| Exact position of something in view | `/tar` + `/waypoint` → datapad "Position: x, 0, z" | height always 0; **not wired in yet** |
| Position of a terminal | three target-bar readings → trilateration → saved to `learned_places.json` | replaced by `/waypoint` later |
| Where is a mission waypoint | panel row (name, distance, arrow) → navigator's estimate | digit model drops digits on 1–19 m |
| What just happened | chat OCR (NPC "says", "Mission complete!"), window OCR (terminal, datapad, dialogs) | chat OCR drops words ("says to" → "says") |

---

## 4. Decisions that are still hard-coded (to hand to System 2)

| Where | Rule today |
|---|---|
| `chain.start` | dead → recover; abort held missions; delete all waypoints; 4 tries then escalate |
| `chain.find` | known place within 1.5 km beats `/find`; over 1.5 km → shuttle |
| `chain._mission_leg` | mount over 150 m; follow → close_in → talk; talk fails → level_route → D* → talk |
| `take_mission` | lowest payout (shortest) delivery, or `mission_prefer` |
| navigator | dismount after 2 stuck on a vehicle; remount over 150 m; growing penalties; give up after 13 stuck |

These stay as the default behaviour. System 2 replaces them one at a time,
measured against the default.

---

## 5. What System 2 needs from the code (not built)

1. **`observe()`**: one snapshot: position + height, facing (compass), planet,
   mounted, dead, panel rows, target bar, open windows, last chat lines, held
   missions with their phase.
2. **Mission record** in the world state after every step: title, phase
   (accepted / picked_up / delivered), pickup and destination estimates,
   attempts. `start` keeps missions it has a record for (today it aborts all).
3. **Resumable procedures**: `deliver` starts from the saved phase.
4. **Skill cards**: every skill declares a description, parameters, preconditions
   and typical failures, so the registry can emit tool definitions for
   llama-server.
5. **Suggested next steps on failure** (`Result.data["next"]`), e.g.
   `npc_not_talked → [level_route, navigate_to]`. The chain follows them; a model
   may overrule.

---

## 6. Process plan

One block per process: goal, where it stands, what breaks, next step, and how
we know it's done. Owner: M = Marc, C = Claude, Q = local Qwen.

### P1 Session and run hygiene
- **Goal:** start from any state and get to a clean, known one.
- **Now:** launch scripts (`scripts/linux/1-3`); `start` = dead check, abort held
  missions (select icon → "Mission Object" → abort), delete all waypoints,
  escalate after 4 tries. Verified.
- **Breaks:** VRAM squeeze with llama-server (game lag); datapad label OCR.
- **Next:** keep missions we have a record for (needs §5.2); a supervisor that
  restarts game/controller (roadmap 6.1).
- **Done when:** 10 starts in a row from random states, no help.

### P2 Death recovery
- **Now:** clone window, cloning facility exit on recorded routes, compass. Verified at Theed.
- **Next:** recorded exits for other cloning facilities as met; insurance later.
- **Done when:** recovery works in every city the bot uses.

### P3 Locating places
- **Now:** `/find`, known and learned places, trilateration. Verified for mission terminals.
- **Breaks:** `/find` dud waypoints; distance unread.
- **Next (C):** `/tar` + `/waypoint` + datapad read for exact positions; use it for
  terminals and NPCs. Then bank, bazaar, ticket terminals, trainers.
- **Done when:** any `/find` category reached within 5 m in Theed, Keren, Moenia.

### P4 Movement
- **Now:** §2 ladder. Navigator: one bump blocks a cell, growing penalties,
  navmesh walls at 500, dismount near target, first-person close-in.
- **Breaks:** waypoint digit misreads (biggest single cause of failures);
  slow wall-following; mounted overshoot.
- **Next:** (M) retrain the digit model on 1–19 m crops — (C) script to extract
  them from `data/debug_logs`; (C) check `level_route` against the navmesh on the
  Theed terrace (`scripts/level_route.py`); (C) benchmark route set (20 Theed legs).
- **Done when:** 20 Theed legs, ≥ 18 arrive, median stuck count ≤ 2.

### P5 Travel between cities and planets
- **Now:** shuttles at 4 Naboo ports verified; port choice by distance.
- **Next:** remaining Naboo ports via `find_nearest` + `/tar ticket`; starports
  and planet-to-planet (roadmap 1.5); zone-load detection.
- **Done when:** "go to Mos Eisley cantina" from Theed, unattended.

### P6 Vehicles
- **Now:** mount (F1, `/tar swoop`, `/mount`), dismount, store; rules in §2.
- **Next:** handle "vehicle damaged/unavailable"; choose speeder vs swoop.

### P7 Terminals and windows
- **Now:** mission terminal fully (list, details, accept with retry, confirm).
  Shared window helpers (OCR, `confirm_ok_xy`, `reads_like`).
- **Breaks:** layout offsets from single recordings (Accept button moved 150 px).
- **Next:** prefer read buttons over offsets everywhere; bank (deposit), bazaar
  (sell), ticket terminals. One recording each (M), then the reader (C).

### P8 Missions
- **Now:** one delivery end to end. Abort works.
- **Breaks:** details OCR ("start: the"); pickup/complete detection relies on chat
  OCR + panel distance.
- **Next:** mission record + resume (§5.2-3); repeat loop (`deliver --count N`)
  with credits read before/after; take several missions in one direction (Marc's
  advice); later other mission types inside cities.
- **Done when:** 10 deliveries in a row unattended; credits/hour logged.

### P9 NPC interaction
- **Now:** name tags (speech bubbles ignored), `/tar`, `/follow`, `defaultAction`,
  outcome from chat.
- **Next:** conversation windows with choices (`conversationStart/ResponseX`),
  dialogue scripts from Core3 (Q job); trainers.

### P10 Waypoints and datapad
- **Now:** delete all / bot-only, abort missions, read positions.
- **Next:** `/waypoint` reads (P3); keep the panel to mission + one bot row.

### P11 Perception models
- **Now:** YOLO panel/digits/arrow, EasyOCR for windows/chat/target bar.
- **Next:** (M) digit model retrain (P4); chat OCR word drops — consider `/logChat`
  file as a second source; first-person frames for the dataset logger (roadmap 1.1).

### P12 Escalation and System 3
- **Now:** escalation folders written; nothing reads them.
- **Next (C, needs M):** llama-server address, model, vision projector → operator
  loop (screenshot → one action → repeat); decide the roadmap's open question
  (may System 3 press keys in an escalation).

### P13 System 2
- **Now:** rules in `chain.py` (§4).
- **Next (C):** §5 in order: observe → mission record → resume → skill cards →
  next-step hints. Then a rule-based picker over skill cards (roadmap 4.2),
  logging (state, choice, outcome) for a learned one (4.3).

### Later (after cities are solid)
Combat with Marc's macros, inventory and looting, profession grinding,
exploration badges outside cities, content badges, Village (roadmap 3.x).

---

## 7. Suggested order

1. P8 repeat loop + P13 §5.1–5.3 (observe, mission record, resume): turns one
   success into a credits loop and makes everything resumable.
2. P4 digit model retrain + benchmark set: removes the biggest failure source.
3. P3 `/waypoint` positions; P7 bank + bazaar.
4. P13 skill cards → tool definitions; P12 operator loop with the 27B.
5. P5 planet travel.
6. Combat and the rest.
