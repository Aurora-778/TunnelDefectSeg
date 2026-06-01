# ResNet50 6-Class Segmentation Baseline

This workspace contains a unified training and evaluation pipeline for tunnel defect segmentation with a fixed 6-class label space:

- `0` background
- `1` simple
- `2` blocky
- `3` pipeline
- `4` vertical
- `5` horizontal

## Current conventions

- Input size: `384 x 384`
- Epochs: `200`
- Train/val/test split: `700 / 150 / 150`
- Default label mode: `multiclass`
- All masks are normalized to folder-level class ids during loading
- Final report format: `Unified Baseline Evaluation Report`

## Key files

- `train_resnet50.py`
  - Main training and evaluation entrypoint
  - Supports resuming from checkpoints when `AUTO_RESUME=1`
  - Reads experiment name from the `EXP_NAME` environment variable
- `data_adapter.py`
  - Discovers samples
  - Splits the dataset deterministically
  - Loads masks and converts them to the 6-class label space
- `metrics_adapter.py`
  - Streaming confusion-matrix metrics for `PA`, `mIoU`, and `mDice`
- `build_6class_labels.py`
  - Rebuilds `multiclass_labels/`
  - Regenerates `class_manifest.csv` and `class_summary.json`

## Training entrypoints

### Resume-capable baseline run

- `run_train_resnet50.bat`
- `run_train_resnet50_visible.ps1`

Default output directory:

- `experiments/ResNet50_FCN_6cls`

### Clean retrain run

- `run_train_resnet50_fixed.bat`
- `run_train_resnet50_fixed_visible.ps1`

This entrypoint sets:

- `EXP_NAME=ResNet50_FCN_6cls_fixed`
- `AUTO_RESUME=0`

Output directory:

- `experiments/ResNet50_FCN_6cls_fixed`

### Resume interrupted fixed run

- `run_train_resnet50_fixed_resume.bat`
- `run_train_resnet50_fixed_resume_visible.ps1`

This entrypoint sets:

- `EXP_NAME=ResNet50_FCN_6cls_fixed`
- `AUTO_RESUME=1`

Use this after an interruption to continue from the latest batch or epoch checkpoint.

## Deep-teach entrypoints

These are explicit launch wrappers for teaching-oriented runs:

- `run_deep_teach_resnet50.bat`
- `run_deep_teach_resnet50_visible.ps1`

## Data layout

Expected image/mask layout:

- `1/images`, `1/labels`
- `2/images`, `2/labels`
- `3/images`, `3/labels`
- `4/images`, `4/labels`
- `5/images`, `5/labels`

The loader maps each folder to its fixed class id in multiclass mode.

## Current generated label snapshot

After running `build_6class_labels.py`:

- `multiclass_labels/` contains folder-level 6-class masks
- `class_manifest.csv` lists all paired samples
- `class_summary.json` stores image and pixel counts

Current snapshot at the time of writing:

- Total pairs: `1000`
- Each disease class: `200` images

## Final evaluation output

The final report includes:

- `mIoU`
- `mDice`
- `PA`
- `mPA`
- `Params(M)`
- `GFLOPs`
- `FPS`
- Per-class `IoU / Dice / Accuracy`

Example output file:

- `experiments/ResNet50_FCN_6cls_fixed/eval_report.txt`

## Notes

- The old legacy report under `experiments/ResNet50_FCN_6cls/` belongs to the previous run and should not be used as the clean retrain result.
- If the masks or folder structure change, regenerate `multiclass_labels/` before training.

## Confidence-risk post-inference module

This workspace now includes a post-inference module for confidence-aware tunnel defect review. It reuses the trained segmentation checkpoint, applies a small test-time augmentation set, fuses aligned predictions, estimates uncertainty, measures defect morphology, and writes an explainable risk report.

Run it on one image or a folder:

```powershell
python run_confidence_risk.py <image-or-folder> --output-dir experiments/confidence_risk --tta-mode light
```

Main artifacts per image:

- `<stem>_single_mask.png`
- `<stem>_fused_mask.png`
- `<stem>_overlay.png`
- `<stem>_uncertainty_heatmap.png`
- `<stem>_disagreement_heatmap.png`
- `<stem>_skeleton.png`
- `<stem>_report.json`

Compare single-pass and fused predictions where labels are available:

```powershell
python evaluate_confidence_risk.py --split test --limit 20 --output experiments/confidence_risk_eval.json
```

Implementation notes and patent-oriented framing are in `docs/patent-notes/tunnel-defect-confidence-risk.md`.
