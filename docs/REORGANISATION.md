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

## Behaviour changes to know about

- **Training presets set `fliplr: 0.0`** for the arrow and digit models. Your
  recorded runs used 0.5, which mirrors an NE arrow into a NW arrow that is
  still labelled NE (and mirrors digits into non-characters). Expect slightly
  different metrics on the next retrain.
- **D\* replans at the next tick** after the navigator marks cells (#8), so it
  will route differently from v0.20, hopefully better.
- **Steering** reacts to about 8.6° of error instead of about 1.5° (#10).

## Turn direction: please check this first

The steering code taps RIGHT when `heading × target > 0` in X/Z. If SWG's X is
east and Z is north, that is backwards: the bot turns away from D*'s next cell.
In the simulator, a mismatched sign never arrives, and a matched sign arrives
every time. **This may be why none of the 13 logged v0.20 runs completed.**

I couldn't verify SWG's handedness from here, so it's now a setting
(`navigation.right_turn_sign`, default `1` = the original behaviour).
`scripts/check_turn_direction.py` measures it in about 10 seconds and tells you the value.

## Known issues (not changed: they need in-game testing)

- **The align loop can't work while stationary.** `align_to_direction` waits
  for the heading to change, but the heading only updates after 1 m of
  movement, and the loop only turns. It usually runs to its 3 s timeout.
- **Cell attempts overshoot.** `attempt_cell` drives forward for a fixed 3–4 s
  whatever the distance. On a swoop that's 48–64 m for a 5–20 m target, so
  every probe ends deep in unmapped ground. In the simulator a 400 m trip past
  a wall took **2–3 km of driving**, and a 400 m long wall wasn't solved in
  15 minutes. This is the strongest lead for the city looping. Stop on
  distance, or at least scale the duration by vehicle speed.
- **Stops are assumed to be walls.** `_enter_recal` marks the cell ahead
  BLOCKED whatever caused the stop (lag, a creature, a menu).
- **`cost_unexplored`.** The code review called 3.0 a bug. The simulator
  disagrees: 1.0 failed walls that 3.0 solved. Kept at 3.0.

## Testing without the game

```
pip install pytest pyyaml numpy opencv-python-headless
python -m pytest tests
```

`tests/test_simulated_drive.py` drives the real navigator through a tiny fake
world (virtual clock, a bot that turns and moves when keys are held, and a HUD
that reports coordinates and a relative-bearing arrow). Use it to try a
navigation change before taking it in-game.
