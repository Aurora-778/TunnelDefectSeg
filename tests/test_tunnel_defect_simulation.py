from pathlib import Path

from tunnel_defect_simulation.scripts.generate_simulation_tables import (
    NUM_DISEASES,
    main,
)


def test_generate_simulation_tables_outputs_expected_files_and_counts():
    sequence_df, disease_df, mapping_df, growth_df = main()
    base_dir = Path("tunnel_defect_simulation")

    assert len(sequence_df) == 603
    assert len(disease_df) == NUM_DISEASES
    assert len(growth_df) == NUM_DISEASES * 3
    assert len(mapping_df) > 0

    for csv_name in [
        "inspection_sequence.csv",
        "disease_instances.csv",
        "frame_disease_mapping.csv",
        "disease_growth_records.csv",
    ]:
        assert (base_dir / "data" / "simulated" / csv_name).exists()

    assert (base_dir / "outputs" / "simulation_summary.md").exists()
    assert all(row["image_path"].startswith("data/images/") for row in sequence_df)
    assert not any(Path(row["image_path"]).exists() for row in sequence_df)
    assert all(isinstance(row["area_growth_rate"], float) for row in growth_df)
