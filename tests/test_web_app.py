import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

import web_app


def test_load_evidence_payload_reads_available_summaries(tmp_path, monkeypatch):
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps({
        "num_samples": 3,
        "metric_summary": {"selected_vs_fused_mIoU": 0.12},
        "review_queue_summary": {
            "supported": True,
            "priority_counts": {"high": 1, "medium": 1, "low": 1, "none": 0},
            "top_k": [{"image": "case.jpg", "priority": "high", "score": 3.2}],
        },
    }), encoding="utf-8")
    monkeypatch.setattr(web_app, "EVIDENCE_FILES", {"test": summary_path, "all": tmp_path / "missing.json"})

    payload = web_app._load_evidence_payload()

    assert payload["ok"] is True
    assert payload["source"] == "generated-json"
    assert payload["summaries"]["test"]["num_samples"] == 3
    assert payload["summaries"]["test"]["review_queue_summary"]["top_k"][0]["image"] == "case.jpg"
    assert payload["missing"] == ["all"]
    assert payload["errors"] == {}


def test_load_evidence_payload_falls_back_when_all_summaries_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "EVIDENCE_FILES", {"test": tmp_path / "missing-test.json"})

    payload = web_app._load_evidence_payload()

    assert payload == {
        "ok": True,
        "source": "fallback",
        "summaries": {},
        "missing": ["test"],
        "errors": {},
    }


def test_load_evidence_payload_reports_invalid_json_without_failing(tmp_path, monkeypatch):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(web_app, "EVIDENCE_FILES", {"all": broken})

    payload = web_app._load_evidence_payload()

    assert payload["ok"] is True
    assert payload["source"] == "fallback"
    assert payload["summaries"] == {}
    assert "all" in payload["errors"]


def test_default_web_config_uses_segformer_probability_source():
    config = web_app.WebModelConfig()

    assert config.model_source == "segformer"
    assert config.python_executable == Path("D:/users/anaconda3/envs/segformer-phase2/python.exe")
    assert config.segformer_config == Path("experiments/segformer_b1/configs/segformer_b1_6cls.py")
    assert config.segformer_checkpoint == Path("experiments/segformer_b1/runs/segformer_b1_6cls/latest.pth")


def test_load_model_once_passes_web_segformer_config(monkeypatch):
    calls = []

    def fake_load_model(**kwargs):
        calls.append(kwargs)
        return "model", "config"

    monkeypatch.setattr(web_app, "load_model", fake_load_model)
    monkeypatch.setattr(web_app, "_MODEL", None)
    monkeypatch.setattr(web_app, "_CONFIG", None)
    monkeypatch.setattr(
        web_app,
        "_WEB_MODEL_CONFIG",
        web_app.WebModelConfig(
            model_source="segformer",
            segformer_config=Path("cfg.py"),
            segformer_checkpoint=Path("ckpt.pth"),
            segformer_repo_root=Path("SegFormer-master"),
            segformer_device="cuda:0",
        ),
    )

    assert web_app._load_model_once() == ("model", "config")
    assert calls == [
        {
            "model_source": "segformer",
            "segformer_config": Path("cfg.py"),
            "segformer_checkpoint": Path("ckpt.pth"),
            "segformer_repo_root": Path("SegFormer-master"),
            "segformer_device": "cuda:0",
        }
    ]


def test_parse_args_applies_web_model_config(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "web_app.py",
            "--model-source",
            "legacy",
            "--python-executable",
            "python",
            "--segformer-device",
            "cpu",
        ],
    )

    args = web_app.parse_args()

    assert args.model_source == "legacy"
    assert args.python_executable == Path("python")
    assert args.segformer_device == "cpu"


def test_windows_web_launcher_defaults_to_segformer_runtime():
    launcher = Path("run_web_app.ps1").read_text(encoding="utf-8")

    assert "D:/users/anaconda3/envs/segformer-phase2/python.exe" in launcher
    assert "[string]$ModelSource = 'segformer'" in launcher
    assert "--model-source $ModelSource" in launcher
    assert "--segformer-config $SegformerConfig" in launcher
    assert "--segformer-checkpoint $SegformerCheckpoint" in launcher


