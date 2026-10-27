# October 2026 reorganisation

The code used to live in two places (`SWGEMU_bot` and `label_work`) with 200+
hard-coded `E:\...` paths. It's now one repo with one settings file. The
navigator logic is the same v0.20 "D* first" design. It's been split into
modules, and the bugs listed below are fixed.

Everything was checked offline: 53 tests, including simulated drives (below).
**Nothing has been run in the game yet**, so do a supervised run before trusting it.

## Getting going on your machine

1. Copy `config/settings.local.example.yaml` to `config/settings.local.yaml`.
2. Set `paths.data_dir` (where datasets and runs go), and `paths.nav_db` if
   you want to keep your existing map (15k cells of Naboo).
3. Put the weights in `models/<name>/weights/best.pt`, or point
   `paths.models_dir` at your old `E:\SWGEMU_bot\jedai\models` folder.
4. **Run `python scripts/check_turn_direction.py` once** (see "Turn direction" below).
5. Start the controller with `python scripts/run_controller.py`, then the
   navigator with `python scripts/run_navigator.py`. Each needs its own terminal.
   They no longer have to share a working directory.

## Where everything went

### From `SWGEMU_bot`

| Old | New |
|---|---|
| `jedai/Scripts/movement/navigation_v0_12.py` | Split into `jedai/navigation/navigator.py` (state machine), `jedai/navigation/runner.py` (main loop), `jedai/perception/hud.py` (vision) and `jedai/control/keys.py` (commands). Entry point: `scripts/run_navigator.py`. An untouched copy is in `archive/navigation/` |
| `master_controller.py` | `jedai/control/controller.py` + `channel.py`. Entry point: `scripts/run_controller.py` |
| `dstar_lite.py` | `jedai/navigation/dstar_lite.py` |
| `nav_database.py` | `jedai/navigation/grid_db.py` (same database format, so your existing `.db` works) |
| `speed_config.json` | `config/vehicles.json` |
| `speed detection and logic/speedometer.py` | `scripts/calibrate_speed.py` |
| `navigation_v0.01*.py`, `v0_04`, `v0_08`, `working movement…/` | `archive/navigation/` (untouched) |
| `JEDAI_NAVIGATION_README.md` | `archive/navigation/JEDAI_NAVIGATION_README_v0.19.md` |
| `depth_perception.py` | `tools/experiments/depth/` |
| `OCR_player_loc.py` | `tools/experiments/ocr_player_loc_easyocr.py` |
| `jedai/Scripts/screenshot_capture.py` | `tools/capture/screenshot_capture.py` |
| `jedai/models/*` (training records) | `models/` |
| `scripts/video_watcher.py`, `extract_frames_gpu.ps1` | `tools/capture/` (the `.ps1` now takes `-InputVideo`) |
| `scripts/label_studio.bat` | `tools/labeling/start_label_studio.py` |
| `test-validation/testvalidation.py` | `tools/validation/test_hud_ocr.py` |
| `Video capture/…/video_to_screenshots.py` | `tools/capture/` |
| `Video capture/…/jedai_vision.py`, `train_clip.py`, `quick_extract.py`, `cluster.py`, `review.py`, `prepare_training.py`, `cleanup_safe.bat` | `tools/experiments/clip/` |
| `Video capture/…/auto-wp.py` | `tools/labeling/prelabel.py` |
| `Video capture/…/gen_sub_dataset.py` | `tools/labeling/crop_detections.py` |
| `Video capture/…/install_cuda_torch.bat` | `tools/install_cuda_torch.bat` |
| `YOLO exports/wp_panel_dataset/data_miner.py` | `tools/labeling/crop_detections.py` |
| `YOLO exports/wp_panel_dataset/auto.py` | `tools/labeling/auto_label.py --pad-left 1 --pad-right 40 --keep-empty` |
| `YOLO exports/*/train.py`, `*.yaml`, `classes.txt` | `tools/training/train.py --preset wp_panel` / `wp_components` (settings in `presets.yaml`) |
| `YOLO exports/distance_and_description_dataset/data-mining.py` | `tools/labeling/mine_panel_rows.py` |
| `YOLO exports/distance_and_description_dataset/splitter.py` | `tools/labeling/split_dataset.py` |
| `PROJECT_STATUS.md` | `docs/PROJECT_STATUS_2026-09.md` |

