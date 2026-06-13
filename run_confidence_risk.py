from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
import torch
from torchvision import transforms

from adaptive_fusion import select_adaptive_mask
from morphology_adapter import CLASS_NAMES, MorphologyConfig, measure_mask, skeleton_mask
from risk_adapter import RiskConfig, score_image, score_review_priority
from segformer_inference_adapter import (
    DEFAULT_SEGFORMER_CHECKPOINT,
    DEFAULT_SEGFORMER_CONFIG,
    DEFAULT_SEGFORMER_REPO_ROOT,
    load_segformer_mask_source,
)
from tta_confidence import (
    default_tta_specs,
    light_tta_specs,
    predict_single_probs,
    predict_tta_probs,
    uncertainty_summary,
)


DEFAULT_PALETTE = [
    [0, 0, 0],
    [0, 200, 255],
    [255, 80, 80],
    [140, 90, 255],
    [255, 200, 0],
    [60, 220, 90],
]

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


def colorize_mask(mask: np.ndarray, palette: list[list[int]] | None = None) -> np.ndarray:
    colors = palette or DEFAULT_PALETTE
    mask = np.asarray(mask)
    rgb = np.zeros((*mask.shape, 3), dtype=np.uint8)
    for class_id, color in enumerate(colors):
        rgb[mask == class_id] = color
    return rgb


def heatmap_from_uncertainty(uncertainty: np.ndarray) -> np.ndarray:
    scaled = np.clip(np.asarray(uncertainty, dtype=np.float32), 0.0, 1.0)
    gray = (scaled * 255).astype(np.uint8)
    return cv2.cvtColor(cv2.applyColorMap(gray, cv2.COLORMAP_JET), cv2.COLOR_BGR2RGB)


def overlay_image(raw_rgb: np.ndarray, mask_rgb: np.ndarray, alpha: float = 0.45) -> np.ndarray:
    raw = np.asarray(raw_rgb, dtype=np.float32)
    mask = np.asarray(mask_rgb, dtype=np.float32)
    return np.clip((1.0 - alpha) * raw + alpha * mask, 0, 255).astype(np.uint8)


def save_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def prediction_stats(mask: np.ndarray) -> dict[str, int]:
    uniq, counts = np.unique(mask, return_counts=True)
    return {str(int(u)): int(c) for u, c in zip(uniq.tolist(), counts.tolist())}


def prediction_consistency_stats(single_mask: np.ndarray, fused_mask: np.ndarray) -> dict:
    single = np.asarray(single_mask)
    fused = np.asarray(fused_mask)
    if single.shape != fused.shape:
        raise ValueError("single_mask and fused_mask must have the same shape")

    class_ious = {}
    present_ious = []
    for class_id in range(1, len(CLASS_NAMES)):
        single_class = single == class_id
        fused_class = fused == class_id
        union = np.logical_or(single_class, fused_class).sum()
        if union == 0:
            class_ious[str(class_id)] = None
            continue
        iou = float(np.logical_and(single_class, fused_class).sum() / union)
        class_ious[str(class_id)] = iou
        present_ious.append(iou)

    single_fg = single > 0
    fused_fg = fused > 0
    fg_union = np.logical_or(single_fg, fused_fg).sum()
    foreground_iou = float(np.logical_and(single_fg, fused_fg).sum() / fg_union) if fg_union else 1.0

    return {
        "single_fused_mIoU": float(np.mean(present_ious)) if present_ious else 1.0,
        "foreground_iou": foreground_iou,
        "pixel_agreement": float(np.mean(single == fused)),
        "class_ious": class_ious,
        "note": "Self-consistency between model outputs only; this is not ground-truth mIoU.",
    }


def _default_mask_source() -> dict:
    return {
        "name": "legacy_resnet50_fcn",
        "type": "torch",
        "probability_tta": True,
        "uncertainty_available": True,
        "disagreement_available": True,
    }


def _measurement_available(source: dict, field: str) -> bool:
    return bool(source.get(field, True))


def _unavailable_summary(reason: str) -> dict:
    return {
        "available": False,
        "mean": None,
        "max": None,
        "high_fraction": None,
        "defect_mean": None,
        "defect_high_fraction": None,
        "reason": reason,
    }


