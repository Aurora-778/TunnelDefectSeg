import json

import web_app


def test_load_evidence_payload_reads_available_summaries(tmp_path, monkeypatch):
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps({"num_samples": 3, "metric_summary": {"selected_vs_fused_mIoU": 0.12}}), encoding="utf-8")
    monkeypatch.setattr(web_app, "EVIDENCE_FILES", {"test": summary_path, "all": tmp_path / "missing.json"})

    payload = web_app._load_evidence_payload()

    assert payload["ok"] is True
    assert payload["source"] == "generated-json"
    assert payload["summaries"]["test"]["num_samples"] == 3
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
