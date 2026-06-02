from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
import torch
import torch.nn.functional as F

from adaptive_fusion import AdaptiveFusionConfig, select_adaptive_mask
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
    selected_mask: np.ndarray | None = None,
    selection_mode: str | None = None,
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
    selected_arr = np.asarray(selected_mask) if selected_mask is not None else np.asarray(fused_mask)
    selected = segmentation_report(selected_arr, target_mask, num_classes=num_classes)
    weak_ids = weak_class_ids or [2, 5]
    return {
        "supported": True,
        "single": single,
        "fused": fused,
        "selected": selected,
        "delta": {
            "mIoU": float(fused["mIoU"] - single["mIoU"]),
            "mDice": float(fused["mDice"] - single["mDice"]),
            "pixel_acc": float(fused["pixel_acc"] - single["pixel_acc"]),
            "selected_vs_single_mIoU": float(selected["mIoU"] - single["mIoU"]),
            "selected_vs_fused_mIoU": float(selected["mIoU"] - fused["mIoU"]),
            "selected_vs_single_mDice": float(selected["mDice"] - single["mDice"]),
            "selected_vs_fused_mDice": float(selected["mDice"] - fused["mDice"]),
        },
        "selection_mode": selection_mode or "fused",
        "weak_class_single": summarize_class_subset(single, weak_ids),
        "weak_class_fused": summarize_class_subset(fused, weak_ids),
        "weak_class_selected": summarize_class_subset(selected, weak_ids),
        "uncertainty_summary": uncertainty_summary(uncertainty, mask=selected_arr) if uncertainty is not None else None,
    }


def load_resized_target(mask_path: str, image_path: str, input_size: tuple[int, int], num_classes: int = 6) -> np.ndarray:
    folder_name = Path(image_path).parent.parent.name
    mask = load_label_mask(mask_path, folder_name, num_classes=num_classes, label_mode="multiclass")
    mask_t = torch.from_numpy(mask).long().unsqueeze(0).unsqueeze(0).float()
    resized = F.interpolate(mask_t, size=input_size, mode="nearest")
    return resized.squeeze(0).squeeze(0).numpy().astype(np.uint8)


def config_dict(config: AdaptiveFusionConfig) -> dict:
    return {
        "stable_self_iou": config.stable_self_iou,
        "stable_area_ratio": config.stable_area_ratio,
        "shrink_ratio": config.shrink_ratio,
        "low_uncertainty": config.low_uncertainty,
        "low_disagreement": config.low_disagreement,
        "min_recovered_component_pixels": config.min_recovered_component_pixels,
    }


def candidate_adaptive_configs() -> list[AdaptiveFusionConfig]:
    return [
        AdaptiveFusionConfig(stable_self_iou=0.95, shrink_ratio=0.60),
        AdaptiveFusionConfig(stable_self_iou=0.95, shrink_ratio=0.72),
        AdaptiveFusionConfig(stable_self_iou=0.97, shrink_ratio=0.72),
        AdaptiveFusionConfig(stable_self_iou=0.98, shrink_ratio=0.80),
    ]


def build_prediction_records(
    pairs: list[tuple[str, str]],
    limit: int | None = None,
    tta_mode: str = "light",
) -> tuple[list[dict], int]:
    model, config = load_model()
    specs = default_tta_specs() if tta_mode == "default" else light_tta_specs()
    selected = pairs[:limit] if limit else pairs
    records = []

    for image_path_str, mask_path_str in selected:
        image_path = Path(image_path_str)
        tensor, _ = preprocess_image(image_path, config.INPUT_SIZE)
        single_probs = predict_single_probs(model, tensor, device=config.DEVICE)
        single_mask = single_probs.argmax(dim=0).numpy().astype(np.uint8)
        tta = predict_tta_probs(model, tensor, specs=specs, device=config.DEVICE)
        fused_mask = tta["fused_mask"].numpy().astype(np.uint8)
        entropy = tta["entropy_uncertainty"].numpy()
        disagreement = tta["disagreement_uncertainty"].numpy()
        target = load_resized_target(mask_path_str, image_path_str, config.INPUT_SIZE, num_classes=config.NUM_CLASSES)
        records.append({
            "image": image_path.name,
            "single_mask": single_mask,
            "fused_mask": fused_mask,
            "target": target,
            "entropy": entropy,
            "disagreement": disagreement,
        })
    return records, config.NUM_CLASSES


