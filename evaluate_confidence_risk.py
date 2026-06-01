from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
import torch
import torch.nn.functional as F

from data_adapter import discover_samples, load_label_mask, split_dataset
from metrics_adapter import confusion_matrix_from_batch, metrics_from_confusion_matrix
from run_confidence_risk import load_model, preprocess_image
from tta_confidence import default_tta_specs, light_tta_specs, predict_single_probs, predict_tta_probs, uncertainty_summary


CLASS_NAMES = {
    0: "background",
    1: "simple",
    2: "blocky",
    3: "pipeline",
    4: "vertical",
    5: "horizontal",
}


def segmentation_report(pred: np.ndarray, target: np.ndarray, num_classes: int = 6) -> dict:
    cm = confusion_matrix_from_batch(np.asarray(pred), np.asarray(target), num_classes)
    pixel_acc, m_iou, m_dice, ious, dices = metrics_from_confusion_matrix(cm)
    return {
        "supported": True,
        "pixel_acc": pixel_acc,
        "mIoU": m_iou,
        "mDice": m_dice,
        "class_ious": {str(i): None if np.isnan(ious[i]) else float(ious[i]) for i in range(num_classes)},
        "class_dices": {str(i): None if np.isnan(dices[i]) else float(dices[i]) for i in range(num_classes)},
        "confusion_matrix": cm.tolist(),
    }


def summarize_class_subset(report: dict, class_ids: list[int]) -> dict:
    values = []
    for class_id in class_ids:
        value = report.get("class_ious", {}).get(str(class_id))
        if value is not None:
            values.append(float(value))
    return {
        "class_ids": [int(x) for x in class_ids],
        "mean_iou": float(np.mean(values)) if values else None,
        "num_supported": len(values),
    }


def compare_predictions(
    single_mask: np.ndarray,
    fused_mask: np.ndarray,
    target_mask: np.ndarray | None,
    uncertainty: np.ndarray | None = None,
    num_classes: int = 6,
    weak_class_ids: list[int] | None = None,
) -> dict:
    if target_mask is None:
        return {
            "supported": False,
            "reason": "missing target mask",
            "uncertainty_summary": uncertainty_summary(uncertainty) if uncertainty is not None else None,
        }

    single = segmentation_report(single_mask, target_mask, num_classes=num_classes)
    fused = segmentation_report(fused_mask, target_mask, num_classes=num_classes)
    weak_ids = weak_class_ids or [2, 5]
    return {
        "supported": True,
        "single": single,
        "fused": fused,
        "delta": {
            "mIoU": float(fused["mIoU"] - single["mIoU"]),
            "mDice": float(fused["mDice"] - single["mDice"]),
            "pixel_acc": float(fused["pixel_acc"] - single["pixel_acc"]),
        },
        "weak_class_single": summarize_class_subset(single, weak_ids),
        "weak_class_fused": summarize_class_subset(fused, weak_ids),
        "uncertainty_summary": uncertainty_summary(uncertainty, mask=fused_mask) if uncertainty is not None else None,
    }


def load_resized_target(mask_path: str, image_path: str, input_size: tuple[int, int], num_classes: int = 6) -> np.ndarray:
    folder_name = Path(image_path).parent.parent.name
    mask = load_label_mask(mask_path, folder_name, num_classes=num_classes, label_mode="multiclass")
    mask_t = torch.from_numpy(mask).long().unsqueeze(0).unsqueeze(0).float()
    resized = F.interpolate(mask_t, size=input_size, mode="nearest")
    return resized.squeeze(0).squeeze(0).numpy().astype(np.uint8)


def evaluate_pairs(
    pairs: list[tuple[str, str]],
    output_path: Path,
    limit: int | None = None,
    tta_mode: str = "light",
) -> dict:
    model, config = load_model()
    specs = default_tta_specs() if tta_mode == "default" else light_tta_specs()
    selected = pairs[:limit] if limit else pairs
    per_sample = []

    for image_path_str, mask_path_str in selected:
        image_path = Path(image_path_str)
        tensor, _ = preprocess_image(image_path, config.INPUT_SIZE)
        single_probs = predict_single_probs(model, tensor, device=config.DEVICE)
        single_mask = single_probs.argmax(dim=0).numpy().astype(np.uint8)
        tta = predict_tta_probs(model, tensor, specs=specs, device=config.DEVICE)
        fused_mask = tta["fused_mask"].numpy().astype(np.uint8)
        target = load_resized_target(mask_path_str, image_path_str, config.INPUT_SIZE, num_classes=config.NUM_CLASSES)
        comparison = compare_predictions(
            single_mask=single_mask,
            fused_mask=fused_mask,
            target_mask=target,
            uncertainty=tta["entropy_uncertainty"].numpy(),
            num_classes=config.NUM_CLASSES,
        )
        comparison["image"] = image_path.name
        per_sample.append(comparison)

    aggregate = aggregate_comparisons(per_sample)
    result = {
        "num_samples": len(per_sample),
        "tta_mode": tta_mode,
        "aggregate": aggregate,
        "samples": per_sample,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def aggregate_comparisons(comparisons: list[dict]) -> dict:
    supported = [item for item in comparisons if item.get("supported")]
    if not supported:
        return {"supported": False, "reason": "no labeled comparisons"}
    uncertainty_values = [
        item["uncertainty_summary"]["mean"]
        for item in supported
        if item.get("uncertainty_summary") is not None
    ]
    return {
        "supported": True,
        "single_mIoU": float(np.mean([item["single"]["mIoU"] for item in supported])),
        "fused_mIoU": float(np.mean([item["fused"]["mIoU"] for item in supported])),
        "delta_mIoU": float(np.mean([item["delta"]["mIoU"] for item in supported])),
        "single_mDice": float(np.mean([item["single"]["mDice"] for item in supported])),
        "fused_mDice": float(np.mean([item["fused"]["mDice"] for item in supported])),
        "mean_uncertainty": float(np.mean(uncertainty_values)) if uncertainty_values else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare single-pass and TTA-fused predictions on labeled samples.")
    parser.add_argument("--data-root", type=Path, default=None, help="Dataset root. Defaults to Config.DATA_ROOT.")
    parser.add_argument("--split", choices=["train", "val", "test", "all"], default="test")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--tta-mode", choices=["light", "default"], default="light")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    from train_resnet50 import Config as config

    data_root = args.data_root or Path(config.DATA_ROOT)
    pairs = discover_samples(data_root, label_mode=config.LABEL_MODE)
    train, val, test = split_dataset(pairs, config.SEED, config.TRAIN_N, config.VAL_N, config.TEST_N)
    split_pairs = {"train": train, "val": val, "test": test, "all": pairs}[args.split]
    output = args.output or (Path(config.SAVE_DIR) / "confidence_risk_eval.json")
    result = evaluate_pairs(split_pairs, output, limit=args.limit, tta_mode=args.tta_mode)
    print(json.dumps(result["aggregate"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
