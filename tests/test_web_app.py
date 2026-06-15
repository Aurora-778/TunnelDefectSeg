import json
import sys
from pathlib import Path

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
