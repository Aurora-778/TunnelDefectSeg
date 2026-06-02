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

This workspace now includes a post-inference module for confidence-aware tunnel defect review. It reuses the trained segmentation checkpoint, applies a small test-time augmentation set, fuses aligned predictions, estimates uncertainty, adaptively selects a practical output mask, measures defect morphology, and writes an explainable risk report.

Run it on one image or a folder:

```powershell
python run_confidence_risk.py <image-or-folder> --output-dir experiments/confidence_risk --tta-mode light
```

Main artifacts per image:

- `<stem>_single_mask.png`
- `<stem>_fused_mask.png`
- `<stem>_hybrid_mask.png`
- `<stem>_selected_mask.png`
- `<stem>_overlay.png`
- `<stem>_selected_overlay.png`
- `<stem>_uncertainty_heatmap.png`
- `<stem>_disagreement_heatmap.png`
- `<stem>_skeleton.png`
- `<stem>_report.json`

The `selected_mask` is chosen by adaptive fusion from single, fused, and hybrid candidates. It is the default mask for morphology, skeleton, risk scoring, and the live demo overlay. The report also includes `Self IoU`, which compares single and fused model outputs only; it is not ground-truth mIoU.

Compare single-pass, fused, and selected predictions where labels are available:

```powershell
python evaluate_confidence_risk.py --split test --limit 20 --output experiments/confidence_risk_eval.json
```

Adaptive thresholds can be searched on the validation split only, so test/all data stay reserved for reporting:

```powershell
python evaluate_confidence_risk.py --split val --limit 0 --search-config --output experiments/adaptive_fusion_config_search_val.json
```

Latest full-run evidence with the validation-selected adaptive defaults:

| Split | Samples | Single mIoU | Fixed fused mIoU | Selected mIoU | Selected vs fused |
|---|---:|---:|---:|---:|---:|
| `val` | 150 | 0.3238 | 0.3091 | 0.3261 | +0.0170 |
| `test` | 150 | 0.3305 | 0.3165 | 0.3306 | +0.0141 |
| `all` | 1000 | 0.3725 | 0.3287 | 0.3680 | +0.0393 |

The adaptive module mainly recovers the mIoU lost by fixed TTA fusion while keeping uncertainty, disagreement, selected overlay, skeleton, morphology, and risk evidence available. It is not a retrained segmentation backbone, so `selected_mIoU` should be read as a safer post-processing output rather than a guaranteed improvement over `single_mIoU` on every split.

Implementation notes and patent-oriented framing are in `docs/patent-notes/tunnel-defect-confidence-risk.md`.

## Live web detector

The `web_app.py` server provides a local drag-and-drop detection UI. It serves `web_demo/index.html`, accepts image uploads, runs the confidence-risk inference pipeline, and returns the generated single/fused/selected masks, overlays, uncertainty, disagreement, skeleton, and JSON risk report.

Uploaded images do not include ground-truth masks, so the UI correctly displays true mIoU as `N/A`. It can still show `Self IoU` as a model self-consistency signal.

Start it on Windows:

```powershell
python web_app.py --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000
```

Generated live outputs are written under `experiments/web_live/`, which is intentionally ignored by git.
