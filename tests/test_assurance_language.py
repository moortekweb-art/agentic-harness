from pathlib import Path

from agentic_harness.gui.api import execution_efforts_payload


ROOT = Path(__file__).resolve().parents[1]


def test_public_docs_distinguish_v012_from_legacy_check_gated_acceptance() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    evidence = (ROOT / "docs" / "EVIDENCE_CONTRACT.md").read_text(encoding="utf-8")

    assert "harness owns the acceptance specification" in readme
    assert "evidence for every frozen ID" in readme
    assert "Legacy v1 assurance level" in evidence
    assert "The v1 contract is **check-gated**" in evidence
    assert "It is not immutable issuer-declared coverage" in " ".join(evidence.split())


def test_public_ui_uses_check_accurate_language() -> None:
    html = (ROOT / "agentic_harness" / "gui" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    javascript = (
        ROOT / "agentic_harness" / "gui" / "static" / "app.js"
    ).read_text(encoding="utf-8")
    thorough = next(row for row in execution_efforts_payload() if row["key"] == "thorough")

    assert "See an independently checked result" in html
    assert "shows how configured independent checking works" in html
    assert "See an independently checked result" in javascript
    assert "structured completion audit" in thorough["summary"]
    assert "requirement-by-requirement completion audit" not in thorough["summary"]