### From `label_work`

| Old | New |
|---|---|
| `AUTO-LABEL-FACTORY/auto-label.py` + `MODELPATH.py` + `DATAPATH.py` | `tools/labeling/auto_label.py --model … --source …` |
| `AUTO-LABEL-FACTORY/merge_datasets.py` | `tools/labeling/merge_datasets.py` |
| `AUTO-LABEL-FACTORY/restart_training.py`, `resume-training.py` | `tools/training/resume.py` (`--low-memory` = batch 4, workers 0) |
| `YOLO FACTORY/yolo_factory.py` | `tools/training/yolo_factory.py` |
| `DATA MINER/data_miner.py` + `MODELPATH.py` | `tools/labeling/crop_detections.py` |
| `train_wp_group_v0100.py`, `v0101.py`, `wp_group.yaml` | `tools/training/train.py --preset wp_group` |
| `wp_direction/split.py` | `tools/labeling/split_dataset.py --strip-ls-suffix` |
| `DirectionV2_merge.py` | `tools/labeling/split_dataset.py --pair-from-pool` |
| `fixlabels.py` | `tools/labeling/strip_confidence.py` |
| `run_autolabel.py` | `tools/labeling/prelabel.py` |
| `rename_screenshots.py` | `tools/capture/rename_screenshots.py` (now has `--dry-run`) |
| `compass/compass.py` | `tools/validation/compass_overlay.py` |
| class lists (`classes.txt`, `notes.json`, `data.yaml`) | `tools/training/presets.yaml` |

**Not copied:**
- `.env` (not read; it may hold secrets)
- images, datasets and Label Studio's database/exports
- `runs/`, the `ls_env` virtualenv, base `yolo*.pt` files, and runtime files (`nav_state.json`, `depth_bridge.json`)

These stay on your drive. Point `paths.data_dir` at them.

## Bugs fixed

Each one was confirmed against the original code before fixing, and is covered by a test.

