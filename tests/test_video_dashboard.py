import csv
import io
import json
from http import HTTPStatus
from pathlib import Path

import web_app


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _patch_video_roots(monkeypatch, tmp_path):
    video_root = tmp_path / "data" / "videos"
    frame_root = tmp_path / "data" / "video_frames"
    inspection_root = tmp_path / "data" / "video_inspection"
    output_root = tmp_path / "outputs" / "video_inspection"
    monkeypatch.setattr(web_app, "VIDEO_DATA_ROOT", video_root)
    monkeypatch.setattr(web_app, "VIDEO_FRAME_ROOT", frame_root)
    monkeypatch.setattr(web_app, "VIDEO_INSPECTION_ROOT", inspection_root)
    monkeypatch.setattr(web_app, "VIDEO_OUTPUT_ROOT", output_root)
    return video_root, frame_root, inspection_root, output_root


def test_video_dashboard_payload_handles_missing_artifacts(tmp_path, monkeypatch):
    _patch_video_roots(monkeypatch, tmp_path)

    payload = web_app._load_video_dashboard("tunnel_demo")

    assert payload["ok"] is True
    assert payload["source"] == "fallback"
    assert "尚未生成该视频分析产物" in payload["missing_message"]
    assert all(video["exists"] is False for video in payload["videos"])
    assert all(table["exists"] is False for table in payload["tables"])
    assert any("不是真实机器人连续巡检视频" in note for note in payload["boundary_notes"])


def test_video_dashboard_payload_exposes_videos_and_tables(tmp_path, monkeypatch):
    video_root, frame_root, inspection_root, output_root = _patch_video_roots(monkeypatch, tmp_path)
    video_root.mkdir(parents=True)
    (video_root / "tunnel_demo.mp4").write_bytes(b"demo")
    (frame_root / "tunnel_demo").mkdir(parents=True)
    (frame_root / "tunnel_demo" / "frame_000001.jpg").write_bytes(b"poster")
    (output_root / "tunnel_demo").mkdir(parents=True)
    (output_root / "tunnel_demo" / "annotated_video.mp4").write_bytes(b"opencv")
    (output_root / "tunnel_demo" / "supervision_annotated_video.mp4").write_bytes(b"supervision")
    (output_root / "tunnel_demo" / "annotated_frames").mkdir(parents=True)
    (output_root / "tunnel_demo" / "annotated_frames" / "frame_000001.jpg").write_bytes(b"poster")
    (output_root / "tunnel_demo" / "supervision_annotated_frames").mkdir(parents=True)
    (output_root / "tunnel_demo" / "supervision_annotated_frames" / "frame_000001.jpg").write_bytes(b"poster")
    _write_csv(
        inspection_root / "tunnel_demo" / "disease_features.csv",
        [{"frame_id": "frame_000001", "disease_area": "12", "risk_level": "low"}],
    )
    _write_csv(
        inspection_root / "tunnel_demo" / "inspection_sequence.csv",
        [{"inspection_id": "tunnel_demo", "frame_id": "frame_000001", "mileage_text": "K0+000.0"}],
    )
    _write_csv(
        output_root / "tunnel_demo" / "video_visualization_manifest.csv",
        [{"frame_id": "frame_000001", "overlay_available": "true"}],
    )
    _write_csv(
        output_root / "tunnel_demo" / "supervision_visualization_manifest.csv",
        [{"frame_id": "frame_000001", "visualization_source": "supervision_optional_layer"}],
    )

    payload = web_app._load_video_dashboard("tunnel_demo")

    assert payload["source"] == "generated-video-artifacts"
    assert {video["key"]: video["exists"] for video in payload["videos"]} == {
        "demo_video": True,
        "opencv_annotated_video": True,
        "supervision_annotated_video": True,
    }
    assert "/static-video/tunnel_demo/annotated_video.mp4" in json.dumps(payload, ensure_ascii=False)
    assert {video["key"]: video["poster_exists"] for video in payload["videos"]} == {
        "demo_video": True,
        "opencv_annotated_video": True,
        "supervision_annotated_video": True,
    }
    assert "/static-video/tunnel_demo/annotated_poster.jpg" in json.dumps(payload, ensure_ascii=False)
    disease_table = next(table for table in payload["tables"] if table["title"] == "disease_features.csv")
    assert disease_table["rows"][0]["disease_area"] == "12"
    assert disease_table["columns"] == ["frame_id", "disease_area", "risk_level"]


def test_video_dashboard_api_route_can_be_served(tmp_path, monkeypatch):
    _patch_video_roots(monkeypatch, tmp_path)
    captured = {}
    handler = object.__new__(web_app.DetectionHandler)
    handler.path = "/api/video-dashboard"
    handler._send_json = lambda payload, status=HTTPStatus.OK: captured.update(payload=payload, status=status)

    web_app.DetectionHandler.do_GET(handler)

    assert captured["status"] == HTTPStatus.OK
    assert captured["payload"]["video_id"] == "tunnel_demo"
    assert captured["payload"]["ok"] is True


