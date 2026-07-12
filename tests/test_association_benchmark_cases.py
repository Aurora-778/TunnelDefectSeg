from pathlib import Path

from evaluation.association_benchmark import evaluate_strategy, load_fixture


FIXTURE = Path("tests/fixtures/association_benchmark/benchmark_fixture.json")


def test_fixture_has_match_reject_and_manual_review_cases():
    fixture = load_fixture(FIXTURE)
    actions = {case["expected_action"] for case in fixture["cases"]}
    case_types = {case["case_type"] for case in fixture["cases"]}

    assert actions == {"match", "reject", "manual_review"}
    assert {"similar_candidates", "future_candidate", "partial_fields", "independent_same_memory_queries"} <= case_types


def test_same_memory_cases_are_explicitly_independent_not_one_to_one_assignment():
    fixture = load_fixture(FIXTURE)
    cases = [case for case in fixture["cases"] if case["case_id"] in {"C11", "C12"}]

    assert len(cases) == 2
    assert {case["case_type"] for case in cases} == {"independent_same_memory_queries"}
    assert all("独立逐帧 query" in case["case_description"] for case in cases)
    assert all("one-to-one" in case["case_description"] for case in cases)


def test_future_candidate_is_filtered_before_all_strategies_score():
    fixture = load_fixture(FIXTURE)
    for strategy in ("nearest_mileage", "area_only", "spatial_only", "weighted_no_id", "with_id_upper_bound"):
        result = next(row for row in evaluate_strategy(fixture, strategy) if row["case_id"] == "C15")
        assert result["candidate_count"] == "1"
        assert result["predicted_memory_id"] != "MEM-FUTURE"


def test_no_id_prediction_does_not_change_when_label_changes():
    fixture = load_fixture(FIXTURE)
    original = evaluate_strategy(fixture, "weighted_no_id")
    changed = load_fixture(FIXTURE)
    for case in changed["cases"]:
        case["current_record"]["disease_id"] = "INTENTIONALLY_DIFFERENT_LABEL"
    relabeled = evaluate_strategy(changed, "weighted_no_id")

    for before, after in zip(original, relabeled):
        assert (before["predicted_action"], before["predicted_memory_id"], before["selected_score"]) == (
            after["predicted_action"], after["predicted_memory_id"], after["selected_score"]
        )
