# JedAI objective tree (project map, 17 Nov 2026)

The goal broken down to the capabilities each step needs. Read with
`CAPABILITIES.md` (what exists, P1-P13 process plans) and `ROADMAP.md`.
Requirements for "glowing" are Core3's (`glowing.lua`, also in the roadmap 3.x):
3 Jedi exploration badges, 2 dangerous exploration badges, 5 easy exploration
badges, 1 profession master badge, 5 content badges.

Status: **have** (verified in-game) · **part** (built or half-built, not proven)
· **none** (not started).

---

## 1. The tree

```
JEDI
├── G  Become glowing (Force-sensitive unlock)
│   ├── G1  5 easy exploration badges
│   ├── G2  3 Jedi exploration badges
│   ├── G3  2 dangerous exploration badges
│   ├── G4  1 profession master badge
│   └── G5  5 content badges: Jabba's, Nym's, a theme park (Rebel or Imperial), the Warren x2
├── V  After glowing: Village of Aurilia (6 phases), then Mellichae (exit quest)
└── J  Grind Jedi: combat, lightsaber crafting, surveying, bazaar, healing,
       high-end PvE (crystals and pearls)
```

A working harness = everything under G and J can be driven by the skills below.

---

## 2. Each objective and what it needs

### G1 · 5 easy exploration badges
- **Needs:** badge coordinates (have: 45 badges on 10 planets in `world_points.json`),
  planet travel (P5), outdoor navigation (P4), entering the badge radius.
- **Plan (Marc, 17 Nov):** at least 5 easy badges are inside city limits, so
  G1 can be done with city navigation + shuttles + starports, no wilderness.
  Naboo has 4 badges (Amidala's Sandy Beach, Dee'ja Falls top, Gungan Sacred
  Place, Theed Falls bottom); the rest come from other planets (e.g. Bela Vistal
  fountain, Tyrena theater on Corellia).
- **Gap:** which badges are "easy" and which sit inside city limits isn't in our
  data yet (Q job: extract the categories from Core3; check each against the
  city navmesh/region bounds).
- **Risk:** only for badges outside cities: an aggressive creature on the route
  ends the run until there's a flee/avoid skill (C1). Pick in-city ones first.
- **Status:** part. Navigation to a badge was tried on Naboo (7-8 Nov runs to
  Amidala's beach).

### G2 · 3 Jedi exploration badges
- **Needs:** as G1. These are spread over several planets.
- **Gap:** category list; outside-city ones need C1.
- **Status:** none (travel exists; not attempted).

### G3 · 2 dangerous exploration badges
- **Needs:** as G1, plus surviving hostile areas: threat detection, route
  around aggro, flee, and accept the vehicle may be lost (Marc).
- **Gap:** C1 (threat avoidance).
- **Status:** none. Hold until C1 exists.

### G4 · 1 profession master badge
- **Needs:** a profession picked for the shortest path to master (crafting or
  combat), XP generation for it, credits for training, trainer conversations.
- **Gaps:** E1 (credits), N2 (conversation trees), K1 (crafting) or C2 (combat),
  trainer location (`/find trainer_*` exists).
- **Status:** none. Credits: part (one delivery done).

### G5 · 5 content badges (Jabba's, Nym's, theme park, Warren x2)
- **Needs:** quest chains (conversations, item handover, inventory), combat to
  survive and kill quest targets, interiors and multi-level navigation (roadmap
  2.0), item recognition (new YOLO models), planet travel.
- **Gaps:** C2, N2, I1 (inventory), M2 (interiors).
- **Status:** none.

### V · After glowing: Village of Aurilia, then Mellichae
- **Village (Dathomir), 6 phases.** Marc will set the phase change to daily on
  his server, so all phases are reachable within about a week of waiting. Each
  phase has its own village tasks (to read from Core3/wiki per phase: what the
  bot must do, and which capabilities that needs).
- **Exit: Mellichae.** A combat encounter, very likely not soloable. Options:
  1. **Nerf it on Marc's server** (cheapest; it's his server and a human couldn't
     reasonably solo it either).
  2. **Several bots** grouped together: needs a client + controller + vision per
     bot (VRAM on this PC is already tight) and group/follow/assist skills.
  3. **An exploit** of the encounter, if one exists and the bot can repeat it.
- **Needs:** M3, M4 (Dathomir outdoors), C1, C2 for village tasks, N2, then
  whichever Mellichae option is chosen.
