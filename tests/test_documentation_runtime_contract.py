import csv
from pathlib import Path


def test_readme_association_count_matches_history_only_demo():
    readme = Path("README.md").read_text(encoding="utf-8")
    with Path("data/simulated/disease_association_records.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert "association rows: 30" not in readme
    assert f"association rows: {len(rows)}" in readme
    assert "I001 只建立 baseline" in readme


def test_documentation_keeps_with_id_as_upper_bound_and_benchmark_separate():
    readme = Path("README.md").read_text(encoding="utf-8")
    contract = Path("docs/artifact_contract.md").read_text(encoding="utf-8")

    assert "upper-bound / sanity check" in readme
    assert "Association Benchmark" in readme
    assert "nearest-mileage" in contract