def _available_uncertainty_summary(uncertainty: np.ndarray, mask: np.ndarray | None = None) -> dict:
    summary = uncertainty_summary(uncertainty, mask=mask)
    summary["available"] = True
    return summary


def write_result_artifacts(
    stem: str,
    raw_resized: np.ndarray,
    single_mask: np.ndarray,
    fused_mask: np.ndarray,
    entropy_uncertainty: np.ndarray,
    disagreement_uncertainty: np.ndarray,
    output_dir: Path,
    tta_specs: list[str] | None = None,
    mask_source: dict | None = None,
    morphology_config: MorphologyConfig | None = None,
    risk_config: RiskConfig | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    source = dict(mask_source or _default_mask_source())
    uncertainty_available = _measurement_available(source, "uncertainty_available")
    disagreement_available = _measurement_available(source, "disagreement_available")
    source["uncertainty_available"] = uncertainty_available
    source["disagreement_available"] = disagreement_available

    selection = select_adaptive_mask(
        single_mask=single_mask,
        fused_mask=fused_mask,
        entropy_uncertainty=entropy_uncertainty,
        disagreement_uncertainty=disagreement_uncertainty,
    )
    selected_mask = selection["selected_mask"]
    hybrid_mask = selection["hybrid_mask"]

    fused_rgb = colorize_mask(fused_mask)
    single_rgb = colorize_mask(single_mask)
    selected_rgb = colorize_mask(selected_mask)
    hybrid_rgb = colorize_mask(hybrid_mask)
    min_component_area = (morphology_config or MorphologyConfig()).min_component_area
    skeleton_rgb = colorize_mask(skeleton_mask(selected_mask, min_component_area=min_component_area))
    entropy_rgb = heatmap_from_uncertainty(entropy_uncertainty)
    disagreement_rgb = heatmap_from_uncertainty(disagreement_uncertainty)
    overlay = overlay_image(raw_resized, fused_rgb)
    selected_overlay = overlay_image(raw_resized, selected_rgb)

    paths = {
        "single_mask": output_dir / f"{stem}_single_mask.png",
        "fused_mask": output_dir / f"{stem}_fused_mask.png",
        "hybrid_mask": output_dir / f"{stem}_hybrid_mask.png",
        "selected_mask": output_dir / f"{stem}_selected_mask.png",
        "overlay": output_dir / f"{stem}_overlay.png",
        "selected_overlay": output_dir / f"{stem}_selected_overlay.png",
        "uncertainty_heatmap": output_dir / f"{stem}_uncertainty_heatmap.png",
        "disagreement_heatmap": output_dir / f"{stem}_disagreement_heatmap.png",
        "skeleton": output_dir / f"{stem}_skeleton.png",
        "report": output_dir / f"{stem}_report.json",
    }

    Image.fromarray(single_rgb).save(paths["single_mask"])
    Image.fromarray(fused_rgb).save(paths["fused_mask"])
    Image.fromarray(hybrid_rgb).save(paths["hybrid_mask"])
    Image.fromarray(selected_rgb).save(paths["selected_mask"])
    Image.fromarray(overlay).save(paths["overlay"])
    Image.fromarray(selected_overlay).save(paths["selected_overlay"])
    Image.fromarray(entropy_rgb).save(paths["uncertainty_heatmap"])
    Image.fromarray(disagreement_rgb).save(paths["disagreement_heatmap"])
    Image.fromarray(skeleton_rgb).save(paths["skeleton"])

    morphology = measure_mask(selected_mask, config=morphology_config or MorphologyConfig())
    if uncertainty_available:
        unc_summary = _available_uncertainty_summary(entropy_uncertainty, mask=selected_mask)
    else:
        unc_summary = _unavailable_summary(
            "Mask source did not provide probability/TTA uncertainty; zero-valued heatmap is a placeholder artifact."
        )
    if disagreement_available:
        disagreement_summary = _available_uncertainty_summary(disagreement_uncertainty, mask=selected_mask)
    else:
        disagreement_summary = _unavailable_summary(
            "Mask source did not provide multiple aligned predictions for disagreement measurement."
        )

    risk_cfg = risk_config or RiskConfig()
    risk_uncertainty = unc_summary if uncertainty_available else None
    risk = score_image(morphology, risk_uncertainty, config=risk_cfg)
    risk["uncertainty_available"] = uncertainty_available
    if not uncertainty_available:
        risk["suggestions"].append(
            "Uncertainty unavailable for this mask source; review relies on morphology and selected-mask evidence."
        )
    single_stats = prediction_stats(single_mask)
    fused_stats = prediction_stats(fused_mask)
    hybrid_stats = prediction_stats(hybrid_mask)
    selected_stats = prediction_stats(selected_mask)
    self_consistency = prediction_consistency_stats(single_mask, fused_mask)
    adaptive_selection = {
        "mode": selection["selection_mode"],
        "reasons": selection["selection_reasons"],
        "consistency": selection["consistency"],
    }
    review_priority = score_review_priority(
        risk,
        uncertainty_summary=unc_summary,
        disagreement_summary=disagreement_summary,
        self_consistency=self_consistency,
        adaptive_selection=adaptive_selection,
        prediction_stats={
            "single": single_stats,
            "fused": fused_stats,
            "selected": selected_stats,
        },
        config=risk_cfg,
    )

    report = {
        "stem": stem,
        "mask_source": source,
        "tta_specs": list(tta_specs or []),
        "class_names": {str(k): v for k, v in CLASS_NAMES.items()},
        "single_prediction_stats": single_stats,
        "fused_prediction_stats": fused_stats,
        "hybrid_prediction_stats": hybrid_stats,
        "selected_prediction_stats": selected_stats,
        "self_consistency": self_consistency,
        "adaptive_selection": adaptive_selection,
        "uncertainty_summary": unc_summary,
        "disagreement_summary": disagreement_summary,
        "morphology": morphology,
        "risk": risk,
        "review_priority": review_priority,
        "artifacts": {name: str(path.name) for name, path in paths.items()},
    }
    save_json(report, paths["report"])
    return report


def collect_images(input_path: Path) -> list[Path]:
    if not input_path.exists():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")
    if input_path.is_file():
        if input_path.suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError(f"Unsupported image suffix: {input_path.suffix}")
        return [input_path]
    images = [p for p in sorted(input_path.rglob("*")) if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
    if not images:
        raise ValueError(f"No supported images found under: {input_path}")
    return images


def preprocess_image(img_path: Path, input_size: tuple[int, int]) -> tuple[torch.Tensor, np.ndarray]:
    tf = transforms.Compose([
        transforms.Resize(input_size),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    img = Image.open(img_path).convert("RGB")
    resized = np.array(img.resize(input_size, Image.BILINEAR))
    return tf(img), resized


def load_model(
    model_source: str = "legacy",
    segformer_config: Path = DEFAULT_SEGFORMER_CONFIG,
    segformer_checkpoint: Path = DEFAULT_SEGFORMER_CHECKPOINT,
    segformer_repo_root: Path = DEFAULT_SEGFORMER_REPO_ROOT,
    segformer_device: str = "cuda:0",
):
    if model_source == "segformer":
        return load_segformer_mask_source(
            config_path=segformer_config,
            checkpoint_path=segformer_checkpoint,
            repo_root=segformer_repo_root,
            device=segformer_device,
        )
    if model_source != "legacy":
        raise ValueError(f"Unsupported model source: {model_source}")

    from train_resnet50 import Config, ResNet50SegmentationModel

    local_pretrained = Path(Config.DATA_ROOT) / Path(Config.PRETRAINED).name
    pretrained_path = str(local_pretrained) if local_pretrained.exists() else Config.PRETRAINED
    model = ResNet50SegmentationModel(
        num_classes=Config.NUM_CLASSES,
        backbone="fcn",
        pretrained_path=pretrained_path,
    ).to(Config.DEVICE)
    ckpt_path = Path(Config.SAVE_DIR) / "best_model.pth"
    if not ckpt_path.exists():
        raise FileNotFoundError(f"Missing checkpoint: {ckpt_path}")
    ckpt = torch.load(ckpt_path, map_location=Config.DEVICE)
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    return model, Config


def _predict_confidence_inputs(model, config, image_path: Path, tta_mode: str) -> dict:
    if hasattr(model, "predict_confidence_inputs"):
        return model.predict_confidence_inputs(image_path, config.INPUT_SIZE, tta_mode=tta_mode)

    specs = default_tta_specs() if tta_mode == "default" else light_tta_specs()
    tensor, raw_resized = preprocess_image(image_path, config.INPUT_SIZE)
    single_probs = predict_single_probs(model, tensor, device=config.DEVICE)
    single_mask = single_probs.argmax(dim=0).numpy().astype(np.uint8)
    tta_result = predict_tta_probs(model, tensor, specs=specs, device=config.DEVICE)
    return {
        "raw_resized": raw_resized,
        "single_mask": single_mask,
        "fused_mask": tta_result["fused_mask"].numpy().astype(np.uint8),
        "entropy_uncertainty": tta_result["entropy_uncertainty"].numpy(),
        "disagreement_uncertainty": tta_result["disagreement_uncertainty"].numpy(),
        "tta_specs": tta_result["specs"],
        "mask_source": {
            "name": "legacy_resnet50_fcn",
            "type": "torch",
            "probability_tta": True,
        },
    }


def process_image(model, config, image_path: Path, output_dir: Path, tta_mode: str = "light") -> dict:
    prediction = _predict_confidence_inputs(model, config, image_path, tta_mode=tta_mode)

    report = write_result_artifacts(
        stem=image_path.stem,
        raw_resized=prediction["raw_resized"],
        single_mask=prediction["single_mask"],
        fused_mask=prediction["fused_mask"],
        entropy_uncertainty=prediction["entropy_uncertainty"],
        disagreement_uncertainty=prediction["disagreement_uncertainty"],
        output_dir=output_dir,
        tta_specs=prediction["tta_specs"],
        mask_source=prediction["mask_source"],
    )
    report["image_path"] = str(image_path)
    save_json(report, output_dir / f"{image_path.stem}_report.json")
    return report


def run_batch(
    input_path: Path,
    output_dir: Path | None = None,
    tta_mode: str = "light",
    model_source: str = "legacy",
    segformer_config: Path = DEFAULT_SEGFORMER_CONFIG,
    segformer_checkpoint: Path = DEFAULT_SEGFORMER_CHECKPOINT,
    segformer_repo_root: Path = DEFAULT_SEGFORMER_REPO_ROOT,
    segformer_device: str = "cuda:0",
) -> dict:
    model, config = load_model(
        model_source=model_source,
        segformer_config=segformer_config,
        segformer_checkpoint=segformer_checkpoint,
        segformer_repo_root=segformer_repo_root,
        segformer_device=segformer_device,
    )
    out_dir = output_dir or (Path(config.SAVE_DIR) / "confidence_risk")
    images = collect_images(input_path)
    reports = []
    for image_path in images:
        reports.append(process_image(model, config, image_path, out_dir, tta_mode=tta_mode))

    summary = {
        "input": str(input_path),
        "output_dir": str(out_dir),
        "model_source": model_source,
        "num_images": len(reports),
        "risk_counts": {},
        "review_priority_counts": {},
        "reports": [item["artifacts"]["report"] for item in reports],
    }
    for report in reports:
        level = report["risk"]["risk_level"]
        summary["risk_counts"][level] = summary["risk_counts"].get(level, 0) + 1
        priority = report.get("review_priority", {}).get("priority", "unknown")
        summary["review_priority_counts"][priority] = summary["review_priority_counts"].get(priority, 0) + 1
    save_json(summary, out_dir / "summary.json")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run confidence-aware tunnel defect risk inference.")
    parser.add_argument("input", type=Path, help="Image file or folder to process.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Optional output directory.")
    parser.add_argument("--tta-mode", choices=["light", "default"], default="light", help="TTA transform set.")
    parser.add_argument("--model-source", choices=["legacy", "segformer"], default="legacy")
    parser.add_argument("--segformer-config", type=Path, default=DEFAULT_SEGFORMER_CONFIG)
    parser.add_argument("--segformer-checkpoint", type=Path, default=DEFAULT_SEGFORMER_CHECKPOINT)
    parser.add_argument("--segformer-repo-root", type=Path, default=DEFAULT_SEGFORMER_REPO_ROOT)
    parser.add_argument("--segformer-device", default="cuda:0")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_batch(
        args.input,
        output_dir=args.output_dir,
        tta_mode=args.tta_mode,
        model_source=args.model_source,
        segformer_config=args.segformer_config,
        segformer_checkpoint=args.segformer_checkpoint,
        segformer_repo_root=args.segformer_repo_root,
        segformer_device=args.segformer_device,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
