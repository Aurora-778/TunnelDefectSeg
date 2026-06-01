import numpy as np
import pytest
import torch

from tta_confidence import (
    TTASpec,
    apply_tta_transform,
    disagreement_uncertainty,
    entropy_uncertainty,
    fuse_probabilities,
    invert_tta_transform,
    predict_tta_probs,
    uncertainty_summary,
)


class ConstantModel(torch.nn.Module):
    def __init__(self, num_classes=3):
        super().__init__()
        self.num_classes = num_classes

    def forward(self, x):
        b, _, h, w = x.shape
        logits = torch.zeros((b, self.num_classes, h, w), dtype=torch.float32, device=x.device)
        logits[:, 1] = 5.0
        return logits


def test_identity_only_tta_returns_expected_mask_shape():
    model = ConstantModel(num_classes=3)
    image = torch.zeros((3, 8, 10), dtype=torch.float32)

    result = predict_tta_probs(model, image, specs=[TTASpec("identity")], device="cpu")

    assert result["fused_mask"].shape == (8, 10)
    assert result["fused_probs"].shape == (3, 8, 10)
    assert set(torch.unique(result["fused_mask"]).tolist()) == {1}


def test_horizontal_flip_inverse_restores_tensor():
    tensor = torch.arange(12, dtype=torch.float32).reshape(1, 3, 4)
    spec = TTASpec("hflip", hflip=True)

    restored = invert_tta_transform(apply_tta_transform(tensor, spec), spec)

    assert torch.equal(restored, tensor)


def test_fuse_probabilities_preserves_probability_simplex():
    aligned = torch.zeros((2, 3, 2, 2), dtype=torch.float32)
    aligned[:, 0] = 0.2
    aligned[:, 1] = 0.3
    aligned[:, 2] = 0.5

    fused = fuse_probabilities(aligned)

    assert fused.shape == (3, 2, 2)
    assert torch.allclose(fused.sum(dim=0), torch.ones((2, 2)))
    assert torch.allclose(fused[2], torch.full((2, 2), 0.5))


def test_entropy_uncertainty_low_for_confident_high_for_ambiguous():
    confident = torch.tensor([[[1.0]], [[0.0]], [[0.0]]])
    ambiguous = torch.tensor([[[1 / 3]], [[1 / 3]], [[1 / 3]]], dtype=torch.float32)

    assert entropy_uncertainty(confident).item() < 0.01
    assert entropy_uncertainty(ambiguous).item() > 0.99


def test_disagreement_uncertainty_increases_when_predictions_disagree():
    aligned = torch.zeros((3, 2, 1, 2), dtype=torch.float32)
    aligned[0, 0, 0, :] = 1.0
    aligned[1, 0, 0, 0] = 1.0
    aligned[1, 1, 0, 1] = 1.0
    aligned[2, 0, 0, :] = 1.0

    disagreement = disagreement_uncertainty(aligned)

    assert disagreement[0, 0].item() == 0.0
    assert disagreement[0, 1].item() > 0.0


def test_uncertainty_summary_separates_defect_pixels():
    unc = np.array([[0.1, 0.8], [0.2, 0.6]], dtype=np.float32)
    mask = np.array([[0, 1], [0, 1]], dtype=np.uint8)

    summary = uncertainty_summary(unc, mask=mask, high_threshold=0.5)

    assert summary["mean"] == float(np.mean(unc))
    assert summary["defect_mean"] == pytest.approx(float(np.mean([0.8, 0.6])))
    assert summary["defect_high_fraction"] == 1.0
