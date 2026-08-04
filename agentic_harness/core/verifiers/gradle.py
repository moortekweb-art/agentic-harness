"""Gradle build-script lexing and test-source inference for verifier boundaries."""

from __future__ import annotations

from pathlib import Path
import re

from agentic_harness.core.errors import ConfigError
from agentic_harness.core.verifiers.common import (
    _configured_test_root,
    _is_excluded_workspace_path,
    is_link_or_reparse,
    require_lexical_regular_path,
)

# ``_gradle_lexical_mask`` keeps keywords and masks string contents, so the
# delegation target is invisible here while the delegating call is not.
_GRADLE_DELEGATED_BUILD_LOGIC = re.compile(
    r"\bapply\b(?:\s|\()*\bfrom\b|\bincludeBuild\b"
)
_GRADLE_TEST_BLOCK = re.compile(
    r"""(?x)
    (?:
        \btest
        |
        \b(?:getByName|named)(?:<[^>]+>)?\(\s*["']test["']\s*\)
    )
    (?:\s*\.\s*get\s*\(\s*\))?
    (?:\.\s*(?P<method>[A-Za-z_][A-Za-z0-9_]*)\s*)?
    \s*\{
    """
)
_GRADLE_DIRECT_TEST_CONFIG = re.compile(
    r"""(?x)
    \bsourceSets\s*
    (?:
        \.\s*test
        |
        \[\s*["']test["']\s*\]
        |
        \.\s*(?:getByName|named)(?:<[^>]+>)?\(\s*["']test["']\s*\)
    )
    [^\n;]*
    """
)
_GRADLE_SOURCE_ROOT_CALL = re.compile(
    r"\b(?:java|kotlin|resources)\s*"
    r"(?:\.\s*|\{\s*)"
    r"(?:srcDirs?|setSrcDirs)\b(?P<expression>[^\n;}]*)"
)
_GRADLE_SOURCE_ROOT_NAME = re.compile(r"\b(?:srcDirs?|setSrcDirs)\b")
_GRADLE_TEST_ALIAS = re.compile(
    r"""(?x)
    (?:
        \b(?:val|var|def)\s+
        |
        (?<![A-Za-z0-9_$.])
        (?:[A-Za-z_][A-Za-z0-9_$.<>,?]*\s+)+
    )
    (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    (?:\s*:\s*[A-Za-z_][A-Za-z0-9_$.<>,?\s]*)?
    \s*=\s*
    \bsourceSets\s*
    (?:
        \.\s*test
        |
        \[\s*["']test["']\s*\]
        |
        \.\s*(?:getByName|named)(?:<[^>]+>)?\(\s*["']test["']\s*\)
    )
    """
)
_GRADLE_GAP_TOKEN = re.compile(
    r"(?:\s+|//[^\n]*(?:\n|$)|/\*(?:[^*]|\*(?!/))*\*/)"
)
_GRADLE_GAP = r"(?:\s|//[^\n]*(?:\n|$)|/\*(?:[^*]|\*(?!/))*\*/)*"
_GRADLE_ALIAS_CLOSURE_SUFFIX = re.compile(
    rf"{_GRADLE_GAP}"
    rf"(?:\.{_GRADLE_GAP}get{_GRADLE_GAP}"
    rf"\({_GRADLE_GAP}\){_GRADLE_GAP})?"
    rf"\.{_GRADLE_GAP}"
    r"(?P<method>[A-Za-z_][A-Za-z0-9_]*)"
    rf"{_GRADLE_GAP}(?:\([^{{}};]*\){_GRADLE_GAP})?"
    r"(?P<opening>\{)"
)
_GRADLE_POTENTIAL_CLOSURE = re.compile(
    rf"\.{_GRADLE_GAP}"
    r"[A-Za-z_][A-Za-z0-9_]*"
    rf"{_GRADLE_GAP}(?:\([^{{}};]*\){_GRADLE_GAP})?"
    r"(?P<opening>\{)"
)
_QUOTED_PATH = re.compile(r"""(?P<quote>['"])(?P<path>[^'"]+)(?P=quote)""")


