"""Enforce the full persistent-orchestrator documentation surface.

These tests guard the public guide for operators who run coding agents through
a persistent terminal multiplexer or external orchestrator. They check that the
guide keeps the canonical completion labels, the complete boundary sentences, a
renderable README link placed inside the Execution Methods section, and that no
private-infrastructure detail leaks into the public prose.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUIDE = REPO_ROOT / "docs" / "PERSISTENT_ORCHESTRATOR.md"
README = REPO_ROOT / "README.md"

CANONICAL_BLOB_URL = (
    "https://github.com/moortekweb-art/agentic-harness/blob/main/"
    "docs/PERSISTENT_ORCHESTRATOR.md"
)

ALLOWED_LABELS = ("Verified done", "Blocked with reason", "Failed with evidence")

# Patterns that must never appear in the public guide prose. The set is broad:
# absolute filesystem paths (including single-segment names), every common
# host/IP form (loopback, IPv4, IPv6, link-local, internal hostnames), bare
# ports, dot-directories, and credential-shaped secret tokens.
FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
    # Hosts and addresses.
    (r"localhost", "localhost host"),
    (r"127\.0\.0\.1", "loopback ip"),
    (r"0\.0\.0\.0", "wildcard bind ip"),
    (r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "ipv4 address"),
    # IPv6, including loopback (::1) and link-local (fe80::) forms.
    (r"\b[0-9a-fA-F:]*::[0-9a-fA-F:]*\b", "ipv6 address with double-colon"),
    (r"\b(?:[0-9a-fA-F]{1,4}:){7,7}[0-9a-fA-F]{1,4}\b", "full ipv6 address"),
    (r"\bfe80::", "ipv6 link-local"),
    (r"(?<![:\w])::(?:1|ffff:)\b", "ipv6 loopback or v4-mapped"),
    # Internal/local hostnames and mDNS names.
    (r"\bhost\.docker\.internal\b", "docker-internal host"),
    (r"\b\w+\.local\b", "mDNS .local hostname"),
    (r"\b\w+\.internal\b", "internal hostname"),
    (r":\d{2,5}\b", "bare port number"),
    # Absolute filesystem paths: both multi-segment and single-segment names.
    # A leading slash followed by a path-like token is a concrete location.
    (r"/mnt/", "absolute /mnt path"),
    (r"/home/", "absolute /home path"),
    (r"/tmp/", "absolute /tmp path"),
    (r"/var/", "absolute /var path"),
    (r"/etc/", "absolute /etc path"),
    (r"/usr/", "absolute /usr path"),
    (r"/opt/", "absolute /opt path"),
    (r"/root/", "absolute /root path"),
    (r"/srv\b", "absolute /srv path"),
    (r"/proc\b", "absolute /proc path"),
    (r"/Users/", "absolute /Users path"),
    # Single-segment absolute paths: "/name" not part of a URL-like token.
    (r"(?<![\w/.-])/(?:[A-Za-z0-9_-]+)(?![\w.-])", "single-segment absolute path"),
    # Dot-directories and dotfiles that reveal private layout.
    (r"\.agentic-harness", "harness dot-directory"),
    (r"\.hermes", "hermes dot-directory"),
    (r"\.claude", "claude dot-directory"),
    (r"\.config\b", "config dot-directory"),
    (r"\.local\b", "local dot-directory"),
    (r"\.venv", "venv dot-directory"),
    (r"\.git\b", "git dot-directory"),
    (r"\.github\b", "github dot-directory"),
    # Credential-shaped secrets: API keys, bearer tokens, passwords.
    (r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}\b", "bearer token"),
    (r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|pwd)\b\s*[:=]\s*\S", "credential assignment"),
    (r"sk-[A-Za-z0-9]{16,}", "provider api key"),
]

# Brand, provider, and private-infrastructure names that must not appear in the
# public guide prose. The guide must stay product- and host-neutral.
FORBIDDEN_BRANDS = [
    "Tailscale", "TITAN", "ORACLE", "Nebula", "Trinity", "Eden", "Keanu",
    "Edge", "Wraith", "Morpheus", "Scout", "Kimi", "Wrench", "Turnstone",
    "vLLM", "Ollama", "llama.cpp", "LM Studio", "OpenAI", "Anthropic",
    "Gemini", "Google", "Codex", "OpenCode", "Aider", "CodeWhale", "Z.ai",
    "GLM", "MetaClaw", "OpenWebUI", "OpenClaw", "ComfyUI", "Kokoro",
    "Devstral", "Gemma", "Qwen", "Supabase", "Mac Mini", "Node1", "Node2",
    "systemd",
]


def guide_text() -> str:
    assert GUIDE.exists(), f"missing guide: {GUIDE}"
    return GUIDE.read_text(encoding="utf-8")


def readme_text() -> str:
    return README.read_text(encoding="utf-8")


def _prose_without_links(text: str) -> str:
    """Return guide text with markdown link URLs stripped.

    The related-guides section legitimately references repository document
    filenames inside absolute GitHub blob URLs. Forbidden-token checks must
    inspect public prose, not the repository path embedded in a link URL.
    """
    return re.sub(r"\]\((https?://[^)]+)\)", "]()", text)


def _heading_offsets(text: str) -> list[tuple[int, str]]:
    """Return (line_start_offset, heading_text) for top-level headings.

    A fence-aware scan is used so that a line beginning with ``## `` inside a
    fenced code block or blockquote is not mistaken for a real heading. This is
    more robust than a raw ``\\n## `` search over unparsed Markdown.
    """
    offsets: list[tuple[int, str]] = []
    in_fence = False
    fence_marker = ""
    for match in re.finditer(r"(?m)^(.*\n)", text):
        line = match.group(1)
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        if stripped.startswith("## "):
            offsets.append((match.start(), stripped[3:].strip()))
    return offsets


def _extract_section(text: str, heading: str) -> str:
    """Return the body of a top-level ``## <heading>`` section.

    The span runs from the heading line to the next top-level heading (or end
    of document), computed from fence-aware heading offsets so that code blocks
    cannot shift the boundary.
    """
    offsets = _heading_offsets(text)
    for idx, (start, title) in enumerate(offsets):
        if title == heading:
            body_start = text.index("\n", start) + 1
            if idx + 1 < len(offsets):
                body_end = offsets[idx + 1][0]
            else:
                body_end = len(text)
            return text[body_start:body_end]
    return ""


def test_guide_exists_and_has_a_title() -> None:
    text = guide_text()

    assert text.startswith("# Persistent Orchestrator Guide\n"), text.splitlines()[0]


def test_guide_uses_only_canonical_completion_labels() -> None:
    text = guide_text()

    for label in ALLOWED_LABELS:
        assert f"**{label}**" in text, f"missing canonical label: {label}"

    # A worker-friendly lowercase variant of a label is fine only inside the
    # canonical bolded label list; a bare lowercase form used as a standalone
    # result category is not allowed.
    lower_done = re.search(r"(?<!\*\*)\bverified done\b(?!\*\*)", text, re.IGNORECASE)
    assert lower_done is None, (
        "guide uses a non-canonical 'verified done' spelling outside the label list"
    )

    # No other invented completion categories may appear as bolded labels.
    bolded = set(re.findall(r"\*\*([^*]+)\*\*", text))
    invented = bolded - {label for label in ALLOWED_LABELS}
    assert not invented, f"guide introduces non-canonical bolded labels: {sorted(invented)}"


def test_guide_completion_section_declares_exactly_the_three_labels() -> None:
    """The Completion labels section must declare exactly the three canonical
    labels and nothing else.

    This guards the contract that only the three approved public labels may
    appear. A future edit that adds a fourth bullet, renames a label, or drops
    one will fail here even if the looser bolded-label check above still passes.
    """
    text = guide_text()

    section = _extract_section(text, "Completion labels")
    assert section, "guide must contain a '## Completion labels' section"

    # The section declares labels as bolded bullets, e.g. "- **Verified done**".
    declared = set(re.findall(r"^\s*-\s*\*\*([^*]+)\*\*", section, re.MULTILINE))
    assert declared == set(ALLOWED_LABELS), (
        "Completion labels section must declare exactly the canonical labels; "
        f"got {sorted(declared)}, expected {sorted(ALLOWED_LABELS)}"
    )


def test_guide_introduces_no_invented_result_categories() -> None:
    """No common non-canonical result synonym may appear as a label-like token.

    This catches invented fourth labels such as Done, Complete, Success,
    Finished, Passed, or a bare Failed/Blocked/Verified used as a standalone
    result category outside the canonical bolded form.
    """
    text = guide_text()
    prose = _prose_without_links(text)

    # Tokens that would constitute an invented completion category if they
    # appear as a bolded standalone result label or after "result:" / "status:".
    invented_synonyms = [
        "Done", "Complete", "Completed", "Success", "Successful",
        "Finished", "Passed", "Accepted", "Rejected", "Cancelled",
        "Canceled",
    ]
    offenders: list[str] = []
    for synonym in invented_synonyms:
        for match in re.finditer(r"\*\*" + re.escape(synonym) + r"\*\*", prose):
            offenders.append(f"bolded invented label: {synonym}")

    # A bare "result:" or "status:" followed by a non-canonical word is an
    # invented result category.
    status_match = re.findall(
        r"(?i)\b(?:result|status)\s*[:=]\s*([A-Za-z]+)", prose
    )
    canonical_lower = {label.lower() for label in ALLOWED_LABELS}
    for value in status_match:
        if value.lower() not in canonical_lower:
            offenders.append(f"non-canonical result/status value: {value!r}")

    assert not offenders, (
        "guide introduces non-canonical completion categories: "
        + "; ".join(sorted(set(offenders)))
    )


def test_guide_contains_every_boundary_sentence() -> None:
    text = guide_text()

    boundaries = {
        "persistence belongs to terminal layer": (
            "belong to the terminal" in text and "orchestrator layer" in text
        ),
        "evidence plus review remains acceptance authority": (
            "plus independent review remains the acceptance" in text
        ),
        "fixed repository/worktree and goal id": (
            "fixed repository or worktree and one goal ID" in text
        ),
        "trust durable artifacts not pane text": (
            "Trust durable artifacts rather than scraping pane text" in text
        ),
        "builder and reviewer roles distinct": (
            "Keep builder and reviewer roles distinct" in text
        ),
        "require verification before completion claim": (
            "Require verification before a completion claim" in text
        ),
    }

    missing = [name for name, present in boundaries.items() if not present]
    assert not missing, f"guide is missing boundary sentences: {missing}"


def test_guide_keeps_builder_and_reviewer_boundary_complete() -> None:
    text = guide_text()
    collapsed = re.sub(r"\s+", " ", text)

    assert "The process that implements the work" in collapsed
    assert "is the builder" in collapsed
    assert "are the reviewer" in collapsed
    assert "must not grade its own work" in collapsed
    assert "must not be replaced by the builder asserting" in collapsed


def test_guide_states_terminal_cannot_make_work_complete() -> None:
    text = guide_text()

    assert "it cannot make work complete" in text
    assert "never, by itself, a completion result" in text


def test_guide_requires_a_check_passes_before_acceptance() -> None:
    text = guide_text()

    assert "configured independent check passes" in text
    assert "is not verification" in text


def _section_span(text: str, heading: str) -> tuple[int, int] | None:
    """Return (start_offset, end_offset) for a top-level ``## <heading>`` span.

    The span includes the heading line and runs up to the next top-level
    heading (or end of document). Offsets come from the fence-aware heading
    scanner, so a ``## `` line inside a fenced code block or blockquote cannot
    shift the boundary. Returns ``None`` if the heading is absent.
    """
    offsets = _heading_offsets(text)
    for idx, (start, title) in enumerate(offsets):
        if title == heading:
            end = offsets[idx + 1][0] if idx + 1 < len(offsets) else len(text)
            return start, end
    return None


def test_readme_links_the_guide_inside_execution_methods() -> None:
    readme = readme_text()

    span = _section_span(readme, "Execution Methods")
    assert span, "README must have an '## Execution Methods' top-level heading"
    exec_start, exec_end = span
    section = readme[exec_start:exec_end]

    # The link must render as markdown and use the canonical absolute blob URL.
    link_pattern = re.compile(
        r"\[(?:[^]]+)\]\(" + re.escape(CANONICAL_BLOB_URL) + r"\)"
    )
    assert link_pattern.search(section), (
        "Execution Methods must contain a renderable markdown link to "
        f"{CANONICAL_BLOB_URL}"
    )

    # The next heading after Execution Methods is the established safety section.
    next_span = _section_span(readme, "Embedded Safety Boundary")
    assert next_span, "README must have an '## Embedded Safety Boundary' heading"
    assert next_span[0] == exec_end, (
        "Embedded Safety Boundary must be the heading immediately after "
        "Execution Methods; README structure changed"
    )


def test_guide_has_no_forbidden_paths_hosts_or_dot_directories() -> None:
    prose = _prose_without_links(guide_text())

    offenders: list[str] = []
    for pattern, label in FORBIDDEN_PATTERNS:
        for match in re.finditer(pattern, prose):
            offenders.append(f"{label}: {match.group()!r}")

    assert not offenders, (
        "guide exposes forbidden path/host/directory tokens in public prose: "
        + "; ".join(offenders)
    )


def test_guide_has_no_brand_provider_or_private_infrastructure_terms() -> None:
    prose = _prose_without_links(guide_text())

    offenders: list[str] = []
    for brand in FORBIDDEN_BRANDS:
        for match in re.finditer(r"\b" + re.escape(brand) + r"\b", prose):
            offenders.append(f"{match.group()!r}")

    assert not offenders, (
        "guide exposes brand/provider/private-infrastructure terms in public prose: "
        + "; ".join(offenders)
    )


def test_guide_related_links_target_real_repository_documents() -> None:
    text = guide_text()
    related_match = re.search(r"## Related guides\n+(.*)$", text, re.DOTALL)
    assert related_match, "guide must end with a Related guides section"
    related = related_match.group(1)

    links = re.findall(r"\]\((https?://[^)]+)\)", related)
    assert links, "Related guides section must contain at least one link"

    for url in links:
        assert url.startswith(
            "https://github.com/moortekweb-art/agentic-harness/blob/main/"
        ), f"related link must use the canonical repository blob URL: {url}"

    # Each referenced path must exist in the repository, and any anchor must
    # match a real heading in that document.
    for url in links:
        path_and_anchor = url.split("/blob/main/", 1)[1]
        anchor_idx = path_and_anchor.find("#")
        if anchor_idx == -1:
            relative = path_and_anchor
            anchor = None
        else:
            relative = path_and_anchor[:anchor_idx]
            anchor = path_and_anchor[anchor_idx + 1 :]

        target = REPO_ROOT / relative
        assert target.exists(), f"related link targets a missing document: {relative}"

        if anchor is not None:
            doc = target.read_text(encoding="utf-8")
            headings = re.findall(r"(?m)^#{1,6}\s+(.+?)\s*$", doc)
            slugified = {
                re.sub(r"[^\w-]", "", heading.lower().replace(" ", "-"))
                for heading in headings
            }
            assert anchor in slugified, (
                f"related link anchor {anchor!r} is not a real heading in {relative}; "
                f"guessed anchors are not allowed"
            )


def test_guide_does_not_invent_private_artifact_locations() -> None:
    text = guide_text()

    # The guide must describe where evidence lives in product-neutral terms,
    # not by naming a concrete private artifact directory or absolute location.
    assert "report at" not in text.lower()
    assert "stored below" not in text.lower()
    assert "written to" not in text.lower()


def test_readme_link_text_is_renderable_and_descriptive() -> None:
    readme = readme_text()
    span = _section_span(readme, "Execution Methods")
    assert span, "README must have an '## Execution Methods' top-level heading"
    section = readme[span[0] : span[1]]

    match = re.search(r"\[([^\]]+)\]\(" + re.escape(CANONICAL_BLOB_URL) + r"\)", section)
    assert match, "Execution Methods must link the guide with descriptive text"
    label = match.group(1).strip().lower()
    assert "persistent" in label and "orchestrator" in label, (
        f"link text should name the persistent orchestrator guide, got: {match.group(1)!r}"
    )
