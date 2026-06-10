from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
import tempfile

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


@dataclass(frozen=True)
class SegFormerTTASpec:
    name: str
    hflip: bool = False


def _resolve_existing(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Missing SegFormer {label}: {resolved}")
    return resolved


def _resize_mask(mask: np.ndarray, input_size: tuple[int, int]) -> np.ndarray:
    h, w = input_size
    image = Image.fromarray(np.asarray(mask, dtype=np.uint8), mode="L")
    return np.asarray(image.resize((w, h), Image.NEAREST), dtype=np.uint8)


def _normalize_probabilities(probabilities: np.ndarray, eps: float = 1e-8) -> np.ndarray:
    probs = np.asarray(probabilities, dtype=np.float32)
    if probs.ndim != 3:
        raise ValueError("SegFormer probabilities must have shape [C, H, W]")
    if probs.shape[0] < 2 or probs.shape[1] == 0 or probs.shape[2] == 0:
        raise ValueError("SegFormer probabilities must contain at least two classes and non-empty spatial axes")
    if not np.all(np.isfinite(probs)):
        raise ValueError("SegFormer probabilities contain NaN or infinite values")
    if np.any(probs < -eps):
        raise ValueError("SegFormer probabilities must be non-negative")

    probs = np.clip(probs, 0.0, None)
    sums = probs.sum(axis=0, keepdims=True)
    uniform = np.full_like(probs, 1.0 / probs.shape[0], dtype=np.float32)
    return np.divide(probs, np.maximum(sums, eps), out=uniform, where=sums > eps).astype(np.float32)


def _coerce_probability_output(output) -> np.ndarray:
    if hasattr(output, "detach"):
        output = output.detach().cpu().numpy()
    probs = np.asarray(output, dtype=np.float32)
    if probs.ndim == 4:
        if probs.shape[0] != 1:
            raise ValueError("SegFormer probability batch output must contain exactly one image")
        probs = probs[0]
    return _normalize_probabilities(probs)


def _resize_probabilities(probabilities: np.ndarray, input_size: tuple[int, int]) -> np.ndarray:
    probs = _normalize_probabilities(probabilities)
    h, w = input_size
    if probs.shape[1:] == (h, w):
        return probs

    resized_channels = []
    for channel in probs:
        image = Image.fromarray(channel.astype(np.float32), mode="F")
        resized = image.resize((w, h), Image.BILINEAR)
        resized_channels.append(np.asarray(resized, dtype=np.float32))
    return _normalize_probabilities(np.stack(resized_channels, axis=0))


def _mask_from_probabilities(probabilities: np.ndarray) -> np.ndarray:
    return _normalize_probabilities(probabilities).argmax(axis=0).astype(np.uint8)


def _entropy_from_probabilities(probabilities: np.ndarray, eps: float = 1e-8) -> np.ndarray:
    probs = _normalize_probabilities(probabilities)
    entropy = -(np.clip(probs, eps, 1.0) * np.log(np.clip(probs, eps, 1.0))).sum(axis=0)
    if probs.shape[0] <= 1:
        return np.zeros_like(entropy, dtype=np.float32)
    return (entropy / math.log(probs.shape[0])).astype(np.float32)


def _normalize_aligned_probabilities(aligned_probabilities: np.ndarray) -> np.ndarray:
    aligned = np.asarray(aligned_probabilities, dtype=np.float32)
    if aligned.ndim != 4:
        raise ValueError("Aligned SegFormer probabilities must have shape [T, C, H, W]")
    if aligned.shape[0] < 1:
        raise ValueError("At least one SegFormer TTA probability map is required")
    return np.stack([_normalize_probabilities(item) for item in aligned], axis=0)


def _disagreement_from_aligned_probabilities(aligned_probabilities: np.ndarray) -> np.ndarray:
    aligned = _normalize_aligned_probabilities(aligned_probabilities)
    pred_stack = aligned.argmax(axis=1)
    fused_pred = _mask_from_probabilities(aligned.mean(axis=0))
    return (pred_stack != fused_pred[None, :, :]).mean(axis=0).astype(np.float32)


def _confidence_inputs_from_aligned_probabilities(
    raw_resized: np.ndarray,
    aligned_probabilities: np.ndarray,
    tta_specs: list[str],
    mask_source: dict,
) -> dict:
    aligned = _normalize_aligned_probabilities(aligned_probabilities)
    fused_probs = _normalize_probabilities(aligned.mean(axis=0))
    source = dict(mask_source)
    source["probability_tta"] = True
    source["uncertainty_available"] = True
    source["disagreement_available"] = True

    return {
        "raw_resized": np.asarray(raw_resized, dtype=np.uint8),
        "single_mask": _mask_from_probabilities(aligned[0]),
        "fused_mask": _mask_from_probabilities(fused_probs),
        "entropy_uncertainty": _entropy_from_probabilities(fused_probs),
        "disagreement_uncertainty": _disagreement_from_aligned_probabilities(aligned),
        "tta_specs": list(tta_specs),
        "mask_source": source,
    }


def _segformer_tta_specs(tta_mode: str = "light") -> list[SegFormerTTASpec]:
    mode = (tta_mode or "light").lower()
    identity = SegFormerTTASpec("segformer_identity")
    if mode in {"none", "off", "single", "identity"}:
        return [identity]
    return [identity, SegFormerTTASpec("segformer_hflip", hflip=True)]


def _prepare_mmseg_data(model, image_input):
    from mmcv.parallel import collate, scatter
    from mmseg.apis.inference import LoadImage
    from mmseg.datasets.pipelines import Compose

    cfg = model.cfg
    device = next(model.parameters()).device
    test_pipeline = Compose([LoadImage()] + cfg.data.test.pipeline[1:])
    data = test_pipeline(dict(img=image_input))
    data = collate([data], samples_per_gpu=1)
    if next(model.parameters()).is_cuda:
        data = scatter(data, [device])[0]
    else:
        data["img_metas"] = [item.data[0] for item in data["img_metas"]]
    return data


def _predict_segformer_probabilities(model, image_input, input_size: tuple[int, int]) -> np.ndarray:
    try:
        import torch
    except Exception as exc:  # pragma: no cover - depends on external SegFormer env
        raise RuntimeError("Unable to import torch for SegFormer probability inference.") from exc

    if not hasattr(model, "inference"):
        raise RuntimeError("SegFormer model does not expose probability inference().")

    data = _prepare_mmseg_data(model, image_input)
    with torch.no_grad():
        output = model.inference(data["img"][0], data["img_metas"][0], rescale=True)
    return _resize_probabilities(_coerce_probability_output(output), input_size)


def _flipped_temp_image_path(image: Image.Image) -> Path:
    handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    temp_path = Path(handle.name)
    handle.close()
    image.transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(temp_path)
    return temp_path


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
            "probability_tta": True,
            "uncertainty_available": True,
            "disagreement_available": True,
            "note": "SegFormer probability source with identity/hflip TTA, entropy uncertainty, and TTA disagreement.",
        }

    def predict_confidence_inputs(
        self,
        image_path: Path,
        input_size: tuple[int, int],
        tta_mode: str = "light",
    ) -> dict:
        raw = Image.open(image_path).convert("RGB")
        raw_resized = np.asarray(raw.resize((input_size[1], input_size[0]), Image.BILINEAR))
        specs = _segformer_tta_specs(tta_mode)

        aligned_probabilities = []
        for spec in specs:
            temp_path: Path | None = None
            try:
                image_input = str(image_path)
                if spec.hflip:
                    temp_path = _flipped_temp_image_path(raw)
                    image_input = str(temp_path)
                probs = _predict_segformer_probabilities(self.model, image_input, input_size)
                if spec.hflip:
                    probs = probs[:, :, ::-1]
                aligned_probabilities.append(_normalize_probabilities(probs))
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)

        return _confidence_inputs_from_aligned_probabilities(
            raw_resized=raw_resized,
            aligned_probabilities=np.stack(aligned_probabilities, axis=0),
            tta_specs=[spec.name for spec in specs],
            mask_source=self.metadata(),
        )


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
