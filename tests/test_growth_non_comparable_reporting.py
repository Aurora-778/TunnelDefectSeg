from scripts.analyze_disease_growth import aggregate_rows, write_markdown_report, write_summary_report
from scripts.generate_visualization_and_recheck_list import (
    build_priority_recheck_list,
    display_growth_status,
    generate_standard_visualizations,
    is_longitudinally_comparable,
    write_recheck_report,
    write_summary_report as write_visualization_summary,
)


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
    first_row = _row("I001", "100")
    last_row = _row("I002", "200")
    last_row["risk_level"] = "高"
    records = aggregate_rows([first_row, last_row])
    report = tmp_path / "growth.md"
    write_markdown_report(records, [first_row, last_row], tmp_path / "input.csv", report)

    text = report.read_text(encoding="utf-8")
    assert "增长率" not in text
    assert "趋势判断" not in text
    assert "不具备纵向比较条件" in text
    assert "静态面积审计：100 px² -> 200 px²" in text
    assert "描述性面积相对差：100.0%" in text
    assert "纵向可比性：不可比较" in text
    assert "风险变化：低 -> 高" not in text
    assert "当前风险等级：高" in text


def test_legacy_comparable_alias_does_not_enable_directional_report(tmp_path):
    first_row = _row("I001", "100")
    last_row = _row("I002", "200")
    last_row["risk_level"] = "高"
    records = aggregate_rows([first_row, last_row])
    records[0]["comparability_status"] = "longitudinally_comparable"
    report = tmp_path / "legacy-growth.md"

    write_markdown_report(records, [first_row, last_row], tmp_path / "input.csv", report)

    text = report.read_text(encoding="utf-8")
    assert "风险变化：低 -> 高" not in text
    assert "当前风险等级：高" in text
    assert "纵向可比性：不可比较" in text


def test_verified_comparable_report_keeps_directional_risk_change(tmp_path):
    first_row = _row("I001", "100")
    last_row = _row("I002", "200")
    last_row["risk_level"] = "高"
    records = aggregate_rows([first_row, last_row])
    records[0]["comparability_status"] = "verified_comparable"
    report = tmp_path / "verified-growth.md"

    write_markdown_report(records, [first_row, last_row], tmp_path / "input.csv", report)

    text = report.read_text(encoding="utf-8")
    assert "风险变化：低 -> 高" in text
    assert "当前风险等级：高" not in text


def test_growth_summary_without_verified_rows_has_no_risk_direction_counts(tmp_path):
    input_rows = [_row("I001", "100"), _row("I002", "200")]
    records = aggregate_rows(input_rows)
    summary = tmp_path / "summary.md"

    write_summary_report(
        tmp_path / "input.csv",
        tmp_path / "growth.csv",
        tmp_path / "growth.md",
        summary,
        input_rows,
        records,
    )

    text = summary.read_text(encoding="utf-8")
    assert "已验证可比记录风险等级变化统计" in text
    assert "暂无可比数据" in text
    assert "- 0: 1" not in text


def test_growth_summary_counts_only_verified_comparable_risk_changes(tmp_path):
    input_rows = [_row("I001", "100"), _row("I002", "200")]
    base = aggregate_rows(input_rows)[0]
    legacy = dict(base, disease_id="D002", comparability_status="longitudinally_comparable", risk_level_change="1")
    verified = dict(base, disease_id="D003", comparability_status="verified_comparable", risk_level_change="2")
    summary = tmp_path / "summary.md"

    write_summary_report(
        tmp_path / "input.csv",
        tmp_path / "growth.csv",
        tmp_path / "growth.md",
        summary,
        input_rows,
        [base, legacy, verified],
    )

    text = summary.read_text(encoding="utf-8")
    assert "- 2: 1" in text
    assert "- 1: 1" not in text
    assert "- 0: 1" not in text


