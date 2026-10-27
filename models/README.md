# Models

Weights are **not** in git (too big). Put each one at
`models/<folder>/weights/best.pt`, or set `paths.models_dir` in
`config/settings.local.yaml` to wherever they already are.

Each folder here keeps the training record: `args.yaml` (exact settings) and
`results.csv` (per-epoch metrics).

| Config key | Folder | Reads | Base | imgsz | mAP50 | mAP50-95 |
|---|---|---|---|---:|---:|---:|
| `group` | `wp_group_v0101` | Waypoint list window | YOLOv8n | 1280 | 0.995 | 0.958 |
| `panel` | `wp_panel_v2_production3` | One waypoint row | YOLOv8n | 640 | 0.995 | 0.984 |
| `comp` | `wp_components_v3` | Name / distance in a row | YOLOv8n | 640 | 0.995 | 0.948 |
| `arrow` | `wp_direction_v2_final` | Waypoint arrow (8 directions) | YOLO11n | 320 | 0.989 | 0.901 |
| `wp_digit` | `wp_distance` | Distance digits | YOLOv8s | 640 | 0.995 | 0.990 |
| `minimap` | `mini_map_v01.00` | Minimap | YOLOv8s | 640 | 0.995 | 0.982 |
| `coords` | `player_coord_v1.01` | x / y / z fields | YOLOv8s | 640 | 0.995 | 0.953 |
| `co_digit` | `white_on_teal_digits_v01.00` | Coordinate digits | YOLOv8s | 340 | 0.993 | 0.989 |

Scores are from the last epoch in each `results.csv`. The arrow and digit
models were trained with horizontal flips (`fliplr: 0.5`), which mislabels
mirrored arrows; the presets now turn that off.

Optional: `depth_anything_v2_vits.pth` (depth experiment) and
`clip_swgemu_best.pth` (CLIP experiment) go directly in this folder.
