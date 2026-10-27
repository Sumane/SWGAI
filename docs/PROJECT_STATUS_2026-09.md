# SWGEMU_bot / JedAI project status

Snapshot: 2026-09-07. Prepared for a returning maintainer and an agent reviewing the project.

## What this project is

JedAI is a Python computer-vision project for SWGEmu, primarily autonomous vehicle travel to a selected waypoint. It captures the game screen, locates HUD elements with trained YOLO models, reads names and numbers, estimates position and movement, plans a route, and sends keyboard commands. The workspace also contains the capture, labeling, training, and experimentation tools used to build that vision system.

This is a substantial prototype with saved weights, training results and navigation data. It is not established here as a working, reproducibly packaged application.

## Evidence and limits of this snapshot

Verified by reading source and existing files, parsing saved training CSVs and configuration with Python's standard library, and querying SQLite in read-only mode. No game automation, model inference, training, dependency installation, or live navigation was run. No implementation files were changed. Saved metrics and logs are historical artifacts, not independently reproduced results. File names and comments indicate variants, not a verified development chronology.

No applicable AGENTS.md was found. There is no root project README or dependency manifest. The visible `.git` directory contains no repository metadata, so commit history was not available for this review.

## Workspace map

| Location | Contents and role |
| --- | --- |
| `jedai/Scripts/movement/` | Navigator variants, key executor, D* Lite planner, SQLite store, speed configuration, depth and OCR experiments, navigation README |
| `jedai/models/` | YOLO weights, training arguments, results CSVs, curves and prediction images; Depth Anything V2 checkpoint |
| `jedai/Scripts/depth_anything_v2/` | Bundled depth-estimation implementation, examples and its own requirements/docs |
| `jedai/Scripts/screenshot_capture.py` | Screen capture utility |
| `Video capture/Desktop/screenshots/` | Capture/extraction, cleanup, clustering, review, CLIP training/inference and uncertain-image collection scripts; saved inference log |
| `YOLO exports/` | Waypoint-panel and distance/description datasets, YAML definitions, mining, auto-labeling and training scripts |
| `scripts/` | FFmpeg video watcher, GPU frame-extraction PowerShell, batch launchers including Label Studio |
| `test-validation/` | `testvalidation.py`, a manual screenshot/OCR validation utility; no saved validation output files were present here |

## Navigation: what is implemented

The likely current candidate is `jedai/Scripts/movement/navigation_v0_12.py`: its header identifies it as **v0.20, D* First Architecture**. This is an inference from its contents and version labels, not confirmation that it is the last known working version.

Its source includes:

- Eight YOLO detectors for waypoint groups, panels, components, arrows, distance digits, minimap, coordinate fields and coordinate digits. EasyOCR reads waypoint names.
- Screen capture with MSS; coordinate-based velocity estimation; selectable movement/vehicle speed profiles.
- Target-position estimation using observed travel direction and waypoint distance.
- D* Lite planning with MOVING, RECALIBRATING and EXPLORING states, clear-cell recording, blocked-cell marking and exploration routines.
- A persistent planet-keyed spatial grid through `nav_database.py`, including normal 5 m and finer 1 m resolution support.
- A waypoint scan/selection flow, alignment, near-target approach and arrival checking.
- Commands written to relative `nav_state.json`; `master_controller.py` reads commands and uses `pydirectinput` to execute keys, returning the file to IDLE.

The configured planet is `naboo`, monitor index is `2`, and stop key is F8. These are source defaults, not validated settings for this machine.

### Variants and documentation mismatch

| File under `jedai/Scripts/movement/` | Evidence from source/docs |
| --- | --- |
| `navigation_v0_12.py` | Header v0.20; D* first design; likely candidate to review first |
| `navigation_v0_08(working frustration without nav_coords).py` | Header v0.18; wall-hug, gap detection and frustration/exploration logic. “working” in the filename is not a tested guarantee |
| `navigation_v0_04(claude).py` | Header v0.04; speed-based obstacle handling and separate executor |
| `navigation_v0.01.py` | Header v0.02; optional depth-bridge integration and separate executor |
| `navigation_v0.01 - (premium eddition built in controller).py` | Imports direct keyboard execution inside the navigator |
| `working movement with controller and nav/` | Additional retained navigator variant; not established as canonical |
| `JEDAI_NAVIGATION_README.md` | Describes v0.19 and `navigation_v0_11.py`, which is absent; its layered wall-hug architecture does not describe the v0.20 candidate accurately |

