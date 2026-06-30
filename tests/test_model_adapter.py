import sys
import types

import numpy as np
import pytest
from PIL import Image

import run_confidence_risk
from run_confidence_risk import _validate_prediction_dict, load_model, process_image


def _prediction(**overrides):
    mask = np.zeros((8, 8), dtype=np.uint8)
    prediction = {
        "raw_resized": np.zeros((8, 8, 3), dtype=np.uint8),
        "single_mask": mask,
        "fused_mask": mask.copy(),
        "entropy_uncertainty": np.zeros((8, 8), dtype=np.float32),
        "disagreement_uncertainty": np.zeros((8, 8), dtype=np.float32),
        "tta_specs": [],
        "mask_source": {"name": "fake", "type": "synthetic"},
    }
    prediction.update(overrides)
    return prediction


def test_validate_prediction_dict_accepts_complete_prediction():
    prediction = _prediction()

    assert _validate_prediction_dict(prediction) is prediction


@pytest.mark.parametrize("prediction", [None, "not-a-dict"])
def test_validate_prediction_dict_rejects_non_dict_prediction(prediction):
    with pytest.raises(ValueError, match="Model adapter prediction must be a dict"):
        _validate_prediction_dict(prediction)


@pytest.mark.parametrize("missing_field", ["single_mask", "mask_source"])
def test_validate_prediction_dict_reports_missing_required_field(missing_field):
    prediction = _prediction()
    prediction.pop(missing_field)

    with pytest.raises(ValueError, match=missing_field):
        _validate_prediction_dict(prediction)


def test_validate_prediction_dict_reports_all_missing_required_fields():
    prediction = _prediction()
    prediction.pop("single_mask")
    prediction.pop("mask_source")

    with pytest.raises(ValueError) as exc_info:
        _validate_prediction_dict(prediction)

    message = str(exc_info.value)
    assert "single_mask" in message
    assert "mask_source" in message


def test_load_model_dispatches_segformer_source(monkeypatch, tmp_path):
    calls = []

    def fake_loader(**kwargs):
        calls.append(kwargs)
        return "segformer-model", "segformer-config"

    monkeypatch.setattr(run_confidence_risk, "load_segformer_mask_source", fake_loader)

    result = load_model(
        model_source="segformer",
        segformer_config=tmp_path / "config.py",
        segformer_checkpoint=tmp_path / "checkpoint.pth",
        segformer_repo_root=tmp_path / "SegFormer-master",
        segformer_device="cpu",
    )

    assert result == ("segformer-model", "segformer-config")
    assert calls == [
        {
            "config_path": tmp_path / "config.py",
            "checkpoint_path": tmp_path / "checkpoint.pth",
            "repo_root": tmp_path / "SegFormer-master",
            "device": "cpu",
        }
    ]


def test_load_model_dispatches_legacy_source(monkeypatch, tmp_path):
    calls = []
    save_dir = tmp_path / "legacy"
    save_dir.mkdir()
    (save_dir / "best_model.pth").write_bytes(b"checkpoint")

    class FakeConfig:
        DATA_ROOT = str(tmp_path)
        PRETRAINED = str(tmp_path / "pretrained.pth")
        NUM_CLASSES = 6
        DEVICE = "cpu"
        SAVE_DIR = str(save_dir)

    class FakeModel:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        def to(self, device):
            calls.append(("to", device))
            return self

        def load_state_dict(self, state):
            calls.append(("load_state_dict", state))

        def eval(self):
            calls.append(("eval", None))

    fake_module = types.SimpleNamespace(
        Config=FakeConfig,
        ResNet50SegmentationModel=FakeModel,
    )
    monkeypatch.setitem(sys.modules, "train_resnet50", fake_module)
    monkeypatch.setattr(run_confidence_risk.torch, "load", lambda *args, **kwargs: {"model_state": {"ok": True}})

    model, config = load_model(model_source="legacy")

    assert isinstance(model, FakeModel)
    assert config is FakeConfig
    assert calls[0][0] == "init"
    assert calls[0][1]["num_classes"] == 6
    assert calls[0][1]["backbone"] == "fcn"
    assert ("to", "cpu") in calls
    assert ("load_state_dict", {"ok": True}) in calls
    assert ("eval", None) in calls


def test_load_model_rejects_unknown_source():
    with pytest.raises(ValueError, match="Unsupported model source: unknown"):
        load_model(model_source="unknown")


class _NoUncertaintyConfig:
    INPUT_SIZE = (8, 8)
    NUM_CLASSES = 6
    DEVICE = "cpu"
    SAVE_DIR = "unused"


class _NoUncertaintySource:
    def predict_confidence_inputs(self, image_path, input_size, tta_mode="light"):
        mask = np.zeros(input_size, dtype=np.uint8)
        mask[2, 2:5] = 1
        return _prediction(
            raw_resized=np.zeros((*input_size, 3), dtype=np.uint8),
            single_mask=mask,
            fused_mask=mask.copy(),
            entropy_uncertainty=np.zeros(input_size, dtype=np.float32),
            disagreement_uncertainty=np.zeros(input_size, dtype=np.float32),
            tta_specs=[],
            mask_source={
                "name": "argmax_only_fake",
                "type": "synthetic",
                "uncertainty_available": False,
                "disagreement_available": False,
            },
        )


def test_process_image_preserves_unavailable_uncertainty_contract(tmp_path):
    image_path = tmp_path / "sample.png"
    Image.fromarray(np.zeros((8, 8, 3), dtype=np.uint8)).save(image_path)

    report = process_image(_NoUncertaintySource(), _NoUncertaintyConfig, image_path, tmp_path)

    assert report["mask_source"]["uncertainty_available"] is False
    assert report["mask_source"]["disagreement_available"] is False
    assert report["uncertainty_summary"]["available"] is False
    assert report["disagreement_summary"]["available"] is False
    assert "placeholder artifact" in report["uncertainty_summary"]["reason"]