def evaluate_records(
    records: list[dict],
    num_classes: int,
    fusion_config: AdaptiveFusionConfig | None = None,
) -> list[dict]:
    per_sample = []
    for record in records:
        selection = select_adaptive_mask(
            single_mask=record["single_mask"],
            fused_mask=record["fused_mask"],
            entropy_uncertainty=record["entropy"],
            disagreement_uncertainty=record["disagreement"],
            config=fusion_config,
        )
        selected_mask = selection["selected_mask"].astype(np.uint8)
        comparison = compare_predictions(
            single_mask=record["single_mask"],
            fused_mask=record["fused_mask"],
            target_mask=record["target"],
            selected_mask=selected_mask,
            selection_mode=selection["selection_mode"],
            uncertainty=record["entropy"],
            num_classes=num_classes,
        )
        comparison["adaptive_selection"] = {
            "mode": selection["selection_mode"],
            "reasons": selection["selection_reasons"],
            "consistency": selection["consistency"],
        }
        comparison["image"] = record["image"]
        per_sample.append(comparison)
    return per_sample


def evaluate_pairs(
    pairs: list[tuple[str, str]],
    output_path: Path,
    limit: int | None = None,
    tta_mode: str = "light",
    fusion_config: AdaptiveFusionConfig | None = None,
) -> dict:
    records, num_classes = build_prediction_records(pairs, limit=limit, tta_mode=tta_mode)
    per_sample = evaluate_records(records, num_classes, fusion_config=fusion_config)

    aggregate = aggregate_comparisons(per_sample)
    result = {
        "num_samples": len(per_sample),
        "tta_mode": tta_mode,
        "adaptive_config": config_dict(fusion_config or AdaptiveFusionConfig()),
        "aggregate": aggregate,
        "samples": per_sample,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def search_adaptive_config(
    pairs: list[tuple[str, str]],
    output_path: Path,
    limit: int | None = None,
    tta_mode: str = "light",
) -> dict:
    records, num_classes = build_prediction_records(pairs, limit=limit, tta_mode=tta_mode)
    candidates = []
    for config in candidate_adaptive_configs():
        per_sample = evaluate_records(records, num_classes, fusion_config=config)
        candidates.append({
            "config": config_dict(config),
            "aggregate": aggregate_comparisons(per_sample),
        })

    best = max(candidates, key=lambda item: item["aggregate"].get("selected_mIoU", -1.0))
    result = {
        "num_samples": len(records),
        "tta_mode": tta_mode,
        "search_split_required": "val",
        "best": best,
        "candidates": candidates,
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
    selection_counts: dict[str, int] = {}
    for item in supported:
        mode = item.get("selection_mode", item.get("adaptive_selection", {}).get("mode", "unknown"))
        selection_counts[mode] = selection_counts.get(mode, 0) + 1
    return {
        "supported": True,
        "single_mIoU": float(np.mean([item["single"]["mIoU"] for item in supported])),
        "fused_mIoU": float(np.mean([item["fused"]["mIoU"] for item in supported])),
        "selected_mIoU": float(np.mean([item["selected"]["mIoU"] for item in supported])),
        "delta_mIoU": float(np.mean([item["delta"]["mIoU"] for item in supported])),
        "delta_selected_vs_single_mIoU": float(np.mean([item["delta"]["selected_vs_single_mIoU"] for item in supported])),
        "delta_selected_vs_fused_mIoU": float(np.mean([item["delta"]["selected_vs_fused_mIoU"] for item in supported])),
        "single_mDice": float(np.mean([item["single"]["mDice"] for item in supported])),
        "fused_mDice": float(np.mean([item["fused"]["mDice"] for item in supported])),
        "selected_mDice": float(np.mean([item["selected"]["mDice"] for item in supported])),
        "selection_mode_counts": selection_counts,
        "mean_uncertainty": float(np.mean(uncertainty_values)) if uncertainty_values else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare single-pass and TTA-fused predictions on labeled samples.")
    parser.add_argument("--data-root", type=Path, default=None, help="Dataset root. Defaults to Config.DATA_ROOT.")
    parser.add_argument("--split", choices=["train", "val", "test", "all"], default="test")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--tta-mode", choices=["light", "default"], default="light")
    parser.add_argument("--search-config", action="store_true", help="Search adaptive fusion thresholds on the val split only.")
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
    if args.search_config:
        if args.split != "val":
            raise ValueError("--search-config must be run with --split val to avoid tuning on test/all data")
        result = search_adaptive_config(split_pairs, output, limit=args.limit, tta_mode=args.tta_mode)
        print(json.dumps(result["best"], indent=2, ensure_ascii=False))
    else:
        result = evaluate_pairs(split_pairs, output, limit=args.limit, tta_mode=args.tta_mode)
        print(json.dumps(result["aggregate"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
