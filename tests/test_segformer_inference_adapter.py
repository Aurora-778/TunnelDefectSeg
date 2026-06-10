import numpy as np
import pytest

from segformer_inference_adapter import (
    _coerce_probability_output,
    _confidence_inputs_from_aligned_probabilities,
    _disagreement_from_aligned_probabilities,
    _entropy_from_probabilities,
    _mask_from_probabilities,
    _normalize_probabilities,
    _resize_probabilities,
    _segformer_tta_specs,
)


def test_normalize_probabilities_preserves_simplex_and_fills_zero_pixels():
    probs = np.zeros((3, 2, 2), dtype=np.float32)
    probs[1, 0, 0] = 2.0
    probs[2, 0, 0] = 2.0

    normalized = _normalize_probabilities(probs)

    np.testing.assert_allclose(normalized.sum(axis=0), 1.0)
    assert normalized[1, 0, 0] == pytest.approx(0.5)
    assert normalized[2, 0, 0] == pytest.approx(0.5)
    np.testing.assert_allclose(normalized[:, 1, 1], np.array([1 / 3, 1 / 3, 1 / 3], dtype=np.float32))


def test_resize_probabilities_keeps_channel_sum_one():
    probs = np.zeros((2, 2, 2), dtype=np.float32)
    probs[0, :, 0] = 0.9
    probs[1, :, 0] = 0.1
    probs[0, :, 1] = 0.2
    probs[1, :, 1] = 0.8

    resized = _resize_probabilities(probs, (4, 5))

    assert resized.shape == (2, 4, 5)
    np.testing.assert_allclose(resized.sum(axis=0), 1.0, atol=1e-6)


def test_coerce_probability_output_rejects_malformed_probability_maps():
    with pytest.raises(ValueError, match="shape"):
        _coerce_probability_output(np.zeros((2, 2), dtype=np.float32))

    bad = np.ones((2, 2, 2), dtype=np.float32)
    bad[0, 0, 0] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        _coerce_probability_output(bad)

    batched = np.ones((2, 3, 2, 2), dtype=np.float32)
    with pytest.raises(ValueError, match="exactly one image"):
        _coerce_probability_output(batched)


def test_entropy_and_mask_come_from_fused_probabilities():
    probs = np.zeros((3, 3, 3), dtype=np.float32)
    probs[0] = 0.1
    probs[1] = 0.8
    probs[2] = 0.1

    mask = _mask_from_probabilities(probs)
    entropy = _entropy_from_probabilities(probs)

    assert np.all(mask == 1)
    assert entropy.shape == (3, 3)
    assert float(entropy.mean()) > 0.0
    assert float(entropy.max()) <= 1.0


def test_disagreement_detects_conflicting_tta_predictions():
    identity = np.zeros((2, 3, 3), dtype=np.float32)
    identity[1, 1, 1] = 1.0
    identity[0, identity[1] == 0] = 1.0

    hflip = identity.copy()
    hflip[:, 1, 1] = 0.0
    hflip[0, 1, 1] = 1.0

    disagreement = _disagreement_from_aligned_probabilities(np.stack([identity, hflip], axis=0))

    assert disagreement[1, 1] == pytest.approx(0.5)
    assert disagreement.sum() == pytest.approx(0.5)


def test_confidence_inputs_from_aligned_probabilities_reports_available_probability_tta():
    raw = np.zeros((4, 4, 3), dtype=np.uint8)
    identity = np.zeros((2, 4, 4), dtype=np.float32)
    identity[1, 1:3, 1:3] = 0.9
    identity[0] = 1.0 - identity[1]

    hflip = identity.copy()
    hflip[1, 2, 2] = 0.2
    hflip[0, 2, 2] = 0.8

    result = _confidence_inputs_from_aligned_probabilities(
        raw_resized=raw,
        aligned_probabilities=np.stack([identity, hflip], axis=0),
        tta_specs=["segformer_identity", "segformer_hflip"],
        mask_source={"name": "segformer_b1"},
    )

    assert result["raw_resized"].shape == raw.shape
    assert result["single_mask"].shape == (4, 4)
    assert result["fused_mask"].shape == (4, 4)
    assert result["entropy_uncertainty"].shape == (4, 4)
    assert result["disagreement_uncertainty"].shape == (4, 4)
    assert result["mask_source"]["probability_tta"] is True
    assert result["mask_source"]["uncertainty_available"] is True
    assert result["mask_source"]["disagreement_available"] is True
    assert result["tta_specs"] == ["segformer_identity", "segformer_hflip"]
    assert float(result["disagreement_uncertainty"].max()) > 0.0


def test_segformer_tta_specs_support_identity_only_and_light_modes():
    assert [spec.name for spec in _segformer_tta_specs("single")] == ["segformer_identity"]
    assert [spec.name for spec in _segformer_tta_specs("light")] == ["segformer_identity", "segformer_hflip"]