def test_video_dashboard_static_route_rejects_path_traversal():
    captured = {}
    handler = object.__new__(web_app.DetectionHandler)
    handler._send_json = lambda payload, status=HTTPStatus.OK: captured.update(payload=payload, status=status)

    web_app.DetectionHandler._serve_video_file(handler, "../bad", "annotated_video.mp4")

    assert captured["status"] == HTTPStatus.NOT_FOUND


def test_video_dashboard_static_route_rejects_filename_traversal():
    captured = {}
    handler = object.__new__(web_app.DetectionHandler)
    handler._send_json = lambda payload, status=HTTPStatus.OK: captured.update(payload=payload, status=status)

    web_app.DetectionHandler._serve_video_file(handler, "tunnel_demo", "../bad.mp4")

    assert captured["status"] == HTTPStatus.NOT_FOUND


def test_video_dashboard_static_route_serves_annotated_video(tmp_path, monkeypatch):
    _, _, _, output_root = _patch_video_roots(monkeypatch, tmp_path)
    video_path = output_root / "tunnel_demo" / "annotated_video.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"fake-mp4")
    captured = {"headers": []}
    handler = object.__new__(web_app.DetectionHandler)
    handler.send_response = lambda status: captured.update(status=status)
    handler.send_header = lambda key, value: captured["headers"].append((key, value))
    handler.end_headers = lambda: captured.update(ended=True)
    handler.wfile = io.BytesIO()

    web_app.DetectionHandler._serve_video_file(handler, "tunnel_demo", "annotated_video.mp4")

    assert captured["status"] == HTTPStatus.OK
    assert ("Content-Type", "video/mp4") in captured["headers"]
    assert handler.wfile.getvalue() == b"fake-mp4"


def test_video_dashboard_static_route_serves_poster_frame(tmp_path, monkeypatch):
    _, frame_root, _, _ = _patch_video_roots(monkeypatch, tmp_path)
    poster_path = frame_root / "tunnel_demo" / "frame_000001.jpg"
    poster_path.parent.mkdir(parents=True)
    poster_path.write_bytes(b"fake-jpg")
    captured = {"headers": []}
    handler = object.__new__(web_app.DetectionHandler)
    handler.send_response = lambda status: captured.update(status=status)
    handler.send_header = lambda key, value: captured["headers"].append((key, value))
    handler.end_headers = lambda: captured.update(ended=True)
    handler.wfile = io.BytesIO()

    web_app.DetectionHandler._serve_video_file(handler, "tunnel_demo", "demo_poster.jpg")

    assert captured["status"] == HTTPStatus.OK
    assert ("Content-Type", "image/jpeg") in captured["headers"]
    assert handler.wfile.getvalue() == b"fake-jpg"


def test_video_dashboard_bad_csv_does_not_crash(tmp_path, monkeypatch):
    _, _, inspection_root, _ = _patch_video_roots(monkeypatch, tmp_path)
    bad_csv = inspection_root / "tunnel_demo" / "disease_features.csv"
    bad_csv.parent.mkdir(parents=True)
    bad_csv.write_bytes(b"\xff\xfe\xfa")

    payload = web_app._load_video_dashboard("tunnel_demo")

    disease_table = next(table for table in payload["tables"] if table["title"] == "disease_features.csv")
    assert disease_table["exists"] is False
    assert "disease_features.csv" in payload["errors"]
    assert "尚未生成该视频分析产物" in disease_table["missing_message"]


def test_video_dashboard_html_contains_nav_and_boundary_copy():
    html = Path("web_demo/index.html").read_text(encoding="utf-8")

    assert 'data-view-link="video-analysis"' in html
    assert "/api/video-dashboard" in html
    assert "tunnel_demo.mp4 是由 KICT 静态裂缝图像和 mask 合成的 demo video" in html
    assert "当前系统不是实时视频流分析系统" in html
    assert "supervision 只是可选可视化工具层" in html
    assert "video.poster_url" in html


def test_video_dashboard_page_only_renders_video_cards_not_csv_tables():
    html = Path("web_demo/index.html").read_text(encoding="utf-8")

    assert 'id="videoCards"' in html
    assert 'id="videoTables"' not in html
    assert "renderVideoTable" not in html
    assert "video.path" not in html


def test_video_dashboard_does_not_touch_simulated_outputs(tmp_path, monkeypatch):
    _patch_video_roots(monkeypatch, tmp_path)
    simulated = tmp_path / "data" / "simulated"
    simulated.mkdir(parents=True)
    sentinel = simulated / "sentinel.csv"
    sentinel.write_text("keep", encoding="utf-8")

    web_app._load_video_dashboard("tunnel_demo")

    assert sentinel.read_text(encoding="utf-8") == "keep"