def _refuse_delegated_gradle_build_logic(root: Path) -> None:
    for directory in sorted(root.glob("**/buildSrc")):
        if _is_excluded_workspace_path(directory) or not directory.is_dir():
            continue
        raise ConfigError(
            "verified best-of-N cannot infer the Gradle buildSrc build-logic closure; "
            "configure review_assets with every buildSrc source and other "
            "repository-controlled verifier input"
        )
    for build_file in sorted(
        {
            *root.glob("**/*.gradle"),
            *root.glob("**/*.gradle.kts"),
        }
    ):
        if _is_excluded_workspace_path(build_file) or is_link_or_reparse(build_file):
            continue
        if not build_file.is_file():
            continue
        try:
            text = build_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigError(
                f"unable to inspect Gradle verifier inputs: {build_file}"
            ) from exc
        if _GRADLE_DELEGATED_BUILD_LOGIC.search(_gradle_lexical_mask(text)) is None:
            continue
        raise ConfigError(
            "verified best-of-N cannot infer the delegated Gradle build-logic closure; "
            "configure review_assets with every applied script, included build, and "
            "other repository-controlled verifier input"
        )


def _gradle_test_roots(root: Path, *, allow_dynamic: bool) -> set[Path]:
    configured: set[Path] = set()
    for build_file in sorted(
        {
            *root.glob("**/build.gradle"),
            *root.glob("**/build.gradle.kts"),
        }
    ):
        require_lexical_regular_path(root, build_file, label=str(build_file))
        try:
            text = build_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigError(f"unable to inspect Gradle verifier inputs: {build_file}") from exc
        expressions: list[str] = []
        unresolved_source_root = False
        for match in _GRADLE_TEST_BLOCK.finditer(text):
            method = match.group("method")
            block = _balanced_brace_body(text, match.end() - 1)
            source_matches = list(_GRADLE_SOURCE_ROOT_CALL.finditer(block))
            expressions.extend(
                item.group("expression")
                for item in source_matches
            )
            unresolved_source_root = unresolved_source_root or (
                len(_GRADLE_SOURCE_ROOT_NAME.findall(block)) != len(source_matches)
            )
            unresolved_source_root = unresolved_source_root or (
                method is not None and method not in {"configure", "apply"}
            )
        for item in _GRADLE_DIRECT_TEST_CONFIG.finditer(text):
            direct_config = item.group(0)
            source_matches = list(_GRADLE_SOURCE_ROOT_CALL.finditer(direct_config))
            expressions.extend(
                source_match.group("expression") for source_match in source_matches
            )
            unresolved_source_root = unresolved_source_root or (
                len(_GRADLE_SOURCE_ROOT_NAME.findall(direct_config))
                != len(source_matches)
            )
        lexical_text = _gradle_lexical_mask(text)
        for alias_match in _GRADLE_TEST_ALIAS.finditer(text):
            alias_name = alias_match.group("name")
            alias = re.escape(alias_name)
            direct_alias = re.compile(
                rf"\b{alias}\s*(?:\.\s*get\s*\(\s*\))?\s*\.\s*"
                r"(?:java|kotlin|resources)\s*\.\s*"
                r"(?:srcDirs?|setSrcDirs)\b(?P<expression>[^\n;}]*)"
            )
            for direct_match in direct_alias.finditer(text):
                expressions.append(direct_match.group("expression"))
            alias_closures = _gradle_alias_closures(
                text, alias_name, lexical_text=lexical_text
            )
            recognized_openings = {
                opening for _, opening, _ in alias_closures
            }
            for method, opening, receiver_supported in alias_closures:
                block = _balanced_brace_body(text, opening)
                source_matches = list(_GRADLE_SOURCE_ROOT_CALL.finditer(block))
                if receiver_supported and method in {"configure", "apply"}:
                    expressions.extend(
                        source_match.group("expression")
                        for source_match in source_matches
                    )
                    unresolved_source_root = unresolved_source_root or (
                        len(_GRADLE_SOURCE_ROOT_NAME.findall(block))
                        != len(source_matches)
                    )
                elif _GRADLE_SOURCE_ROOT_NAME.search(block):
                    unresolved_source_root = True
            for opening in _gradle_unrecognized_alias_closure_openings(
                text,
                alias_name,
                lexical_text=lexical_text,
                recognized_openings=recognized_openings,
                declaration_name_start=alias_match.start("name"),
            ):
                if _GRADLE_SOURCE_ROOT_NAME.search(
                    _balanced_brace_body(text, opening)
                ):
                    unresolved_source_root = True
            for line in text.splitlines():
                if not re.search(rf"\b{alias}\b", line):
                    continue
                alias_source_matches = list(direct_alias.finditer(line))
                unresolved_source_root = unresolved_source_root or (
                    len(_GRADLE_SOURCE_ROOT_NAME.findall(line))
                    != len(alias_source_matches)
                )
        if unresolved_source_root and not allow_dynamic:
            raise ConfigError(
                "verified best-of-N cannot infer a Gradle test source root syntax; "
                "configure review_assets with every selected test directory"
            )
        for expression in expressions:
            paths = _literal_gradle_test_paths(expression)
            if paths is None and not allow_dynamic:
                raise ConfigError(
                    "verified best-of-N cannot infer a dynamic Gradle test source root; "
                    "configure review_assets with every selected test directory"
                )
            for value in paths or []:
                configured.add(
                    _configured_test_root(
                        root,
                        build_file.parent,
                        value,
                        ecosystem="Gradle",
                        allow_dynamic=allow_dynamic,
                    )
                )
    return configured


