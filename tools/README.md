# Tools

Everything for building the vision models. Every tool takes `--help`. Paths
default to `paths.data_dir` from `config/settings.yaml` / `settings.local.yaml`,
and `--model` accepts a config key (`minimap`, `panel`…), a folder under
`models/`, or a `.pt` path.

## The dataset loop

The same loop built every HUD model: capture → crop with the previous model →
auto-label → correct → **retrain from scratch** → repeat.

```
# 1. Capture gameplay
python tools/capture/screenshot_capture.py --output <data_dir>/screens --interval 3
#    ...or from a recording (AV1-safe, GPU):
powershell -File tools/capture/extract_frames_gpu.ps1 -InputVideo D:\rec\run.mp4 -Scale
python tools/capture/video_watcher.py --watch-dir D:\rec          # auto-extract new recordings

# 2. Crop what the next model should look at
python tools/labeling/crop_detections.py --model minimap --source <data_dir>/screens \
    --out <data_dir>/crops/minimap --class 0 --pad 2 --limit 500
python tools/labeling/mine_panel_rows.py --source <data_dir>/screens --out <data_dir>/crops/rows

# 3a. Auto-label with a rough model, review previews, then train
python tools/labeling/auto_label.py --model co_digit --source <data_dir>/crops/coords --name coord_digits

# 3b. ...or label/correct in Label Studio
python tools/labeling/prelabel.py --model panel --source <images> --name check   # pre-annotations
python tools/labeling/start_label_studio.py
#    export as YOLO into <data_dir>/import_stage, then:
python tools/training/yolo_factory.py mini_map --epochs 200

# 4. Split / merge / clean datasets
python tools/labeling/split_dataset.py --images <dir> --labels <dir> --name wp_direction --strip-ls-suffix
python tools/labeling/merge_datasets.py --from old_set --into new_set
python tools/labeling/strip_confidence.py <labels_dir>

# 5. Train (settings per model in tools/training/presets.yaml)
python tools/training/train.py --list
python tools/training/train.py --preset wp_panel --name wp_panel_v3 --deploy
python tools/training/resume.py wp_panel_v3 --low-memory           # after a crash
```

Datasets live in `<data_dir>/datasets/<name>/` as
`images/{train,val}` + `labels/{train,val}` (+ `classes.txt`). Runs go to
`<data_dir>/runs/`. `--deploy` copies `best.pt` into `models/<name>/`.

## Checking models

```
python tools/validation/test_hud_ocr.py --source <screens> --samples 5   # cascade + Tesseract check
python tools/validation/compass_overlay.py                               # 16-point overlay for labelling arrows
```

## Experiments (not used by the navigator)

- `experiments/clip/`: CLIP game-state classifier (cluster → label → train →
  live inference). The early logs show about 17% top confidence, so it never
  became useful.
- `experiments/depth/`: Depth Anything V2 left/centre/right steering. Failed
  on small obstacles.
- `experiments/ocr_player_loc_easyocr.py`: EasyOCR coordinate reading,
  replaced by the YOLO digit models.