def test_noncomparable_visualization_summary_uses_audit_wording(tmp_path):
    records = aggregate_rows([_row("I001", "100"), _row("I002", "200")])
    summary = tmp_path / "visualization_summary.md"
    write_visualization_summary(
        tmp_path / "growth.csv",
        tmp_path / "engineering.csv",
        tmp_path / "recheck.csv",
        [],
        tmp_path / "visualization_report.md",
        tmp_path / "recheck_report.md",
        summary,
        records,
        [],
    )

    text = summary.read_text(encoding="utf-8")
    assert "病害增长结果" not in text
    assert "增长趋势分布" not in text
    assert "面积审计与可比性状态" in text
    assert "明显增长: 0" not in text
    assert "基本稳定: 0" not in text


def test_noncomparable_recheck_uses_neutral_audit_wording(tmp_path):
    records = aggregate_rows([_row("I001", "100"), _row("I002", "200")])
    records[0]["last_risk_level"] = "高"
    records[0]["attention_level"] = "重点关注"
    rows = build_priority_recheck_list(records)
    report = tmp_path / "recheck.md"
    write_recheck_report(rows, report, tmp_path / "recheck.csv")

    text = report.read_text(encoding="utf-8")
    assert "静态面积审计：100 px² -> 200 px²" in text
    assert "增长趋势" not in text
    assert "增长率" not in text
    assert "面积减小" not in text
    assert "基本稳定" not in text
    assert "真实复检数据后再判断方向性变化" in text


def test_noncomparable_stale_direction_does_not_select_recheck():
    records = aggregate_rows([_row("I001", "100"), _row("I002", "200")])
    records[0]["attention_level"] = "常规记录"
    records[0]["last_risk_level"] = "低"
    records[0]["growth_trend"] = "明显增长"

    assert build_priority_recheck_list(records) == []
    assert display_growth_status(records[0]) == "不可比较"


def test_display_growth_status_preserves_insufficient_history():
    row = {"comparability_status": "insufficient_history", "growth_trend": "明显增长"}

    assert display_growth_status(row) == "数据不足"


def test_only_verified_comparable_enables_directional_claims():
    assert is_longitudinally_comparable({"comparability_status": "verified_comparable"}) is True
    assert is_longitudinally_comparable({"comparability_status": "longitudinally_comparable"}) is False
    assert is_longitudinally_comparable({"comparability_status": "simulated_metadata_comparable"}) is False


def test_noncomparable_recheck_order_ignores_area_growth_rate():
    records = aggregate_rows([_row("I001", "100"), _row("I002", "200")])
    larger = dict(records[0], disease_id="D001", attention_level="持续观察", last_risk_level="中", last_area_px="300", area_growth_rate="-0.9")
    smaller = dict(records[0], disease_id="D002", attention_level="持续观察", last_risk_level="中", last_area_px="100", area_growth_rate="0.9")

    rows = build_priority_recheck_list([smaller, larger])

    assert [row["disease_id"] for row in rows] == ["D001", "D002"]


def test_noncomparable_recheck_order_ignores_risk_level_change():
    records = aggregate_rows([_row("I001", "100"), _row("I002", "200")])
    first = dict(
        records[0],
        disease_id="D001",
        attention_level="持续观察",
        last_risk_level="中",
        last_area_px="100",
        risk_level_change="-2",
    )
    second = dict(
        records[0],
        disease_id="D002",
        attention_level="持续观察",
        last_risk_level="中",
        last_area_px="100",
        risk_level_change="2",
    )

    rows = build_priority_recheck_list([second, first])

    assert [row["disease_id"] for row in rows] == ["D001", "D002"]


