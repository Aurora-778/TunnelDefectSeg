from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import math
import numpy as np
import torch
import torch.nn.functional as F
from torchvision.transforms import InterpolationMode
from torchvision.transforms import functional as TF


@dataclass(frozen=True)
class TTASpec:
    name: str
    hflip: bool = False
    vflip: bool = False
    rotate_degrees: float = 0.0


def default_tta_specs() -> list[TTASpec]:
    """Conservative defaults that preserve defect-class semantics."""
    return [
        TTASpec("identity"),
        TTASpec("hflip", hflip=True),
        TTASpec("rot_neg5", rotate_degrees=-5.0),
        TTASpec("rot_pos5", rotate_degrees=5.0),
    ]


def light_tta_specs() -> list[TTASpec]:
    return [TTASpec("identity"), TTASpec("hflip", hflip=True)]


def apply_tta_transform(tensor: torch.Tensor, spec: TTASpec) -> torch.Tensor:
    out = tensor
    if spec.hflip:
        out = torch.flip(out, dims=(-1,))
    if spec.vflip:
        out = torch.flip(out, dims=(-2,))
    if spec.rotate_degrees:
        out = TF.rotate(
            out,
            angle=spec.rotate_degrees,
            interpolation=InterpolationMode.BILINEAR,
            fill=0.0,
        )
    return out


def invert_tta_transform(tensor: torch.Tensor, spec: TTASpec) -> torch.Tensor:
    out = tensor
    if spec.rotate_degrees:
        out = TF.rotate(
            out,
            angle=-spec.rotate_degrees,
            interpolation=InterpolationMode.BILINEAR,
            fill=0.0,
        )
    if spec.vflip:
        out = torch.flip(out, dims=(-2,))
    if spec.hflip:
        out = torch.flip(out, dims=(-1,))
    return out


def normalize_probabilities(probs: torch.Tensor, eps: float = 1e-8) -> torch.Tensor:
    sums = probs.sum(dim=0, keepdim=True)
    uniform = torch.full_like(probs, 1.0 / probs.shape[0])
    return torch.where(sums > eps, probs / sums.clamp_min(eps), uniform)


def fuse_probabilities(aligned_probs: torch.Tensor) -> torch.Tensor:
    if aligned_probs.ndim != 4:
        raise ValueError("aligned_probs must have shape [T, C, H, W]")
    return normalize_probabilities(aligned_probs.mean(dim=0))


def entropy_uncertainty(mean_probs: torch.Tensor, eps: float = 1e-8) -> torch.Tensor:
    if mean_probs.ndim != 3:
        raise ValueError("mean_probs must have shape [C, H, W]")
    n_classes = mean_probs.shape[0]
    entropy = -(mean_probs.clamp_min(eps) * mean_probs.clamp_min(eps).log()).sum(dim=0)
    if n_classes <= 1:
        return torch.zeros_like(entropy)
    return entropy / math.log(n_classes)


def disagreement_uncertainty(aligned_probs: torch.Tensor) -> torch.Tensor:
    if aligned_probs.ndim != 4:
        raise ValueError("aligned_probs must have shape [T, C, H, W]")
    pred_stack = aligned_probs.argmax(dim=1)
    fused_pred = aligned_probs.mean(dim=0).argmax(dim=0)
    return (pred_stack != fused_pred.unsqueeze(0)).float().mean(dim=0)


@torch.no_grad()
def predict_single_probs(model: torch.nn.Module, image_tensor: torch.Tensor, device: torch.device | str) -> torch.Tensor:
    model.eval()
    x = image_tensor.unsqueeze(0).to(device)
    logits = model(x)[0].detach().cpu()
    return F.softmax(logits, dim=0)


@torch.no_grad()
def predict_tta_probs(
    model: torch.nn.Module,
    image_tensor: torch.Tensor,
    specs: Iterable[TTASpec] | None = None,
    device: torch.device | str = "cpu",
) -> dict:
    model.eval()
    tta_specs = list(specs or default_tta_specs())
    aligned = []

    for spec in tta_specs:
        transformed = apply_tta_transform(image_tensor, spec)
        probs = predict_single_probs(model, transformed, device=device)
        restored = invert_tta_transform(probs, spec)
        aligned.append(normalize_probabilities(restored))

    aligned_probs = torch.stack(aligned, dim=0)
    fused_probs = fuse_probabilities(aligned_probs)
    fused_mask = fused_probs.argmax(dim=0).to(torch.uint8)
    return {
        "specs": [spec.name for spec in tta_specs],
        "aligned_probs": aligned_probs,
        "fused_probs": fused_probs,
        "fused_mask": fused_mask,
        "entropy_uncertainty": entropy_uncertainty(fused_probs),
        "disagreement_uncertainty": disagreement_uncertainty(aligned_probs),
    }


def uncertainty_summary(
    uncertainty: np.ndarray,
    mask: np.ndarray | None = None,
    defect_class_ids: Iterable[int] | None = None,
    high_threshold: float = 0.35,
) -> dict:
    uncertainty = np.asarray(uncertainty, dtype=np.float32)
    if uncertainty.size == 0:
        return {
            "mean": 0.0,
            "max": 0.0,
            "high_fraction": 0.0,
            "defect_mean": 0.0,
            "defect_high_fraction": 0.0,
        }

    summary = {
        "mean": float(np.mean(uncertainty)),
        "max": float(np.max(uncertainty)),
        "high_fraction": float(np.mean(uncertainty >= high_threshold)),
        "defect_mean": 0.0,
        "defect_high_fraction": 0.0,
    }
    if mask is None:
        return summary

    mask = np.asarray(mask)
    class_ids = list(defect_class_ids or [1, 2, 3, 4, 5])
    defect = np.isin(mask, class_ids)
    if np.any(defect):
        defect_unc = uncertainty[defect]
        summary["defect_mean"] = float(np.mean(defect_unc))
        summary["defect_high_fraction"] = float(np.mean(defect_unc >= high_threshold))
    return summary