def test_detect_image_exports_structured_report(tmp_path, monkeypatch):
    image = tmp_path / "demo.png"
    Image.fromarray(np.zeros((8, 8, 3), dtype=np.uint8)).save(image)
    image_bytes = image.read_bytes()

    monkeypatch.setattr(web_app, "OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(web_app, "_load_model_once", lambda: ("model", "config"))

    def fake_process_image(model, config, image_path, output_dir, tta_mode="light"):
        artifacts = {
            "single_mask": "demo_single_mask.png",
            "fused_mask": "demo_fused_mask.png",
            "selected_mask": "demo_selected_mask.png",
            "overlay": "demo_overlay.png",
            "selected_overlay": "demo_selected_overlay.png",
            "uncertainty_heatmap": "demo_uncertainty_heatmap.png",
            "disagreement_heatmap": "demo_disagreement_heatmap.png",
            "skeleton": "demo_skeleton.png",
            "report": "demo_report.json",
        }
        return {
            "stem": image_path.stem,
            "mask_source": {
                "name": "segformer_b1",
                "type": "mmsegmentation",
                "probability_tta": True,
            },
            "tta_specs": ["identity", "hflip"],
            "single_prediction_stats": {"0": 56, "1": 8},
            "fused_prediction_stats": {"0": 56, "1": 8},
            "selected_prediction_stats": {"0": 56, "1": 8},
            "self_consistency": {"foreground_iou": 1.0},
            "adaptive_selection": {"mode": "fused", "reasons": ["stable"], "consistency": {"foreground_iou": 1.0}},
            "uncertainty_summary": {"available": True, "mean": 0.1, "defect_mean": 0.2},
            "disagreement_summary": {"available": True, "mean": 0.05, "defect_mean": 0.1},
            "morphology": {"defect_area_pixels": 8},
            "morphology_delta": {"fused_to_selected": {"explanations": ["selected preserves morphology close to fused"]}},
            "risk": {"risk_level": "low", "score": 1.0, "review_required": False, "suggestions": ["routine review"]},
            "review_priority": {"priority": "low", "score": 0.5, "review_required": False, "reasons": ["routine"], "note": "manual review only"},
            "views": web_app._view_payload(
                {
                    "artifacts": artifacts,
                    "uncertainty_summary": {"available": True},
                    "disagreement_summary": {"available": True},
                },
                "job",
                image_path.name,
            ),
            "artifacts": artifacts,
        }

    monkeypatch.setattr(web_app, "process_image", fake_process_image)

    report = web_app._detect_image("demo.png", image_bytes, "light")

    assert report["inspection_report_url"].endswith("demo_inspection_report.json")
    assert report["inspection_report"]["schema_version"] == "inspection-report.v1"
    assert report["inspection_report"]["multidomain_results"]["schema_version"] == "multidomain-result.v1"
    exported = list(tmp_path.rglob("demo_inspection_report.json"))
    assert len(exported) == 1
    saved = json.loads(exported[0].read_text(encoding="utf-8"))
    assert saved["source_image"]["name"] == "demo.png"
    assert saved["artifacts"]["inspection_report"] == "demo_inspection_report.json"


def test_load_robot_route_report_reads_static_asset(tmp_path, monkeypatch):
    route_report = tmp_path / "robot_route_report.json"
    route_report.write_text(json.dumps({"schema_version": "robot-inspection-report.v1", "summary": {"track_count": 1}}), encoding="utf-8")
    monkeypatch.setattr(web_app, "ROBOT_ROUTE_REPORT_FILE", route_report)

    payload = web_app._load_robot_route_report()

    assert payload["ok"] is True
    assert payload["source"] == "static-json"
    assert payload["report"]["schema_version"] == "robot-inspection-report.v1"
    assert payload["report"]["summary"]["track_count"] == 1


def test_load_robot_route_report_falls_back_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "ROBOT_ROUTE_REPORT_FILE", tmp_path / "missing.json")

    payload = web_app._load_robot_route_report()

    assert payload["ok"] is True
    assert payload["source"] == "fallback"
    assert payload["report"] is None
    assert payload["missing"].endswith("missing.json")