def _gradle_unrecognized_alias_closure_openings(
    text: str,
    alias: str,
    *,
    lexical_text: str,
    recognized_openings: set[int],
    declaration_name_start: int,
) -> set[int]:
    openings: set[int] = set()
    for alias_match in re.finditer(
        rf"\b{re.escape(alias)}\b",
        lexical_text,
    ):
        if alias_match.start() == declaration_name_start:
            continue
        search_start = alias_match.end()
        for suffix in _GRADLE_POTENTIAL_CLOSURE.finditer(
            text,
            search_start,
        ):
            opening = suffix.start("opening")
            if opening not in recognized_openings:
                openings.add(opening)
    return openings


def _gradle_alias_closures(
    text: str,
    alias: str,
    *,
    lexical_text: str | None = None,
) -> list[tuple[str, int, bool]]:
    if lexical_text is None:
        lexical_text = _gradle_lexical_mask(text)
    receivers: dict[int, bool] = {
        match.end(): True
        for match in re.finditer(rf"\b{re.escape(alias)}\b", lexical_text)
    }
    for opening_match in re.finditer(r"\(", lexical_text):
        opening = opening_match.start()
        closing = _balanced_parenthesis_end(
            text, opening, lexical_text=lexical_text
        )
        if closing is None:
            continue
        receiver_body = text[opening + 1 : closing]
        if not re.search(
            rf"\b{re.escape(alias)}\b",
            lexical_text[opening + 1 : closing],
        ):
            continue
        line_start = text.rfind("\n", 0, opening) + 1
        prefix = _GRADLE_GAP_TOKEN.sub("", text[line_start:opening])
        if prefix and (prefix[-1].isalnum() or prefix[-1] in "_$.)]"):
            continue
        receivers[closing + 1] = _supported_gradle_alias_receiver(
            receiver_body, alias
        )

    closures: set[tuple[str, int, bool]] = set()
    for receiver_end, receiver_supported in receivers.items():
        suffix = _GRADLE_ALIAS_CLOSURE_SUFFIX.match(text, receiver_end)
        if suffix is not None:
            closures.add(
                (
                    suffix.group("method"),
                    suffix.start("opening"),
                    receiver_supported,
                )
            )
    return sorted(closures, key=lambda item: item[1])