Do not assume features from all variants are combined. In particular, `depth_perception.py` writes `depth_bridge.json`, and the older v0.02 source supports depth integration; no depth reference was found in `navigation_v0_12.py`. CLIP scene classification is also a separate experiment, not established as part of the current navigation loop.

## Saved evidence of progress

### Trained vision models

All eight model paths referenced by the v0.20 candidate have corresponding `weights/best.pt` files under `jedai/models/`. The older `player_coord_v1.00` weights also exist. Training folders include `args.yaml`, CSV metrics and visual evaluation artifacts.

The following values are from the **last recorded row** of each `results.csv`, not necessarily the epoch selected for `best.pt`. Scores are unit-scale bounding-box mAP.

| Model folder | Last epoch | mAP50 | mAP50–95 |
| --- | ---: | ---: | ---: |
| `mini_map_v01.00` | 100 | 0.99500 | 0.98190 |
| `player_coord_v1.00` | 100 | 0.99500 | 0.89397 |
| `player_coord_v1.01` | 151 | 0.99500 | 0.95275 |
| `white_on_teal_digits_v01.00` | 200 | 0.99327 | 0.98912 |
| `wp_components_v3` | 150 | 0.99500 | 0.94765 |
| `wp_direction_v2_final` | 150 | 0.98860 | 0.90124 |
| `wp_distance` | 100 | 0.99460 | 0.98965 |
| `wp_group_v0101` | 100 | 0.99500 | 0.95775 |
| `wp_panel_v2_production3` | 147 | 0.99500 | 0.98382 |

These are encouraging recorded detector results. Dataset independence, duplicate-frame leakage, new UI/resolution conditions, text-reading accuracy and end-to-end navigation performance were not assessed. Prediction images were inventoried, not visually audited.

### Navigation memory

The existing `jedai/Scripts/movement/jedai_nav.db` was queried read-only:

- `nav_cells`: 15,192 rows, all under Naboo: 12,225 UNEXPLORED, 2,523 CLEAR and 444 BLOCKED.
- `nav_runs`: 13 rows, all Naboo, all `completed=0`.

This proves saved state is present; it does not prove successful journeys. The current candidate calls `start_run` and has a `complete_run` call in its arrival logic. Incomplete records alone cannot distinguish interrupted sessions, unsuccessful navigation or earlier instrumentation behavior. The README says unexplored space uses no rows, but this database contains explicit unexplored rows.

### Other experiments and validation

`jedai_vision.py` implements a CLIP-based game-state classifier with confidence logging and uncertain screenshot capture. The first two saved JSONL entries, dated 2026-03-10, have top confidence around 0.173. Those samples do not establish useful classification accuracy. Its configured `models/clip_swgemu_best.pth` is absent from the inspected screenshots/models folder, which contains no files.

`depth_anything_v2_vits.pth` exists, along with independent depth inference code. No integrated v0.20 depth test was verified.

`testvalidation.py` runs a group → panel → component detector cascade followed by colored-text extraction and Tesseract, saving results from randomly selected screenshots. It is a manual diagnostic tool, not an automated assertion suite. Only this script was present under `test-validation`.

## Data and training workflow

The scripts support the following workflow, though it is not a single unified pipeline:

1. Capture gameplay or monitor recordings. `scripts/video_watcher.py` calls FFmpeg and includes an IVF/GPU path intended for problematic AV1 recordings.
2. Extract frames, clean/select images, cluster/review screenshots, and prepare subsets using the scripts in the screenshots folder.
3. Label or auto-label HUD regions; Label Studio has a batch launcher. YOLO dataset folders include mining, splitting and auto-labeling utilities.
4. Train panel/component detectors with Ultralytics YOLOv8 nano. The inspected training scripts specify `device=0`, 640-pixel inputs and Windows output paths under `E:\label_work\runs\detect`.
5. Inspect saved detector metrics/predictions and run the manual screenshot validation utility.
6. Separately, `train_clip.py` reads class-named image folders and trains a CLIP classifier; `jedai_vision.py` logs live predictions and collects uncertain images.

