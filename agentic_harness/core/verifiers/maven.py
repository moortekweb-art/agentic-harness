"""Maven POM test-source inference for verified tournament verifier boundaries."""

from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from agentic_harness.core.errors import ConfigError
from agentic_harness.core.verifiers.common import (
    _configured_test_root,
    require_lexical_regular_path,
)


def _maven_test_roots(root: Path, *, allow_dynamic: bool) -> set[Path]:
    configured: set[Path] = set()
    for pom in sorted(root.glob("**/pom.xml")):
        require_lexical_regular_path(root, pom, label=str(pom))
        try:
            document = ET.parse(pom)
        except (ET.ParseError, OSError) as exc:
            raise ConfigError(f"unable to inspect Maven verifier inputs: {pom}") from exc
        for element in document.iter():
            if element.tag.rsplit("}", 1)[-1] != "testSourceDirectory":
                continue
            value = (element.text or "").strip()
            if not value:
                continue
            configured.add(
                _configured_test_root(
                    root,
                    pom.parent,
                    value,
                    ecosystem="Maven",
                    allow_dynamic=allow_dynamic,
                )
            )
        for test_resources in (
            element
            for element in document.iter()
            if element.tag.rsplit("}", 1)[-1] == "testResources"
        ):
            for resource in test_resources:
                if resource.tag.rsplit("}", 1)[-1] != "testResource":
                    continue
                for child in resource:
                    if child.tag.rsplit("}", 1)[-1] != "directory":
                        continue
                    value = (child.text or "").strip()
                    if value:
                        configured.add(
                            _configured_test_root(
                                root,
                                pom.parent,
                                value,
                                ecosystem="Maven",
                                allow_dynamic=allow_dynamic,
                            )
                        )
        for execution in (
            element
            for element in document.iter()
            if element.tag.rsplit("}", 1)[-1] == "execution"
        ):
            goals = {
                (goal.text or "").strip()
                for goal in execution.iter()
                if goal.tag.rsplit("}", 1)[-1] == "goal"
            }
            selected_tags: set[str] = set()
            if "add-test-source" in goals:
                selected_tags.add("source")
            if "add-test-resource" in goals:
                selected_tags.add("directory")
            if not selected_tags:
                continue
            for child in execution.iter():
                if child.tag.rsplit("}", 1)[-1] not in selected_tags:
                    continue
                value = (child.text or "").strip()
                if value:
                    configured.add(
                        _configured_test_root(
                            root,
                            pom.parent,
                            value,
                            ecosystem="Maven",
                            allow_dynamic=allow_dynamic,
                        )
                    )
    return configured