def _supported_gradle_alias_receiver(receiver_body: str, alias: str) -> bool:
    compact = _GRADLE_GAP_TOKEN.sub("", receiver_body)
    while (
        compact.startswith("(")
        and compact.endswith(")")
        and _balanced_parenthesis_end(compact, 0) == len(compact) - 1
    ):
        compact = compact[1:-1]
    return compact == alias or bool(
        re.fullmatch(rf"{re.escape(alias)}\.get\(\)", compact)
    )


def _balanced_parenthesis_end(
    text: str,
    opening: int,
    *,
    lexical_text: str | None = None,
) -> int | None:
    if lexical_text is None:
        lexical_text = _gradle_lexical_mask(text)
    if opening >= len(lexical_text) or lexical_text[opening] != "(":
        return None
    depth = 0
    for index in range(opening, len(lexical_text)):
        character = lexical_text[index]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _gradle_lexical_mask(text: str) -> str:
    """Preserve structural offsets while masking Gradle comments and strings."""

    masked = list(text)
    index = 0
    state = "code"
    quote = ""
    block_depth = 0
    while index < len(text):
        if state == "code":
            if text.startswith("//", index):
                masked[index : index + 2] = "  "
                index += 2
                state = "line_comment"
                continue
            if text.startswith("/*", index):
                masked[index : index + 2] = "  "
                index += 2
                state = "block_comment"
                block_depth = 1
                continue
            if text.startswith('"""', index) or text.startswith("'''", index):
                quote = text[index : index + 3]
                masked[index : index + 3] = "   "
                index += 3
                state = "triple_string"
                continue
            if text[index] in {'"', "'"}:
                quote = text[index]
                masked[index] = " "
                index += 1
                state = "string"
                continue
            index += 1
            continue

        if state == "line_comment":
            if text[index] == "\n":
                state = "code"
            else:
                masked[index] = " "
            index += 1
            continue

        if state == "block_comment":
            if text.startswith("/*", index):
                masked[index : index + 2] = "  "
                index += 2
                block_depth += 1
                continue
            if text.startswith("*/", index):
                masked[index : index + 2] = "  "
                index += 2
                block_depth -= 1
                if block_depth == 0:
                    state = "code"
                continue
            if text[index] != "\n":
                masked[index] = " "
            index += 1
            continue

        if state == "string":
            if text[index] == "\\":
                masked[index] = " "
                if index + 1 < len(text):
                    if text[index + 1] != "\n":
                        masked[index + 1] = " "
                    index += 2
                else:
                    index += 1
                continue
            if text[index] == quote:
                masked[index] = " "
                index += 1
                state = "code"
                continue
            if text[index] != "\n":
                masked[index] = " "
            index += 1
            continue

        if text.startswith(quote, index):
            masked[index : index + 3] = "   "
            index += 3
            state = "code"
            continue
        if text[index] != "\n":
            masked[index] = " "
        index += 1

    return "".join(masked)


def _literal_gradle_test_paths(expression: str) -> list[str] | None:
    paths = [match.group("path") for match in _QUOTED_PATH.finditer(expression)]
    scrubbed = _QUOTED_PATH.sub("", expression)
    scrubbed = re.sub(r"\b(?:files|listOf|setOf)\b", "", scrubbed)
    if "$" in scrubbed or re.search(r"[A-Za-z_]", scrubbed):
        return None
    return paths or None


def _balanced_brace_body(text: str, opening: int) -> str:
    depth = 0
    for index in range(opening, len(text)):
        character = text[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[opening + 1 : index]
    return text[opening + 1 :]
