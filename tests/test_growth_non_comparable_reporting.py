from scripts.analyze_disease_growth import aggregate_rows, write_markdown_report


def _row(inspection_id: str, area: str) -> dict[str, str]:
    return {
        "inspection_id": inspection_id, "disease_id": "D001", "disease_type": "crack", "frame_count": "1",
        "start_frame": "1", "end_frame": "1", "start_time": "2026-01-01", "end_time": "2026-01-01",
        "start_mileage_m": "12000", "end_mileage_m": "12000", "start_mileage_text": "K12+000.0",
        "end_mileage_text": "K12+000.0", "start_ring": "1", "end_ring": "1", "main_clock_direction": "12点",
        "max_area_px": area, "mean_area_px": area, "total_area_px": area, "risk_level": "低",
        "observation_source": "kict_static_mask_cyclic_demo", "comparability_status": "not_longitudinally_comparable",
        "engineering_description": "fixture",
    }


def test_noncomparable_markdown_uses_static_audit_not_growth_rate(tmp_path):
    records = aggregate_rows([_row("I001", "100"), _row("I002", "200")])
    report = tmp_path / "growth.md"
    write_markdown_report(records, [_row("I001", "100"), _row("I002", "200")], tmp_path / "input.csv", report)

    text = report.read_text(encoding="utf-8")
    assert "增长率" not in text
    assert "不具备纵向比较条件" in text
    assert "静态描述性审计" in text