| # | Bug | Evidence in the original | Effect |
|---|---|---|---|
| 1 | Malformed f-string in the target-estimate refresh (`{old[0]:.0f if old[0] else '?'}`) | Raises `ValueError` / `TypeError` every time | **Crashed the navigator** the first time the target estimate refreshed with D* active |
| 2 | Status line formatted `px:.0f` when coordinates weren't read | `TypeError` | **Crash** whenever a coordinate read failed but the distance read worked |
| 3 | `_enter_recal` did maths on `pos` when it was `None` | Code path | Crash if coordinates failed at the moment it stopped |
| 4 | `speed_config.json` had a trailing `# E:\…` comment | `JSONDecodeError` line 25 | Your calibrated speeds were **silently ignored** (built-in defaults used) |
| 5 | The speedometer saved to `jedai/config/speed_config.json` as `speeder`; the navigator read `movement/speed_config.json` as `speederbike` | Paths and keys differ | Calibration never reached the navigator |
| 6 | D* `km` accumulated from the position two updates ago | Two one-cell moves gave `km = 15` (paper: 10) | Wrong priority keys after moving |
| 7 | D* "node loss": the node popped when the search stopped was removed from the heap but left in the open set | 10–24 nodes lost per run in a replanning test | Parts of the map never re-expanded, giving stale paths |
| 8 | D* cache miss: cells the navigator wrote straight to the DB never reached the planner | After blocking the next cell, D* still routed into it | Planner ignored most of what the bot learned while driving |
| 9 | Moving the goal on a live planner without re-initialising (g values are distances to the *old* goal) | Code + simulator | Routed to the previous target estimate |
| 10 | Steering cross product not normalised (deadband 0.15 *metres*) | Steered at about 1.5° error and oscillated | Now an angle (about 8.6°). Also fixed the dead zone this created when the next cell is directly behind |
| 11 | `merge_datasets.py` renamed a clashing image and its label independently | Code | Could pair an image with the wrong label |
| 12 | Command file written in place, so the controller could read half a file | Status review | Atomic write + retry |
| 13 | `nav_state.json` was relative, so both scripts needed the same working directory | Status review | Now a config path |
| 14 | Stale 1 m sub-cells: blocking a cell creates 1 m sub-cells around it, and a neighbour blocked or cleared later kept its old sub-cells. `get_state()` prefers 1 m data | Block (132,12) then (142,12): `get_state` returns unexplored, and still does after marking it clear | **Any cell within about 10 m of an earlier obstacle ignored every later mark.** Recalibration retried the same blocked cell forever while D\* (which reads 5 m cells) thought it was blocked. Probably the review's "bad CLEAR marking" |
| 15 | **Target estimate from one arrow reading.** When the arrow first read N, the target was projected once along the heading: `target = position + heading × distance` | First in-game drive (27 Oct): swoop, waypoint 2,882 m away, D\* drove away from it. Simulator: 3 km targets ended 408–631 m out and never arrived | N means "within ±22.5°", which at 3 km is up to ±1,100 m sideways. It was never refreshed, because the arrow then stopped reading N. **Fix:** `target_estimator.py` trilaterates from a reading every 5 m (the distance is exact to the metre; the arrow only says which sector). D\* follows the estimate once it is pinned down; before that the bot follows the arrow. Simulator: 3 km targets now arrive, estimate within 8–25 m |
| 16 | D\* heuristic in metres (5 per cell) but edge costs per cell (1 or 3) | Path read back from D\* next to a wall went in a circle | It overestimated, so the search stopped before the cells round the bot were settled. Bot orbited one spot for minutes. Now cost_clear per cell (admissible); D\* plans at most 300 m ahead (a "carrot") to stay fast |
| 17 | Target behind: steering tapped left/right/left as the cross product's sign flipped | Simulator: drove 700 m the wrong way after a recalibration | Now picks a side and keeps turning |
| 18 | Arrow fallback returned 0 (straight on) for S, SE and SW | Code | Drove away from a target behind it |
| 19 | Steered at D\*'s next cell, 5 m away, which a vehicle passes before the next tick | Simulator | Steers 3 cells ahead |
| 20 | `attempt_cell` drove for a fixed 3–4 s, and counted 1.5 m of movement in **any** direction as reaching the cell, then marked it CLEAR | Simulator: wall cells marked clear 50+ times as the bot slid along the wall | Stops at the cell; CLEAR only if it got there; BLOCKED only if pushing got it nowhere; reverses into cells behind instead of turning blind (which spiralled 12 m round a 5 m cell) |
| 21 | When stuck, the cell **5 m** ahead was marked BLOCKED | Simulator | Pressed against a wall, the bot is often already reported inside the wall's cell, so the real obstacle was never marked. Now the point 1 m ahead |
| 22 | Grinding along a wall at an angle never counted as stopped (speed stays above 10%) | Simulator with wall sliding | New check: less than 8 m covered in 6 s = stuck. The cells beside the slide are marked blocked (on foot) |
| 23 | The kill key was only read once per tick (~2 s with vision), so a quick tap of F8 was missed | First in-game drive | A background thread watches it every 20 ms and remembers the press |
| 24 | The simulator let the bot tunnel through a 10 m wall (one big step per key event), and stopped it dead at walls instead of sliding | — | **The old wall test passed for the wrong reason.** With that fixed, v0.20 logic fails every wall case; the current code gets round a 120 m wall (3/3) but a 300 m wall is still too slow (1/3 in 15 min). See known issues |
| 25 | **Coordinate filter froze the position.** Any reading more than 50 m from the last accepted one was rejected, forever | Second in-game drive: "moved 0.0m" on every attempt, speed 0 at the start of the main loop | With ~2 s vision ticks a swoop covers 30+ m per tick; one long gap and every later reading was "a jump". Now the allowance grows with time (vehicle max speed × time), and three readings that agree with each other replace a bad old one (`reading_filter.py`) |
| 26 | **OCR distance misreads used as real** (887 m read as 9887, 8989, 8898; 1535 in the sweep) | Second in-game drive | Wrecked the target estimate (it jumped to 7 km away) and the explore sweep's left/right choice. Distances are now gated the same way, and the estimator uses robust (Huber) fitting with a median-based spread |
| 27 | Cell attempts with no heading drove straight on for the whole attempt | Second in-game drive: "not reached (moved 78.6m)" ×6, going further away each time | A short forward pulse gets a heading first; an attempt gives up after travelling the distance + 15 m |
| 28 | A D\* goal set early (bot stuck before the estimate was confident) was never updated | Second in-game drive | The goal now follows the estimate whenever D\* is running |
| 29 | Kill key latch cleared itself when read, so an inner loop could swallow F8 | Second in-game drive ("No acknowledgement" after F8) | Sticky: once seen, it stays pressed |
| 30 | **Recalibration probed 5 m cells with ~2 s position reads** | Third in-game drive (Theed): many `BLOCKED (moved 0.0m)` for cells it had just walked through, and probes overshooting by 15–25 m; distance went up during almost every recalibration | At 10–16 m/s and 2 s per read, a 5 m probe either overshoots or ends before the bot gets going. Clear cells round the bot were marked as walls and D\* routed round them, away from the waypoint. **Replaced by an unstick manoeuvre:** reverse 1 s, turn 45° (towards the D\* path or the arrow; the other way next time; wider if it keeps happening), drive 2 s. Only the point the bot was pushing into is marked blocked. Simulated city with 2 s ticks: the old code got 934 m away from an 800 m target and failed 2 of 6; now 6 of 6 |
| 31 | Heading from 1 m moves with whole-metre coordinates (45° steps), and no check against the arrow | Third in-game drive: heading flipping between axis directions | Needs 3 m of movement now. Once the target estimate is good, a heading more than 70° from what the arrow implies is replaced by the arrow's |
| 32 | "Same place" for the dismount rule was 15 m, but an unstick moves the bot 10–30 m | Simulator: a swoop ground along a building for 15 minutes without dismounting | 40 m |
| 33 | Systematic distance misreads (887 → 8987 several reads running) were accepted after 3 agreeing reads | Third in-game drive (the waypoint list itself showed 8987 m) | A jump is only believed after holding 8 s with nothing plausible in between |
| 34 | **D\* steered straight back into the same wall after every unstick** | Fourth in-game drive (Theed, on foot): stuck 31 times within 20 m | Three causes. (1) The CLEAR line drawn between two position reads 2 s apart re-cleared cells just found blocked, so the wall kept being forgotten; now only a cell the bot was actually read standing in can clear a block. (2) The blocked mark went in the bot's own cell, which was then re-cleared as it moved off; now it's the next cell along the heading. (3) Bumping learns one 5 m cell at a time. **New: wall-following.** When stuck, the bot turns to 60° from the wall and pushes along it, so the game slides it to the end of the wall, mapping the wall as it goes (on foot); D\* takes over when it's moving freely again. Stuck 4 times in one place: it backs out along its own track. Simulator with 2 s ticks: 300 m wall 900 s+ (failed) → 117 s; dead-end pocket failed → 415 s |
| 35 | D\* steering looked 15 m ahead, but a runner covers ~20 m per 2 s tick | Simulator: circling near a corner | Looks at least 1.5 ticks of travel ahead (measured tick time × vehicle speed) |

