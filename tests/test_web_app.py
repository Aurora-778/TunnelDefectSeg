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


def test_load_orchestrator_status_reads_checkpoint(tmp_path, monkeypatch):
    state_path = tmp_path / "run_state.json"
    state_path.write_text(
        json.dumps({"task_status": {"memory": "success", "association": "running", "doc": "failed"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(web_app, "ORCHESTRATOR_STATE_FILE", state_path)

    payload = web_app._load_orchestrator_status()

    assert payload["progress"] == 0.333
    assert payload["running_task"] == "association"
    assert payload["completed"] == ["memory"]
    assert payload["failed"] == ["doc"]
    assert payload["task_status"]["association"] == "running"


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


def test_load_robot_dashboard_reads_generated_artifacts(tmp_path, monkeypatch):
    kict_root = tmp_path / "kict"
    image_dir = kict_root / "images"
    mask_dir = kict_root / "masks"
    image_dir.mkdir(parents=True)
    mask_dir.mkdir(parents=True)
    Image.new("RGB", (12, 12), (80, 80, 80)).save(image_dir / "case.png")
    mask = Image.new("L", (12, 12), 0)
    for x in range(3, 8):
        mask.putpixel((x, 6), 255)
    mask.save(mask_dir / "case.png")

    route_report = tmp_path / "robot_route_report.json"
    route_report.write_text(json.dumps({
        "schema_version": "robot-inspection-report.v1",
        "summary": {"frame_count": 8, "track_count": 3, "review_count": 2, "limitations": ["pose_missing"]},
    }), encoding="utf-8")
    priority_csv = tmp_path / "priority_recheck_list.csv"
    priority_csv.write_text(
        "priority_rank,disease_id,disease_type,attention_level,growth_trend,last_risk_level,recheck_reason\n"
        "2,D002,crack,重点关注,持续增长,高,增长较快\n"
        "1,D001,spalling,重点关注,基本稳定,高,风险较高\n",
        encoding="utf-8",
    )
    growth_csv = tmp_path / "disease_growth_analysis.csv"
    growth_csv.write_text(
        "disease_id,disease_type,inspection_count,growth_trend,attention_level,last_risk_level,last_inspection\n"
        "D001,spalling,3,基本稳定,重点关注,高,I003\n"
        "D002,crack,3,持续增长,重点关注,高,I003\n"
        "D003,crack,2,基本稳定,一般关注,中,I002\n",
        encoding="utf-8",
    )
    mileage_chart = tmp_path / "mileage_risk_distribution.png"
    mileage_chart.write_bytes(b"png")
    kict_records = tmp_path / "robot_kict_frame_records.csv"
    kict_records.write_text(
        "image_id,inspection_id,disease_id,kict_image_file,kict_mask_file,kict_image_path,kict_mask_path,mileage_text,clock_direction\n"
        "I003_000001,I003,D001,images/case.png,masks/case.png,images/case.png,masks/case.png,K12+001.0,3点\n"
        "I003_000002,I003,D002,images/case.png,masks/case.png,images/case.png,masks/case.png,K12+002.0,4点\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(web_app, "ROBOT_ROUTE_REPORT_FILE", route_report)
    monkeypatch.setattr(web_app, "PRIORITY_RECHECK_FILE", priority_csv)
    monkeypatch.setattr(web_app, "DISEASE_GROWTH_FILE", growth_csv)
    monkeypatch.setattr(web_app, "ROBOT_KICT_FRAME_RECORDS_FILE", kict_records)
    monkeypatch.setattr(web_app, "EVIDENCE_OVERLAY_ROOT", tmp_path / "evidence_overlays")
    monkeypatch.setattr(web_app, "KICT_DATASET_ROOTS", [kict_root])
    monkeypatch.setattr(web_app, "VISUALIZATION_FILES", {"mileage_risk": mileage_chart})

    payload = web_app._load_robot_dashboard()

    assert payload["ok"] is True
    assert payload["source"] == "generated-artifacts"
    assert payload["summary"]["frame_count"] == 8
    assert payload["summary"]["inspection_count"] == 3
    assert payload["summary"]["priority_recheck_count"] == 2
    assert payload["summary"]["high_risk_count"] == 2
    assert [row["disease_id"] for row in payload["priority_rechecks"]] == ["D001", "D002"]
    assert payload["growth_distribution"] == {"基本稳定": 2, "持续增长": 1}
    assert payload["visualization_links"]["mileage_risk"] == "/static-outputs/visualizations/mileage_risk_distribution.png"
    assert payload["priority_rechecks"][0]["evidence_status"] == "ok"
    assert payload["priority_rechecks"][0]["evidence_overlay_url"].startswith("/static-outputs/evidence-overlays/")
    assert (tmp_path / "evidence_overlays" / Path(payload["priority_rechecks"][0]["evidence_overlay_url"]).name).exists()
    assert "pose_missing" in payload["limitations"]
    assert "rule_evidence" in payload["limitations"]


def test_load_robot_dashboard_handles_missing_csv_without_failing(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "ROBOT_ROUTE_REPORT_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(web_app, "PRIORITY_RECHECK_FILE", tmp_path / "missing-priority.csv")
    monkeypatch.setattr(web_app, "DISEASE_GROWTH_FILE", tmp_path / "missing-growth.csv")
    monkeypatch.setattr(web_app, "VISUALIZATION_FILES", {})

    payload = web_app._load_robot_dashboard()

    assert payload["ok"] is True
    assert payload["source"] == "fallback"
    assert payload["priority_rechecks"] == []
    assert payload["growth_distribution"] == {}
    assert "priority_recheck_list_missing" in payload["limitations"]
    assert "disease_growth_analysis_missing" in payload["limitations"]


def test_robot_dashboard_api_route_returns_payload(monkeypatch):
    sent = {}
    handler = object.__new__(web_app.DetectionHandler)
    handler.path = "/api/robot-dashboard"
    handler._send_json = lambda payload, status=None: sent.update(payload=payload, status=status)
    monkeypatch.setattr(web_app, "_load_robot_dashboard", lambda: {"ok": True, "summary": {"track_count": 1}})

    web_app.DetectionHandler.do_GET(handler)

    assert sent["payload"]["ok"] is True
    assert sent["payload"]["summary"]["track_count"] == 1


def test_platform_api_routes_return_run_payloads(monkeypatch):
    sent = {}
    handler = object.__new__(web_app.DetectionHandler)
    handler._send_json = lambda payload, status=None: sent.update(payload=payload, status=status)
    monkeypatch.setattr(web_app, "dag_payload", lambda root: {"ok": True, "nodes": [{"id": "memory"}]})
    monkeypatch.setattr(web_app, "runs_payload", lambda root: {"ok": True, "runs": [{"run_id": "run_001"}]})
    monkeypatch.setattr(web_app, "run_payload", lambda root, run_id: {"ok": True, "run_id": run_id})

    handler.path = "/api/dag"
    web_app.DetectionHandler.do_GET(handler)
    assert sent["payload"]["nodes"][0]["id"] == "memory"

    handler.path = "/api/runs"
    web_app.DetectionHandler.do_GET(handler)
    assert sent["payload"]["runs"][0]["run_id"] == "run_001"

    handler.path = "/api/run/run_001"
    web_app.DetectionHandler.do_GET(handler)
    assert sent["payload"]["run_id"] == "run_001"


def test_project_summary_reads_generated_tables(tmp_path, monkeypatch):
    engineering_csv = tmp_path / "disease_engineering_report.csv"
    engineering_csv.write_text(
        "inspection_id,disease_id,disease_type,risk_level\n"
        "I001,D001,crack,高\n"
        "I002,D001,crack,高\n",
        encoding="utf-8",
    )
    growth_csv = tmp_path / "disease_growth_analysis.csv"
    growth_csv.write_text(
        "disease_id,inspection_count,growth_trend,last_risk_level\n"
        "D001,3,明显增长,高\n"
        "D002,2,基本稳定,中\n",
        encoding="utf-8",
    )
    recheck_csv = tmp_path / "priority_recheck_list.csv"
    recheck_csv.write_text("priority_rank,disease_id\n1,D001\n", encoding="utf-8")
    chart = tmp_path / "mileage_risk_distribution.png"
    chart.write_bytes(b"png")

    monkeypatch.setattr(web_app, "ENGINEERING_REPORT_FILE", engineering_csv)
    monkeypatch.setattr(web_app, "DISEASE_GROWTH_FILE", growth_csv)
    monkeypatch.setattr(web_app, "PRIORITY_RECHECK_FILE", recheck_csv)
    monkeypatch.setattr(web_app, "VISUALIZATION_FILES", {"mileage_risk": chart})

    payload = web_app._load_project_summary()

    assert payload["ok"] is True
    assert payload["source"] == "generated-artifacts"
    assert payload["inspection_count"] == 3
    assert payload["disease_count"] == 2
    assert payload["engineering_record_count"] == 2
    assert payload["priority_recheck_count"] == 1
    assert payload["high_risk_count"] == 1
    assert payload["obvious_growth_count"] == 1
    assert payload["visualization_count"] == 1


def test_project_summary_falls_back_when_files_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "ENGINEERING_REPORT_FILE", tmp_path / "missing-engineering.csv")
    monkeypatch.setattr(web_app, "DISEASE_GROWTH_FILE", tmp_path / "missing-growth.csv")
    monkeypatch.setattr(web_app, "PRIORITY_RECHECK_FILE", tmp_path / "missing-recheck.csv")
    monkeypatch.setattr(web_app, "VISUALIZATION_FILES", {})

    payload = web_app._load_project_summary()

    assert payload["ok"] is True
    assert payload["source"] == "fallback"
    assert payload["missing"]


def test_table_api_payloads_keep_expected_fields(tmp_path):
    table = tmp_path / "table.csv"
    table.write_text("disease_id,disease_type,extra\nD001,crack,ignored\n", encoding="utf-8")

    payload = web_app._load_csv_payload(table, "table", ["disease_id", "disease_type", "missing_field"])

    assert payload["ok"] is True
    assert payload["items"] == [{"disease_id": "D001", "disease_type": "crack", "missing_field": ""}]


def test_visualization_assets_use_static_outputs_url(tmp_path, monkeypatch):
    chart = tmp_path / "attention_level_distribution.png"
    chart.write_bytes(b"png")
    monkeypatch.setattr(web_app, "VISUALIZATION_FILES", {"attention_level": chart})

    payload = web_app._load_visualization_assets()

    assert payload["ok"] is True
    assert payload["items"] == [{
        "key": "attention_level",
        "title": "关注等级分布",
        "url": "/static-outputs/visualizations/attention_level_distribution.png",
    }]


def test_visualization_static_route_rejects_path_traversal():
    sent = {}
    handler = object.__new__(web_app.DetectionHandler)
    handler._send_json = lambda payload, status=None: sent.update(payload=payload, status=status)

    web_app.DetectionHandler._serve_visualization_file(handler, "../secret.png")

    assert sent["status"] == web_app.HTTPStatus.NOT_FOUND


def test_evidence_overlay_static_route_rejects_path_traversal():
    sent = {}
    handler = object.__new__(web_app.DetectionHandler)
    handler._send_json = lambda payload, status=None: sent.update(payload=payload, status=status)

    web_app.DetectionHandler._serve_evidence_overlay_file(handler, "../secret.png")

    assert sent["status"] == web_app.HTTPStatus.NOT_FOUND


def test_web_demo_has_robot_dashboard_and_preserves_single_image_review():
    html = Path("web_demo/index.html").read_text(encoding="utf-8")

    assert "机器人隧道巡检病害时空监测 Dashboard" in html
    assert 'data-view-link="dashboard"' in html
    assert 'data-app-view="dashboard"' in html
    assert 'data-app-view="detect"' in html
    assert "function showAppView" in html
    assert "function initAppNavigation" in html
    assert "/api/project-summary" in html
    assert "/api/engineering-report" in html
    assert "/api/growth-analysis" in html
    assert "/api/recheck-list" in html
    assert "/api/visualization-assets" in html
    assert "重点复检清单" in html
    assert "原图 + mask 融合图" in html
    assert "evidence_overlay_url" in html
    assert "工程化病害报告" in html
    assert "跨巡检增长分析" in html
    assert "可视化图表" in html
    assert "单图检测 / 现场复核" in html
    assert "拖入图片实时检测" in html
    assert "/api/detect" in html
    assert "本系统使用 KICT Tunnel Crack Segmentation Dataset" in html
    assert "增强模块优势证据" not in html
    assert "Metric glossary" not in html
    assert "mIoU" in html
    assert "Self IoU" in html
    assert "mask" in html
    assert "GT" in html
    assert "uncertainty" in html


def test_web_demo_hides_inactive_detect_grids_and_expands_dashboard_evidence():
    html = Path("web_demo/index.html").read_text(encoding="utf-8")

    assert ".dashboard.app-view:not(.active-view)" in html
    assert ".lower.app-view:not(.active-view)" in html
    assert ".dashboard.app-view.active-view" in html
    assert ".lower.app-view.active-view" in html
    assert "display: none;" in html
    assert "grid-template-rows: auto auto;" in html
    assert "overflow: visible;" in html
    assert "height: auto;" in html