def test_noncomparable_recheck_normalizes_stale_directional_fields(tmp_path):
    records = aggregate_rows([_row("I001", "100"), _row("I002", "200")])
    records[0].update(
        {
            "attention_level": "重点关注",
            "last_risk_level": "高",
            "risk_level_change": "2",
            "growth_trend": "明显增长",
            "growth_description": "旧产物声称病害明显增长且风险上升。",
        }
    )

    rows = build_priority_recheck_list(records)
    report = tmp_path / "recheck.md"
    write_recheck_report(rows, report, tmp_path / "recheck.csv")
    text = report.read_text(encoding="utf-8")

    assert rows[0]["growth_trend"] == "不可比较"
    assert "风险等级较首次巡检出现上升" not in rows[0]["recheck_reason"]
    assert "明显增长" not in rows[0]["growth_description"]
    assert "风险上升" not in rows[0]["growth_description"]
    assert "旧产物声称" not in text


def test_insufficient_history_priority_row_is_not_directional():
    row = {
        "disease_id": "D001",
        "disease_type": "crack",
        "inspection_count": "1",
        "first_inspection": "I001",
        "last_inspection": "I001",
        "first_area_px": "100",
        "last_area_px": "100",
        "area_growth_rate": "0",
        "area_growth_px": "0",
        "first_risk_level": "高",
        "last_risk_level": "高",
        "risk_level_change": "0",
        "growth_trend": "基本稳定",
        "attention_level": "重点关注",
        "last_mileage_range": "K12+000.0",
        "main_clock_direction": "12点",
        "growth_description": "旧产物声称基本稳定。",
        "comparability_status": "insufficient_history",
    }

    rows = build_priority_recheck_list([row])

    assert rows[0]["growth_trend"] == "不可比较"
    assert "基本稳定" not in rows[0]["growth_description"]


def test_noncomparable_recheck_report_hides_risk_direction(tmp_path):
    records = aggregate_rows([_row("I001", "100"), _row("I002", "200")])
    records[0].update(
        {
            "attention_level": "重点关注",
            "first_risk_level": "低",
            "last_risk_level": "高",
            "risk_level_change": "2",
        }
    )
    rows = build_priority_recheck_list(records)
    report = tmp_path / "recheck.md"

    write_recheck_report(rows, report, tmp_path / "recheck.csv")

    text = report.read_text(encoding="utf-8")
    assert "当前风险等级：高" in text
    assert "风险变化：低 -> 高" not in text


def test_priority_projection_does_not_mutate_growth_input():
    records = aggregate_rows([_row("I001", "100"), _row("I002", "200")])
    records[0].update({"attention_level": "重点关注", "growth_trend": "明显增长"})
    original = dict(records[0])

    build_priority_recheck_list(records)

    assert records[0] == original


def test_noncomparable_risk_change_is_not_plotted(monkeypatch, tmp_path):
    records = aggregate_rows([_row("I001", "100"), _row("I002", "200")])
    records[0]["risk_level_change"] = "2"
    captured: dict[str, tuple[list[str], list[int | float]]] = {}

    def capture_chart(labels, values, title, xlabel, ylabel, output):
        captured[title] = (labels, values)
        return output

    monkeypatch.setattr(
        "scripts.generate_visualization_and_recheck_list.save_bar_chart",
        capture_chart,
    )
    generate_standard_visualizations(records, tmp_path)

    labels, values = captured["风险等级变化分布"]
    assert labels == ["暂无可比数据"]
    assert values == [0]


def test_legacy_comparable_status_does_not_enter_directional_chart(monkeypatch, tmp_path):
    records = aggregate_rows([_row("I001", "100"), _row("I002", "200")])
    records[0].update({"comparability_status": "longitudinally_comparable", "risk_level_change": "2"})
    captured: dict[str, tuple[list[str], list[int | float]]] = {}

    def capture_chart(labels, values, title, xlabel, ylabel, output):
        captured[title] = (labels, values)
        return output

    monkeypatch.setattr("scripts.generate_visualization_and_recheck_list.save_bar_chart", capture_chart)
    generate_standard_visualizations(records, tmp_path)

    assert captured["风险等级变化分布"] == (["暂无可比数据"], [0])
