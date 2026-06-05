from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


DEFAULT_SEGFORMER_REPO_ROOT = Path(r"C:\Users\26822\Desktop\隧道病害检测\third_party\SegFormer-master")
DEFAULT_SEGFORMER_CONFIG = Path("experiments/segformer_b1/configs/segformer_b1_6cls.py")
DEFAULT_SEGFORMER_CHECKPOINT = Path("experiments/segformer_b1/runs/segformer_b1_6cls/latest.pth")


@dataclass(frozen=True)
class SegFormerRuntimeConfig:
    INPUT_SIZE: tuple[int, int] = (384, 384)
    NUM_CLASSES: int = 6
    DEVICE: str = "cuda:0"
    SAVE_DIR: str = "experiments/segformer_b1/confidence_risk"


def _resolve_existing(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Missing SegFormer {label}: {resolved}")
    return resolved


def _resize_mask(mask: np.ndarray, input_size: tuple[int, int]) -> np.ndarray:
    h, w = input_size
    image = Image.fromarray(np.asarray(mask, dtype=np.uint8), mode="L")
    return np.asarray(image.resize((w, h), Image.NEAREST), dtype=np.uint8)


class SegFormerMaskSource:
    def __init__(self, model, config_path: Path, checkpoint_path: Path, device: str):
        self.model = model
        self.config_path = config_path
        self.checkpoint_path = checkpoint_path
        self.device = device

    def metadata(self) -> dict:
        return {
            "name": "segformer_b1",
            "type": "mmsegmentation",
            "config": str(self.config_path),
            "checkpoint": str(self.checkpoint_path),
            "device": self.device,
            "probability_tta": False,
            "note": "SegFormer mask source; confidence-risk artifacts are generated from mask output.",
        }

    def predict_confidence_inputs(
        self,
        image_path: Path,
        input_size: tuple[int, int],
        tta_mode: str = "light",
    ) -> dict:
        from mmseg.apis import inference_segmentor

        result = inference_segmentor(self.model, str(image_path))
        if not result:
            raise RuntimeError(f"SegFormer produced no prediction for: {image_path}")

        single_mask = _resize_mask(np.asarray(result[0], dtype=np.uint8), input_size)
        uncertainty = np.zeros(single_mask.shape, dtype=np.float32)
        raw = Image.open(image_path).convert("RGB")
        raw_resized = np.asarray(raw.resize((input_size[1], input_size[0]), Image.BILINEAR))

        return {
            "raw_resized": raw_resized,
            "single_mask": single_mask,
            "fused_mask": single_mask.copy(),
            "entropy_uncertainty": uncertainty,
            "disagreement_uncertainty": uncertainty.copy(),
            "tta_specs": ["segformer_single"],
            "mask_source": self.metadata(),
        }


def load_segformer_mask_source(
    config_path: Path = DEFAULT_SEGFORMER_CONFIG,
    checkpoint_path: Path = DEFAULT_SEGFORMER_CHECKPOINT,
    repo_root: Path = DEFAULT_SEGFORMER_REPO_ROOT,
    device: str = "cuda:0",
) -> tuple[SegFormerMaskSource, SegFormerRuntimeConfig]:
    config_path = _resolve_existing(config_path, "config")
    checkpoint_path = _resolve_existing(checkpoint_path, "checkpoint")
    repo_root = _resolve_existing(repo_root, "repo root")

    repo_str = str(repo_root)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)

    try:
        from mmseg.apis import init_segmentor
    except Exception as exc:  # pragma: no cover - depends on the external SegFormer env
        raise RuntimeError(
            "Unable to import mmseg. Run SegFormer mode from the segformer-phase2 environment "
            "or use --model-source legacy."
        ) from exc

    model = init_segmentor(str(config_path), str(checkpoint_path), device=device)
    return SegFormerMaskSource(model, config_path, checkpoint_path, device), SegFormerRuntimeConfig(DEVICE=device)