- **Status:** none. Decision on Mellichae: Marc's (option 1 is the default
  assumption for planning).

### J · Grind Jedi
- **Needs:** strong combat (C2-C3), lightsaber crafting (K1-K2), surveying and
  harvesting resources (K3), bazaar buying (E2), healing (H1), high-end PvE for
  crystals and pearls (C3, group or solo-able spots), inventory (I1).
- **Status:** none.

---

## 3. Capabilities, what they unlock, and where they stand

| Code | Capability | Unlocks | Status | Process (CAPABILITIES.md) |
|---|---|---|---|---|
| M1 | City navigation, shuttles, mounts | everything | have | P4, P5, P6 |
| M2 | Interiors and levels (buildings, caves, dungeons) | G5, J | part (navmesh levels, clone exit) | P4, roadmap 2.0 |
| M3 | Planet travel (shuttles + starports) | G1, G2, G3, G5, V, J | have (built; Naboo ports verified, first off-planet badge run still to log) | P5 |
| M4 | Outdoor travel outside cities | G1-G3 | part (navigator works outdoors; no threat handling) | P4 |
| C1 | **Threat awareness and avoidance**: see aggressive creatures, route around, flee, mount up | G1-G3 safely, everything outside | none | new |
| C2 | Basic combat (Marc's macros: pull, fight, heal, retreat) | G4 (combat route), G5, J | none | roadmap 3.2 |
| C3 | High-end PvE (elites, bosses, loot) | G5, J | none | — |
| E1 | **Earning credits** (mission loop) | G4, J, ticket costs | part: 1 delivery | P8 |
| E2 | Bazaar: buy and sell | J, G4 (supplies) | none | P7 |
| E3 | Bank | safety of credits | none | P7 |
| N1 | Talk to an NPC (target, follow, talk, read result) | missions, trainers, quests | have | P9 |
| N2 | **Conversation trees** (choose options) | G4 trainers, G5 quests, V | none | P9 |
| I1 | Inventory and items (loot, use, hand in, equip) | G5, J | none | roadmap 3.4 |
| K1 | Crafting (tools, schematics, assembly) | G4 (crafting route), J saber | none | — |
| K2 | Lightsaber crafting (crystals, tuning) | J | none | — |
| K3 | Surveying and harvesting | K1, J | none | — |
| H1 | Healing (self, wounds, buffs) | C2, J | none | — |
| S1 | Death recovery | everything | have (Theed) | P2 |
| S2 | Run hygiene, escalation | everything | have / part | P1, P12 |
| S3 | System 2 interface (observe, mission record, resume, skill cards) | the decision model | none | P13 |
| P1 | Perception: panel, digits, target bar, chat, windows | everything | have, digits weak | P11 |
| P2 | New vision: creatures/threats, items, quest objects | C1, C2, G5 | none | P11 |

---

## 4. Build order that follows from the tree

1. **E1 credits loop + S3** (observe, mission record, resume). Money is needed for
   travel tickets, training and the bazaar; S3 makes every later skill drivable
   by the decision model.
2. **G1 in cities** (5 easy badges inside city limits, with shuttles and
   starports), then **G2**: extract badge categories and city bounds (Q), plan
   by distance from ports, run them. First real "objective" run for System 3.
3. **C1 threat awareness** (P2 vision for aggressive creatures, avoid/flee),
   then **G3**.
4. **N2 conversation trees + E2 bazaar + E3 bank.**
5. **Pick the G4 profession.** Decide crafting (K1, K3; safer, also needed for
   J) or combat (C2, also needed for G5 and J). Recommendation to test: an
   Artisan-line master, if its XP can be earned without combat; to confirm
   against Core3.
6. **C2 combat** (Marc's macros) + **H1 healing** + **I1 inventory**.
7. **M2 interiors**, then **G5** content badges, the Warren first (two badges).
8. **V** village phases (about a week with daily phase changes), Mellichae by
   the chosen option (nerf by default), then **J**: K2, C3, crystals and pearls.

Every step ends with a benchmark from CAPABILITIES.md §6 ("done when"), so the
map shows progress as numbers, not impressions.

---

## 5. Open questions

- Badge categories (easy / Jedi / dangerous) on this server: extract from Core3.
- Which profession is the fastest master for a bot.
- What each village phase asks of the bot (from Core3/wiki); Mellichae: nerf,
  several bots, or an exploit (Marc).
- Whether the bot plays alone throughout (high-end PvE solo limits J).
