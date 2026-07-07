import json
from http import HTTPStatus

import web_app


def test_algorithm_events_payload_handles_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, "ALGORITHM_EVENTS_FILE", tmp_path / "missing.json")

    payload = web_app._load_algorithm_events()

    assert payload["ok"] is True
    assert payload["source"] == "fallback"
    assert payload["events"] == []
    assert "generate_algorithm_events.py" in payload["missing_message"]


def test_algorithm_events_payload_reads_generated_json(tmp_path, monkeypatch):
    events_file = tmp_path / "outputs" / "algorithm_visualization" / "algorithm_events.json"
    events_file.parent.mkdir(parents=True)
    events_file.write_text(
        json.dumps({
            "schema_version": "algorithm-events.v1",
            "generated_at": "2026-07-07T00:00:00Z",
            "source_artifacts": [{"label": "source", "path": "data/source.csv", "exists": True}],
            "events": [{"event_id": "E01", "stage": "kict_mask_geometry"}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(web_app, "ALGORITHM_EVENTS_FILE", events_file)

    payload = web_app._load_algorithm_events()

    assert payload["source"] == "generated-json"
    assert payload["schema_version"] == "algorithm-events.v1"
    assert payload["events"][0]["event_id"] == "E01"
    assert payload["errors"] == {}


def test_algorithm_events_payload_reports_broken_json(tmp_path, monkeypatch):
    events_file = tmp_path / "algorithm_events.json"
    events_file.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(web_app, "ALGORITHM_EVENTS_FILE", events_file)

    payload = web_app._load_algorithm_events()

    assert payload["ok"] is True
    assert payload["source"] == "error"
    assert payload["events"] == []
    assert "algorithm_events" in payload["errors"]


def test_algorithm_events_api_route_can_be_served(monkeypatch):
    captured = {}
    handler = object.__new__(web_app.DetectionHandler)
    handler.path = "/api/algorithm-events"
    handler._send_json = lambda payload, status=HTTPStatus.OK: captured.update(payload=payload, status=status)
    monkeypatch.setattr(web_app, "_load_algorithm_events", lambda: {"ok": True, "events": [{"event_id": "E01"}]})

    web_app.DetectionHandler.do_GET(handler)

    assert captured["status"] == HTTPStatus.OK
    assert captured["payload"]["events"][0]["event_id"] == "E01"


def test_algorithm_events_api_does_not_touch_simulated_outputs(tmp_path, monkeypatch):
    simulated = tmp_path / "data" / "simulated"
    simulated.mkdir(parents=True)
    sentinel = simulated / "sentinel.csv"
    sentinel.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(web_app, "ALGORITHM_EVENTS_FILE", tmp_path / "missing.json")

    web_app._load_algorithm_events()

    assert sentinel.read_text(encoding="utf-8") == "keep"
