from __future__ import annotations

from pathlib import Path

from evaluation.run_v012_assurance_matrix import load_matrix


ROOT = Path(__file__).resolve().parents[1]


def test_v012_assurance_matrix_is_complete_and_resolvable() -> None:
    matrix = load_matrix(ROOT / "evaluation" / "v012_assurance_cases.json")

    assert matrix["release"] == "0.12.0"
    assert len(matrix["cases"]) == 10
    assert len({row["category"] for row in matrix["cases"]}) == 10
    for row in matrix["cases"]:
        test_path = row["test"].split("::", 1)[0]
        assert (ROOT / test_path).is_file()


def test_v012_protocol_freezes_release_model_seed_and_acceptance_rule() -> None:
    protocol = (ROOT / "evaluation" / "V012_ASSURANCE_PROTOCOL.md").read_text(
        encoding="utf-8"
    )

    assert "immutable `v0.12.0` tag" in protocol
    assert "`gpt-5.6-sol`" in protocol
    assert "`2026071701`" in protocol
    assert "zero false verified completions" in protocol
    assert "external beta is required" in protocol
