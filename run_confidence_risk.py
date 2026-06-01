from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
import torch
from torchvision import transforms

from morphology_adapter import CLASS_NAMES, MorphologyConfig, measure_mask, skeleton_mask
from risk_adapter import RiskConfig, score_image
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


def write_result_artifacts(
    stem: str,
    raw_resized: np.ndarray,
    single_mask: np.ndarray,
    fused_mask: np.ndarray,
    entropy_uncertainty: np.ndarray,
    disagreement_uncertainty: np.ndarray,
    output_dir: Path,
    tta_specs: list[str] | None = None,
    morphology_config: MorphologyConfig | None = None,
    risk_config: RiskConfig | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    fused_rgb = colorize_mask(fused_mask)
    single_rgb = colorize_mask(single_mask)
    skeleton_rgb = colorize_mask(skeleton_mask(fused_mask, min_component_area=(morphology_config or MorphologyConfig()).min_component_area))
    entropy_rgb = heatmap_from_uncertainty(entropy_uncertainty)
    disagreement_rgb = heatmap_from_uncertainty(disagreement_uncertainty)
    overlay = overlay_image(raw_resized, fused_rgb)

    paths = {
        "single_mask": output_dir / f"{stem}_single_mask.png",
        "fused_mask": output_dir / f"{stem}_fused_mask.png",
        "overlay": output_dir / f"{stem}_overlay.png",
        "uncertainty_heatmap": output_dir / f"{stem}_uncertainty_heatmap.png",
        "disagreement_heatmap": output_dir / f"{stem}_disagreement_heatmap.png",
        "skeleton": output_dir / f"{stem}_skeleton.png",
        "report": output_dir / f"{stem}_report.json",
    }

    Image.fromarray(single_rgb).save(paths["single_mask"])
    Image.fromarray(fused_rgb).save(paths["fused_mask"])
    Image.fromarray(overlay).save(paths["overlay"])
    Image.fromarray(entropy_rgb).save(paths["uncertainty_heatmap"])
    Image.fromarray(disagreement_rgb).save(paths["disagreement_heatmap"])
    Image.fromarray(skeleton_rgb).save(paths["skeleton"])

    morphology = measure_mask(fused_mask, config=morphology_config or MorphologyConfig())
    unc_summary = uncertainty_summary(entropy_uncertainty, mask=fused_mask)
    risk = score_image(morphology, unc_summary, config=risk_config or RiskConfig())

    report = {
        "stem": stem,
        "tta_specs": list(tta_specs or []),
        "class_names": {str(k): v for k, v in CLASS_NAMES.items()},
        "single_prediction_stats": prediction_stats(single_mask),
        "fused_prediction_stats": prediction_stats(fused_mask),
        "uncertainty_summary": unc_summary,
        "morphology": morphology,
        "risk": risk,
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


def load_model():
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


def process_image(model, config, image_path: Path, output_dir: Path, tta_mode: str = "light") -> dict:
    specs = default_tta_specs() if tta_mode == "default" else light_tta_specs()
    tensor, raw_resized = preprocess_image(image_path, config.INPUT_SIZE)
    single_probs = predict_single_probs(model, tensor, device=config.DEVICE)
    single_mask = single_probs.argmax(dim=0).numpy().astype(np.uint8)
    tta_result = predict_tta_probs(model, tensor, specs=specs, device=config.DEVICE)

    report = write_result_artifacts(
        stem=image_path.stem,
        raw_resized=raw_resized,
        single_mask=single_mask,
        fused_mask=tta_result["fused_mask"].numpy().astype(np.uint8),
        entropy_uncertainty=tta_result["entropy_uncertainty"].numpy(),
        disagreement_uncertainty=tta_result["disagreement_uncertainty"].numpy(),
        output_dir=output_dir,
        tta_specs=tta_result["specs"],
    )
    report["image_path"] = str(image_path)
    save_json(report, output_dir / f"{image_path.stem}_report.json")
    return report


def run_batch(input_path: Path, output_dir: Path | None = None, tta_mode: str = "light") -> dict:
    model, config = load_model()
    out_dir = output_dir or (Path(config.SAVE_DIR) / "confidence_risk")
    images = collect_images(input_path)
    reports = []
    for image_path in images:
        reports.append(process_image(model, config, image_path, out_dir, tta_mode=tta_mode))

    summary = {
        "input": str(input_path),
        "output_dir": str(out_dir),
        "num_images": len(reports),
        "risk_counts": {},
        "reports": [item["artifacts"]["report"] for item in reports],
    }
    for report in reports:
        level = report["risk"]["risk_level"]
        summary["risk_counts"][level] = summary["risk_counts"].get(level, 0) + 1
    save_json(summary, out_dir / "summary.json")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run confidence-aware tunnel defect risk inference.")
    parser.add_argument("input", type=Path, help="Image file or folder to process.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Optional output directory.")
    parser.add_argument("--tta-mode", choices=["light", "default"], default="light", help="TTA transform set.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_batch(args.input, output_dir=args.output_dir, tta_mode=args.tta_mode)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