Not every stored model has a clearly paired training entry point in the two inspected dataset folders. Exact dataset/checkpoint provenance and reproducibility remain to be mapped.

## Setup and intended launch

The code targets a Windows desktop with the game visible. This workspace is currently mounted on Linux; many scripts retain absolute `E:\SWGEMU_bot\...` and `E:\label_work\...` paths. Those paths require reconciliation before use. This review did not test platform compatibility.

Core imports imply Python with `ultralytics`, `easyocr`, `opencv-python`, `numpy`, `mss`, `keyboard`, and `pydirectinput`, with PyTorch used by the vision stack. GPU/CUDA configuration needs checking; EasyOCR attempts GPU then falls back to CPU, while the inspected training scripts explicitly request GPU 0. SQLite is provided by Python's standard library. Separate experiments need additional packages such as CLIP, Pillow, torchvision and tqdm; the Tesseract utility also needs `pytesseract` and a configured Tesseract executable. Video processing needs FFmpeg. No complete pinned environment was identified. In this inspection environment `python3` was available, while `python` was not.

After validating paths, dependencies, game key bindings, monitor and planet, the intended two-process sequence is:

```text
# In two Windows terminals with the SAME working directory:
# <project>\jedai\Scripts\movement
python master_controller.py
# In the second terminal:
python navigation_v0_12.py
```

The navigator prompts for vehicle, scans waypoints and asks for a waypoint ID; the user returns focus to the game for the countdown. F8 is the coded stop key. The shared working directory matters because `nav_state.json` is relative, while the database and speed configuration are script-relative. These commands describe the code's intended use; they were not executed during this review.

## Known inconsistencies and review priorities

1. **Invalid speed JSON, verified:** `speed_config.json` has a trailing `# E:\...` comment after its JSON object. Standard JSON parsing fails with “Extra data” at line 25. The v0.20 constructor catches the error silently and retains built-in defaults, so custom overrides are ignored. No fix was made in this documentation pass.
2. **Canonical version unresolved:** filenames, internal versions and README disagree. Start with v0.20 for architecture review, and ask the maintainer which variant last worked before replacing or deleting older versions.
3. **Current-machine setup unresolved:** hardcoded paths, monitor 2, Naboo and Windows key injection prevent assuming this Linux-mounted copy is ready to launch.
4. **Command exchange needs review:** both processes rewrite one JSON file without an atomic message queue. The navigator waits up to two seconds for IDLE. Assess partial reads, command loss, timeout behavior and shutdown/autorun handling before relying on unattended operation; these are code-review concerns, not reproduced failures.
5. **Success evidence missing:** no run row is marked complete, and high detector mAP is not a navigation acceptance test. The older README's claims about improved subsequent runs are design claims, not measured outcomes established here.
6. **Experimental integrations are separate:** depth support is in an older variant; CLIP's configured checkpoint is missing. Do not treat either as a working v0.20 dependency.
7. **Reproduction gaps:** no unified requirements/lockfile or verified pairing of all checkpoints to datasets; manual validation produces no pass/fail regression criteria.

## Suggested next checks for a resuming agent

- Confirm the intended Windows host, last known working navigator and intended next milestone with the maintainer.
- Review v0.20 alongside the planner, database and executor. Preserve existing weights, database and older variants before implementation work.
- Resolve configuration/path handling and invalid speed JSON in a separate implementation change; establish a pinned environment once the actual runtime is known.
- Build offline checks with saved screenshots for waypoint association, signed coordinate parsing, digit ordering, stale/missing detections and target estimation. Split evaluation by recording/session to reduce near-duplicate leakage.
- Exercise planning and command behavior with synthetic maps and a non-key-sending executor, including unreachable targets, timeouts and stopping.
- Only then perform an explicitly supervised in-game run, recording the selected source version, route, UI settings, arrival outcome and failure cases. Compare repeated routes before claiming learning improvements.

The best-supported current assessment is: trained HUD perception and a substantial navigation implementation exist, with persisted terrain data, but the canonical working version and live reliability still need confirmation.