## Behaviour changes to know about

- **Training presets set `fliplr: 0.0`** for the arrow and digit models. Your
  recorded runs used 0.5, which mirrors an NE arrow into a NW arrow that is
  still labelled NE (and mirrors digits into non-characters). Expect slightly
  different metrics on the next retrain.
- **D\* replans at the next tick** after the navigator marks cells (#8), so it
  will route differently from v0.20, hopefully better.
- **Steering** reacts to about 8.6° of error instead of about 1.5° (#10).

## Turn direction (CONFIRMED 27 Oct)

Measured in-game with `scripts/check_turn_direction.py`, 3/3 trials: RIGHT
turns the heading clockwise (X = east, Z = north), so the original v0.20
steering was mirrored. The default is now `right_turn_sign: -1`. The original
analysis follows.

### Turn direction: original note

The steering code taps RIGHT when `heading × target > 0` in X/Z. If SWG's X is
east and Z is north, that is backwards: the bot turns away from D*'s next cell.
In the simulator, a mismatched sign never arrives, and a matched sign arrives
every time. **This may be why none of the 13 logged v0.20 runs completed.**

I couldn't verify SWG's handedness from here, so it's now a setting
(`navigation.right_turn_sign`, default `1` = the original behaviour).
`scripts/check_turn_direction.py` measures it in about 10 seconds and tells you the value.

## Mounted vs on foot (added 27 Oct)

**The rule: CLEAR from anything, BLOCKED only on foot.**

- **Mounted:** CLEAR is recorded as normal. A cell the bot drove through is clear however fast it went, and lag only shifts which already-driven cell gets the mark.
- **On foot only:** BLOCKED is recorded. A vehicle takes metres to start and stop and slides along walls, so it can't say which cell stopped it and can carry straight past the only opening. On foot it stops where it hits, so the mark is trustworthy. Blocked cells are what D\* plans around, so they need to be right.
- **Stuck twice within `stuck_radius_m` (15 m) on a vehicle → dismount.** The bot switches to the `running` profile; from then on, each confirmed blocked cell gives D\* a better suggested route.
- **How it dismounts:** by default it types `/dismount` in chat (Enter, text, Enter). Alternatively, set `dismount_method: key` and `dismount_key` to a toolbar key. The controller has a new `TYPE` command for this.
- **`mounted_mapping`:** `clear_only` (the default), `none` (nothing while mounted), or `all` (the old behaviour, for comparison).

The simulator can't fully judge this rule yet. It models coordinate lag and sliding along walls, but not vehicle momentum (starting and stopping distance), which is the main reason for the rule. Adding momentum would make it useful for tuning `dismount_after_stuck` and `stuck_radius_m`.

## Known issues (not changed: they need in-game testing)

- **Long walls are slow.** D\* assumes unknown ground is open, so after each
  contact it plans "through" the wall a little further along. On foot that's
  about 20 s per 5 m of wall: a 120 m wall takes 3–13 minutes in the
  simulator, and a 300 m wall usually more than 15. Ideas: follow the wall
  (pick a side and keep it) instead of replanning on every contact, or turn
  the slide track into a wall line.
- **Turning on foot is blind.** The heading comes from movement, so turning to
  a cell beside the bot needs short forward pulses. A measured turn rate per
  vehicle would let it turn on the spot.
- **Stops are assumed to be walls.** `_enter_recal` marks the cell ahead
  BLOCKED whatever caused the stop (lag, a creature, a menu).
- **`cost_unexplored`.** The code review called 3.0 a bug. The simulator
  disagrees: 1.0 failed walls that 3.0 solved. Kept at 3.0.

## Run recordings (debugging)

Every `run_navigator.py` run is recorded to its own folder under
`data/debug_logs/` (setting `paths.debug_dir`):

| File | What |
|---|---|
| `report.html` | Open in a browser: what went wrong, the path map, the distance chart, every event with its screenshot |
| `path.png` | Top-down map: path coloured by state, blocked (red) and clear (green) cells, where it thought the waypoint was, stuck/unstick/dismount markers |
| `distance.png` | Waypoint distance over time, red where it was getting further away |
| `summary.txt` | Each "wrong way" spell: when, how far, and whether D\* or the arrow was steering |
| `ticks.jsonl` | Every tick: raw and accepted coordinates and distance, arrow, speed, heading, state, steering decision, target estimate, D\* goal |
| `events.jsonl` | stuck, unstick, dismount, goal moves, cells marked blocked, misreads rejected, heading overrides |
| `console.log` | Everything printed |
| `shots/` | A screenshot every 5 s, and one at every event |

`python scripts/review_run.py` rebuilds the report for the latest run and
prints the summary and last events (`--list` lists runs). To get help with a
run, zip its folder and send it.

**Watchdog:** a run stops itself if the bot gets `abort_if_further_m` (300 m)
further from the waypoint than its closest point so far. Set it to 0 to turn
it off; `record_runs: false` turns recording off.

The recorder found its first bug in the simulator: steering was one 0.1 s tap
(~10°) per ~2 s tick, about 5°/s, so the bot orbited the waypoint 100 m out
for six minutes. Taps are now proportional to how far off it is
(`steer_gain`, `turn_rate_deg_s`).

## Testing without the game

```
pip install pytest pyyaml numpy opencv-python-headless
python -m pytest tests
```

`tests/test_simulated_drive.py` drives the real navigator through a tiny fake
world (virtual clock, a bot that turns and moves when keys are held, and a HUD
that reports coordinates and a relative-bearing arrow). Use it to try a
navigation change before taking it in-game.
