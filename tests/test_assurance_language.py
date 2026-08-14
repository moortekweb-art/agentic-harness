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


def test_split_workspace_note_is_plain_and_quiet_when_aligned() -> None:
    """The note names the consequence in ordinary words and hides by default."""

    html = (ROOT / "agentic_harness" / "gui" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    javascript = (
        ROOT / "agentic_harness" / "gui" / "static" / "app.js"
    ).read_text(encoding="utf-8")

    note = next(
        line for line in html.splitlines() if "id=\"workspaceSplitNote\"" in line
    )
    plain_html = " ".join(html.split())
    assert "hidden" in note
    assert "associated with a different project" in plain_html
    assert "folder from the work area" in plain_html
    assert "another page can show different work" in plain_html
    # Shown only on a real split, so an aligned deployment stays visually quiet.
    assert "identity.split === true" in javascript
    assert "els.workspaceSplitNote.hidden = !workspaceSplit;" in javascript
