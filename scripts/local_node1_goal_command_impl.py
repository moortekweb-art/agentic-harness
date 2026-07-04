#!/usr/bin/env python3
"""Natural-language-ish command shim for Hermes local Node1 long goals.

This keeps the chat/controller integration thin: Hermes can pass the operator's
message to this script and get a deterministic supervisor command without
asking the operator to remember shell syntax.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from local_node1_goal_phases import Phase, goal_state_from_payload

SUPERVISOR = Path(
    "/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller/scripts/"
    "local-node1-goal-supervisor.py"
)
MANAGER = Path("/mnt/raid0/documentation/scripts/local-node1-goal-manager.py")
WRAPPER = Path("/mnt/raid0/documentation/scripts/local-goal")
STATE_PATH = Path(
    "/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller/state/"
    "local-node1-goal-command-latest.json"
)
REPORT_PATH = Path(
    "/mnt/raid0/home-ai-inference/.hermes-control/profiles/controller/reports/"
    "local-node1-goal-command-latest.md"
)
DOC_ROOT = Path("/mnt/raid0/documentation")
NO_FOOTER_INTENTS = {
    "brief",
    "doctor",
    "trust-boundary",
    "capabilities",
    "readiness-audit",
    "progress",
    "next-proof",
    "completion-summary",
    "completion-audit",
    "audit-health",
    "soak-plan",
    "harness-modes",
    "guide",
    "glm-handoff-plan",
    "glm-supervisor",
    "shortcuts",
    "last-run",
    "model-eval-next",
    "model-status",
    "model-promotion-decision",
    "model-promotion-plan",
    "model-promotion-apply",
    "model-promotion-verify",
    "model-promotion-waiver",
    "free",
    "can-start",
    "stuck",
    "ready-review",
    "can-accept",
}


def now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def normalize(text: str) -> str:
    value = re.sub(r"\s+", " ", text.strip().lower())
    typo_map = {
        "ehat": "what",
        "ehats": "whats",
        "ehat's": "what's",
        "happenning": "happening",
        "hapenning": "happening",
        "orinth": "ornith",
        "modle": "model",
    }
    for typo, replacement in typo_map.items():
        value = re.sub(rf"\b{re.escape(typo)}\b", replacement, value)
    return value


def exact_text_key(text: str) -> str:
    """Normalize exact operator phrases while ignoring trailing punctuation."""
    return normalize(text).strip(" ?!.")


OPS_AUDIT_REPORT_MARKERS = (
    "cronjob response",
    "hourly-codex-ops-audit",
    "keanu ops real audit",
    "4-pass workflow",
    "pre-flight",
    "scorecard",
    "terminal audit",
    "repair pass",
    "cron sla",
    "current truth",
    "watchdog",
    "blockers",
    "disposition",
    "report:",
)


def looks_like_ops_audit_report_context(text: str) -> bool:
    """Detect pasted hourly ops-audit reports so they are not treated as commands."""
    marker_count = sum(1 for marker in OPS_AUDIT_REPORT_MARKERS if marker in text)
    return (
        "hourly-codex-ops-audit" in text
        or "keanu ops real audit" in text
        or marker_count >= 3
    )


def has_local_goal_audit_health_intent(text: str) -> bool:
    """Return True only for explicit local-goal integration audit health requests."""
    explicit_phrase = any(
        phrase in text
        for phrase in (
            "audit-health",
            "audit health",
            "audit lock",
            "lock health",
            "integration audit lock",
            "local harness audit stuck",
            "local harness audit running",
            "local harness audit busy",
            "local goal audit stuck",
            "local goal audit running",
            "local goal audit busy",
            "node1 goal integration audit",
            "node1 integration audit",
            "agentic harness audit stuck",
            "agentic harness audit running",
            "agentic harness audit busy",
        )
    )
    if not explicit_phrase:
        return False
    return any(
        phrase in text
        for phrase in ("local harness", "local goal", "agentic harness", "node1")
    )


def extract_handoff_goal(message: str) -> str:
    """Extract a /goal-style handoff payload from conversational phrases.

    Supports patterns like:
      - "handoff this /goal: implement ..."
      - "offload /goal implement ... to local goal"
      - "/goal implement ..."
      - "transfer /goal: ..."
    """
    patterns = [
        r"(?:hand\s*off|handoff|offload|transfer|send)\s+(?:this\s+)?/goal\s*:\s*(.+)$",
        r"/goal\s*:\s*(.+)$",
        r"/goal\s+(.+)$",
        r"(?:hand\s*off|handoff|offload|transfer|send)\s+.*?/goal\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE | re.DOTALL)
        if match:
            goal = match.group(1).strip()
            # Remove conversational suffixes that can trail a handoff.
            if not goal:
                continue
            goal = re.split(
                r"\s+to\s+(?:local\s+)?node1\b|\s+to\s+(?:the\s+)?hermes\b",
                goal,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            goal = goal.strip()
            if goal:
                return goal
    return ""


def choose_planner(text: str) -> str:
    planner_text = text
    if "planner" in text:
        planner_text = text.split("planner", 1)[0]
        if " with " in planner_text:
            planner_text = planner_text.rsplit(" with ", 1)[-1]
    if "glm" in planner_text or "glim" in planner_text:
        return "glm-5.2"
    if "kimi" in planner_text:
        return "kimi-coding"
    if "thinkmax" in planner_text or "think max" in planner_text:
        return "thinkmax"
    if "deepseek" in planner_text or "deep seek" in planner_text:
        return "deepseek-v4-pro"
    if "gpt" in planner_text or "5.5" in planner_text or "xhigh" in planner_text:
        return "gpt-5.5"
    return "none"


def choose_cloud_worker(text: str) -> str:
    if "glm" in text or "glim" in text:
        return "opencode-glm-build"
    return "opencode-kimi-build"


def extract_goal_text(message: str) -> str:
    goal_text = extract_handoff_goal(message)
    if goal_text:
        return goal_text

    patterns = [
        r"(?:goal|/goal|task)\s*:\s*(.+)$",
        # Cloud-lane prefixes so 'cloud option: <goal>', 'cloud executor: <goal>',
        # 'cloud lane: <goal>', 'non-codex cloud: <goal>' enqueue the goal on the
        # cloud executor lane.
        r"(?:cloud\s+option|cloud\s+executor|cloud\s+lane|cloud\s+build(?:er)?|non[-\s]?codex\s+cloud|cloud\s+worker|cloud)\s*:\s*(.+)$",
        r"(?:planner|builder)\s*:\s*(.+)$",
        r"(?:planner|builder)\s+(.+)$",
        r"(?:with goal|for goal|as local goal)\s+(.+)$",
        # Natural-language planner phrases without an explicit delimiter.
        # Handles GLM-style phrases like:
        #   "start local goal with glm to fix the harness"
        #   "start local goal glm 5.2 fix one bounded issue"
        #   "start local goal with kimi add a test"
        # The goal is the text remaining after the planner name + optional version.
        r"(?:with\s+)?(?:glm|glim|kimi|thinkmax|think\s+max|deepseek|deep\s+seek|gpt)"
        r"(?:\s*[\d.]+)?"
        r"(?:\s+planner)?"
        r"\s+(?:to\s+)?(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE | re.DOTALL)
        if match:
            goal = match.group(1).strip()
            # Reject captures that are just a planner keyword with no real goal
            # (e.g. "planner" left over from "start local goal with glm 5.2 planner").
            if goal.lower() in ("planner", "builder"):
                continue
            goal = re.split(
                r"\s+(?:done criteria|criteria)\s*:",
                goal,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            return goal.strip()
    return ""


def extract_harness_mode_goal(message: str, mode: str) -> str:
    """Extract the task text from the advertised Hermes harness-mode phrases."""
    patterns_by_mode = {
        "default": [
            r"use\s+(?:the\s+)?default\s+harness\s+mode\s+for\s+(?:this\s+)?goal\s*:\s*(.+)$",
            r"use\s+mode\s+1\s+for\s+(?:this\s+)?goal\s*:\s*(.+)$",
        ],
        "codex_saving": [
            r"have\s+glm(?:-?5\.2)?\s+supervise\s+.*?codex\s+.*?spot[- ]?check.*?:\s*(.+)$",
            r"use\s+mode\s+2\s+for\s+(?:this\s+)?goal\s*:\s*(.+)$",
        ],
        "cloud_canary": [
            r"run\s+(?:a\s+)?bounded\s+cloud\s+canary\s+with\s+(?:the\s+)?glm\s+worker\s*:\s*(.+)$",
            r"use\s+mode\s+3\s+for\s+(?:this\s+)?goal\s*:\s*(.+)$",
        ],
    }
    for pattern in patterns_by_mode.get(mode, []):
        match = re.search(pattern, message, flags=re.IGNORECASE | re.DOTALL)
        if match:
            goal = match.group(1).strip()
            if goal:
                return goal
    return ""


def extract_plain_programming_goal(message: str) -> str:
    """Treat a direct programming request as the goal text.

    The Hermes controller may be running on a conversational model such as
    MiniMax M3. Keep this deterministic so the model only has to pass through
    the operator's plain-language request; this shim chooses the harness command.
    """
    value = re.sub(
        r"^\s*/?(?:local[-_ ]?goal|node1[-_ ]?goal)\s*:?\s*",
        "",
        message.strip(),
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"^\s*(?:please\s+)?(?:hermes,?\s+|can\s+you|could\s+you|would\s+you|i\s+want\s+you\s+to|have\s+hermes)\s+",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()
    if not value:
        return ""

    first_word = re.match(r"^([a-zA-Z][\w-]*)\b", value)
    if not first_word:
        return ""
    verb = first_word.group(1).lower()
    implementation_verbs = {
        "add",
        "build",
        "create",
        "debug",
        "fix",
        "finish",
        "implement",
        "improve",
        "integrate",
        "make",
        "patch",
        "refactor",
        "repair",
        "test",
        "update",
        "wire",
    }
    if verb not in implementation_verbs:
        return ""
    return value


def extract_implicit_mission_goal(message: str) -> str:
    """Extract a broad long-running goal from plain operator language."""
    value = re.sub(
        r"^\s*/?(?:local[-_ ]?goal|node1[-_ ]?goal)\s*:?\s*",
        "",
        message.strip(),
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"^\s*(?:please\s+)?(?:hermes,?\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|i\s+want\s+you\s+to\s+|have\s+hermes\s+)",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()
    value = re.sub(
        r"\b(?:dry\s*run|parse\s*only|route\s*check|routing\s*check|without\s+executing|no\s+side\s+effects)\b",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", value).strip()


def _looks_like_implicit_long_mission(text: str) -> bool:
    """Conservatively detect plain long-horizon work that should become mission mode."""
    duration_or_loop = any(
        phrase in text
        for phrase in (
            "next 10 hours",
            "next ten hours",
            "for 10 hours",
            "for ten hours",
            "for about 10 hours",
            "for about ten hours",
            "long horizon",
            "long-horizon",
            "until review passes",
            "until accepted",
        )
    )
    if not duration_or_loop:
        return False
    if not (
        "keep working" in text
        or "review passes" in text
        or "accepted" in text
        or "agent society" in text
        or "website" in text
        or "usefulness" in text
        or "harness" in text
        or "local goal" in text
        or "local-goal" in text
        or "node1 goal" in text
        or "node1 /goal" in text
    ):
        return False
    return any(
        re.search(rf"\b{verb}\b", text)
        for verb in (
            "add",
            "adding",
            "build",
            "building",
            "create",
            "creating",
            "debug",
            "debugging",
            "fix",
            "fixing",
            "finish",
            "finishing",
            "implement",
            "implementing",
            "improve",
            "improving",
            "integrate",
            "integrating",
            "patch",
            "patching",
            "repair",
            "repairing",
            "test",
            "testing",
            "update",
            "updating",
        )
    )


def extract_done_criteria(message: str) -> str:
    patterns = [
        r"(?:done criteria|criteria|done)\s*:\s*(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    return ""


def extract_nudge_text(message: str) -> str:
    patterns = [
        r"(?:nudge|steer|guide)\s+(?:the\s+)?(?:local\s+)?(?:node1\s+)?(?:goal|harness)?\s*:\s*(.+)$",
        r"(?:nudge|steer|guide)\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE | re.DOTALL)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return ""


def _has_log_intent(text: str) -> bool:
    """Check for log/show-log intent without matching 'login' or 'catalog'."""
    log_phrases = ("show log", "tail log", "recent log", "show logs", "tail logs")
    if any(phrase in text for phrase in log_phrases):
        return True
    # " log" at word boundary — must be followed by space/end, not another letter
    if re.search(r"(?:^|\s)log(?:\s|$)", text):
        return True
    return False


def _has_supervise_intent(text: str) -> bool:
    """Detect requests for active Hermes supervision, not passive status."""
    phrases = (
        "supervise",
        "fully supervise",
        "supervisor mode",
        "unattended",
        "auto continue",
        "auto-continue",
        "auto accept",
        "auto-accept",
        "auto dispatch",
        "auto-dispatch",
        "auto commit",
        "auto-commit",
        "keep working",
        "keep it working",
        "keep the harness working",
        "watch and continue",
        "monitor local goal",
        "monitor local harness",
        "monitor node1 goal",
        "watch local goal",
        "watch local harness",
        "watch node1 goal",
    )
    return any(phrase in text for phrase in phrases)


def _has_progress_report_intent(text: str) -> bool:
    """Detect requests for a compact progress/readiness report."""
    if any(
        phrase in text
        for phrase in (
            "how is this progressing",
            "how is it progressing",
            "how is the harness progressing",
            "how is the agentic harness progressing",
            "how is the local harness progressing",
            "how is the local goal progressing",
            "how is the harness coming along",
            "how is the agentic harness coming along",
            "how is the local harness coming along",
            "how is the local goal coming along",
            "how is this coming along",
            "what is happening",
            "whats happening",
            "what's happening",
            "what is going on",
            "whats going on",
            "what's going on",
            "how is the progress",
            "how is progress",
            "how is it going",
            "hows it going",
            "how's it going",
            "how is this going",
            "how is the harness going",
            "any update",
            "any updates",
            "update me",
            "give me an update",
            "check in",
            "check on it",
            "check the harness",
            "check local goal",
            "keep me posted",
            "keep me updated",
            "let me know what happens",
            "where are we at",
            "where do we stand",
            "where are we now",
            "where are we with the harness",
            "where are we with the agentic harness",
            "where are we with the local goal",
            "what is the harness status",
            "what is the agentic harness status",
            "what is the local goal status",
            "what's the status of the harness",
            "what is the status of the harness",
            "status of the harness",
            "status of local goal",
            "status of the local goal",
            "is the harness okay",
            "is the agentic harness okay",
            "is local goal okay",
            "is the local goal okay",
            "is everything okay",
            "is everything ok",
            "are we still good",
            "is it alive",
            "is the harness alive",
            "is node1 alive",
            "still alive",
            "still working",
            "progress update",
            "progress report",
            "status update",
            "how much progress",
        )
    ):
        return True
    return "progress" in text and any(
        phrase in text
        for phrase in (
            "agentic harness",
            "local harness",
            "local goal",
            "node1 goal",
            "node1 harness",
        )
    )


def _has_accepted_evidence_readback_intent(text: str) -> bool:
    """Detect read-only questions about the latest accepted run evidence."""
    phrases = (
        "what files did the last local goal change",
        "what files did the last goal change",
        "what files did the last run change",
        "what did the last local goal change",
        "what did the last goal change",
        "what did the last run change",
        "what did the last run do",
        "what did the accepted run change",
        "what did the accepted local goal change",
        "show the last run",
        "show me the accepted evidence",
        "show me the accepted local goal evidence",
        "show accepted evidence",
        "show accepted local goal evidence",
        "accepted local goal evidence",
        "show latest accepted run",
        "show last accepted run",
        "what evidence was accepted",
        "what accepted evidence",
        "what was accepted",
        "what was verified",
        "what verification passed",
        "what verification did it run",
        "what tests passed",
        "what did review accept",
        "what did the review accept",
        "what did review say",
        "show review evidence",
        "review evidence",
        "show the review",
        "what did it accept",
        "accept evidence",
        "accept the evidence",
        "should i accept the evidence",
        "can i accept the evidence",
        "what files are owned by the last run",
        "what are the owned files",
        "owned files from the last run",
    )
    return any(phrase in text for phrase in phrases)


def _has_dirty_acceptance_question_intent(text: str) -> bool:
    """Detect read-only questions about whether dirty work blocks acceptance."""
    has_dirty_scope = "dirty" in text or "worktree" in text or "uncommitted" in text
    if not has_dirty_scope:
        return False
    return any(
        phrase in text
        for phrase in (
            "block acceptance",
            "blocks acceptance",
            "blocking acceptance",
            "block accept",
            "blocks accept",
            "safe to accept",
            "dirty work block",
            "dirty files block",
            "dirty work non-blocking",
            "dirty work non blocking",
            "dirty files non-blocking",
            "dirty files non blocking",
        )
    )


def _has_next_proof_intent(text: str) -> bool:
    """Detect requests for the next evidence needed to trust the harness more."""
    if text in {"next proof", "next-proof", "proof next"}:
        return True
    has_harness_scope = any(
        phrase in text
        for phrase in (
            "agentic harness",
            "local harness",
            "local goal",
            "node1 goal",
            "node1 harness",
            "harness",
        )
    )
    if not has_harness_scope:
        return False
    return any(
        phrase in text
        for phrase in (
            "what proof remains",
            "what proof is left",
            "what evidence remains",
            "what evidence is left",
            "what is left to prove",
            "what do we still need to prove",
            "next proof",
            "proof next",
            "autonomy proof",
            "unattended proof",
            "what hardening remains",
            "what hardening is left",
            "hardening remains",
            "hardening is left",
            "hardening left",
            "next hardening",
            "optional hardening",
            "soak test",
            "soak proof",
            "100% proof",
            "100 percent proof",
            "get to 100%",
            "get to 100 percent",
        )
    )


def _has_completion_audit_intent(text: str) -> bool:
    """Detect requests for requirement-by-requirement harness completion evidence."""
    if text in {
        "completion audit",
        "completion-audit",
        "harness audit",
        "completion proof",
        "completion evidence",
        "requirement audit",
        "requirements audit",
    }:
        return True
    has_harness_scope = any(
        phrase in text
        for phrase in (
            "agentic harness",
            "local harness",
            "local goal harness",
            "node1 harness",
            "node1 goal",
            "harness",
        )
    )
    if not has_harness_scope:
        return False
    return any(
        phrase in text
        for phrase in (
            "completion audit",
            "completion evidence",
            "completion proof",
            "requirement audit",
            "requirement-by-requirement",
            "requirements audit",
            "is the harness complete",
            "is the agentic harness complete",
            "is the local harness complete",
            "is the harness done",
            "is the agentic harness done",
            "is the local harness done",
            "is the harness finished",
            "is the agentic harness finished",
            "is the local harness finished",
            "can the harness be called complete",
            "prove the harness is ready",
            "prove the harness is complete",
            "prove the local goal harness is ready",
            "prove the local goal harness is complete",
            "audit harness completion",
            "audit local goal completion",
        )
    )


def _has_completion_summary_intent(text: str) -> bool:
    """Detect requests for the compact harness completion headline."""
    if text in {
        "completion summary",
        "completion-summary",
        "harness summary",
        "harness-summary",
        "proof summary",
        "proof-summary",
    }:
        return True
    has_harness_scope = any(
        phrase in text
        for phrase in (
            "agentic harness",
            "local harness",
            "local goal harness",
            "node1 harness",
            "node1 goal",
            "harness",
        )
    )
    if not has_harness_scope:
        return False
    return any(
        phrase in text
        for phrase in (
            "completion summary",
            "proof summary",
            "short completion",
            "short proof",
            "summary proof",
            "quick proof",
            "proof headline",
            "completion headline",
        )
    )


def _has_soak_plan_intent(text: str) -> bool:
    """Detect requests for the exact optional unattended soak proof commands."""
    has_harness_scope = any(
        phrase in text
        for phrase in (
            "agentic harness",
            "local harness",
            "local goal",
            "node1 goal",
            "node1 harness",
            "harness",
        )
    )
    if not has_harness_scope or "soak" not in text:
        return False
    return any(
        phrase in text
        for phrase in (
            "what do i type",
            "what should i type",
            "what command",
            "exact command",
            "show command",
            "give me command",
            "plan",
            "recipe",
            "how do i run",
            "how to run",
            "can i run",
            "start",
            "run",
        )
    )


def _has_harness_modes_intent(text: str) -> bool:
    """Detect read-only questions about Hermes gateway harness modes and canaries."""
    if text in {
        "harness modes",
        "harness mode",
        "champion modes",
        "goal modes",
        "mode guide",
        "gateway modes",
        "which harness mode should i use",
        "what harness mode should i use",
        "which mode should i use",
    }:
        return True
    has_scope = any(
        phrase in text
        for phrase in (
            "harness",
            "local goal",
            "node1 goal",
            "agentic",
            "hermes controller",
            "gateway",
            "champion",
        )
    )
    if not has_scope:
        return False
    return any(
        phrase in text
        for phrase in (
            "which harness mode",
            "what harness mode",
            "which mode should",
            "what mode should",
            "four modes",
            "four harness modes",
            "4 modes",
            "4 harness modes",
            "four types",
            "four harness types",
            "4 types",
            "4 harness types",
            "mode guide",
            "gateway mode",
            "champion mode",
            "opencode executes",
            "glm supervises",
            "glm 5.2 monitor",
            "glm-5.2 monitor",
            "glm 5.2 supervise",
            "glm-5.2 supervise",
        )
    )


def _has_harness_teaching_intent(text: str) -> bool:
    """Detect plain-language requests for Hermes to teach local-goal usage."""
    if "soak" in text:
        return False
    has_scope = any(
        phrase in text
        for phrase in (
            "agentic harness",
            "local harness",
            "local goal harness",
            "node1 harness",
            "node1 goal",
            "local goal",
            "harness",
        )
    )
    if not has_scope:
        return False
    return any(
        phrase in text
        for phrase in (
            "teach me",
            "teach me how",
            "show me how",
            "walk me through",
            "how do i use",
            "how to use",
            "help me use",
            "what do i type",
            "what should i type",
            "what command do i type",
            "how do i start",
            "how to start",
            "how should i start",
            "explain how",
            "operator guide",
            "human guide",
            "show guide",
            "usage guide",
            "using the harness",
            "use it seamlessly",
            "use it seamlesly",
            "use it seemlessly",
            "use it seemlesly",
            "seamlessly",
            "seamlesly",
            "seemlessly",
            "seemlesly",
        )
    )


def _has_ornith_promotion_plan_intent(text: str) -> bool:
    """Detect requests for exact durable Ornith promotion commands."""
    return (
        "ornith" in text
        and (
            "what do i type" in text
            or "what should i type" in text
            or "exact command" in text
            or "terminal command" in text
            or "what command" in text
            or "paste" in text
            or "commands" in text
            or "plan" in text
            or "drop-in" in text
            or "drop in" in text
        )
        and (
            "permanent" in text
            or "permanently" in text
            or "durable" in text
            or "promote" in text
            or "promotion" in text
        )
    )


def _has_harness_next_work_intent(text: str) -> bool:
    """Detect plain "keep going / do next" requests for the local harness."""
    if any(
        phrase in text
        for phrase in (
            "what should i do next",
            "what should we do next",
            "what do i do next",
            "what do we do next",
            "what next",
            "what's next",
            "whats next",
            "what now",
            "now what",
            "what else",
            "what do i type",
            "what should i type",
        )
    ):
        return False
    phrases = (
        "do next",
        "do the next",
        "do the next step",
        "do the next thing",
        "next harness step",
        "next agentic harness step",
        "continue the harness",
        "continue with the harness",
        "keep working on",
        "keep improving",
        "keep developing",
        "continue developing",
        "continue improving",
        "make the harness better",
        "make the agentic harness better",
        "make the local harness better",
        "fix the next harness issue",
        "fix the next agentic harness issue",
        "fix the next local harness issue",
        "fix another harness issue",
        "fix another agentic harness issue",
        "fix another local harness issue",
        "do another hardening pass",
        "hardening pass",
        "keep going",
        "carry on",
        "move forward",
    )
    return any(phrase in text for phrase in phrases)


def _mentions_local_goal_harness(text: str) -> bool:
    """Detect operator phrases that mean the local Node1 /goal harness."""
    phrases = (
        "agentic harness",
        "local agentic harness",
        "local harness",
        "node1 harness",
        "node1 /goal",
        "node1 goal",
        "codex-like goal",
        "codex like goal",
        "local /goal",
        "local goal",
        "local-node1-goal",
        "local node1 goal",
    )
    return any(phrase in text for phrase in phrases)


def _has_local_goal_action_question_intent(text: str) -> bool:
    """Detect operator questions about live actions so they do not execute."""
    if not any(word in text for word in ("accept", "review", "stop", "pause")):
        return False
    question_phrases = (
        "can i ",
        "can we ",
        "could i ",
        "could we ",
        "should i ",
        "should we ",
        "do i need to ",
        "do we need to ",
        "do you think i should ",
        "do you think we should ",
        "is it ready",
        "is this ready",
        "is the local goal ready",
        "is the harness ready",
        "ready for review",
        "ready to review",
        "ready to accept",
        "safe to pause",
        "safe to stop",
        "safe to accept",
        "safe to review",
    )
    return "?" in text or any(phrase in text for phrase in question_phrases)


def _has_lane_help_question_intent(text: str) -> bool:
    """Detect operator questions about optional lanes without starting work."""
    lane_terms = (
        "cloud executor",
        "cloud local goal",
        "cloud lane",
        "planner local builder",
        "planner assisted",
        "planner-assisted",
        "gpt 5.5 planner",
        "glm planner",
        "glm 5.2 planner",
        "kimi planner",
        "premium planner",
    )
    if not any(term in text for term in lane_terms):
        return False
    if ":" in text and any(
        word in text for word in ("start", "enqueue", "queue", "transfer")
    ):
        return False
    help_terms = (
        "what do i type",
        "what should i type",
        "what command",
        "what does",
        "what is",
        "how do i",
        "how do we",
        "can i",
        "can we",
        "should i",
        "should we",
        "is ",
        "meaning",
        "explain",
    )
    return "?" in text or any(term in text for term in help_terms)


def _has_explicit_cloud_executor_intent(text: str) -> bool:
    """Detect explicit cloud-executor requests that must NOT fall through to status/doctor.

    The generic 'cloud' detection in parse_command only fires when paired with
    start/transfer/enqueue/local-goal keywords.  This catches the shorter
    operator phrasings — 'cloud option', 'cloud executor', 'non-codex cloud',
    'use the cloud lane' — so they route to the cloud path (or the shortcuts
    card when no goal is attached) instead of landing on status/doctor/capabilities.
    """
    # Strong explicit-cloud phrases that are unambiguous.
    strong_phrases = (
        "cloud option",
        "cloud executor",
        "non-codex cloud",
        "non codex cloud",
        "cloud worker",
        "cloud lane",
        "cloud build",
        "cloud builder",
        "cloud assisted",
        "cloud-assisted",
        "glm cloud",
        "kimi cloud",
        "cloud glm",
        "cloud kimi",
        "use the cloud",
        "try the cloud",
        "cloud instead",
    )
    return any(phrase in text for phrase in strong_phrases)


def _doctor_command(
    base: list[str],
) -> tuple[list[str], str, str]:
    return (
        base + ["doctor"],
        "doctor",
        "show local goal status, mission, lanes, and recommended next action",
    )


def _has_trust_boundary_intent(text: str) -> bool:
    """Detect operator questions about leaving the local-goal watcher unattended."""
    return any(
        phrase in text
        for phrase in (
            "babysit",
            "trust the agentic harness",
            "trust the local harness",
            "trust the harness",
            "leave it alone",
            "let it work",
            "let it keep working",
            "let you keep working",
            "let the harness keep working",
            "let the local goal keep working",
            "keep checking it",
            "keep checking the harness",
            "keep going by itself",
            "leave the harness",
            "leave the local goal",
            "leave node1",
            "running overnight",
            "walk away",
            "without babysitting",
            "will hermes tell me",
            "will hermes notify me",
            "will it ask me",
            "will it notify me",
            "will it tell me",
            "notify me if",
            "tell me if it needs me",
            "ask me for approval",
        )
    )


def _has_pronoun_model_quality_intent(text: str) -> bool:
    """Detect short model followups after an operator has been discussing Ornith."""
    if any(
        word in text
        for word in (
            "window",
            "service",
            "cutover",
            "open",
            "restore",
            "rollback",
            "packet",
            "bundle",
            "pause",
            "stop",
            "accept",
            "review",
        )
    ):
        return False
    direct_phrases = {
        "is it good",
        "is it good?",
        "do you trust it",
        "do you trust it?",
        "should we use it",
        "should we use it?",
        "should i use it",
        "should i use it?",
        "should we promote it",
        "should we promote it?",
        "should i promote it",
        "should i promote it?",
        "can we promote it",
        "can we promote it?",
        "can i promote it",
        "can i promote it?",
        "promote it",
        "promote it?",
        "make it permanent",
        "make it permanent?",
        "make this permanent",
        "make this permanent?",
        "make the model permanent",
        "make the model permanent?",
        "make the new model permanent",
        "make the new model permanent?",
    }
    if text in direct_phrases:
        return True
    return (
        any(phrase in text for phrase in (" it ", " it?", " it."))
        and any(
            phrase in text
            for phrase in ("harness", "local goal", "local-goal", "node1")
        )
        and any(
            phrase in text
            for phrase in (
                "good",
                "trust",
                "better",
                "best",
                "use",
                "replace",
                "switch",
                "promote",
                "promotion",
            )
        )
    )


def has_dry_run_intent(message: str) -> bool:
    """Detect text-level route inspection for Hermes chat commands."""
    text = normalize(message)
    phrases = (
        "dry run",
        "dry-run",
        "parse only",
        "parse-only",
        "route check",
        "routing check",
        "preview",
        "what would happen",
        "do not execute",
        "don't execute",
        "dont execute",
        "without executing",
        "no execute",
        "no side effects",
    )
    return any(phrase in text for phrase in phrases)


def parse_command(
    message: str, *, goal_file: str | None = None
) -> tuple[list[str], str, str]:
    text = normalize(message)
    exact_key = exact_text_key(message)
    alias_text = text
    for prefix in (
        "dry run ",
        "dry-run ",
        "parse only ",
        "parse-only ",
        "route check ",
        "routing check ",
        "preview ",
        "what would happen if i ",
        "what would happen if ",
    ):
        if alias_text.startswith(prefix):
            alias_text = alias_text.removeprefix(prefix).strip()
            break
    is_slash_local_goal_command = False
    for command_prefix in (
        "/local-goal ",
        "/local_goal ",
        "/node1-goal ",
        "/node1_goal ",
    ):
        if alias_text.startswith(command_prefix):
            is_slash_local_goal_command = True
            alias_text = alias_text.removeprefix(command_prefix).strip()
            break
    alias_key = alias_text.strip(" ?!.")
    base = ["python3", str(SUPERVISOR)]
    local_harness_mentioned = _mentions_local_goal_harness(text)

    if looks_like_ops_audit_report_context(text) and not is_slash_local_goal_command:
        return (
            [str(WRAPPER), "doctor", "--json"],
            "doctor",
            "quoted hourly ops-audit report is not a local-goal command",
        )

    glm_supervisor_requested = (
        ("glm" in text or "glim" in text)
        and (
            "tmux" in text
            or "background" in text
            or "loop" in text
            or "persistent" in text
            or "keep watching" in text
            or "keep supervising" in text
            or "monitor codex" in text
            or "codex monitor" in text
            or "supervisor session" in text
            or "supervisor tmux" in text
            or "glm supervisor" in text
            or "glm 5.2 supervisor" in text
            or "glm-5.2 supervisor" in text
        )
        and (
            local_harness_mentioned
            or "harness" in text
            or "local goal" in text
            or "node1" in text
            or "agentic" in text
            or "codex" in text
        )
    )
    if glm_supervisor_requested:
        supervisor_action_text = alias_text
        if any(
            term in supervisor_action_text
            for term in ("stop", "kill", "end", "turn off", "disable")
        ):
            action = "stop"
            reason = "stop the GLM advisory tmux supervisor loop"
        elif any(
            term in supervisor_action_text
            for term in (
                "start",
                "run",
                "launch",
                "turn on",
                "enable",
                "use glm",
                "hand off",
                "handoff",
            )
        ):
            action = "start"
            reason = "start the GLM advisory tmux supervisor loop"
        else:
            action = "status"
            reason = "show the GLM advisory tmux supervisor loop status"
        return (
            [
                str(WRAPPER),
                "glm-supervisor",
                action,
                "--reviewer",
                "glm-5.2",
                "--timeout",
                "300",
                "--json",
            ],
            "glm-supervisor",
            reason,
        )

    exact_external_review_aliases = {
        "ask glm to review the local goal": "glm-5.2",
        "ask glm to look at it": "glm-5.2",
        "get glm opinion on the harness": "glm-5.2",
        "have glm review the local harness": "glm-5.2",
        "kimi supervisor review for node1 goal": "kimi-coding",
        "ask kimi to check the local goal": "kimi-coding",
        "external check local goal with kimi": "kimi-coding",
    }
    external_review_key = (
        alias_text if alias_text in exact_external_review_aliases else alias_key
    )
    if external_review_key in exact_external_review_aliases:
        reviewer = exact_external_review_aliases[external_review_key]
        return (
            base + ["external-review", "--reviewer", reviewer, "--json"],
            "external-review",
            f"run advisory external supervisor review with {reviewer}",
        )

    has_external_review_intent = (
        ("external" in text or "glm" in text or "glim" in text or "kimi" in text)
        and (
            "review" in text
            or "supervisor" in text
            or "supervise" in text
            or "look at" in text
            or "check" in text
            or "opinion" in text
        )
        and (
            "local" in text
            or "goal" in text
            or "node1" in text
            or local_harness_mentioned
            or " it" in text
        )
        and not (
            "planner" in text
            or "start" in text
            or "enqueue" in text
            or "queue" in text
            or "cloud" in text
            or "transfer" in text
        )
    )

    direct_wrapper_aliases = {
        "help": (
            "shortcuts",
            "show grouped safe local-goal help and shortcuts",
        ),
        "shortcuts": (
            "shortcuts",
            "show grouped safe local-goal help and shortcuts",
        ),
        "cheatsheet": (
            "shortcuts",
            "show grouped safe local-goal help and shortcuts",
        ),
        "progress": (
            "progress",
            "show compact local harness progress report",
        ),
        "next-proof": (
            "next-proof",
            "show the next proof needed to harden unattended harness trust",
        ),
        "completion-summary": (
            "completion-summary",
            "show compact local harness completion proof summary",
        ),
        "completion summary": (
            "completion-summary",
            "show compact local harness completion proof summary",
        ),
        "harness-summary": (
            "completion-summary",
            "show compact local harness completion proof summary",
        ),
        "harness summary": (
            "completion-summary",
            "show compact local harness completion proof summary",
        ),
        "proof-summary": (
            "completion-summary",
            "show compact local harness completion proof summary",
        ),
        "proof summary": (
            "completion-summary",
            "show compact local harness completion proof summary",
        ),
        "completion-audit": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "completion audit": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "completion": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "harness-audit": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "harness audit": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "completion proof": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "completion evidence": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "harness-modes": (
            "harness-modes",
            "show the Hermes gateway harness modes, including Mode 4B, and recommended default",
        ),
        "harness modes": (
            "harness-modes",
            "show the Hermes gateway harness modes, including Mode 4B, and recommended default",
        ),
        "champion-modes": (
            "harness-modes",
            "show the Hermes gateway harness modes, including Mode 4B, and recommended default",
        ),
        "champion modes": (
            "harness-modes",
            "show the Hermes gateway harness modes, including Mode 4B, and recommended default",
        ),
        "goal-modes": (
            "harness-modes",
            "show the Hermes gateway harness modes, including Mode 4B, and recommended default",
        ),
        "goal modes": (
            "harness-modes",
            "show the Hermes gateway harness modes, including Mode 4B, and recommended default",
        ),
        "mode-guide": (
            "harness-modes",
            "show the Hermes gateway harness modes, including Mode 4B, and recommended default",
        ),
        "mode guide": (
            "harness-modes",
            "show the Hermes gateway harness modes, including Mode 4B, and recommended default",
        ),
        "which harness mode should i use": (
            "harness-modes",
            "show the Hermes gateway harness modes, including Mode 4B, and recommended default",
        ),
        "which harness mode should i use?": (
            "harness-modes",
            "show the Hermes gateway harness modes, including Mode 4B, and recommended default",
        ),
        "phone-modes": (
            "phone-modes",
            "show exact Hermes/Telegram commands for local, planner-local, and cloud goal modes",
        ),
        "phone modes": (
            "phone-modes",
            "show exact Hermes/Telegram commands for local, planner-local, and cloud goal modes",
        ),
        "phone-commands": (
            "phone-modes",
            "show exact Hermes/Telegram commands for local, planner-local, and cloud goal modes",
        ),
        "phone commands": (
            "phone-modes",
            "show exact Hermes/Telegram commands for local, planner-local, and cloud goal modes",
        ),
        "phone-goal-modes": (
            "phone-modes",
            "show exact Hermes/Telegram commands for local, planner-local, and cloud goal modes",
        ),
        "what do i send to hermes for a goal": (
            "phone-modes",
            "show exact Hermes/Telegram commands for local, planner-local, and cloud goal modes",
        ),
        "what do i type for a goal": (
            "phone-modes",
            "show exact Hermes/Telegram commands for local, planner-local, and cloud goal modes",
        ),
        "what should i send for a local goal": (
            "phone-modes",
            "show exact Hermes/Telegram commands for local, planner-local, and cloud goal modes",
        ),
        "what should i send for a cloud goal": (
            "phone-modes",
            "show exact Hermes/Telegram commands for local, planner-local, and cloud goal modes",
        ),
        "audit-health": (
            "audit-health",
            "show local-goal integration audit lock and artifact health",
        ),
        "audit-lock": (
            "audit-health",
            "show local-goal integration audit lock and artifact health",
        ),
        "lock-health": (
            "audit-health",
            "show local-goal integration audit lock and artifact health",
        ),
        "soak-plan": (
            "soak-plan",
            "show the exact optional unattended soak proof commands without starting work",
        ),
        "estimate": (
            "completion-summary",
            "show evidence-backed autonomy grade from completion summary",
        ),
        "can i start a local goal?": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "can i start a local goal now": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "can i start a local goal now?": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "can i start the local harness now": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "can i start the local harness now?": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "can i start now": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "can i start a goal now": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "should i start a goal now": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "can i run a local goal now": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "can i run it now": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "can i run it now?": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "should i run it now": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "should i run it now?": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "is it safe to start now": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "is it safe to launch the harness": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "is it safe to launch the harness?": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "should i launch it": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "should i launch it?": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "can i start if vllm is busy": (
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        ),
        "is node1 free for a local goal?": (
            "free",
            "show whether the local-goal lane is free",
        ),
        "is the local goal lane free?": (
            "free",
            "show whether the local-goal lane is free",
        ),
        "is local goal lane free?": (
            "free",
            "show whether the local-goal lane is free",
        ),
        "is the local-goal lane free?": (
            "free",
            "show whether the local-goal lane is free",
        ),
        "what is node1 doing?": (
            "free",
            "show whether the local-goal lane is free",
        ),
        "what is node1 vllm doing?": (
            "free",
            "show whether the local-goal lane is free",
        ),
        "is node1 busy?": (
            "free",
            "show whether the local-goal lane is free",
        ),
        "is node1 busy with the agentic harness?": (
            "free",
            "show whether the local-goal lane is free",
        ),
        "is node1 vllm busy?": (
            "free",
            "show whether the local-goal lane is free",
        ),
        "why is node1 busy?": (
            "free",
            "show whether the local-goal lane is free",
        ),
        "does node1 have other activity?": (
            "free",
            "show whether the local-goal lane is free",
        ),
        "is vllm busy?": (
            "free",
            "show whether the local-goal lane is free",
        ),
        "what is vllm doing?": (
            "free",
            "show whether the local-goal lane is free",
        ),
        "are gpus idle?": (
            "free",
            "show whether the local-goal lane is free",
        ),
        "are the gpus busy?": (
            "free",
            "show whether the local-goal lane is free",
        ),
        "can i accept the local goal?": (
            "can-accept",
            "show whether accepting the current local goal is appropriate",
        ),
        "should i accept it?": (
            "can-accept",
            "show whether accepting the current local goal is appropriate",
        ),
        "can i accept it now?": (
            "can-accept",
            "show whether accepting the current local goal is appropriate",
        ),
        "can i accept the harness result?": (
            "can-accept",
            "show whether accepting the current local goal is appropriate",
        ),
        "is it ready to accept?": (
            "can-accept",
            "show whether accepting the current local goal is appropriate",
        ),
        "approve it": (
            "can-accept",
            "show whether accepting the current local goal is appropriate",
        ),
        "approve the local goal": (
            "can-accept",
            "show whether accepting the current local goal is appropriate",
        ),
        "approve the evidence": (
            "can-accept",
            "show whether accepting the current local goal is appropriate",
        ),
        "approve the accepted evidence": (
            "can-accept",
            "show whether accepting the current local goal is appropriate",
        ),
        "is the local goal ready for review?": (
            "ready-review",
            "show whether the current local goal is ready for review",
        ),
        "should i review it?": (
            "ready-review",
            "show whether the current local goal is ready for review",
        ),
        "should i review it": (
            "ready-review",
            "show whether the current local goal is ready for review",
        ),
        "can i review it now?": (
            "ready-review",
            "show whether the current local goal is ready for review",
        ),
        "can i review it now": (
            "ready-review",
            "show whether the current local goal is ready for review",
        ),
        "is it ready for review?": (
            "ready-review",
            "show whether the current local goal is ready for review",
        ),
        "is the harness ready for review?": (
            "ready-review",
            "show whether the current local goal is ready for review",
        ),
        "is node1 stuck?": (
            "stuck",
            "show whether the local-goal lane appears stuck",
        ),
        "is it stuck?": (
            "stuck",
            "show whether the local-goal lane appears stuck",
        ),
        "is the harness stuck?": (
            "stuck",
            "show whether the local-goal lane appears stuck",
        ),
        "did it crash?": (
            "stuck",
            "show whether the local-goal lane appears stuck",
        ),
        "did it stop?": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "why did it stop?": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "what should i do next": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "what should we do next": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "what do i do next": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "what do we do next": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "what now": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "what now then": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "what next": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "what next?": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "whats next": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "what's next": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "now what": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "what else": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "no it did not work": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "it did not work": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "still nothing": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "what do i type": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what should i type": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what do i type next": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what do i type next?": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what should i type next": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what should i type next?": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what do i type to start a bounded goal": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what do i type to start a bounded goal?": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what command starts a bounded local goal": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what command starts a bounded local goal?": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "how do i start the next local goal": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "how do i start the next local goal?": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "how do i start a bounded local goal": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "how do i start a bounded local goal?": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what do i paste to start a local goal": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what do i paste to start a local goal?": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "give me the command to start a local goal": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what is the safest way to start a local goal": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what is the safest way to start a local goal?": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what should i type for a bounded task": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what should i type for a bounded task?": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what should i type to start a local goal": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what should i type to start a local goal?": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what do i type to start a local goal": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "what do i type to start a local goal?": (
            "brief",
            "show the shortest bounded local-goal start command",
        ),
        "how is the agentic harness": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "how is the agentic harness?": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "how is agentic harness": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "how is agentic harness?": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "how is the local harness": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "how is the local harness?": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "how is the harness": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "how is the harness?": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "start local goal": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "start a local goal": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "start a goal": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "start goal": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "start it": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "run it": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "run a local goal": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "queue it": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "queue a goal": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "queue a local goal": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "launch local harness": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "launch the local harness": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "pause it": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "stop it": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "should i stop it?": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "should i stop the harness?": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "can i stop it now?": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "is it safe to stop?": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "should i pause it?": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "can i pause it now?": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "is it safe to pause?": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "should i continue it?": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "can i continue it now?": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "should i resume it?": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "can i resume it now?": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "new goal": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "create local goal": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "quick start": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "quickstart": (
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        ),
        "done": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "finished": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "it is done": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "its done": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "it's done": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "looks done": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "is it done": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "did it finish": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "verify it": (
            "next-proof",
            "show the next proof needed to harden unattended harness trust",
        ),
        "show proof": (
            "next-proof",
            "show the next proof needed to harden unattended harness trust",
        ),
        "show evidence": (
            "next-proof",
            "show the next proof needed to harden unattended harness trust",
        ),
        "show the evidence": (
            "next-proof",
            "show the next proof needed to harden unattended harness trust",
        ),
        "prove it": (
            "next-proof",
            "show the next proof needed to harden unattended harness trust",
        ),
        "check progress": (
            "progress",
            "show compact local harness progress report",
        ),
        "any progress": (
            "progress",
            "show compact local harness progress report",
        ),
        "progress?": (
            "progress",
            "show compact local harness progress report",
        ),
        "anything happen": (
            "progress",
            "show compact local harness progress report",
        ),
        "what happened": (
            "progress",
            "show compact local harness progress report",
        ),
        "where is it": (
            "progress",
            "show compact local harness progress report",
        ),
        "show last run": (
            "last-run",
            "show latest accepted local-goal evidence and changed-file summary",
        ),
        "what did it do": (
            "progress",
            "show compact local harness progress report",
        ),
        "is it working": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "did it work": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "did that work": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "did the harness work": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "all good": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "are we good": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "review it": (
            "ready-review",
            "show whether the current local goal is ready for review",
        ),
        "accept it": (
            "can-accept",
            "show whether accepting the current local goal is appropriate",
        ),
        "can you accept": (
            "can-accept",
            "show whether accepting the current local goal is appropriate",
        ),
        "is it safe to accept": (
            "can-accept",
            "show whether accepting the current local goal is appropriate",
        ),
        "/local-goal progress": (
            "progress",
            "show compact local harness progress report",
        ),
        "/local_goal progress": (
            "progress",
            "show compact local harness progress report",
        ),
        "/node1-goal progress": (
            "progress",
            "show compact local harness progress report",
        ),
        "/node1_goal progress": (
            "progress",
            "show compact local harness progress report",
        ),
        "/local-goal next-proof": (
            "next-proof",
            "show the next proof needed to harden unattended harness trust",
        ),
        "/local_goal next-proof": (
            "next-proof",
            "show the next proof needed to harden unattended harness trust",
        ),
        "/node1-goal next-proof": (
            "next-proof",
            "show the next proof needed to harden unattended harness trust",
        ),
        "/node1_goal next-proof": (
            "next-proof",
            "show the next proof needed to harden unattended harness trust",
        ),
        "/local-goal completion-summary": (
            "completion-summary",
            "show compact local harness completion proof summary",
        ),
        "/local_goal completion-summary": (
            "completion-summary",
            "show compact local harness completion proof summary",
        ),
        "/node1-goal completion-summary": (
            "completion-summary",
            "show compact local harness completion proof summary",
        ),
        "/node1_goal completion-summary": (
            "completion-summary",
            "show compact local harness completion proof summary",
        ),
        "/local-goal completion-audit": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "/local_goal completion-audit": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "/node1-goal completion-audit": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "/node1_goal completion-audit": (
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        ),
        "/local-goal audit-health": (
            "audit-health",
            "show local-goal integration audit lock and artifact health",
        ),
        "/local_goal audit-health": (
            "audit-health",
            "show local-goal integration audit lock and artifact health",
        ),
        "/node1-goal audit-health": (
            "audit-health",
            "show local-goal integration audit lock and artifact health",
        ),
        "/node1_goal audit-health": (
            "audit-health",
            "show local-goal integration audit lock and artifact health",
        ),
        "/local-goal audit-lock": (
            "audit-health",
            "show local-goal integration audit lock and artifact health",
        ),
        "/node1_goal audit-lock": (
            "audit-health",
            "show local-goal integration audit lock and artifact health",
        ),
        "/local-goal soak-plan": (
            "soak-plan",
            "show the exact optional unattended soak proof commands without starting work",
        ),
        "/local_goal soak-plan": (
            "soak-plan",
            "show the exact optional unattended soak proof commands without starting work",
        ),
        "/node1-goal soak-plan": (
            "soak-plan",
            "show the exact optional unattended soak proof commands without starting work",
        ),
        "/node1_goal soak-plan": (
            "soak-plan",
            "show the exact optional unattended soak proof commands without starting work",
        ),
        "/local-goal estimate": (
            "completion-summary",
            "show evidence-backed autonomy grade from completion summary",
        ),
        "/local_goal estimate": (
            "completion-summary",
            "show evidence-backed autonomy grade from completion summary",
        ),
        "/node1-goal estimate": (
            "completion-summary",
            "show evidence-backed autonomy grade from completion summary",
        ),
        "/node1_goal estimate": (
            "completion-summary",
            "show evidence-backed autonomy grade from completion summary",
        ),
        "/local-goal help": (
            "shortcuts",
            "show grouped safe local-goal help and shortcuts",
        ),
        "/local_goal help": (
            "shortcuts",
            "show grouped safe local-goal help and shortcuts",
        ),
        "/node1-goal help": (
            "shortcuts",
            "show grouped safe local-goal help and shortcuts",
        ),
        "/node1_goal help": (
            "shortcuts",
            "show grouped safe local-goal help and shortcuts",
        ),
        "/local-goal shortcuts": (
            "shortcuts",
            "show grouped safe local-goal help and shortcuts",
        ),
        "/local_goal shortcuts": (
            "shortcuts",
            "show grouped safe local-goal help and shortcuts",
        ),
        "/node1-goal cheatsheet": (
            "shortcuts",
            "show grouped safe local-goal help and shortcuts",
        ),
        "/node1_goal cheatsheet": (
            "shortcuts",
            "show grouped safe local-goal help and shortcuts",
        ),
        "model-promotion-plan": (
            "model-promotion-plan",
            "show the read-only durable Ornith promotion plan",
        ),
        "/local-goal model-promotion-plan": (
            "model-promotion-plan",
            "show the read-only durable Ornith promotion plan",
        ),
        "/local_goal model-promotion-plan": (
            "model-promotion-plan",
            "show the read-only durable Ornith promotion plan",
        ),
        "/node1-goal model-promotion-plan": (
            "model-promotion-plan",
            "show the read-only durable Ornith promotion plan",
        ),
        "/node1_goal model-promotion-plan": (
            "model-promotion-plan",
            "show the read-only durable Ornith promotion plan",
        ),
        "ornith-promotion-plan": (
            "model-promotion-plan",
            "show the read-only durable Ornith promotion plan",
        ),
        "model-promotion-apply": (
            "model-promotion-apply",
            "show the guarded durable Ornith promotion apply preview",
        ),
        "/local-goal model-promotion-apply": (
            "model-promotion-apply",
            "show the guarded durable Ornith promotion apply preview",
        ),
        "/local_goal model-promotion-apply": (
            "model-promotion-apply",
            "show the guarded durable Ornith promotion apply preview",
        ),
        "/node1-goal model-promotion-apply": (
            "model-promotion-apply",
            "show the guarded durable Ornith promotion apply preview",
        ),
        "/node1_goal model-promotion-apply": (
            "model-promotion-apply",
            "show the guarded durable Ornith promotion apply preview",
        ),
        "ornith-promotion-apply": (
            "model-promotion-apply",
            "show the guarded durable Ornith promotion apply preview",
        ),
        "model-promotion-verify": (
            "model-promotion-verify",
            "verify current Ornith promotion durability without mutating services",
        ),
        "/local-goal model-promotion-verify": (
            "model-promotion-verify",
            "verify current Ornith promotion durability without mutating services",
        ),
        "/local_goal model-promotion-verify": (
            "model-promotion-verify",
            "verify current Ornith promotion durability without mutating services",
        ),
        "/node1-goal model-promotion-verify": (
            "model-promotion-verify",
            "verify current Ornith promotion durability without mutating services",
        ),
        "/node1_goal model-promotion-verify": (
            "model-promotion-verify",
            "verify current Ornith promotion durability without mutating services",
        ),
        "ornith-promotion-verify": (
            "model-promotion-verify",
            "verify current Ornith promotion durability without mutating services",
        ),
        "is it permanent yet": (
            "model-promotion-verify",
            "verify current Ornith promotion durability without mutating services",
        ),
        "is it durable yet": (
            "model-promotion-verify",
            "verify current Ornith promotion durability without mutating services",
        ),
        "is the default model permanent": (
            "model-promotion-verify",
            "verify current Ornith promotion durability without mutating services",
        ),
        "is the default model permanent?": (
            "model-promotion-verify",
            "verify current Ornith promotion durability without mutating services",
        ),
        "did promotion work": (
            "model-promotion-verify",
            "verify current Ornith promotion durability without mutating services",
        ),
        "model-promotion-decision": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "/local-goal model-promotion-decision": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "/local_goal model-promotion-decision": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "/node1-goal model-promotion-decision": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "/node1_goal model-promotion-decision": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "ornith-promotion-decision": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "what is the open choice": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "what is the open choice?": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "what does the open choice mean": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "what does the open choice mean?": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "what is the operator choice": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "what is the operator choice?": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "what does operator choice mean": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "what does operator choice mean?": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "make it permanent": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "make it durable": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "make this permanent": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "make this durable": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "make ornith permanent": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "make ornith durable": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "make ornith default": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "make ornith the default": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "make ornith the default model": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "default ornith now": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "make that the default model": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "make the new model default": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "set ornith as default": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "use ornith permanently": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "should we switch to ornith?": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "should we switch to ornith": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "are you team ornith now": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "are we team ornith now": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "is ornith the right model now": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "should we use ornith for the harness": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "should we keep using ornith": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "is ornith temporary or permanent": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "keep ornith as the harness model": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "should we make ornith the harness model": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "should we make ornith the harness model?": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "promote it": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "can we promote it": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "can i promote it": (
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        ),
        "model-promotion-waiver": (
            "model-promotion-waiver",
            "show the read-only Ornith operator waiver for the unreliable Qwopus baseline",
        ),
        "/local-goal model-promotion-waiver": (
            "model-promotion-waiver",
            "show the read-only Ornith operator waiver for the unreliable Qwopus baseline",
        ),
        "/local_goal model-promotion-waiver": (
            "model-promotion-waiver",
            "show the read-only Ornith operator waiver for the unreliable Qwopus baseline",
        ),
        "/node1-goal model-promotion-waiver": (
            "model-promotion-waiver",
            "show the read-only Ornith operator waiver for the unreliable Qwopus baseline",
        ),
        "/node1_goal model-promotion-waiver": (
            "model-promotion-waiver",
            "show the read-only Ornith operator waiver for the unreliable Qwopus baseline",
        ),
        "ornith-waiver": (
            "model-promotion-waiver",
            "show the read-only Ornith operator waiver for the unreliable Qwopus baseline",
        ),
        "use it as much as you want": (
            "model-promotion-waiver",
            "show the read-only Ornith operator waiver for the unreliable Qwopus baseline",
        ),
        "good use it as much as you want": (
            "model-promotion-waiver",
            "show the read-only Ornith operator waiver for the unreliable Qwopus baseline",
        ),
        "continue developing with it as much as you want": (
            "model-promotion-waiver",
            "show the read-only Ornith operator waiver for the unreliable Qwopus baseline",
        ),
        "restore ornith": (
            "model-service-window-restore",
            "preview the guarded Ornith restore after the Qwopus service window",
        ),
        "go back to ornith": (
            "model-service-window-restore",
            "preview the guarded Ornith restore after the Qwopus service window",
        ),
        "switch to qwopus": (
            "model-service-window-open",
            "preview the guarded Qwopus service-window opener without executing it",
        ),
        "switch to qwopus now": (
            "model-service-window-open",
            "preview the guarded Qwopus service-window opener without executing it",
        ),
        "is qwopus running": (
            "model-status",
            "show the active local-goal model and promotion gate",
        ),
        "did qwopus switch happen": (
            "model-status",
            "show the active local-goal model and promotion gate",
        ),
        "did it switch to qwopus": (
            "model-status",
            "show the active local-goal model and promotion gate",
        ),
        "is the harness working as intended": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "is the harness working as intended?": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "what do you think of the harness now": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "what do you think of the harness now?": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "is the agentic harness working": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
        "is the agentic harness working?": (
            "brief",
            "show the shortest phone-readable local goal answer",
        ),
    }
    direct_wrapper_key = (
        alias_text if alias_text in direct_wrapper_aliases else alias_key
    )
    if direct_wrapper_key in direct_wrapper_aliases:
        intent, reason = direct_wrapper_aliases[direct_wrapper_key]
        if (
            intent in {"free", "can-start", "stuck", "ready-review", "can-accept"}
            and not is_slash_local_goal_command
        ):
            return [str(WRAPPER), intent, message.strip(), "--json"], intent, reason
        return [str(WRAPPER), intent, "--json"], intent, reason

    default_mode_goal = extract_harness_mode_goal(message, "default")
    if default_mode_goal:
        return (
            base
            + [
                "premium-start",
                "--planner",
                "glm-5.2",
                "--executor",
                "opencode",
                "--goal",
                default_mode_goal,
            ],
            "harness-mode-default-start",
            "start Mode 1 default harness: GLM-5.2 planner/supervisor with OpenCode local executor",
        )

    codex_saving_goal = extract_harness_mode_goal(message, "codex_saving")
    if codex_saving_goal:
        return (
            base
            + [
                "premium-start",
                "--planner",
                "glm-5.2",
                "--executor",
                "opencode",
                "--goal",
                codex_saving_goal,
            ],
            "harness-mode-codex-saving-start",
            "start Mode 2 Codex-saving harness: GLM-5.2 supervises while Codex spot-checks",
        )

    cloud_canary_goal = extract_harness_mode_goal(message, "cloud_canary")
    if cloud_canary_goal:
        return (
            base
            + [
                "enqueue",
                "--planner",
                "glm-5.2",
                "--executor",
                "opencode",
                "--executor-worker",
                "opencode-glm-build",
                "--goal",
                cloud_canary_goal,
            ],
            "harness-mode-cloud-canary",
            "start Mode 3 bounded cloud canary with GLM-5.2 planner and opencode-glm-build executor",
        )

    if (
        "mode 4b" in text
        or "4b" in text
        and "glm" in text
        and "implementation canary" in text
        or "glm direct implementation canary" in text
        or "direct glm implementation canary" in text
    ):
        return (
            [
                str(WRAPPER),
                "adapter-canary-plan",
                "--executor-worker",
                "glm52-direct-implementation-canary",
                "--json",
            ],
            "harness-mode-glm-direct-implementation-canary-plan",
            "show Mode 4B direct GLM-5.2 one-file implementation canary plan",
        )

    if (
        "fully local glm executor canary" in text
        or "fully local glm canary" in text
        or "direct glm audit/proposal lane" in text
        or "direct glm audit lane" in text
        or "mode 4" in text
    ) and ("ready" in text or "check" in text or "plan" in text or "canary" in text):
        return (
            [
                str(WRAPPER),
                "adapter-canary-plan",
                "--executor-worker",
                "glm52-direct",
                "--json",
            ],
            "harness-mode-glm-local-canary-plan",
            "show Mode 4 GLM-5.2 direct-worker readiness; implementation is contract-blocked unless a future canary changes that",
        )

    if _has_harness_modes_intent(text):
        return (
            [str(WRAPPER), "harness-modes", "--json"],
            "harness-modes",
            "show the Hermes gateway harness modes, including Mode 4B, and recommended default",
        )

    if _has_harness_teaching_intent(text):
        return (
            [str(WRAPPER), "guide"],
            "guide",
            "teach phone-friendly local-goal harness usage and next commands",
        )

    exact_trust_boundary_aliases = {
        "can i leave it",
        "can i leave this",
        "can i leave it running",
        "can i walk away",
        "can i just let it work",
        "can i let it keep working",
        "can i let you keep working",
        "can i let the harness keep working",
        "should i let it keep working",
        "should i let you keep working",
        "should i let the harness keep working",
        "do i need to keep checking it",
        "will it ask me if it needs help",
        "will it keep going by itself",
        "if you say say so then want me to let you keep working",
        "if you say say so, then want me to let you keep working",
        "if you say so then want me to let you keep working",
        "if you say so then i want to let you keep working",
        "if you say so, then i want to let you keep working",
        "do i need to babysit",
    }
    trust_boundary_key = (
        alias_text if alias_text in exact_trust_boundary_aliases else alias_key
    )
    if trust_boundary_key in exact_trust_boundary_aliases:
        return (
            base + ["doctor"],
            "trust-boundary",
            "show whether the local-goal watcher can run without babysitting",
        )

    exact_supervise_aliases = {
        "do it",
        "yes do it",
        "ok do it",
        "okay do it",
        "sure do it",
        "go ahead",
        "approved",
        "work on it",
        "make it happen",
    }
    supervise_key = alias_text if alias_text in exact_supervise_aliases else alias_key
    if supervise_key in exact_supervise_aliases:
        return (
            base + ["supervise", "--json"],
            "supervise",
            "actively supervise local goal with review, continue, dispatch, and owned-change commit gates",
        )

    if (
        re.search(
            r"\b(?:start|run|queue|launch)\s+(?:a\s+)?(?:local\s+)?goal\s*:",
            text,
        )
        and not any(word in text for word in ("cloud", "premium", "planner"))
        and not any(
            word in text
            for word in ("mission", "umbrella", "handoff", "offload", "delegate")
        )
    ):
        goal = extract_goal_text(message)
        if goal:
            return (
                base + ["start", "--executor", "opencode", "--goal", goal],
                "start",
                "start direct local goal",
            )
        return _doctor_command(base)

    # Mission mode: one umbrella goal that derives/runs subgoals over time.
    mission_keywords = any(
        keyword in text
        for keyword in (
            "mission",
            "umbrella",
            "handoff",
            "offload",
            "delegate",
        )
    )

    # Explicit /goal handoff from another model/session should become mission mode.
    if mission_keywords and "/goal" in text:
        planner = choose_planner(text)
        cmd = base + [
            "mission-create",
            "--planner",
            planner,
            "--executor",
            "opencode",
            "--max-subgoals",
            "12",
        ]
        if goal_file:
            cmd.extend(["--goal-file", goal_file])
        else:
            goal = extract_goal_text(message)
            if not goal:
                return (
                    base + ["mission-show", "--json"],
                    "mission-show",
                    "show mission state",
                )
            cmd.extend(["--goal", goal])
        done_criteria = extract_done_criteria(message)
        if done_criteria:
            cmd.extend(["--done-criteria", done_criteria])
        if "dry run" in text or "dry-run" in text:
            cmd.append("--dry-run")
        return (
            cmd,
            "mission-create",
            "create local mission mode umbrella goal",
        )

    if mission_keywords:
        if _has_local_goal_action_question_intent(text):
            return (
                base + ["mission-show", "--json"],
                "mission-show",
                "show mission state",
            )
        if "stop" in text:
            return base + ["mission-stop"], "mission-stop", "stop active mission"
        if "resume" in text or "continue" in text:
            return (
                base + ["mission-resume"],
                "mission-resume",
                "resume stopped mission",
            )
        if "monitor" in text or "check in" in text or "check-in" in text:
            return base + ["mission-monitor"], "mission-monitor", "monitor mission"
        if (
            "show" in text
            or "status" in text
            or "state" in text
            or "what is" in text
            or "how is" in text
        ):
            return (
                base + ["mission-show", "--json"],
                "mission-show",
                "show mission state",
            )
        if "create" in text or "start" in text or "transfer" in text or "/goal" in text:
            planner = choose_planner(text)
            cmd = base + [
                "mission-create",
                "--planner",
                planner,
                "--executor",
                "opencode",
            ]
            if goal_file:
                cmd.extend(["--goal-file", goal_file])
            else:
                goal = extract_goal_text(message)
                if not goal:
                    mission_match = re.search(
                        r"(?:mission|umbrella)(?:\s+goal)?\s*:\s*(.+)$",
                        message,
                        flags=re.IGNORECASE | re.DOTALL,
                    )
                    if mission_match:
                        goal = mission_match.group(1).strip()
                if goal:
                    cmd.extend(["--goal", goal])
            done_criteria = extract_done_criteria(message)
            if done_criteria:
                cmd.extend(["--done-criteria", done_criteria])
            if "dry run" in text or "dry-run" in text:
                cmd.append("--dry-run")
            return cmd, "mission-create", "create local mission mode umbrella goal"

    if (
        "brief" in text
        or "short answer" in text
        or "short version" in text
        or "quick answer" in text
        or "one line" in text
        or "tl;dr" in text
        or "tldr" in text
    ) and ("local" in text or "goal" in text or "harness" in text or "node1" in text):
        return (
            base + ["brief"],
            "brief",
            "show the shortest phone-readable local goal answer",
        )

    if _has_lane_help_question_intent(text):
        return (
            [str(WRAPPER), "shortcuts", "--json"],
            "shortcuts",
            "show grouped safe local-goal help and shortcuts",
        )

    if ("doctor" in text or "triage" in text) and (
        "local" in text or "goal" in text or "harness" in text or "node1" in text
    ):
        return (
            base + ["doctor"],
            "doctor",
            "show phone-friendly local goal status, mission, and lane summary",
        )

    if _has_soak_plan_intent(text):
        return (
            [str(WRAPPER), "soak-plan", "--json"],
            "soak-plan",
            "show the exact optional unattended soak proof commands without starting work",
        )

    if _has_completion_summary_intent(text):
        return (
            [str(WRAPPER), "completion-summary", "--json"],
            "completion-summary",
            "show compact local harness completion proof summary",
        )

    if _has_completion_audit_intent(text):
        return (
            [str(WRAPPER), "completion-audit", "--json"],
            "completion-audit",
            "show requirement-by-requirement local harness completion evidence",
        )

    if has_local_goal_audit_health_intent(text) and (
        is_slash_local_goal_command or not looks_like_ops_audit_report_context(text)
    ):
        return (
            [str(WRAPPER), "audit-health", "--json"],
            "audit-health",
            "show local-goal integration audit lock and artifact health",
        )

    if _has_next_proof_intent(text):
        return (
            [str(WRAPPER), "next-proof", "--json"],
            "next-proof",
            "show the next proof needed to harden unattended harness trust",
        )

    if _has_accepted_evidence_readback_intent(text):
        return (
            [str(WRAPPER), "last-run", "--json"],
            "last-run",
            "show latest accepted local-goal evidence and changed-file summary",
        )

    if _has_dirty_acceptance_question_intent(text):
        return (
            [str(WRAPPER), "current-truth", "--json"],
            "current-truth",
            "show dirty-worktree acceptance boundary from current truth",
        )

    if "readiness audit" in text and (
        "local" in text or "goal" in text or "harness" in text or "node1" in text
    ):
        return (
            ["python3", str(MANAGER), "readiness-audit", "--json"],
            "readiness-audit",
            "show local harness readiness audit",
        )

    if (
        "percentage" in text
        or "percent" in text
        or "how complete" in text
        or "completion estimate" in text
        or "readiness estimate" in text
    ) and ("local" in text or "goal" in text or "harness" in text or "node1" in text):
        return (
            [str(WRAPPER), "completion-summary", "--json"],
            "completion-summary",
            "show evidence-backed autonomy grade from completion summary",
        )

    if _has_progress_report_intent(text):
        return (
            [str(WRAPPER), "progress", "--json"],
            "progress",
            "show compact local harness progress report",
        )

    if "qwopus" in text and (
        "did qwopus switch" in text
        or "did it switch to qwopus" in text
        or "did the qwopus service window" in text
        or "did hermes execute the qwopus service window" in text
        or "did the qwopus window" in text
        or "did the qwopus cutover" in text
        or "did anything change on node1 for qwopus" in text
        or "is node1 on qwopus" in text
        or "is qwopus running" in text
        or "are we on qwopus" in text
    ):
        return (
            [str(WRAPPER), "model-status", "--json"],
            "model-status",
            "show the active local-goal model and promotion gate",
        )

    if "qwopus" in text and (
        "switch to" in text
        or "switch it to" in text
        or "what do i paste to switch" in text
        or "what command do i paste to switch" in text
        or "put node1 on qwopus" in text
        or "make qwopus active" in text
        or "set qwopus as default" in text
    ):
        return (
            [str(WRAPPER), "model-service-window-open", "--json"],
            "model-service-window-open",
            "preview the guarded Qwopus service-window opener without executing it",
        )

    if (
        "ornith" in text
        and (
            "did the ornith restore" in text
            or "did the restore" in text
            or "did restore" in text
            or "did rollback" in text
            or "did it restore ornith" in text
            or "did it go back to ornith" in text
            or "is node1 back on ornith" in text
            or "are we back on ornith" in text
            or "is the harness using ornith" in text
        )
    ) or (
        "did the ornith restore" in text
        or "did the restore" in text
        or "did restore" in text
        or "did rollback" in text
    ):
        return (
            [str(WRAPPER), "model-status", "--json"],
            "model-status",
            "show the active local-goal model and promotion gate",
        )

    if "ornith" in text and (
        text in {"restore ornith", "go back to ornith"}
        or (
            (
                "restore" in text
                or "restoring" in text
                or "rollback" in text
                or "roll back" in text
            )
            and ("approve" in text or "approved" in text or "execute" in text)
        )
        or "what do i paste to restore" in text
        or "what command do i paste to restore" in text
    ):
        return (
            [str(WRAPPER), "model-service-window-restore", "--json"],
            "model-service-window-restore",
            "preview the guarded Ornith restore after the Qwopus service window",
        )

    if "ornith" in text and "running" in text:
        return (
            [str(WRAPPER), "model-status", "--json"],
            "model-status",
            "show the active local-goal model and promotion gate",
        )

    if "qwopus" in text and (
        "active now" in text
        or "active after" in text
        or "anything change" in text
        or "mutate node1" in text
    ):
        if "mutate" in text or "anything change" in text:
            return (
                [str(WRAPPER), "model-service-window-open", "--json"],
                "model-service-window-open",
                "preview the guarded Qwopus service-window opener without executing it",
            )
        return (
            [str(WRAPPER), "model-status", "--json"],
            "model-status",
            "show the active local-goal model and promotion gate",
        )

    if "restore" in text and (
        "execute" in text
        or "mutate" in text
        or "already" in text
        or "restored now" in text
    ):
        if "already" in text or "restored now" in text:
            return (
                [str(WRAPPER), "model-status", "--json"],
                "model-status",
                "show the active local-goal model and promotion gate",
            )
        return (
            [str(WRAPPER), "model-service-window-restore", "--json"],
            "model-service-window-restore",
            "preview the guarded Ornith restore after the Qwopus service window",
        )

    if (
        (
            "restore" in text
            or "restoring" in text
            or "rollback" in text
            or "roll back" in text
            or "cancel" in text
            or "abort" in text
            or "close" in text
            or "undo" in text
            or "go back" in text
            or ("stop" in text and "qwopus" in text)
        )
        and ("ornith" in text or "qwopus" in text)
        and (
            "qwopus" in text
            or "window" in text
            or "baseline" in text
            or "canary" in text
            or "paste" in text
            or "terminal command" in text
        )
    ):
        return (
            [str(WRAPPER), "model-service-window-restore", "--json"],
            "model-service-window-restore",
            "preview the guarded Ornith restore after the Qwopus service window",
        )

    if (
        "ornith" in text
        and (
            "despite" in text
            or "waive" in text
            or "waiver" in text
            or "skip" in text
            or "use it as much as" in text
            or "use ornith as much as" in text
            or "as much as" in text
            or "problems" in text
            or "unreliable" in text
        )
        and (
            "use it" in text
            or "use ornith" in text
            or "as much as" in text
            or "despite" in text
            or "waive" in text
            or "waiver" in text
            or "skip" in text
            or "problems" in text
            or "unreliable" in text
        )
    ):
        return (
            [str(WRAPPER), "model-promotion-waiver", "--json"],
            "model-promotion-waiver",
            "show the read-only Ornith operator waiver for the unreliable Qwopus baseline",
        )

    if (
        "qwopus" in text
        and ("window" in text or "service window" in text or "cutover window" in text)
        and (
            "can i open" in text
            or "can we open" in text
            or "safe" in text
            or "check" in text
            or "preflight" in text
            or "before baseline" in text
        )
    ):
        return (
            [str(WRAPPER), "model-service-window-check", "--json"],
            "model-service-window-check",
            "check whether the Qwopus service window can be opened safely",
        )

    if (
        "ornith" in text
        and (
            "what do i type" in text
            or "what should i type" in text
            or "exact command" in text
            or "terminal command" in text
            or "what command" in text
            or "paste" in text
        )
        and (
            "permanent" in text
            or "permanently" in text
            or "durable" in text
            or "promote" in text
            or "promotion" in text
        )
    ):
        return (
            [str(WRAPPER), "model-promotion-decision", "--json"],
            "model-promotion-decision",
            "show the read-only Ornith promotion decision with preview and terminal-only approval labels",
        )

    if _has_ornith_promotion_plan_intent(text):
        return (
            [str(WRAPPER), "model-promotion-plan", "--json"],
            "model-promotion-plan",
            "show the read-only durable Ornith promotion plan",
        )

    if (
        "ornith" in text
        and (
            "approve" in text
            or "approved" in text
            or "execute" in text
            or "apply" in text
        )
        and (
            "promote" in text
            or "promotion" in text
            or "permanent" in text
            or "durable" in text
            or "default" in text
            or "harness model" in text
        )
    ):
        return (
            [str(WRAPPER), "model-promotion-apply", "--json"],
            "model-promotion-apply",
            "show the guarded durable Ornith promotion apply preview",
        )

    if (
        "open choice" in text
        or "operator choice" in text
        or (
            "ornith" in text
            and ("temporary" in text or "canary" in text)
            and (
                "what" in text
                or "why" in text
                or "mean" in text
                or "leave" in text
                or "keep" in text
                or "decide later" in text
                or "defer" in text
                or "hold" in text
                or "not promote" in text
            )
        )
        or (
            ("temporary candidate" in text or "temporary canary" in text)
            and ("what" in text or "why" in text or "mean" in text)
        )
    ):
        return (
            [str(WRAPPER), "model-promotion-decision", "--json"],
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        )

    if (
        "ornith" in text
        and (
            "permanent yet" in text
            or "permanent now" in text
            or "durable yet" in text
            or "durable now" in text
            or "promotion work" in text
            or "promotion worked" in text
            or "promotion verify" in text
            or "verify promotion" in text
            or "verify durable" in text
            or "still temporary" in text
            or "not durable" in text
            or "is it permanent" in text
            or "is it durable" in text
            or "did it promote" in text
            or "did we promote" in text
            or "did promotion" in text
        )
        and not (
            "what do i type" in text or "what should i type" in text or "plan" in text
        )
    ):
        return (
            [str(WRAPPER), "model-promotion-verify", "--json"],
            "model-promotion-verify",
            "verify current Ornith promotion durability without mutating services",
        )

    if (
        "ornith" in text
        and (
            "promote" in text
            or "promoting" in text
            or "promotion" in text
            or "permanent" in text
            or "permanently" in text
            or "durable" in text
            or "evidence" in text
            or "proof" in text
            or "missing" in text
            or "need" in text
            or "blocked" in text
            or "blocks" in text
            or "decide later" in text
            or "defer" in text
            or "hold" in text
            or "hold off" in text
            or "not promote" in text
            or re.search(r"\bwin\b", text)
            or "won" in text
            or "beat" in text
            or "beaten" in text
        )
        and not ("packet" in text or "bundle" in text)
    ):
        return (
            [str(WRAPPER), "model-promotion-decision", "--json"],
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        )

    if _has_pronoun_model_quality_intent(text) or text in {
        "make it durable",
        "make it durable?",
        "make this durable",
        "make this durable?",
        "make it permanent",
        "make it permanent?",
        "make this permanent",
        "make this permanent?",
        "promote it",
        "promote it?",
        "can we promote it",
        "can we promote it?",
        "can i promote it",
        "can i promote it?",
        "is it permanent yet",
        "is it permanent yet?",
        "is it durable yet",
        "is it durable yet?",
        "did promotion work",
        "did promotion work?",
    }:
        if (
            "permanent yet" in text
            or "durable yet" in text
            or "promotion work" in text
            or "promotion worked" in text
        ):
            return (
                [str(WRAPPER), "model-promotion-verify", "--json"],
                "model-promotion-verify",
                "verify current Ornith promotion durability without mutating services",
            )
        return (
            [str(WRAPPER), "model-promotion-decision", "--json"],
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        )

    if "qwopus" in text and (
        "baseline" in text
        or "nontrivial" in text
        or "completion" in text
        or "completions" in text
        or "complete harness" in text
        or "complete local" in text
        or "baseline evidence" in text
        or "completion evidence" in text
        or "completion reliability" in text
        or "safe to use" in text
        or "use for the harness" in text
        or "handle 192k" in text
        or "support 192k" in text
        or "192k" in text
        or "seq4" in text
        or "seq=4" in text
        or "max-num-seqs" in text
        or re.search(r"\bprove\b", text)
        or re.search(r"\bproves\b", text)
        or "proven" in text
    ):
        if (
            "what is" in text
            or "what do i type" in text
            or "what should i type" in text
            or "what command" in text
            or "plan" in text
            or "recipe" in text
        ):
            return (
                [str(WRAPPER), "model-nontrivial-baseline-plan", "--json"],
                "model-nontrivial-baseline-plan",
                "show read-only Qwopus nontrivial completion-baseline plan",
            )
        if (
            "start" in text
            or "can i run" in text
            or "can we run" in text
            or "should i run" in text
            or "should we run" in text
            or ("run" in text and "dry run" not in text)
            or "kick off" in text
            or "begin" in text
            or "launch" in text
            or "queue" in text
            or "ready" in text
            or "check" in text
        ):
            return (
                [str(WRAPPER), "model-nontrivial-baseline-check", "--json"],
                "model-nontrivial-baseline-check",
                "check whether Qwopus nontrivial completion baseline can start now",
            )
        return (
            [str(WRAPPER), "model-completion-risk-check", "--json"],
            "model-completion-risk-check",
            "explain the Qwopus completion risk and current baseline gate",
        )

    model_decision_or_eval_wording = (
        "eval" in text
        or "evaluation" in text
        or "promote" in text
        or "promoting" in text
        or "promotion" in text
        or "harness model" in text
        or "evidence" in text
        or "proof" in text
        or "missing" in text
        or "need" in text
        or "blocked" in text
        or "blocks" in text
        or re.search(r"\bwin\b", text)
        or "won" in text
        or "beat" in text
        or "beaten" in text
    )

    if (
        "model" in text
        and (
            "active" in text
            or "current" in text
            or "using" in text
            or "status" in text
            or "which" in text
            or "running" in text
            or "on the new model" in text
            or "on ornith" in text
            or "on qwopus" in text
            or "switch happen" in text
            or "switch happened" in text
            or "switch to" in text
            or (
                "default" in text
                and ("what" in text or "which" in text or text.startswith("is "))
            )
            or "swap" in text
            or "change" in text
            or "canary" in text
            or "ready" in text
            or "doing" in text
        )
        and not model_decision_or_eval_wording
    ) or (
        ("ornith" in text or "qwopus" in text)
        and (
            "active" in text
            or "current" in text
            or "using" in text
            or "status" in text
            or "running" in text
            or "on the new model" in text
            or "on ornith" in text
            or "on qwopus" in text
            or "switch happen" in text
            or "switch happened" in text
            or "switch to" in text
            or (
                "default" in text
                and ("what" in text or "which" in text or text.startswith("is "))
            )
            or "canary" in text
            or "ready" in text
            or "doing" in text
        )
        and not model_decision_or_eval_wording
    ):
        return (
            [str(WRAPPER), "model-status", "--json"],
            "model-status",
            "show the active local-goal model and promotion gate",
        )

    generic_model_quality = (
        "model" in text
        and not any(
            word in text
            for word in (
                "window",
                "service",
                "cutover",
                "open",
                "restore",
                "rollback",
                "packet",
                "bundle",
            )
        )
        and any(
            phrase in text
            for phrase in (
                "good",
                "trust",
                "better",
                "best",
                "use",
                "replace",
                "switch",
                "promote",
                "promotion",
                "permanent",
                "permanently",
                "default",
                "durable",
            )
        )
    )
    if generic_model_quality:
        return (
            [str(WRAPPER), "model-promotion-decision", "--json"],
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        )

    if _has_pronoun_model_quality_intent(text):
        return (
            [str(WRAPPER), "model-promotion-decision", "--json"],
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        )

    if (
        (
            ("a/b" in text or "ab test" in text or "a b test" in text)
            and not any(
                word in text
                for word in (
                    "window",
                    "service",
                    "cutover",
                    "open",
                    "restore",
                    "rollback",
                    "packet",
                    "bundle",
                )
            )
        )
        or "model decision" in text
        and any(
            phrase in text
            for phrase in (
                "next",
                "what now",
                "what should i do",
                "what should we do",
                "what do i do",
                "what do we do",
                "step",
            )
        )
        and not any(word in text for word in ("packet", "bundle"))
    ):
        return (
            [str(WRAPPER), "model-eval-next", "--json"],
            "model-eval-next",
            "show the next safe Ornith/Qwopus evaluation step",
        )

    if (
        "model" in text
        and not any(
            word in text
            for word in (
                "window",
                "service",
                "cutover",
                "open",
                "restore",
                "rollback",
                "packet",
                "bundle",
                "decision",
            )
        )
        and (
            "next" in text
            or "what now" in text
            or "what should i do" in text
            or "what should we do" in text
            or "what do i do" in text
            or "what do we do" in text
            or "what do i type" in text
            or "what should i type" in text
            or "step" in text
            or "eval" in text
            or "test" in text
            or "canary" in text
        )
    ) or (
        ("ornith" in text or "qwopus" in text)
        and not any(
            word in text
            for word in (
                "window",
                "service",
                "cutover",
                "open",
                "restore",
                "rollback",
                "packet",
                "bundle",
                "decision",
            )
        )
        and (
            "next" in text
            or "what now" in text
            or "what should i do" in text
            or "what should we do" in text
            or "what do i do" in text
            or "what do we do" in text
            or "what do i type" in text
            or "what should i type" in text
            or "step" in text
            or "eval" in text
            or "test" in text
            or "canary" in text
        )
    ):
        if "what do i type" in text or "what should i type" in text:
            return (
                [str(WRAPPER), "model-nontrivial-baseline-plan", "--json"],
                "model-nontrivial-baseline-plan",
                "show read-only Qwopus nontrivial completion-baseline plan",
            )
        return (
            [str(WRAPPER), "model-eval-next", "--json"],
            "model-eval-next",
            "show the next safe Ornith/Qwopus evaluation step",
        )

    if "qwopus" in text and (
        "baseline" in text
        or "completion" in text
        or "completions" in text
        or "nontrivial" in text
        or "model" in text
        or "eval" in text
        or "promotion" in text
        or "ornith" in text
        or "next" in text
        or "window" in text
        or "cutover" in text
        or "execute" in text
        or "run" in text
        or "start" in text
        or "active" in text
        or "default" in text
        or "put" in text
        or "cancel" in text
        or "abort" in text
        or "close" in text
        or "undo" in text
        or "roll back" in text
        or "rollback" in text
        or "go back" in text
        or "go ahead" in text
        or "approve" in text
        or "approved" in text
        or "approval" in text
        or "fail" in text
        or "failed" in text
        or "timeout" in text
        or "timeouts" in text
        or "wrong" in text
        or "problem" in text
        or "issue" in text
        or "worry" in text
        or "worries" in text
        or "evidence" in text
        or re.search(r"\bprove\b", text)
        or re.search(r"\bproves\b", text)
        or "proven" in text
        or "reliability" in text
        or "packet" in text
        or "bundle" in text
        or "decision" in text
        or "good" in text
        or "trust" in text
        or "working" in text
    ):
        service_window_packet = any(
            phrase in text
            for phrase in (
                "window",
                "cutover",
                "open",
                "service",
                "switch",
                "switching",
                "active",
                "default",
                "restore",
                "rollback",
            )
        )
        if (
            service_window_packet
            and not text.startswith(("is ", "are ", "did ", "does ", "was ", "were "))
            and (
                "switch" in text
                or "switching" in text
                or "active" in text
                or "default" in text
                or "put" in text
            )
        ):
            return (
                [str(WRAPPER), "model-service-window-open", "--json"],
                "model-service-window-open",
                "preview the guarded Qwopus service-window opener without executing it",
            )
        if (
            "bundle" in text
            or "decision packet" in text
            or "model decision" in text
            or ("packet" in text and not service_window_packet)
        ):
            return (
                [str(WRAPPER), "model-decision-packet", "--json"],
                "model-decision-packet",
                "write the read-only Qwopus model decision packet bundle",
            )
        if service_window_packet and (
            "approve" in text
            or "approved" in text
            or "approval" in text
            or "execute" in text
            or "mutate" in text
            or "anything change" in text
            or "go ahead" in text
        ):
            return (
                [str(WRAPPER), "model-service-window-open", "--json"],
                "model-service-window-open",
                "preview the guarded Qwopus service-window opener without executing it",
            )
        if (
            "what do i type" in text
            or "what should i type" in text
            or "what command" in text
        ) and (
            "completion" in text
            or "completions" in text
            or "baseline" in text
            or "evidence" in text
            or re.search(r"\bprove\b", text)
            or re.search(r"\bproves\b", text)
            or "proven" in text
            or "reliability" in text
        ):
            return (
                [str(WRAPPER), "model-nontrivial-baseline-plan", "--json"],
                "model-nontrivial-baseline-plan",
                "show read-only Qwopus nontrivial completion-baseline plan",
            )
        if (
            "risk" in text
            or "problem" in text
            or "issue" in text
            or "worry" in text
            or "worries" in text
            or "wrong" in text
            or "evidence" in text
            or re.search(r"\bprove\b", text)
            or re.search(r"\bproves\b", text)
            or "failure" in text
            or "fail" in text
            or "failed" in text
            or "fixed" in text
            or "resolved" in text
            or "solve" in text
            or "solved" in text
            or "reliability" in text
            or "proven" in text
            or "stall" in text
            or "stalled" in text
            or "timeout" in text
            or "timeouts" in text
            or "0 byte" in text
            or "zero byte" in text
        ):
            return (
                [str(WRAPPER), "model-completion-risk-check", "--json"],
                "model-completion-risk-check",
                "explain the Qwopus completion risk and current baseline gate",
            )
        if (
            "promote" in text
            or "promotion" in text
            or "better" in text
            or "replace" in text
            or "switch" in text
            or "use" in text
            or "good" in text
            or "trust" in text
            or "working" in text
        ) and not ("packet" in text or "bundle" in text):
            if "ornith" not in text and (
                "trust" in text or "good" in text or "working" in text
            ):
                return (
                    [str(WRAPPER), "model-completion-risk-check", "--json"],
                    "model-completion-risk-check",
                    "explain the Qwopus completion risk and current baseline gate",
                )
            return (
                [str(WRAPPER), "model-promotion-decision", "--json"],
                "model-promotion-decision",
                "show the read-only Ornith/Qwopus promotion decision",
            )
        if "next" in text and (
            "window" in text or "service" in text or "cutover" in text
        ):
            return (
                [str(WRAPPER), "model-service-window-next", "--json"],
                "model-service-window-next",
                "show the next safe Qwopus service-window command",
            )
        if (
            "restore" in text
            or "rollback" in text
            or "roll back" in text
            or "cancel" in text
            or "abort" in text
            or "close" in text
            or "undo" in text
            or "go back" in text
            or "stop" in text
        ) and ("ornith" in text or "canary" in text or "window" in text):
            return (
                [str(WRAPPER), "model-service-window-restore", "--json"],
                "model-service-window-restore",
                "preview the guarded Ornith restore after the Qwopus service window",
            )
        if (
            "window" in text or "cutover" in text or "open" in text or "service" in text
        ) and (
            "preview" in text
            or "guarded" in text
            or "approve" in text
            or "approved" in text
            or "approval" in text
            or "terminal command" in text
            or "what command" in text
            or "exact execute command" in text
            or "paste" in text
            or "show open" in text
            or "show the open" in text
            or "what would happen" in text
        ):
            return (
                [str(WRAPPER), "model-service-window-open", "--json"],
                "model-service-window-open",
                "preview the guarded Qwopus service-window opener without executing it",
            )
        if (
            "window" in text or "cutover" in text or "open" in text or "service" in text
        ) and not ("what do i type" in text or "what should i type" in text):
            return (
                [str(WRAPPER), "model-service-window-check", "--json"],
                "model-service-window-check",
                "check whether the Qwopus service window can be opened safely",
            )
        if (
            "plan" in text
            or "recipe" in text
            or "how" in text
            or "what do i type" in text
            or "what should i type" in text
        ):
            return (
                [str(WRAPPER), "model-nontrivial-baseline-plan", "--json"],
                "model-nontrivial-baseline-plan",
                "show read-only Qwopus nontrivial completion-baseline plan",
            )
        if (
            "next" in text
            or "what now" in text
            or "where are we" in text
            or "status" in text
            or "eval" in text
            or "promotion" in text
            or "ornith" in text
            or "better" in text
        ):
            return (
                [str(WRAPPER), "model-eval-next", "--json"],
                "model-eval-next",
                "show the next safe Ornith/Qwopus evaluation step",
            )
        return (
            [str(WRAPPER), "model-nontrivial-baseline-check", "--json"],
            "model-nontrivial-baseline-check",
            "check whether Qwopus nontrivial completion baseline can start now",
        )

    if (
        "ornith" in text
        and (
            "promote" in text
            or "promoting" in text
            or "promotion" in text
            or "better" in text
            or "replace" in text
            or "switch" in text
            or "use" in text
            or "good" in text
            or "trust" in text
            or "working" in text
            or "model decision" in text
            or "evidence" in text
            or "proof" in text
            or "missing" in text
            or "need" in text
            or "blocked" in text
            or "blocks" in text
            or re.search(r"\bwin\b", text)
            or "won" in text
            or "beat" in text
            or "beaten" in text
        )
        and not ("packet" in text or "bundle" in text)
    ):
        return (
            [str(WRAPPER), "model-promotion-decision", "--json"],
            "model-promotion-decision",
            "show the read-only Ornith/Qwopus promotion decision",
        )

    # ---- Explicit cloud-executor routing ---------------------------------
    # Catch short cloud phrasings ('cloud option', 'cloud executor',
    # 'non-codex cloud', 'use the cloud lane') BEFORE the generic
    # capabilities/help/status blocks swallow them.  This fixes the
    # misroute where 'cloud option' landed on status/doctor instead of the
    # cloud executor lane.
    #
    # Skip this block when the phrase contains the old cloud-block keywords
    # ('start', 'transfer', 'enqueue', 'local goal', '/goal') so the existing
    # cloud-start path at the later generic cloud detection keeps its proven
    # planner/worker defaults and backward-compatible behavior.
    _old_cloud_keywords = (
        "start" in text
        or "transfer" in text
        or "enqueue" in text
        or "local goal" in text
        or "/goal" in text
    )
    if _has_explicit_cloud_executor_intent(text) and not _old_cloud_keywords:
        goal = extract_goal_text(message)
        if goal:
            worker = choose_cloud_worker(text)
            planner = (
                choose_planner(text)
                if ("planner" in text or "premium" in text)
                else "glm-5.2"
            )
            cmd = base + [
                "enqueue",
                "--planner",
                planner,
                "--executor",
                "opencode",
                "--executor-worker",
                worker,
                "--goal",
                goal,
            ]
            return (
                cmd,
                "enqueue-cloud",
                f"enqueue explicit cloud executor local-goal lane with {worker}",
            )
        # No goal attached: show the shortcuts card so the operator sees the
        # cloud lane, its availability note, and its exact start command.
        return (
            [str(WRAPPER), "shortcuts", "--json"],
            "shortcuts",
            "show grouped safe local-goal help and shortcuts (cloud executor lane)",
        )

    if (
        "capabilities" in text
        or "capability" in text
        or "lanes" in text
        or "options" in text
        or "what can" in text
        or "help" in text
    ):
        return (
            base + ["capabilities", "--json"],
            "capabilities",
            "show local goal lane capabilities",
        )

    exact_harness_next_command = exact_key in {
        "do next",
        "do the next",
        "do the next step",
        "do the next thing",
        "keep going",
        "carry on",
        "move forward",
    }
    if _has_trust_boundary_intent(text) and any(
        phrase in text
        for phrase in (
            "let you keep working",
            "let it work",
            "keep checking it",
            "keep going by itself",
            "will it ask me",
            "will it notify me",
            "will it tell me",
        )
    ):
        return (
            base + ["doctor"],
            "trust-boundary",
            "show whether the local-goal watcher can run without babysitting",
        )

    if _has_trust_boundary_intent(text) and (
        "can i " in text
        or "should i " in text
        or "can we " in text
        or "should we " in text
        or "?" in text
    ):
        return (
            base + ["doctor"],
            "trust-boundary",
            "show whether the local-goal watcher can run without babysitting",
        )

    if (
        local_harness_mentioned or "harness" in text or exact_harness_next_command
    ) and _has_harness_next_work_intent(text):
        return (
            base + ["supervise", "--json"],
            "supervise",
            "actively supervise local goal with review, continue, dispatch, and owned-change commit gates",
        )

    if _has_trust_boundary_intent(text) and (
        "local" in text
        or "goal" in text
        or "harness" in text
        or "node1" in text
        or local_harness_mentioned
        or "hermes" in text
    ):
        return (
            base + ["doctor"],
            "trust-boundary",
            "show whether the local-goal watcher can run without babysitting",
        )

    exact_local_next_question = exact_key in {
        "what should i do next",
        "what should we do next",
        "what do i do next",
        "what do we do next",
        "what now",
        "now what",
        "what else",
        "what do i type",
        "what should i type",
    }

    if has_external_review_intent:
        reviewer = choose_planner(text)
        if reviewer == "none":
            reviewer = "glm-5.2"
        return (
            base + ["external-review", "--reviewer", reviewer, "--json"],
            "external-review",
            f"run advisory external supervisor review with {reviewer}",
        )

    if _has_lane_help_question_intent(text):
        return (
            [str(WRAPPER), "shortcuts", "--json"],
            "shortcuts",
            "show grouped safe local-goal help and shortcuts",
        )

    if (
        ("node1" in text or "vllm" in text or "gpu" in text or "gpus" in text)
        and (
            "busy" in text
            or "free" in text
            or "idle" in text
            or "available" in text
            or "other activity" in text
            or "what is" in text
            or "what's" in text
            or "whats" in text
        )
        and "current truth" not in text
        and "current-truth" not in text
        and "truth report" not in text
    ):
        return (
            [str(WRAPPER), "free", "--json"],
            "free",
            "show whether the local-goal lane is free",
        )

    next_or_type_question = (
        "what next" in text
        or "what's next" in text
        or "whats next" in text
        or "what should i do next" in text
        or "what should we do next" in text
        or "what do i do next" in text
        or "what do we do next" in text
        or "next step" in text
        or "what now" in text
        or "now what" in text
        or "what else" in text
        or "what do i do now" in text
        or "what should we do now" in text
        or "what do i type" in text
        or "what should i type" in text
        or "what command" in text
    )
    if next_or_type_question and (
        "local" in text
        or "goal" in text
        or "harness" in text
        or "node1" in text
        or local_harness_mentioned
        or exact_local_next_question
    ):
        return (
            base + ["brief"],
            "brief",
            "show the shortest phone-readable local goal answer",
        )

    if (
        "what next" in text
        or "what's next" in text
        or "whats next" in text
        or "what should i do next" in text
        or "what should we do next" in text
        or "what do we do next" in text
        or "next step" in text
        or "what now" in text
        or "now what" in text
        or "what else" in text
        or "what do i do now" in text
        or "what should we do now" in text
        or "what do i type" in text
        or "what should i type" in text
        or "what command" in text
        or "can i start" in text
        or "can we start" in text
        or "should i start" in text
        or "should we start" in text
        or "is node1 free" in text
        or "is node1 busy" in text
        or "is node1 available" in text
        or "local goal lane free" in text
        or "local-goal lane free" in text
        or "babysit" in text
        or "trust" in text
        or "leave it alone" in text
        or "leave the harness" in text
        or "leave the local goal" in text
        or "leave node1" in text
        or "running overnight" in text
        or "walk away" in text
        or "without babysitting" in text
        or "will hermes tell me" in text
        or "will hermes notify me" in text
        or "notify me if" in text
        or "tell me if it needs me" in text
        or "ask me for approval" in text
        or "working as intended" in text
        or "is it working" in text
        or "is the harness working" in text
        or "is the agentic harness working" in text
        or "what do you think" in text
    ) and (
        "local" in text
        or "goal" in text
        or "harness" in text
        or "node1" in text
        or local_harness_mentioned
        or (
            "hermes" in text
            and (
                "tell me if it needs me" in text
                or "notify me if" in text
                or "ask me for approval" in text
            )
        )
        or exact_local_next_question
    ):
        return (
            base + ["doctor"],
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        )

    if local_harness_mentioned and _has_local_goal_action_question_intent(text):
        return (
            base + ["doctor"],
            "doctor",
            "show local goal status, mission, lanes, and recommended next action",
        )

    if has_external_review_intent:
        reviewer = choose_planner(text)
        if reviewer == "none":
            reviewer = "glm-5.2"
        return (
            base + ["external-review", "--reviewer", reviewer, "--json"],
            "external-review",
            f"run advisory external supervisor review with {reviewer}",
        )

    if ("glm" in text or "glim" in text) and (
        "do your job" in text
        or "take over" in text
        or "handoff" in text
        or "hand off" in text
        or "delegate" in text
        or "replace codex" in text
        or "codex usage" in text
        or "work on the harness" in text
        or "work on agentic harness" in text
    ):
        return (
            [str(WRAPPER), "glm-handoff-plan", "--json"],
            "glm-handoff-plan",
            "show the safe GLM-5.2 handoff plan for one bounded harness task",
        )

    if _looks_like_implicit_long_mission(text):
        planner = choose_planner(text)
        goal = extract_implicit_mission_goal(message)
        cmd = base + [
            "mission-create",
            "--planner",
            planner,
            "--executor",
            "opencode",
            "--max-subgoals",
            "12",
        ]
        if goal:
            cmd.extend(["--goal", goal])
        if "dry run" in text or "dry-run" in text:
            cmd.append("--dry-run")
        return (
            cmd,
            "mission-create",
            "create local mission mode umbrella goal from long-running plain-language request",
        )

    if (
        "nudge" in text
        or "steer" in text
        or "guide next iteration" in text
        or "next iteration" in text
        and "feedback" in text
    ) and (
        "local" in text or "goal" in text or "node1" in text or local_harness_mentioned
    ):
        feedback = extract_nudge_text(message) or extract_goal_text(message)
        cmd = base + ["nudge"]
        if feedback:
            cmd.extend(["--goal", feedback])
        return (
            cmd,
            "nudge",
            "write one-shot guidance for the next local-goal loop iteration",
        )

    if _has_supervise_intent(text):
        return (
            base + ["supervise", "--json"],
            "supervise",
            "actively supervise local goal with review, continue, dispatch, and owned-change commit gates",
        )

    if "cloud" in text and (
        "start" in text
        or "transfer" in text
        or "enqueue" in text
        or "local goal" in text
        or "/goal" in text
    ):
        planner = (
            choose_planner(text) if ("planner" in text or "premium" in text) else "none"
        )
        worker = choose_cloud_worker(text)
        cmd = base + [
            "enqueue",
            "--planner",
            planner,
            "--executor",
            "opencode",
            "--executor-worker",
            worker,
        ]
        if goal_file:
            cmd.extend(["--goal-file", goal_file])
        else:
            goal = extract_goal_text(message)
            if goal:
                cmd.extend(["--goal", goal])
            else:
                return _doctor_command(base)
        return (
            cmd,
            "enqueue-cloud",
            f"enqueue cloud builder local-goal lane with {worker}",
        )

    # Premium/planner next — these are high-signal and can contain "start"/"goal"
    if (
        "premium" in text
        or "planner" in text
        or "gpt" in text
        or "glm" in text
        or "glim" in text
        or "kimi" in text
        or "thinkmax" in text
        or "deepseek" in text
    ):
        planner = choose_planner(text)
        cmd = base + ["premium-start", "--planner", planner, "--executor", "opencode"]
        if goal_file:
            cmd.extend(["--goal-file", goal_file])
        else:
            goal = extract_goal_text(message)
            if goal:
                cmd.extend(["--goal", goal])
            else:
                return _doctor_command(base)
        return (
            cmd,
            "premium-start",
            f"start premium planner/local builder goal with {planner}",
        )

    if "integration" in text and ("audit" in text or local_harness_mentioned):
        return (
            base + ["integration-audit", "--json"],
            "integration-audit",
            "audit Hermes local-goal integration",
        )

    if any(
        phrase in text
        for phrase in (
            "can i start",
            "can we start",
            "should i start",
            "should we start",
        )
    ) and (
        local_harness_mentioned
        or any(
            word in text
            for word in (
                "goal",
                "harness",
                "node1",
                "vllm",
                "gpu",
                "local",
                "queue",
                "busy",
                "free",
                "now",
            )
        )
    ):
        return (
            [str(WRAPPER), "can-start", "--json"],
            "can-start",
            "show whether the local-goal lane can start a new bounded goal",
        )

    # Stop must come before start. Slash-command args are often just "stop".
    if text == "stop" or (
        ("stop" in text or "pause" in text)
        and ("local" in text or "goal" in text or "node1" in text or "harness" in text)
    ):
        return base + ["stop"], "stop", "stop active local goal"

    # Continue / resume
    if "continue" in text or "resume" in text:
        goal = extract_goal_text(message)
        if not goal:
            return (
                base + ["supervise", "--json"],
                "supervise",
                "actively supervise local goal with review, continue, dispatch, and owned-change commit gates",
            )
        cmd = base + ["continue"]
        cmd.extend(["--goal", goal])
        return cmd, "continue", "continue active local goal"

    # Accept / review
    if text == "accept" or (
        "accept" in text and ("local" in text or "goal" in text or "node1" in text)
    ):
        return base + ["accept"], "accept", "accept completed local goal"
    if text == "review" or (
        "review" in text and ("local" in text or "goal" in text or "node1" in text)
    ):
        return base + ["review"], "review", "review completed local goal"

    plain_goal = extract_plain_programming_goal(message)
    if plain_goal:
        return (
            base + ["start", "--executor", "opencode", "--goal", plain_goal],
            "start",
            "start direct local goal from plain programming request",
        )

    # Monitor
    if "monitor" in text or "check in" in text or "check-in" in text:
        return base + ["monitor"], "monitor", "monitor local goal"

    if (
        "current truth" in text or "current-truth" in text or "truth report" in text
    ) and ("local" in text or "goal" in text or "harness" in text or "node1" in text):
        return (
            [str(WRAPPER), "current-truth", "--json"],
            "current-truth",
            "show local goal current-truth report",
        )

    # Status
    if (
        "status" in text
        or "how is" in text
        or "what is it doing" in text
        or (
            local_harness_mentioned
            and any(
                phrase in text
                for phrase in (
                    "does hermes know",
                    "what gives",
                    "what about",
                    "current truth",
                    "current-truth",
                    "aware",
                    "awareness",
                    "know about",
                    "running",
                )
            )
        )
    ):
        return base + ["status", "--json"], "status", "show local goal status"

    # Watch (monitor) with periodic updates
    if "watch" in text or "show me progress" in text:
        return base + ["monitor"], "monitor", "monitor local goal"

    # Log (after premium to avoid false matches on 'login')
    if _has_log_intent(text):
        return base + ["log"], "log", "show local goal log"

    # Queue inspection must beat the broad "local goal" start fallback.
    if "queue" in text and (
        "show" in text
        or "status" in text
        or "inspect" in text
        or "list" in text
        or "what" in text
        or local_harness_mentioned
    ):
        return (
            [str(WRAPPER), "queue-summary", "--json"],
            "queue-summary",
            "show compact local goal queue summary",
        )

    # Start / transfer
    if "start" in text or "transfer" in text or "local goal" in text or "/goal" in text:
        cmd = base + ["start", "--executor", "opencode"]
        if goal_file:
            cmd.extend(["--goal-file", goal_file])
        else:
            goal = extract_goal_text(message)
            if not goal:
                goal = extract_plain_programming_goal(message)
            if goal:
                cmd.extend(["--goal", goal])
            else:
                return _doctor_command(base)
        return cmd, "start", "start direct local goal"

    return base + ["status", "--json"], "status", "default to local goal status"


def run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(DOC_ROOT),
        text=True,
        capture_output=True,
        timeout=1200,
        check=False,
    )


def run_supervisor_json(
    args: list[str],
) -> tuple[dict | None, subprocess.CompletedProcess[str]]:
    cmd = ["python3", str(SUPERVISOR), *args]
    proc = run_command(cmd)
    return parse_supervisor_payload(proc.stdout), proc


def run_manager_json(
    args: list[str],
) -> tuple[dict | None, subprocess.CompletedProcess[str]]:
    cmd = ["python3", str(MANAGER), *args]
    proc = run_command(cmd)
    return parse_supervisor_payload(proc.stdout), proc


def parse_supervisor_payload(text: str) -> dict | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_artifact_paths(text: str) -> list[str]:
    """Return likely local artifact paths mentioned in command output."""
    seen: set[str] = set()
    paths: list[str] = []
    for match in re.finditer(
        r"/mnt/raid0/[^\s`'\"<>|)]+(?:\.md|\.json|\.log|\.txt)",
        text,
    ):
        value = match.group(0).rstrip(".,:;")
        if ".." in Path(value).parts:
            continue
        if value not in seen:
            seen.add(value)
            paths.append(value)
    return paths[:5]


def has_explicit_empty_start_intent(message: str) -> bool:
    """Return True when the operator explicitly asked to start with no goal."""
    stripped = message.strip()
    if not stripped:
        return False
    patterns = (
        r"^(?:/local-goal\s+)?start\s+local\s+goal\s*:?\s*$",
        r"^(?:/local-goal\s+)?transfer\s+/goal\s*:?\s*$",
        r"^/goal\s*:?\s*$",
    )
    return any(re.fullmatch(pattern, stripped, flags=re.IGNORECASE) for pattern in patterns)


def _artifact_kind(path: str) -> str:
    name = Path(path).name.lower()
    if name == "prompt.md":
        return "worker prompt"
    if name == "latest.md":
        return "latest summary"
    if name == "session.log":
        return "session transcript"
    if name == "complete.json":
        return "completion marker"
    if name.endswith(".md"):
        return "markdown artifact"
    if name.endswith(".json"):
        return "json artifact"
    if name.endswith(".log"):
        return "log artifact"
    if name.endswith(".txt"):
        return "text artifact"
    return "artifact"


def _artifact_kinds(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    kinds: list[str] = []
    for path in paths:
        kind = _artifact_kind(path)
        if kind not in seen:
            seen.add(kind)
            kinds.append(kind)
    return kinds


def chat_safe_command_tokens(cmd: list[str]) -> list[str]:
    """Return command tokens without absolute paths that chat may auto-attach."""
    safe: list[str] = []
    for token in cmd:
        if token.startswith("/"):
            name = Path(token).name
            safe.append(Path(name).stem if name.endswith(".py") else name)
        else:
            safe.append(token)
    return safe


def looks_like_chat_unsafe_artifact_output(text: str) -> bool:
    """Detect markdown/report payloads that should not be pasted into Telegram.

    The command shim is the human-facing boundary. Large markdown artifacts,
    planner packets, prompts, reports, logs, and generated inventories should
    stay on disk unless the operator explicitly asks for the contents.
    """
    stripped = text.strip()
    if not stripped:
        return False

    lower = stripped.lower()
    hard_markers = (
        "# canary productionization inventory",
        "# local node1 `/goal` current-state handoff",
        "# local node1 goal command",
        "# local goal",
        "## immediate queue",
        "## live status",
        "## output",
        "```text",
        "generated:",
        "generated: `",
        "| item | source | disposition | next action |",
        "reports/local-node1-goal-harness",
        "planner-packets/",
        "/prompt.md",
        "/latest.md",
        "/session.log",
        "/complete.json",
        "/handoff.md",
        "/report.md",
    )
    marker_count = sum(1 for marker in hard_markers if marker in lower)
    lines = stripped.splitlines()
    table_lines = sum(1 for line in lines if line.strip().startswith("|"))
    heading_lines = sum(1 for line in lines if line.lstrip().startswith("#"))
    fenced = "```" in stripped

    if marker_count >= 2:
        return True
    if len(stripped) > 2500 and (heading_lines >= 2 or table_lines >= 3 or fenced):
        return True
    if len(lines) > 40 and (heading_lines >= 1 or table_lines >= 3):
        return True
    return False


def summarize_chat_unsafe_artifact_output(
    text: str,
    *,
    intent: str,
    cmd: list[str],
) -> str:
    paths = _extract_artifact_paths(text)
    kinds = _artifact_kinds(paths)
    lines = [
        "Artifact output suppressed for chat.",
        "What happened: Hermes produced a markdown/report artifact, but chat now keeps those contents on disk instead of pasting them here.",
        "Does Michael need to do anything? No, unless the status says review or blocked.",
        "Next: ask for details only if you want the artifact contents.",
    ]
    if paths:
        lines.append(f"Artifact files kept on disk: {len(paths)}")
        if kinds:
            lines.append("Artifact kinds: " + ", ".join(kinds))
    else:
        lines.append("Artifact path: not shown by the command output.")
    lines.append(f"Intent: {intent}")
    if cmd:
        safe_cmd = chat_safe_command_tokens(cmd)
        lines.append(
            f"Command: {' '.join(safe_cmd[:4])}{' ...' if len(safe_cmd) > 4 else ''}"
        )
    return "\n".join(lines)


def chat_safe_command_output(text: str, *, intent: str, cmd: list[str]) -> str:
    """Return command output safe to expose to chat/document auto-attach layers."""
    if looks_like_chat_unsafe_artifact_output(text):
        return summarize_chat_unsafe_artifact_output(text, intent=intent, cmd=cmd)
    return text


def attach_current_truth_model_decision(payload: dict | None) -> dict | None:
    """Attach compact model-decision state to readiness summaries when available."""
    if not isinstance(payload, dict):
        return payload
    if payload.get("contract") != "local_node1_goal_harness_readiness.v1":
        return payload
    if isinstance(payload.get("model_promotion_decision"), dict):
        return payload
    proc = run_command([str(WRAPPER), "current-truth", "--json"])
    if proc.returncode != 0:
        return payload
    current_truth = parse_supervisor_payload(proc.stdout)
    if not isinstance(current_truth, dict):
        return payload
    decision = current_truth.get("model_promotion_decision")
    if not isinstance(decision, dict):
        return payload
    enriched = dict(payload)
    enriched["model_promotion_decision"] = {
        key: decision.get(key)
        for key in (
            "available",
            "status",
            "decision_required",
            "operator_can_choose_promotion",
            "terminal_approval_command",
        )
        if key in decision
    }
    return enriched


def _short_text(value: object, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _human_status_text(value: object) -> str:
    text = str(value or "")
    replacements = {
        "Node1 vLLM": "Node1 model server",
        "vLLM": "Node1 model server",
        "vllM": "Node1 model server",
        "vllm": "Node1 model server",
        "tmux": "background terminal",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _indent_block(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "  unavailable"
    return "\n".join(f"  {line}" for line in text.splitlines())


def _last_run_is_accepted_soak(last_run_payload: dict | None) -> bool:
    if not isinstance(last_run_payload, dict):
        return False
    owned = " ".join(
        str(item) for item in (last_run_payload.get("owned_files_sample") or [])
    )
    text = " ".join(
        str(last_run_payload.get(name) or "")
        for name in ("title", "summary", "run_dir", "review_status")
    )
    return (
        last_run_payload.get("available") is True
        and last_run_payload.get("review_status") == "accepted"
        and "soak" in f"{text} {owned}".lower()
    )


def _accepted_soak_evidence_present(
    last_run_payload: dict | None,
    completion_summary_payload: dict | None,
) -> bool:
    """Return whether any current proof surface has accepted soak evidence."""
    if _last_run_is_accepted_soak(last_run_payload):
        return True
    if not isinstance(completion_summary_payload, dict):
        return False
    soak_evidence = completion_summary_payload.get("soak_evidence")
    if isinstance(soak_evidence, dict) and soak_evidence.get("accepted") is True:
        return True
    autonomy_grade = completion_summary_payload.get("autonomy_grade")
    if (
        isinstance(autonomy_grade, dict)
        and autonomy_grade.get("unattended_soak_evidence") is True
    ):
        return True
    bounded_readiness = completion_summary_payload.get("bounded_local_cloud_readiness")
    return (
        isinstance(bounded_readiness, dict)
        and bounded_readiness.get("unattended_soak_evidence") is True
    )


def _model_operator_can_choose_promotion(model_decision: dict | None) -> bool:
    """Return true when the model packet exposes an explicit operator choice."""
    if not isinstance(model_decision, dict):
        return False
    return model_decision.get("status") == "ready-for-operator-decision" and (
        model_decision.get("operator_can_choose_promotion") is True
        or model_decision.get("decision_required") is True
    )


def _lane_summary(capabilities_payload: dict | None) -> str:
    if not isinstance(capabilities_payload, dict):
        return "  unavailable"
    lanes = capabilities_payload.get("lanes")
    if not isinstance(lanes, dict):
        lanes = (capabilities_payload.get("capabilities") or {}).get("lanes")
    if not isinstance(lanes, dict):
        return "  unavailable"
    lines: list[str] = []
    for name in ("local", "premium_planner_local_builder", "cloud_executor"):
        lane = lanes.get(name)
        if not isinstance(lane, dict):
            continue
        available = lane.get("available_now")
        reason = (
            lane.get("availability_reason")
            or lane.get("unavailable_reason")
            or "unknown"
        )
        classification = lane.get("classification") or "unknown"
        lines.append(
            f"  {name}: {classification}, available_now={available}, reason={reason}"
        )
    return "\n".join(lines) if lines else "  unavailable"


def _supervision_summary(capabilities_payload: dict | None) -> str:
    if not isinstance(capabilities_payload, dict):
        return "  unavailable"
    supervision = capabilities_payload.get("supervision")
    if not isinstance(supervision, dict):
        return "  unavailable"
    watcher = supervision.get("watcher")
    if not isinstance(watcher, dict):
        return "  watcher: unknown"
    state = watcher.get("state") or "unknown"
    summary = watcher.get("summary") or ""
    timer_active = watcher.get("timer_active")
    service_ok = watcher.get("service_ok")
    return "\n".join(
        [
            f"  watcher: {state} (timer_active={timer_active}, service_ok={service_ok})",
            f"  {_short_text(summary, 220)}",
        ]
    )


def _summary_field(summary: str, name: str) -> str:
    prefix = f"{name}:"
    for line in summary.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return ""


def _node1_has_other_vllm_activity(status_payload: dict | None) -> bool:
    if not isinstance(status_payload, dict):
        return False
    capabilities = status_payload.get("capabilities")
    if isinstance(capabilities, dict) and isinstance(
        capabilities.get("current_state"), dict
    ):
        current_state = capabilities["current_state"]
        if (
            current_state.get("local_goal_lane_free") is True
            and current_state.get("node1_vllm_has_other_activity") is True
        ):
            return True
    runtime = status_payload.get("runtime")
    vllm = runtime.get("vllm") if isinstance(runtime, dict) else {}
    if not isinstance(vllm, dict):
        return False
    try:
        running = float(vllm.get("running") or 0)
        waiting = float(vllm.get("waiting") or 0)
    except (TypeError, ValueError):
        running = 0.0
        waiting = 0.0
    return status_payload.get("node1_is_idle") is True and running > 0 and waiting <= 0


def doctor_decision_summary(
    status_payload: dict | None,
    mission_payload: dict | None,
    current_truth_payload: dict | None = None,
) -> list[str]:
    """Return the first-screen decision for phone/Hermes operators."""
    decision_state = doctor_decision_state(
        status_payload, mission_payload, current_truth_payload
    )
    decision_lines = decision_state.get("decision_lines")
    if not isinstance(decision_lines, list):
        decision_lines = []
    return [
        "Operator decision:",
        f"  {decision_state['operator_decision']}",
        f"  Type: {decision_state['operator_command']}",
        *decision_lines,
    ]


def doctor_decision_state(
    status_payload: dict | None,
    mission_payload: dict | None,
    current_truth_payload: dict | None = None,
) -> dict:
    """Return the structured operator decision behind doctor output."""
    status_summary = human_supervisor_summary(status_payload)
    mission_summary = human_supervisor_summary(mission_payload)
    state = _summary_field(status_summary, "Status") or "unknown"
    next_action = _summary_field(status_summary, "Next")
    mission_state = _summary_field(mission_summary, "Status") or "unknown"
    model_decision = {}
    if isinstance(current_truth_payload, dict) and isinstance(
        current_truth_payload.get("model_promotion_decision"), dict
    ):
        model_decision = current_truth_payload["model_promotion_decision"]
    model_ready_for_choice = _model_operator_can_choose_promotion(model_decision)

    decision_lines = []
    lane_free_but_vllm_busy = _node1_has_other_vllm_activity(status_payload)

    if state in {"working", "repairing", "reviewing"}:
        decision = "Wait. The local-goal supervisor should keep working; do not start another Node1 goal."
        command = "/local-goal supervise local harness"
    elif state == "blocked":
        decision = "Intervention needed. Ask Hermes to supervise so it can try the bounded repair path."
        command = "/local-goal supervise local harness"
    elif (
        state in {"accepted", "ready", "complete"}
        and mission_state == "complete"
        and model_ready_for_choice
    ):
        if lane_free_but_vllm_busy:
            decision = "Local-goal lane is free, but Node1 GPUs are busy with other vLLM activity. You can start one bounded goal, but it may wait. Ornith durability is a separate operator choice."
        else:
            decision = "Ready for one bounded local goal. Ornith durability is a separate operator choice."
        command = "/local-goal start local goal: <bounded task>"
        phone_preview = model_decision.get("phone_safe_preview") or (
            "Use scripts/local-goal model-promotion-apply or "
            "/local-goal model-promotion-apply to inspect the approval packet; "
            "this does not mutate services."
        )
        terminal_only = model_decision.get("terminal_only_mutation") or (
            "Only a terminal command with --execute --confirm PROMOTE_ORNITH_PERMANENT "
            "makes Ornith durable."
        )
        decision_lines.extend(
            [
                "  Model choice: /local-goal model-promotion-decision",
                f"  Phone-safe preview: {phone_preview}",
                f"  Terminal-only mutation: {terminal_only}",
            ]
        )
        model_choice_command = "/local-goal model-promotion-decision"
    elif (
        state in {"accepted", "ready", "complete"}
        and mission_state == "complete"
        and lane_free_but_vllm_busy
    ):
        decision = "Local-goal lane is free, but Node1 GPUs are busy with other vLLM activity. You can start one bounded goal, but it may wait."
        command = "/local-goal start local goal: <bounded task>"
    elif state in {"accepted", "ready", "complete"} and mission_state == "complete":
        decision = "Local-goal lane is free. Start one new bounded local goal when you are ready."
        command = "/local-goal start local goal: <bounded task>"
    elif state in {"accepted", "ready", "complete"}:
        decision = "Local-goal lane is free, but the mission is not complete. Check or monitor the mission before starting unrelated work."
        command = "/local-goal monitor mission"
    else:
        decision = next_action or "Check status again before starting work."
        command = "/local-goal status local goal"

    result = {
        "operator_decision": decision,
        "operator_command": command,
        "next_action": decision,
        "decision_lines": decision_lines,
    }
    if command.startswith("/local-goal start local goal:"):
        result["start_command"] = command
    if model_ready_for_choice:
        result["model_choice_command"] = locals().get(
            "model_choice_command", "/local-goal model-promotion-decision"
        )
        result["phone_safe_preview"] = model_decision.get("phone_safe_preview")
        result["terminal_only_mutation"] = model_decision.get("terminal_only_mutation")
    return result


def model_durability_summary(current_truth_payload: dict | None) -> list[str]:
    """Return compact model durability lines for phone/operator views."""
    if not isinstance(current_truth_payload, dict):
        return ["Model durability:", "  unavailable"]
    model_status = current_truth_payload.get("model_status")
    model_decision = current_truth_payload.get("model_promotion_decision")
    if not isinstance(model_decision, dict):
        model_decision = {}
    if not isinstance(model_status, dict):
        if not model_decision:
            return ["Model durability:", "  unavailable"]
        model_status = {}
    durability = model_status.get("durability")
    if not isinstance(durability, dict):
        durability = {}
    promotion_gate = model_status.get("promotion_gate")
    if not isinstance(promotion_gate, dict):
        promotion_gate = {}
    ready_for_operator_choice = _model_operator_can_choose_promotion(model_decision)
    gate_status = promotion_gate.get("status")
    status = durability.get("status")
    if not status and ready_for_operator_choice:
        status = "operator_decision_required"
    if not status and gate_status:
        status = f"promotion_gate:{gate_status}"
    lines = [
        "Model durability:",
        f"  Status: {status or 'unknown'}",
    ]
    reason = durability.get("reason")
    if reason:
        lines.append(f"  Reason: {reason}")
    next_command = durability.get("next_command")
    if next_command:
        lines.append(f"  Next: {next_command}")
    if gate_status:
        lines.append(f"  Promotion gate: {gate_status}")
    terminal_command = model_decision.get("terminal_approval_command")
    if ready_for_operator_choice:
        phone_preview = model_decision.get("phone_safe_preview") or (
            "Use scripts/local-goal model-promotion-apply or "
            "/local-goal model-promotion-apply to inspect the approval packet; "
            "this does not mutate services."
        )
        terminal_only = model_decision.get("terminal_only_mutation") or (
            "Only a terminal command with --execute --confirm PROMOTE_ORNITH_PERMANENT "
            "makes Ornith durable."
        )
        lines.append(f"  Phone-safe preview: {phone_preview}")
        lines.append(f"  Terminal-only mutation: {terminal_only}")
    if ready_for_operator_choice and terminal_command:
        lines.append(f"  Terminal-only promotion: {terminal_command}")
    return lines


def model_operator_action_lines(model_decision: dict | None) -> list[str]:
    """Return the explicit human action block for pending model choices."""
    if not isinstance(model_decision, dict):
        model_decision = {}
    ready_for_operator_choice = _model_operator_can_choose_promotion(model_decision)

    meaning = model_decision.get("promotion_allowed_meaning") or (
        "false means the harness will not promote automatically; it does not mean Ornith failed the A/B gate."
    )
    preview = (
        model_decision.get("approval_preview_command")
        or "scripts/local-goal model-promotion-apply"
    )
    terminal = model_decision.get("terminal_approval_command")
    lines = [
        "Operator actions open:",
        "  - ornith_durable_promotion_decision: Choose whether to make Ornith durable",
        f"    Meaning: {meaning}",
        f"    Preview: {preview} (Phone-safe preview; does not mutate services.)",
    ]
    if not ready_for_operator_choice:
        lines.append(
            "    Status: optional decision path visible; durable promotion remains blocked until evidence gates pass."
        )
    if terminal:
        lines.append(f"    Terminal-only mutation: {terminal}")
    return lines


def doctor_summary(
    status_payload: dict | None,
    mission_payload: dict | None,
    capabilities_payload: dict | None,
    last_run_payload: dict | None = None,
    current_truth_payload: dict | None = None,
    completion_summary_payload: dict | None = None,
) -> str:
    """Build a phone-friendly operator summary for Hermes group chat."""
    status_summary = human_supervisor_summary(status_payload)
    mission_summary = human_supervisor_summary(mission_payload)
    last_soak = _accepted_soak_evidence_present(
        last_run_payload, completion_summary_payload
    )
    proof_line = (
        "  Accepted soak evidence is present in the latest accepted run."
        if last_soak
        else "  Accepted soak evidence is not the latest accepted run; check next-proof for remaining evidence."
    )
    lines = [
        "Local Goal Doctor",
        "",
        *doctor_decision_summary(
            status_payload, mission_payload, current_truth_payload
        ),
        "",
        "Current status:",
        _indent_block(status_summary),
        "",
        "Current mission:",
        _indent_block(mission_summary),
        "",
        "Current lanes:",
        _lane_summary(capabilities_payload),
        "",
        *model_durability_summary(current_truth_payload),
        "",
        *model_operator_action_lines(
            current_truth_payload.get("model_promotion_decision")
            if isinstance(current_truth_payload, dict)
            else None
        ),
        "",
        "Supervision:",
        _supervision_summary(capabilities_payload),
        "",
        "Trust boundary:",
        proof_line,
        "  You do not need to babysit routine watcher cycles when watcher is active.",
        "  Usable for bounded local goals; broad unattended autonomy is not claimed.",
        "  Still review accepted evidence before trusting product-sensitive changes.",
        "",
        "Safe read-only / preview commands:",
        "  /local-goal harness modes",
        "  /local-goal progress",
        "  /local-goal show local goal queue",
        "  /local-goal next-proof",
        "  /local-goal model-eval-next",
        "  /local-goal model-promotion-decision",
        "  /local-goal model-promotion-apply",
        "  /local-goal supervise local harness",
        "  /local-goal show mission",
        "  /local-goal help",
        "",
        "Start commands (starts work):",
        "  /local-goal start local goal: <bounded task>",
    ]
    return "\n".join(lines)


def brief_summary(
    status_payload: dict | None,
    mission_payload: dict | None,
    capabilities_payload: dict | None,
    last_run_payload: dict | None = None,
    current_truth_payload: dict | None = None,
    completion_summary_payload: dict | None = None,
) -> str:
    """Build the shortest practical phone answer for "what now?" checks."""
    status_summary = human_supervisor_summary(status_payload)
    mission_summary = human_supervisor_summary(mission_payload)
    structured_state = ""
    if isinstance(status_payload, dict):
        structured_state = str(
            status_payload.get("classification") or status_payload.get("phase") or ""
        ).strip()
    state = structured_state or _summary_field(status_summary, "Status") or "unknown"
    next_action = (
        _summary_field(status_summary, "Next") or "Check status before starting work."
    )
    structured_mission_state = ""
    if isinstance(mission_payload, dict):
        structured_mission_state = str(
            mission_payload.get("status") or mission_payload.get("mission_status") or ""
        ).strip()
    mission_state = (
        structured_mission_state
        or _summary_field(mission_summary, "Status")
        or "unknown"
    )
    last_soak = _accepted_soak_evidence_present(
        last_run_payload, completion_summary_payload
    )
    current_state = {}
    if isinstance(status_payload, dict):
        capabilities = status_payload.get("capabilities")
        if isinstance(capabilities, dict) and isinstance(
            capabilities.get("current_state"), dict
        ):
            current_state = capabilities["current_state"]
        if not current_state:
            current_state = {
                "local_goal_lane_free": status_payload.get("local_goal_lane_free"),
                "node1_vllm_idle": status_payload.get("node1_vllm_idle"),
                "node1_vllm_has_other_activity": status_payload.get(
                    "node1_vllm_has_other_activity"
                ),
            }
    if not current_state and isinstance(capabilities_payload, dict):
        capabilities_state = capabilities_payload.get("current_state")
        if isinstance(capabilities_state, dict):
            current_state = capabilities_state
    lane_free_but_vllm_busy = (
        current_state.get("local_goal_lane_free") is True
        and current_state.get("node1_vllm_has_other_activity") is True
    )
    lane_free = current_state.get("local_goal_lane_free")
    node1_vllm_idle = current_state.get("node1_vllm_idle")
    node1_vllm_other_activity = current_state.get("node1_vllm_has_other_activity")
    vllm_runtime = {}
    if isinstance(status_payload, dict):
        runtime = status_payload.get("runtime")
        if isinstance(runtime, dict) and isinstance(runtime.get("vllm"), dict):
            vllm_runtime = runtime["vllm"]
    vllm_evidence = ""
    if vllm_runtime:
        vllm_evidence = " (running={} waiting={})".format(
            vllm_runtime.get("running") or 0,
            vllm_runtime.get("waiting") or 0,
        )

    watcher = {}
    if isinstance(capabilities_payload, dict):
        supervision = capabilities_payload.get("supervision")
        if isinstance(supervision, dict) and isinstance(
            supervision.get("watcher"), dict
        ):
            watcher = supervision["watcher"]
        elif isinstance(capabilities_payload.get("watcher"), dict):
            watcher = capabilities_payload["watcher"]
    watcher_active = bool(watcher.get("timer_active")) and bool(
        watcher.get("service_ok")
    )

    model_decision = {}
    if isinstance(current_truth_payload, dict) and isinstance(
        current_truth_payload.get("model_promotion_decision"), dict
    ):
        model_decision = current_truth_payload["model_promotion_decision"]
    model_ready_for_choice = _model_operator_can_choose_promotion(model_decision)

    if state in {"working", "repairing", "reviewing"}:
        verdict = "Working; do not start another Node1 goal."
        command = "/local-goal supervise local harness"
    elif state == "blocked":
        verdict = "Needs intervention."
        command = "/local-goal supervise local harness"
    elif state in {"accepted", "ready", "complete"} and mission_state == "complete":
        if model_ready_for_choice:
            verdict = "Ready for one bounded local goal. Ornith durability is optional."
            command = "/local-goal start local goal: <bounded task>"
        else:
            verdict = "Ready for one bounded local goal."
            command = "/local-goal start local goal: <bounded task>"
    else:
        verdict = next_action
        command = "/local-goal status local goal"

    phone_preview = model_decision.get("phone_safe_preview") or (
        "Use /local-goal model-promotion-apply to inspect the approval packet; "
        "this does not mutate services."
    )
    terminal_only = model_decision.get("terminal_only_mutation") or (
        "Only --execute --confirm PROMOTE_ORNITH_PERMANENT makes Ornith durable."
    )
    terminal_command = model_decision.get("terminal_approval_command") or (
        "scripts/local-goal model-promotion-apply --execute --confirm PROMOTE_ORNITH_PERMANENT"
    )
    model_lines = [
        "Open action: Ornith durability decision is optional.",
        "Model choice: /local-goal model-promotion-decision",
        f"Preview: {phone_preview}",
        f"Mutation: {terminal_only}",
        f"Terminal-only command: {terminal_command}",
    ]

    babysit = (
        "No active babysitting needed; watcher is active."
        if watcher_active
        else "Do not rely on unattended watching until the watcher is fixed."
    )
    proof = (
        "Accepted soak proof is present."
        if last_soak
        else "Soak proof is not current; use /local-goal next-proof."
    )
    wait_lines = []
    if lane_free_but_vllm_busy:
        wait_lines.append(
            "Wait: local-goal lane is free, but Node1 vLLM has separate capacity activity; a new run may queue."
        )
    queue_info_lines = []
    if isinstance(status_payload, dict):
        queue = status_payload.get("queue") or {}
        q_running = int(queue.get("running") or 0)
        q_queued = int(queue.get("queued") or 0)
        q_failed = int(queue.get("failed_to_start") or 0)
        q_failed_label = queue.get("failed_to_start_label") or ""
        if q_running > 0 or q_queued > 0:
            queue_info_lines.append(
                f"Queue: running={q_running} queued={q_queued} (active work)"
            )
        elif q_failed > 0:
            label = (
                "historical_residue"
                if q_failed_label == "historical_residue"
                else q_failed_label
            )
            queue_info_lines.append(
                f"Queue: clear; old failed start attempts={q_failed} ({label} — not active work)"
            )
        else:
            queue_info_lines.append("Queue: clear")
    else:
        queue_info_lines.append("Queue: unknown")
    lane_line = "Lane: unknown"
    if lane_free is True:
        lane_line = "Lane: free"
    elif lane_free is False:
        lane_line = "Lane: busy"
    node1_line = "Node1: vLLM unknown"
    if node1_vllm_idle is True:
        node1_line = f"Node1: vLLM idle{vllm_evidence}"
    elif node1_vllm_other_activity is True:
        node1_line = f"Node1: vLLM capacity busy; start may wait{vllm_evidence}"
    elif node1_vllm_idle is False:
        node1_line = f"Node1: vLLM not idle{vllm_evidence}"
    command_label = (
        "Start (starts work)"
        if "/local-goal start local goal:" in command
        else "Next command"
    )
    terminal_start_lines = [
        "Terminal start: scripts/local-goal quick-start --goal '<bounded task>'",
    ]
    if command_label != "Start (starts work)":
        terminal_start_lines.insert(
            0,
            "Start (starts work): /local-goal start local goal: <bounded task>",
        )
    return "\n".join(
        [
            "Local Goal Brief",
            f"State: {state}; mission={mission_state}",
            f"Answer: {verdict}",
            lane_line,
            node1_line,
            *wait_lines,
            *queue_info_lines,
            f"Babysit: {babysit}",
            f"Proof: {proof}",
            "Boundary: bounded local goals ready; broad autonomy not claimed.",
            f"{command_label}: {command}",
            *terminal_start_lines,
            *model_lines,
        ]
    )


def trust_boundary_summary(
    status_payload: dict | None,
    mission_payload: dict | None,
    capabilities_payload: dict | None,
    last_run_payload: dict | None = None,
    current_truth_payload: dict | None = None,
    completion_summary_payload: dict | None = None,
) -> str:
    """Build a direct phone answer for unattended/watcher trust questions."""
    status_summary = human_supervisor_summary(status_payload)
    mission_summary = human_supervisor_summary(mission_payload)
    state = _summary_field(status_summary, "Status") or "unknown"
    mission_state = _summary_field(mission_summary, "Status") or "unknown"
    last_soak = _accepted_soak_evidence_present(
        last_run_payload, completion_summary_payload
    )
    model_decision = {}
    if isinstance(current_truth_payload, dict) and isinstance(
        current_truth_payload.get("model_promotion_decision"), dict
    ):
        model_decision = current_truth_payload["model_promotion_decision"]
    model_ready_for_choice = _model_operator_can_choose_promotion(model_decision)
    watcher = {}
    if isinstance(capabilities_payload, dict):
        supervision = capabilities_payload.get("supervision")
        if isinstance(supervision, dict) and isinstance(
            supervision.get("watcher"), dict
        ):
            watcher = supervision["watcher"]
        elif isinstance(capabilities_payload.get("watcher"), dict):
            watcher = capabilities_payload["watcher"]
    watcher_active = bool(watcher.get("timer_active")) and bool(
        watcher.get("service_ok")
    )
    lane_free_but_vllm_busy = _node1_has_other_vllm_activity(status_payload)
    if state in {"working", "repairing", "reviewing"} and watcher_active:
        answer = "No. Leave it running; Hermes/local-goal watcher should keep supervising this run."
        operator_action = (
            "Check back only if Hermes reports review needed, blocked, or failed."
        )
    elif (
        state in {"accepted", "ready", "complete"}
        and mission_state == "complete"
        and model_ready_for_choice
    ):
        if lane_free_but_vllm_busy:
            answer = "No active local goal is running, so there is nothing to babysit right now. The lane is free, but Node1 vLLM has separate capacity activity; a new bounded goal may wait."
        else:
            answer = "No active local goal is running, so there is nothing to babysit right now. The lane is ready for the next bounded task."
        operator_action = "Start one explicit bounded goal when you want more work done; Ornith durability is a separate choice at /local-goal model-promotion-decision."
    elif state in {"accepted", "ready", "complete"} and mission_state == "complete":
        if lane_free_but_vllm_busy:
            answer = "No active local goal is running, so there is nothing to babysit right now. The lane is free, but Node1 vLLM has separate capacity activity; a new bounded goal may wait."
        else:
            answer = "No active local goal is running, so there is nothing to babysit right now. The lane is ready for the next bounded task."
        operator_action = (
            "Start one explicit bounded goal when you want more work done."
        )
    elif watcher_active:
        answer = "Mostly no. The watcher is active, but check the mission/status before assuming the next action."
        operator_action = "Use /local-goal supervise local harness if you want Hermes to make the next safe move."
    else:
        answer = "No. Do not rely on unattended operation until the watcher service/timer is active again."
        operator_action = (
            "Run /local-goal doctor and fix the watcher before walking away."
        )

    proof = (
        "Accepted soak evidence is present."
        if last_soak
        else "Latest accepted soak evidence is not current; use next-proof before trusting product-sensitive work."
    )
    hermes_will = (
        "monitor, continue, dispatch, and accept only when gates pass"
        if watcher_active
        else "not run unattended supervision until the watcher is healthy"
    )
    human_must = (
        "start bounded goals explicitly, choose the separate Ornith durability decision when ready, and review accepted evidence before product-sensitive trust"
        if model_ready_for_choice
        else "review accepted evidence before product-sensitive trust"
    )
    operator_action_lines = model_operator_action_lines(model_decision)
    if operator_action_lines:
        operator_action_lines = ["", *operator_action_lines]
    return "\n".join(
        [
            "Local Goal Trust Boundary",
            "",
            f"Answer: {answer}",
            f"Operator action: {operator_action}",
            "",
            "Who handles what:",
            f"  Hermes/watcher: {hermes_will}.",
            f"  Human/operator: {human_must}.",
            *operator_action_lines,
            "",
            "Current state:",
            _indent_block(status_summary),
            "",
            *model_durability_summary(current_truth_payload),
            "",
            "Supervision:",
            _supervision_summary(capabilities_payload),
            "",
            "Trust boundary:",
            f"  {proof}",
            "  Routine watcher cycles do not need babysitting when the watcher is active.",
            "  Usable for bounded local goals; broad unattended autonomy is not claimed.",
            "  Product-sensitive changes still need accepted evidence review.",
            "",
            "Safe commands:",
            "  /local-goal progress",
            "  /local-goal supervise local harness",
            "  /local-goal next-proof",
            "  /local-goal model-promotion-decision",
        ]
    )


def doctor_structured_state(payload: dict | None) -> dict:
    """Return the compact machine-readable state behind doctor/brief output."""
    payload = payload if isinstance(payload, dict) else {}
    status_payload = (
        payload.get("status") if isinstance(payload.get("status"), dict) else {}
    )
    mission_payload = (
        payload.get("mission") if isinstance(payload.get("mission"), dict) else {}
    )
    capabilities_payload = (
        payload.get("capabilities")
        if isinstance(payload.get("capabilities"), dict)
        else {}
    )
    current_truth_payload = (
        payload.get("current_truth")
        if isinstance(payload.get("current_truth"), dict)
        else {}
    )

    active_goal = (
        status_payload.get("active_goal")
        if isinstance(status_payload.get("active_goal"), dict)
        else {}
    )
    queue = (
        status_payload.get("queue")
        if isinstance(status_payload.get("queue"), dict)
        else {}
    )
    current_state = {}
    status_capabilities = status_payload.get("capabilities")
    if isinstance(status_capabilities, dict) and isinstance(
        status_capabilities.get("current_state"), dict
    ):
        current_state = status_capabilities["current_state"]
    if not current_state and isinstance(
        capabilities_payload.get("current_state"), dict
    ):
        current_state = capabilities_payload["current_state"]

    lanes_payload = (
        capabilities_payload.get("lanes")
        if isinstance(capabilities_payload.get("lanes"), dict)
        else {}
    )
    lanes = {}
    for name, lane in lanes_payload.items():
        if isinstance(lane, dict):
            lanes[name] = {
                "classification": lane.get("classification"),
                "installed": lane.get("installed"),
                "available_now": lane.get("available_now"),
                "availability_reason": lane.get("availability_reason"),
            }

    watcher = {}
    supervision = capabilities_payload.get("supervision")
    if isinstance(supervision, dict) and isinstance(supervision.get("watcher"), dict):
        watcher = supervision["watcher"]
    model_decision = (
        current_truth_payload.get("model_promotion_decision")
        if isinstance(current_truth_payload.get("model_promotion_decision"), dict)
        else {}
    )
    completed = mission_payload.get("completed_subgoals")
    done_criteria = mission_payload.get("done_criteria")
    safe_next_commands = [
        "scripts/local-goal brief",
        "scripts/local-goal doctor --json",
        "scripts/local-goal progress",
        "scripts/local-goal queue-summary",
        "scripts/local-goal next-proof",
        "scripts/local-goal completion-summary",
        "scripts/local-goal glm-handoff-plan",
        "scripts/local-goal glm-supervisor status",
        "scripts/local-goal model-promotion-decision",
        "scripts/local-goal model-promotion-waiver",
        "scripts/local-goal model-promotion-apply  # phone-safe preview; does not mutate services",
        "scripts/local-goal model-promotion-verify",
    ]
    operator_actions_open = []
    model_operator_can_choose_promotion = _model_operator_can_choose_promotion(
        model_decision
    )
    if model_operator_can_choose_promotion:
        operator_actions_open.append("ornith_durable_promotion_decision")
    decision_state = doctor_decision_state(
        status_payload, mission_payload, current_truth_payload
    )

    state = {
        "contract": "local_node1_goal_doctor_state.v1",
        "classification": status_payload.get("classification"),
        "recommended_action": status_payload.get("recommended_action"),
        "tmux_running": active_goal.get("tmux_running"),
        "awaiting_review": active_goal.get("awaiting_review"),
        "accepted": active_goal.get("accepted"),
        "objective": active_goal.get("objective"),
        "queue_queued": queue.get("queued"),
        "queue_running": queue.get("running"),
        "queue_failed": queue.get("failed_to_start"),
        "queue_failed_label": queue.get("failed_to_start_label"),
        "local_goal_lane_free": current_state.get("local_goal_lane_free"),
        "node1_vllm_idle": current_state.get("node1_vllm_idle"),
        "node1_vllm_has_other_activity": current_state.get(
            "node1_vllm_has_other_activity"
        ),
        "mission_status": mission_payload.get("status")
        or mission_payload.get("mission_status"),
        "mission_completed_subgoals": len(completed)
        if isinstance(completed, list)
        else None,
        "mission_done_criteria": len(done_criteria)
        if isinstance(done_criteria, list)
        else None,
        "watcher_active": bool(watcher.get("timer_active"))
        and bool(watcher.get("service_ok")),
        "watcher_state": watcher.get("state"),
        "lanes": lanes,
        "model_promotion_status": model_decision.get("status"),
        "model_operator_can_choose_promotion": model_operator_can_choose_promotion,
        "model_terminal_approval_command": model_decision.get(
            "terminal_approval_command"
        ),
        "operator_actions_open": operator_actions_open,
        "safe_next_commands": safe_next_commands,
        "model_promotion_waiver_command": "scripts/local-goal model-promotion-waiver",
    }
    for key in (
        "operator_decision",
        "operator_command",
        "next_action",
        "start_command",
        "model_choice_command",
        "phone_safe_preview",
        "terminal_only_mutation",
    ):
        value = decision_state.get(key)
        if value:
            state[key] = value
    return state


def doctor_envelope_status(doctor_state: dict | None) -> dict:
    """Return top-level JSON status fields for doctor-like responses."""
    state = doctor_state if isinstance(doctor_state, dict) else {}
    classification = str(state.get("classification") or "unknown")
    queue_running = int(state.get("queue_running") or 0)
    queue_queued = int(state.get("queue_queued") or 0)
    tmux_running = state.get("tmux_running") is True
    lane_free = state.get("local_goal_lane_free") is True
    watcher_active = state.get("watcher_active") is True
    if tmux_running or queue_running or queue_queued:
        status = "working"
    elif classification == "accepted" and lane_free:
        status = "ready"
    elif classification in {"needs_review", "reviewing"}:
        status = "needs_review"
    elif classification in {"blocked", "failed", "stuck"}:
        status = "blocked"
    elif lane_free:
        status = "ready"
    else:
        status = classification
    envelope = {
        "ok": status in {"ready", "working", "needs_review"} and watcher_active,
        "status": status,
        "local_goal_lane_free": lane_free,
        "tmux_running": tmux_running,
        "queue_running": queue_running,
        "queue_queued": queue_queued,
        "watcher_active": watcher_active,
    }
    for key in (
        "operator_decision",
        "operator_command",
        "next_action",
        "start_command",
        "model_choice_command",
        "phone_safe_preview",
        "terminal_only_mutation",
    ):
        value = state.get(key)
        if value:
            envelope[key] = value
    return envelope


def run_doctor_command(
    cmd: list[str], *, trust_boundary: bool = False, brief: bool = False
) -> tuple[subprocess.CompletedProcess[str], dict, str]:
    """Run the phone-friendly composite status check for Hermes chat."""
    status_payload, status_proc = run_supervisor_json(["status", "--json"])
    mission_payload, mission_proc = run_supervisor_json(["mission-show", "--json"])
    capabilities_payload, capabilities_proc = run_supervisor_json(
        ["capabilities", "--json"]
    )
    last_run_payload, last_run_proc = run_manager_json(["last-run", "--json"])
    current_truth_proc = run_command([str(WRAPPER), "current-truth", "--json"])
    current_truth_payload = parse_supervisor_payload(current_truth_proc.stdout)
    completion_summary_proc = run_command([str(WRAPPER), "completion-summary", "--json"])
    completion_summary_payload = parse_supervisor_payload(
        completion_summary_proc.stdout
    )
    if brief:
        summary = brief_summary(
            status_payload,
            mission_payload,
            capabilities_payload,
            last_run_payload,
            current_truth_payload,
            completion_summary_payload,
        )
    elif trust_boundary:
        summary = trust_boundary_summary(
            status_payload,
            mission_payload,
            capabilities_payload,
            last_run_payload,
            current_truth_payload,
            completion_summary_payload,
        )
    else:
        summary = doctor_summary(
            status_payload,
            mission_payload,
            capabilities_payload,
            last_run_payload,
            current_truth_payload,
            completion_summary_payload,
        )
    returncode = (
        0
        if all(
            proc.returncode == 0
            for proc in (
                status_proc,
                mission_proc,
                capabilities_proc,
                last_run_proc,
                current_truth_proc,
                completion_summary_proc,
            )
        )
        else 1
    )
    stderr = "\n".join(
        proc.stderr.strip()
        for proc in (status_proc, mission_proc, capabilities_proc)
        if proc.stderr.strip()
    )
    payload = {
        "contract": "local_node1_goal_brief.v1"
        if brief
        else "local_node1_goal_doctor.v1",
        "status": status_payload,
        "mission": mission_payload,
        "capabilities": capabilities_payload,
        "current_truth": current_truth_payload,
    }
    payload["doctor_state"] = doctor_structured_state(payload)
    proc = subprocess.CompletedProcess(
        args=cmd,
        returncode=returncode,
        stdout=summary + "\n",
        stderr=stderr,
    )
    return proc, payload, summary


def _mission_title(active_subgoal: dict) -> str:
    title = str(active_subgoal.get("title") or "").strip()
    criterion = str(active_subgoal.get("criterion") or "").strip()
    return _short_text(title or criterion or "active mission subgoal")


def human_supervisor_summary(payload: dict | None) -> str:
    """Return a compact operator-readable local-goal status.

    Keep raw machine details in the JSON artifact; chat/status output should
    answer only: state, reason, next action, and current goal.
    """
    if not payload:
        return ""

    contract = str(payload.get("contract") or "")
    if contract == "local_node1_goal_command.v1":
        nested = payload.get("supervisor_payload")
        if isinstance(nested, dict):
            nested_summary = summarize_supervisor_payload(nested)
            if nested_summary:
                return nested_summary
        summary = payload.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()

    if contract == "local_node1_goal_availability_question.v1":
        title = {
            "free": "Local Goal Free",
            "can-start": "Local Goal Can Start",
            "stuck": "Local Goal Stuck",
        }.get(str(payload.get("mode") or ""), "Local Goal Availability")
        lines = [
            title,
            f"Question: {_short_text(payload.get('question'), 180)}",
            f"State: {payload.get('state') or 'unknown'}",
            f"Can start: {str(payload.get('can_start')).lower()}",
            f"Lane free: {str(payload.get('lane_free')).lower()}",
            f"Node1 idle: {str(payload.get('node1_idle')).lower()}",
            f"Capacity: {_short_text(payload.get('capacity_evidence'), 180)}",
            f"Stuck: {str(payload.get('stuck')).lower()}",
            f"Reason: {_short_text(payload.get('reason'), 260)}",
            f"Next: {_short_text(payload.get('next'), 260)}",
        ]
        if payload.get("lane_free") is True and payload.get("node1_idle") is not True:
            lines.append(
                "Capacity note: local-goal lane is free, but a new run may wait behind separate vLLM work."
            )
        objective = payload.get("objective")
        if objective:
            lines.append(f"Goal: {_short_text(objective, 160)}")
        lines.append(
            "Queue: running={} queued={}".format(
                payload.get("queue_running") or 0,
                payload.get("queue_queued") or 0,
            )
        )
        return "\n".join(lines)

    if contract == "local_node1_goal_review_question.v1":
        title = (
            "Local Goal Can Accept"
            if payload.get("mode") == "can-accept"
            else "Local Goal Ready Review"
        )
        lines = [
            title,
            f"Question: {_short_text(payload.get('question'), 180)}",
            f"State: {payload.get('state') or 'unknown'}",
            f"Can review: {str(payload.get('can_review')).lower()}",
            f"Can accept: {str(payload.get('can_accept')).lower()}",
            f"Reason: {_short_text(payload.get('reason'), 260)}",
            f"Next: {_short_text(payload.get('next'), 260)}",
        ]
        objective = payload.get("objective")
        if objective:
            lines.append(f"Goal: {_short_text(objective, 160)}")
        lines.append(
            "Queue: running={} queued={}".format(
                payload.get("queue_running") or 0,
                payload.get("queue_queued") or 0,
            )
        )
        return "\n".join(lines)

    if contract == "local_goal_glm_supervisor.v1":
        running = payload.get("running") is True
        lines = [
            "GLM Supervisor",
            f"Status: {payload.get('status') or ('running' if running else 'stopped')}",
            f"Running: {str(running).lower()}",
            f"Session: {_short_text(payload.get('session'), 120)}",
            f"Reviewer: {_short_text(payload.get('reviewer') or 'glm-5.2', 80)}",
            f"Timeout: {payload.get('timeout_seconds') or 300}s",
            f"Interval: {payload.get('interval_seconds') or 300}s",
        ]
        attach_command = payload.get("attach_command")
        if attach_command:
            lines.append(f"Attach: {_short_text(attach_command, 180)}")
        pane = payload.get("pane")
        if pane:
            lines.append(f"Pane: {_short_text(pane, 120)}")
        if running:
            lines.append(
                "Next: Let GLM advise in tmux while deterministic review and acceptance remain the final gate."
            )
        else:
            lines.append(
                "Next: Start it with /local-goal start GLM supervisor tmux for the local harness."
            )
        return "\n".join(lines)

    if contract == "local_node1_goal_glm_handoff_plan.v1":
        lines = [
            "GLM-5.2 Handoff Plan",
            f"Status: {payload.get('status') or 'unknown'}",
            f"Recommended mode: {_short_text(payload.get('recommended_mode'), 120)}",
            f"Lane free: {str(payload.get('lane_free')).lower()}",
            f"Node1 vLLM idle: {str(payload.get('node1_vllm_idle')).lower()}",
            f"Start may wait: {str(payload.get('start_may_wait')).lower()}",
            f"Boundary: {_short_text(payload.get('supervision_boundary'), 260)}",
            f"Best first step: {_short_text(payload.get('best_first_step'), 260)}",
        ]
        dry_run = payload.get("dry_run_command")
        start = payload.get("start_command")
        cloud = payload.get("cloud_worker_command")
        advisory = payload.get("advisory_supervisor_command")
        if dry_run:
            lines.extend(["Dry run:", f"  {_short_text(dry_run, 260)}"])
        if start:
            lines.extend(
                [
                    "Start one bounded GLM-planner local goal:",
                    f"  {_short_text(start, 300)}",
                ]
            )
        if cloud:
            lines.extend(
                ["Optional cloud worker lane:", f"  {_short_text(cloud, 300)}"]
            )
        if advisory:
            lines.extend(
                ["Optional advisory tmux loop:", f"  {_short_text(advisory, 220)}"]
            )
        review_commands = payload.get("review_commands")
        if isinstance(review_commands, list) and review_commands:
            lines.append("Review after GLM:")
            lines.extend(
                f"  {_short_text(command, 220)}" for command in review_commands[:4]
            )
        return "\n".join(lines)

    if contract == "local_node1_goal_last_run_summary.v1":
        if payload.get("available") is not True:
            return "Last accepted run: unavailable\nReason: no accepted local-goal run evidence found."
        lines = [
            f"Last accepted run: {payload.get('title') or 'unknown'}",
            f"Status: {payload.get('status') or 'unknown'} (review: {payload.get('review_status') or 'unknown'})",
        ]
        summary = payload.get("summary")
        if isinstance(summary, str) and summary.strip():
            lines.append(f"Summary: {summary.strip()}")
        run_dir = payload.get("run_dir")
        if run_dir:
            lines.append(f"Run directory: {run_dir}")
        verification_count = payload.get("verification_count")
        if verification_count is not None:
            lines.append(f"Verification entries: {verification_count}")
        owned_files = payload.get("owned_files_sample")
        if isinstance(owned_files, list) and owned_files:
            lines.append("Verified output files:")
            lines.extend(f"  {path}" for path in owned_files[:8])
        marked_unverified = payload.get("unverified_marked_owned_files_sample")
        if isinstance(marked_unverified, list) and marked_unverified:
            lines.append("Marked-owned but not completion-evidence-backed:")
            lines.extend(f"  {path}" for path in marked_unverified[:8])
        note = payload.get("changed_file_note")
        if isinstance(note, str) and note.strip():
            lines.append(f"Worktree note: {note.strip()}")
        return "\n".join(lines)

    if contract == "local_node1_goal_hermes_integration_audit.v1":
        ok = payload.get("ok") is True and payload.get("missing") in ([], None)
        state = "ready" if ok else "blocked"
        reason = (
            "Hermes and the local Node1 harness integration checks are green."
            if ok
            else "Hermes/local Node1 integration has missing or failed checks."
        )
        next_action = (
            "No setup action needed; use status or supervise for the active goal."
            if ok
            else "Open the integration audit report and fix the missing checks."
        )
        return f"Status: {state}\nReason: {reason}\nNext: {next_action}"

    if contract == "local_node1_goal_harness_readiness.v1":
        missing = (
            payload.get("missing") if isinstance(payload.get("missing"), list) else []
        )
        unmet = (
            payload.get("unmet_objective_requirements")
            if isinstance(payload.get("unmet_objective_requirements"), list)
            else []
        )
        requirements = (
            payload.get("objective_requirements")
            if isinstance(payload.get("objective_requirements"), list)
            else []
        )
        ok_count = sum(
            1
            for item in requirements
            if isinstance(item, dict) and item.get("ok") is True
        )
        total_count = len(requirements)
        ok = payload.get("ok") is True and not missing and not unmet
        model_decision = payload.get("model_promotion_decision")
        if not isinstance(model_decision, dict):
            model_decision = {}
        model_ready_for_choice = _model_operator_can_choose_promotion(model_decision)
        if ok:
            estimate = 90
            status = "ready"
            reason = (
                f"Readiness audit passed with {ok_count}/{total_count} objective requirements met."
                if total_count
                else "Readiness audit passed."
            )
            next_action = "Use it for bounded local goals; keep review gates for product-sensitive work."
        else:
            base = int((ok_count / total_count) * 85) if total_count else 50
            estimate = max(40, min(85, base))
            status = str(payload.get("status") or "needs work")
            reason = f"Readiness audit has {len(missing)} missing check(s) and {len(unmet)} unmet requirement(s)."
            next_action = (
                "Fix missing readiness checks before trusting unattended harness work."
            )
        remaining = (
            "required harness proof is satisfied; optional hardening remains real-world goal use, longer soak windows, and the explicit Ornith promotion operator choice."
            if model_ready_for_choice
            else "required harness proof is satisfied; optional hardening is real-world goal use, longer soak windows, and model promotion decision follow-up."
        )
        extra_lines = []
        if model_ready_for_choice:
            phone_preview = model_decision.get("phone_safe_preview") or (
                "Use scripts/local-goal model-promotion-apply or "
                "/local-goal model-promotion-apply to inspect the approval packet; "
                "this does not mutate services."
            )
            terminal_only = model_decision.get("terminal_only_mutation") or (
                "Only a terminal command with --execute --confirm PROMOTE_ORNITH_PERMANENT "
                "makes Ornith durable."
            )
            terminal_command = model_decision.get("terminal_approval_command")
            extra_lines.extend(
                [
                    "Model decision: A/B evidence is complete; operator can choose Ornith promotion.",
                    f"Phone-safe preview: {phone_preview}",
                    f"Terminal-only mutation: {terminal_only}",
                ]
            )
            if terminal_command:
                extra_lines.append(
                    f"Terminal-only mutation command: {terminal_command}"
                )
        elif model_decision:
            extra_lines.append(
                "Model decision: "
                f"status={model_decision.get('status')} "
                "operator_can_choose_promotion="
                f"{_model_operator_can_choose_promotion(model_decision)}"
            )
        estimate_line = (
            f"Harness readiness: bounded local goals ready "
            f"(broad-autonomy estimate: {estimate}%; 100% broad autonomy not claimed)"
            if ok
            else f"Harness readiness: not bounded-goal ready (estimate: {estimate}%)"
        )
        lines = [
            estimate_line,
            f"Status: {status}",
            "Usable for bounded local goals: true"
            if ok
            else "Usable for bounded local goals: false",
            "100% broad-autonomy claim: not claimed"
            if ok
            else "100% broad-autonomy claim: no",
            f"Reason: {reason}",
            f"Next: {next_action}",
            f"Remaining: {remaining}",
        ]
        lines.extend(extra_lines)
        lines.extend(
            [
                "Hardening commands:",
                "  scripts/local-goal next-proof",
                "  scripts/local-goal soak-plan",
                "  scripts/local-goal model-eval-next",
                "  scripts/local-goal model-promotion-decision",
                "  scripts/local-goal model-promotion-apply  # phone-safe preview; does not mutate services",
                "  scripts/local-goal model-promotion-verify",
            ]
        )
        return "\n".join(lines)

    if contract == "local_node1_goal_completion_summary.v1":
        surfaces = payload.get("operator_surfaces_summary")
        if not isinstance(surfaces, dict):
            surfaces = {}
        verified = surfaces.get("verified")
        total = surfaces.get("total")
        missing_surfaces = surfaces.get("missing")
        surface_line = f"{verified}/{total} verified"
        if isinstance(missing_surfaces, list) and missing_surfaces:
            surface_line += (
                f" (missing: {', '.join(str(item) for item in missing_surfaces[:6])})"
            )
        routes = payload.get("control_routes_summary")
        if not isinstance(routes, dict):
            routes = {}
        routes_verified = routes.get("verified")
        routes_total = routes.get("total")
        missing_routes = routes.get("missing")
        route_line = f"{routes_verified}/{routes_total} verified"
        if isinstance(missing_routes, list) and missing_routes:
            route_line += (
                f" (missing: {', '.join(str(item) for item in missing_routes[:6])})"
            )
        capabilities = payload.get("capability_gates_summary")
        if not isinstance(capabilities, dict):
            capabilities = {}
        capabilities_verified = capabilities.get("verified")
        capabilities_total = capabilities.get("total")
        missing_capabilities = capabilities.get("missing")
        capability_line = f"{capabilities_verified}/{capabilities_total} verified"
        if isinstance(missing_capabilities, list) and missing_capabilities:
            capability_line += f" (missing: {', '.join(str(item) for item in missing_capabilities[:6])})"
        safety = payload.get("safety_checks_summary")
        if not isinstance(safety, dict):
            safety = {}
        safety_verified = safety.get("verified")
        safety_total = safety.get("total")
        missing_safety = safety.get("missing")
        safety_line = f"{safety_verified}/{safety_total} verified"
        if isinstance(missing_safety, list) and missing_safety:
            safety_line += (
                f" (missing: {', '.join(str(item) for item in missing_safety[:6])})"
            )
        lines = [
            "Local Goal Completion Summary",
            f"Status: {payload.get('status')}",
            f"Usable for bounded local goals: {str(payload.get('usable_for_bounded_local_goals')).lower()}",
            f"Required evidence ok: {str(payload.get('required_evidence_ok')).lower()}",
            f"Operator surfaces: {surface_line}",
            f"Control routes: {route_line}",
            f"Capability gates: {capability_line}",
            f"Safety checks: {safety_line}",
            f"Start may wait: {str(payload.get('start_may_wait')).lower()}",
        ]
        autonomy = payload.get("autonomy_grade")
        if isinstance(autonomy, dict):
            label = autonomy.get("label")
            remaining = autonomy.get("remaining")
            if label:
                lines.insert(4, f"Autonomy grade: {_short_text(label, 120)}")
            if remaining:
                lines.insert(5, f"Remaining: {_short_text(remaining, 220)}")
        start_guidance = payload.get("start_guidance")
        if start_guidance:
            lines.append(f"Start guidance: {_short_text(start_guidance, 260)}")
        actions = payload.get("operator_actions_open")
        details = payload.get("operator_action_details")
        if not isinstance(details, dict):
            details = {}
        if isinstance(actions, list) and actions:
            lines.append(
                f"Operator actions open: {', '.join(str(item) for item in actions[:3])}"
            )
            for action in actions[:3]:
                detail = details.get(action) if isinstance(action, str) else {}
                if not isinstance(detail, dict):
                    detail = {}
                label = detail.get("label")
                meaning = detail.get("meaning")
                preview = detail.get("preview_command")
                preview_note = detail.get("preview_note")
                mutation = detail.get("terminal_only_mutation") or detail.get(
                    "terminal_only_mutation_command"
                )
                if label:
                    lines.append(
                        f"  - {_short_text(action, 90)}: {_short_text(label, 180)}"
                    )
                if meaning:
                    lines.append(f"    Meaning: {_short_text(meaning, 220)}")
                if preview:
                    suffix = (
                        f" ({_short_text(preview_note, 120)})" if preview_note else ""
                    )
                    lines.append(f"    Preview: {_short_text(preview, 180)}{suffix}")
                if mutation:
                    lines.append(
                        f"    Terminal-only mutation: {_short_text(mutation, 220)}"
                    )
        missing = payload.get("missing")
        if isinstance(missing, list) and missing:
            lines.append(
                f"Missing required evidence: {', '.join(str(item) for item in missing[:6])}"
            )
        safe_commands = payload.get("safe_next_commands")
        if isinstance(safe_commands, list) and safe_commands:
            lines.append(f"Next safe command: {_short_text(safe_commands[0], 220)}")
        start_commands = payload.get("start_next_commands")
        if isinstance(start_commands, list) and start_commands:
            lines.append(f"Start command: {_short_text(start_commands[0], 220)}")
        source_command = payload.get("source_command")
        if source_command:
            lines.append(f"Full evidence: {_short_text(source_command, 220)}")
        return "\n".join(lines)

    if contract == "local_node1_goal_completion_audit.v1":
        route_summary = payload.get("control_routes_summary")
        if not isinstance(route_summary, dict):
            route_summary = {}
        route_line = ""
        if route_summary.get("label"):
            route_line = str(route_summary.get("label"))
        elif payload.get("control_routes"):
            routes = payload.get("control_routes")
            if isinstance(routes, dict):
                route_line = (
                    f"{routes.get('verified')}/{routes.get('required')} verified"
                )
        capability_summary = payload.get("capability_gates_summary")
        if not isinstance(capability_summary, dict):
            capability_summary = {}
        capability_line = ""
        if capability_summary.get("label"):
            capability_line = str(capability_summary.get("label"))
        elif payload.get("capability_gates"):
            capabilities = payload.get("capability_gates")
            if isinstance(capabilities, dict):
                capability_line = f"{capabilities.get('verified')}/{capabilities.get('required')} verified"
        safety_summary = payload.get("safety_checks_summary")
        if not isinstance(safety_summary, dict):
            safety_summary = {}
        safety_line = ""
        if safety_summary.get("label"):
            safety_line = str(safety_summary.get("label"))
        elif payload.get("safety_checks"):
            safety = payload.get("safety_checks")
            if isinstance(safety, dict):
                safety_line = (
                    f"{safety.get('verified')}/{safety.get('required')} verified"
                )
        lines = [
            "Local Goal Completion Audit",
            f"Status: {payload.get('status')}",
            f"Usable for bounded local goals: {str(payload.get('usable_for_bounded_local_goals')).lower()}",
            "100% broad-autonomy claim: not claimed",
            f"Required evidence ok: {str(payload.get('required_evidence_ok')).lower()}",
            f"Optional model operator choice open: {str(payload.get('optional_model_operator_choice_open')).lower()}",
            f"Control routes: {route_line}"
            if route_line
            else "Control routes: unknown",
            f"Capability gates: {capability_line}"
            if capability_line
            else "Capability gates: unknown",
            f"Safety checks: {safety_line}"
            if safety_line
            else "Safety checks: unknown",
            f"Start may wait: {str(payload.get('start_may_wait')).lower()}",
            f"Start guidance: {_short_text(payload.get('start_guidance'), 260)}",
        ]
        autonomy = payload.get("autonomy_grade")
        if isinstance(autonomy, dict):
            label = autonomy.get("label")
            remaining = autonomy.get("remaining")
            if label:
                lines.insert(4, f"Autonomy grade: {_short_text(label, 120)}")
            if remaining:
                lines.insert(5, f"Remaining: {_short_text(remaining, 220)}")
        actions = payload.get("operator_actions_open")
        details = payload.get("operator_action_details")
        if not isinstance(details, dict):
            details = {}
        if isinstance(actions, list) and actions:
            lines.append("Operator actions open:")
            for action in actions[:3]:
                detail = details.get(action) if isinstance(action, str) else {}
                if not isinstance(detail, dict):
                    detail = {}
                label = detail.get("label") or action
                lines.append(
                    f"  - {_short_text(action, 90)}: {_short_text(label, 180)}"
                )
                meaning = detail.get("meaning")
                if meaning:
                    lines.append(f"    Meaning: {_short_text(meaning, 220)}")
                preview = detail.get("preview_command")
                if preview:
                    lines.append(
                        "    Preview: {} ({})".format(
                            _short_text(preview, 180),
                            _short_text(
                                detail.get("preview_note") or "preview only", 120
                            ),
                        )
                    )
                mutation = detail.get("terminal_only_mutation") or detail.get(
                    "terminal_only_mutation_command"
                )
                if mutation:
                    lines.append(
                        f"    Terminal-only mutation: {_short_text(mutation, 220)}"
                    )
        requirements = payload.get("requirements")
        if isinstance(requirements, list) and requirements:
            lines.append("Requirements:")
            priority = {
                "readiness_gate_green": 0,
                "hermes_integration_green": 1,
                "gateway_service_active": 2,
                "local_goal_lane_free": 3,
                "node1_vllm_capacity_clear": 4,
                "mission_complete": 5,
                "latest_run_accepted": 6,
                "accepted_soak_evidence_present": 7,
                "model_decision_explicitly_operator_gated": 8,
            }
            ordered_requirements = sorted(
                (item for item in requirements if isinstance(item, dict)),
                key=lambda item: (
                    priority.get(str(item.get("requirement")), 100),
                    str(item.get("requirement") or ""),
                ),
            )
            for item in ordered_requirements[:9]:
                if not isinstance(item, dict):
                    continue
                scope = (
                    "required"
                    if item.get("required_for_bounded_ready") is True
                    else "info"
                )
                if (
                    item.get("requirement") == "node1_vllm_capacity_clear"
                    and item.get("ok") is not True
                ):
                    marker = "busy"
                else:
                    marker = "ok" if item.get("ok") is True else "missing"
                lines.append(
                    "  - {}: {} [{}] ({})".format(
                        _short_text(item.get("requirement"), 80),
                        marker,
                        scope,
                        _short_text(item.get("evidence"), 180),
                    )
                )
        safe_commands = payload.get("safe_next_commands")
        if isinstance(safe_commands, list) and safe_commands:
            lines.append("Safe next commands:")
            lines.extend(f"  - {_short_text(item, 220)}" for item in safe_commands[:5])
        return "\n".join(lines)

    if contract == "local_node1_goal_next_proof.v1":
        lines = [
            "Local Goal Next Proof",
            f"Status: {payload.get('status')}",
            f"Required now: {str(payload.get('required_now')).lower()}",
            f"Readiness: {payload.get('readiness_status')} ok={str(payload.get('readiness_ok')).lower()}",
            f"Local-goal lane free: {str(payload.get('local_goal_lane_free')).lower()}",
            f"Start may wait: {str(payload.get('start_may_wait')).lower()}",
            "Node1 vLLM capacity clear: {} ({})".format(
                str(payload.get("node1_vllm_capacity_clear")).lower(),
                _short_text(payload.get("node1_vllm_capacity_evidence"), 180),
            ),
            f"Mission: {payload.get('mission_status')}",
            f"Last accepted soak proof: {str(payload.get('last_accepted_soak_proof')).lower()}",
            f"Next proof: {_short_text(payload.get('next_proof'), 280)}",
            f"Reason: {_short_text(payload.get('reason'), 280)}",
            f"Next: {_short_text(payload.get('suggested_operator_action'), 280)}",
        ]
        missing = payload.get("missing")
        unmet = payload.get("unmet_objective_requirements")
        if isinstance(missing, list) and missing:
            lines.append("Missing checks:")
            lines.extend(f"  - {_short_text(item, 160)}" for item in missing[:5])
        if isinstance(unmet, list) and unmet:
            lines.append("Unmet requirements:")
            lines.extend(f"  - {_short_text(item, 160)}" for item in unmet[:5])
        actions = payload.get("operator_actions_open")
        details = payload.get("operator_action_details")
        if not isinstance(details, dict):
            details = {}
        if isinstance(actions, list) and actions:
            lines.append("Operator actions open:")
            for action in actions[:3]:
                detail = details.get(action) if isinstance(action, str) else {}
                if not isinstance(detail, dict):
                    detail = {}
                label = detail.get("label") or action
                lines.append(
                    f"  - {_short_text(action, 90)}: {_short_text(label, 180)}"
                )
                meaning = detail.get("meaning")
                if meaning:
                    lines.append(f"    Meaning: {_short_text(meaning, 220)}")
                preview = detail.get("preview_command")
                if preview:
                    lines.append(
                        "    Preview: {} ({})".format(
                            _short_text(preview, 180),
                            _short_text(
                                detail.get("preview_note") or "preview only", 120
                            ),
                        )
                    )
        safe_commands = payload.get("safe_commands")
        if isinstance(safe_commands, list) and safe_commands:
            lines.append("Safe commands:")
            lines.extend(
                (
                    f"  - {_short_text(item, 220)}"
                    + (
                        "  # phone-safe preview; does not mutate services"
                        if item == "scripts/local-goal model-promotion-apply"
                        else ""
                    )
                )
                for item in safe_commands[:5]
            )
        return "\n".join(lines)

    if contract == "local_node1_goal_soak_plan.v1":
        lines = [
            "Local Goal Soak Plan",
            f"Status: {payload.get('status')}",
            f"Can start: {str(payload.get('can_start')).lower()}",
            f"Start may wait: {str(payload.get('start_may_wait')).lower()}",
            f"Start guidance: {_short_text(payload.get('start_guidance'), 280)}",
            "Node1 vLLM capacity clear: {} ({})".format(
                str(payload.get("node1_vllm_capacity_clear")).lower(),
                _short_text(payload.get("node1_vllm_capacity_evidence"), 180),
            ),
            f"Readiness: {payload.get('readiness_status')} ok={str(payload.get('readiness_ok')).lower()}",
            f"Local-goal lane free: {str(payload.get('local_goal_lane_free')).lower()}",
            f"Mission: {payload.get('mission_status')}",
            f"Scope: {payload.get('scope')}",
            f"Does not start work: {str(payload.get('does_not_start_work')).lower()}",
            f"Reason: {_short_text(payload.get('reason'), 280)}",
            "Start command:",
            f"  {_short_text(payload.get('start_command'), 500)}",
            "Monitor command:",
            f"  {_short_text(payload.get('monitor_command'), 300)}",
        ]
        hermes_commands: list[str] = []
        for key in ("hermes_start_command", "hermes_monitor_command"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                hermes_commands.append(value)
        check_values = payload.get("hermes_check_commands")
        if isinstance(check_values, list):
            hermes_commands.extend(str(item) for item in check_values if item)
        if hermes_commands:
            lines.append("Hermes / Telegram commands:")
            lines.extend(
                f"  {_short_text(command, 300)}" for command in hermes_commands[:6]
            )
        aliases = payload.get("telegram_alias_commands")
        if isinstance(aliases, list) and aliases:
            lines.append("Telegram-safe aliases:")
            lines.extend(f"  {_short_text(command, 220)}" for command in aliases[:5])
        commands = payload.get("check_commands")
        if isinstance(commands, list) and commands:
            lines.append("Check commands:")
            lines.extend(f"  {_short_text(command, 220)}" for command in commands[:5])
        return "\n".join(lines)

    if "active_subgoal" in payload and "completed_subgoals" in payload:
        mission_status = str(payload.get("status") or "unknown").lower()
        active = (
            payload.get("active_subgoal")
            if isinstance(payload.get("active_subgoal"), dict)
            else {}
        )
        completed = payload.get("completed_subgoals")
        completed_count = len(completed) if isinstance(completed, list) else 0
        total = len(payload.get("done_criteria") or [])
        if mission_status == "complete":
            state = "complete"
            reason = "Mission done criteria are marked complete."
            next_action = (
                "Review the final evidence before starting another Node1 goal."
            )
        elif active:
            state = "working"
            reason = f"Mission is active on {_mission_title(active)}."
            next_action = "No action needed; the watcher will supervise this subgoal."
        else:
            state = "ready"
            reason = "Mission is active and waiting for the next subgoal to be derived."
            next_action = "Let supervise/timer derive the next subgoal."
        lines = [
            f"Status: {state}",
            f"Reason: {reason}",
            f"Next: {next_action}",
        ]
        if total:
            lines.append(f"Progress: {completed_count}/{total} subgoals accepted")
        return "\n".join(lines)

    lanes = payload.get("lanes") if isinstance(payload.get("lanes"), dict) else {}
    current_state = (
        payload.get("current_state")
        if isinstance(payload.get("current_state"), dict)
        else {}
    )
    if lanes:
        node1_free = current_state.get("node1_is_free")
        queue_busy = current_state.get("queue_has_active_work")
        reason = (
            current_state.get("recommended_action")
            or current_state.get("availability_reason")
            or "Capability state loaded."
        )
        lines = [
            f"Status: {'ready' if node1_free is True and queue_busy is not True else 'busy'}",
            f"Reason: {_short_text(reason, 260)}",
        ]
        if node1_free is True and queue_busy is not True:
            lines.append(
                "Next: Use Local Node1 for normal bounded tasks; use Planner + "
                "Local for broad tasks; use Cloud Executor only as an optional "
                "bounded lane."
            )
        else:
            lines.append(
                "Next: Wait for the active local-goal run to clear, or ask "
                "Hermes to supervise local harness."
            )
        state_bits = []
        if "local_goal_lane_free" in current_state:
            state_bits.append(
                "local_goal_lane_free={}".format(
                    current_state.get("local_goal_lane_free")
                )
            )
        if "node1_vllm_idle" in current_state:
            state_bits.append(
                "node1_vllm_idle={}".format(current_state.get("node1_vllm_idle"))
            )
        if "node1_vllm_has_other_activity" in current_state:
            state_bits.append(
                "node1_vllm_has_other_activity={}".format(
                    current_state.get("node1_vllm_has_other_activity")
                )
            )
        if state_bits:
            lines.append("Current state: " + " ".join(state_bits))
        supervision = (
            payload.get("supervision")
            if isinstance(payload.get("supervision"), dict)
            else {}
        )
        watcher = (
            supervision.get("watcher")
            if isinstance(supervision.get("watcher"), dict)
            else {}
        )
        if watcher:
            lines.append(
                "Supervision: watcher={} timer_active={} service_ok={} execstart_ok={}".format(
                    watcher.get("state") or "unknown",
                    watcher.get("timer_active"),
                    watcher.get("service_ok"),
                    watcher.get("execstart_ok"),
                )
            )
            watcher_summary = watcher.get("summary")
            if watcher_summary:
                lines.append(f"  {_short_text(watcher_summary, 220)}")
        labels = {
            "local": "Local Node1",
            "premium_planner_local_builder": "Planner + Local",
            "cloud_executor": "Cloud Executor",
        }
        for lane_name in ("local", "premium_planner_local_builder", "cloud_executor"):
            lane = lanes.get(lane_name)
            if not isinstance(lane, dict):
                continue
            available = (
                "available" if lane.get("available_now") is True else "unavailable"
            )
            classification = lane.get("classification") or "unknown"
            reason_text = (
                lane.get("availability_reason")
                or lane.get("unavailable_reason")
                or "unknown"
            )
            lines.append(
                f"Lane: {labels.get(lane_name, lane_name)} - {available} "
                f"({classification}, reason={reason_text})"
            )
        return "\n".join(lines)

    items = payload.get("items") if isinstance(payload.get("items"), list) else None
    if items is not None:
        active_statuses = {"queued", "starting", "running", "needs_review", "reviewing"}
        active_items = [
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("status") or "").lower() in active_statuses
        ]
        counts: dict[str, int] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "unknown").lower()
            counts[status] = counts.get(status, 0) + 1
        lines = [
            f"Status: {'working' if active_items else 'idle'}",
            f"Reason: {len(active_items)} active queue item(s); {len(items)} total historical item(s).",
        ]
        if active_items:
            lines.append(
                "Next: Let supervise/monitor handle the active queue, or inspect queue --json for details."
            )
            for item in active_items[:5]:
                title = _short_text(
                    item.get("title")
                    or item.get("goal")
                    or item.get("id")
                    or "queued goal",
                    140,
                )
                lines.append(
                    "Active: "
                    f"{item.get('status') or 'unknown'} "
                    f"{item.get('id') or item.get('queue_id') or ''} "
                    f"planner={item.get('planner') or 'none'} "
                    f"executor={item.get('executor') or 'unknown'}"
                )
                lines.append(f"  title: {title}")
            if len(active_items) > 5:
                lines.append(f"  ... {len(active_items) - 5} more active item(s)")
        else:
            lines.append(
                "Next: Queue is clear; start one explicit local goal only if status says local-goal lane is free and has no vLLM wait warning."
            )
        if counts:
            summary = ", ".join(f"{name}={counts[name]}" for name in sorted(counts))
            lines.append(f"History: {summary}")
        return "\n".join(lines)

    supervisor_shape_keys = {
        "active_goal",
        "classification",
        "phase",
        "recommended_action",
        "runtime",
        "queue",
        "recovery_block",
        "dispatch",
        "objective",
        "accepted",
        "tmux_running",
        "awaiting_review",
    }
    if not any(key in payload for key in supervisor_shape_keys):
        return ""

    classification = str(payload.get("classification") or "").strip().lower()
    phase = str(payload.get("phase") or "").strip().lower()
    action = str(payload.get("recommended_action") or "").strip()
    active_goal = (
        payload.get("active_goal")
        if isinstance(payload.get("active_goal"), dict)
        else {}
    )
    queue = payload.get("queue") if isinstance(payload.get("queue"), dict) else {}
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    recovery = (
        payload.get("recovery_block")
        if isinstance(payload.get("recovery_block"), dict)
        else {}
    )
    dispatch = (
        payload.get("dispatch") if isinstance(payload.get("dispatch"), dict) else {}
    )
    goal_state = goal_state_from_payload(payload)

    objective = _short_text(active_goal.get("objective") or payload.get("objective"))
    tmux_running = goal_state.phase is Phase.EXECUTING and (
        active_goal.get("tmux_running") is True
        or payload.get("tmux_running") is True
        or classification in {"working", "running", "dispatching"}
    )
    awaiting_review = goal_state.phase is Phase.REVIEWING
    accepted = goal_state.accepted
    recovery_reason = str(recovery.get("recovery_block_reason") or "").strip()
    dispatch_status = str(dispatch.get("status") or "").strip()
    vllm = runtime.get("vllm") if isinstance(runtime.get("vllm"), dict) else {}
    queue_running = int(queue.get("running") or 0) if queue else 0
    queue_queued = int(queue.get("queued") or 0) if queue else 0
    vllm_running = float(vllm.get("running") or 0) if vllm else 0.0
    vllm_waiting = float(vllm.get("waiting") or 0) if vllm else 0.0
    local_goal_free = not tmux_running and queue_running == 0 and queue_queued == 0
    local_goal_free_with_other_vllm = local_goal_free and (
        vllm_running > 0 or vllm_waiting > 0
    )

    if goal_state.phase is Phase.BLOCKED or recovery_reason or dispatch_status.startswith("blocked"):
        state = "blocked"
    elif "repair" in phase or "continu" in phase:
        state = "repairing"
    elif goal_state.phase is Phase.DONE and accepted and not tmux_running:
        state = "accepted"
    elif awaiting_review:
        state = "waiting for review"
    elif goal_state.phase is Phase.DONE or classification in {"complete"}:
        state = "complete"
    elif goal_state.phase is Phase.FAILED or classification == "failed":
        state = "failed"
    elif classification in {"stuck", "blocked"}:
        state = "blocked"
    elif tmux_running or classification in {"working", "running", "dispatching"}:
        state = "working"
    else:
        state = "ready"

    action_needed = "No"
    exact_phrase = ""

    if state == "working":
        reason = _human_status_text(action or "The local-goal worker is running.")
        next_action = (
            "No action needed; the watcher will supervise and continue if needed."
        )
    elif state == "repairing":
        reason = (
            _human_status_text(action)
            or "Hermes is feeding a review/disposition failure back to Node1."
        )
        next_action = "No action needed unless the state changes to blocked."
    elif state == "waiting for review":
        reason = "Worker stopped and says it is done. Hermes watcher will review it automatically before you start anything new."
        next_action = "No action needed; the watcher will review it automatically. If it passes, the watcher accepts it; if it fails, the watcher continues the same goal with feedback."
    elif state == "accepted":
        reason = (
            _human_status_text(action)
            or "The active run was accepted and the local-goal lane is free."
        )
        if queue_queued > 0:
            next_action = (
                "No action needed; the watcher should dispatch the queued subgoal."
            )
        elif local_goal_free_with_other_vllm:
            next_action = "Local-goal lane is free, but the Node1 model server has separate capacity activity; a new bounded goal may wait."
        else:
            next_action = "Local-goal lane is free; check mission-show for umbrella mission state, or start another goal."
    elif state == "complete":
        reason = _human_status_text(action) or "The goal or mission is complete."
        next_action = "Review the final evidence before starting another Node1 goal."
    elif state == "failed":
        reason = (
            recovery.get("hard_failure_reason")
            or dispatch.get("detail")
            or _human_status_text(action)
            or "The local-goal worker reported a failed run."
        )
        if recovery.get("operator_intervention_required") is True or recovery.get(
            "hard_blocked"
        ) is True:
            action_needed = "Yes"
            next_action = (
                recovery.get("next_operator_step")
                or payload.get("next_operator_step")
                or "Michael needs to send: supervise local harness"
            )
        else:
            next_action = (
                "No action needed; Hermes will supervise the failed run and "
                "continue the same goal with feedback if the review gate allows it."
            )
    elif state == "blocked":
        action_needed = "Yes"
        reason = (
            recovery.get("hard_failure_reason")
            or dispatch.get("detail")
            or _human_status_text(action)
            or "The harness hit a blocker."
        )
        next_action = (
            recovery.get("next_operator_step")
            or dispatch.get("next_operator_step")
            or payload.get("next_operator_step")
            or "Michael needs to send: supervise local harness"
        )
    else:
        reason = _human_status_text(action) or "Local-goal lane is free."
        next_action = "Start or queue a local goal when ready."
        action_needed = "Only if you want to start new work"

    if "Michael needs to send:" in next_action:
        exact_phrase = next_action.split("Michael needs to send:", 1)[1].strip()
    elif action_needed == "Yes":
        exact_phrase = "supervise local harness"

    lines = [
        f"Status: {state}",
        f"What is happening now: {_short_text(reason, 260)}",
        f"Does Michael need to do anything? {action_needed}",
        f"Exact phrase to send Hermes: {exact_phrase or 'none'}",
        f"Reason: {_short_text(reason, 260)}",
        f"Next: {_short_text(next_action, 260)}",
    ]
    if state == "waiting for review":
        lines.extend(
            [
                "Do not start another Node1 goal yet.",
                "Plain English: the worker handed in its work; Hermes will review, accept, or continue it automatically.",
            ]
        )
    elif state == "failed":
        if not exact_phrase:
            lines.append(
                "Plain English: the run failed, but Hermes is allowed to retry or continue it automatically."
            )
    elif state == "blocked":
        if not exact_phrase:
            lines.append("Plain English: the run is blocked and needs the listed next action.")
    if objective:
        lines.append(f"Goal: {objective}")
    current_subgoal = active_goal.get("current_subgoal")
    if isinstance(current_subgoal, dict):
        lines.append(f"Subgoal: {_mission_title(current_subgoal)}")
    if queue:
        queued = queue.get("queued")
        running = queue.get("running")
        if queued is not None or running is not None:
            queue_running_display = running or 0
            queue_queued_display = queued or 0
            if tmux_running:
                if not queue_running_display and not queue_queued_display:
                    lines.append("Queue: no separate queued work")
                else:
                    lines.append(
                        "Queue: "
                        f"running={queue_running_display} "
                        f"queued={queue_queued_display} "
                        "(separate queued work)"
                    )
                lines.append("Active worker: yes, local-goal background terminal worker is running")
            else:
                lines.append(
                    f"Queue: running={queue_running_display} queued={queue_queued_display}"
                )
    if vllm:
        if local_goal_free:
            if vllm_running > 0 or vllm_waiting > 0:
                lines.append(
                    "Node1: local-goal free; model server has separate capacity activity "
                    f"running={vllm.get('running') or 0} "
                    f"waiting={vllm.get('waiting') or 0}"
                )
            else:
                lines.append("Node1: local-goal free; model server idle")
        else:
            lines.append(
                "Node1: local-goal active; "
                f"model server running={vllm.get('running') or 0} "
                f"waiting={vllm.get('waiting') or 0}"
            )
    return "\n".join(lines)


def summarize_supervisor_payload(payload: dict | None) -> str:
    if not payload:
        return ""
    contract = payload.get("contract")
    if contract == "local_goal_audit_health.v1":
        integration_ok = payload.get("integration_ok") is True
        refresh_running = (
            payload.get("refresh_in_progress") is True
            or payload.get("ready_refresh_running") is True
        )
        missing = payload.get("integration_missing")
        lines = [
            "Local-goal harness check",
            (
                "Status: healthy"
                if integration_ok and not refresh_running
                else "Status: needs attention"
            ),
        ]
        if integration_ok:
            lines.append(
                "What it means: Hermes can see the local-goal harness and its audit evidence."
            )
        if refresh_running:
            lines.append(
                "What it means: an integration refresh is still running; wait before starting another check."
            )
        if isinstance(missing, list) and missing:
            lines.append("Missing:")
            lines.extend(f"  - {_short_text(item, 180)}" for item in missing[:5])
        lines.append(
            "Next: send a bounded local-goal task when you want Node1 to do work."
        )
        return "\n".join(lines)
    if contract == "local_node1_goal_model_status.v1":
        current_service = payload.get("current_service")
        if not isinstance(current_service, dict):
            current_service = {}
        candidate = payload.get("candidate")
        if not isinstance(candidate, dict):
            candidate = {}
        local_status = payload.get("local_goal_status")
        if not isinstance(local_status, dict):
            local_status = {}
        promotion_gate = payload.get("promotion_gate")
        if not isinstance(promotion_gate, dict):
            promotion_gate = {}
        durability = payload.get("durability")
        if not isinstance(durability, dict):
            durability = {}
        lines = [
            "Local Goal Model Status",
            f"Status: {payload.get('canary_mode')}",
            f"Current model: {_short_text(current_service.get('model_path') or 'unknown', 260)}",
            f"Candidate: {_short_text(candidate.get('path') or 'unknown', 260)}",
            f"Service live: {payload.get('current_service_live')}",
            f"Local-goal lane free: {payload.get('local_goal_lane_free')}",
            f"Durability: {_short_text(durability.get('status') or 'unknown', 160)}",
            f"Promotion gate: {_short_text(promotion_gate.get('status') or 'unknown', 160)}",
        ]
        durability_reason = durability.get("reason")
        if durability_reason:
            lines.append(f"Durability reason: {_short_text(durability_reason, 260)}")
        if durability.get("next_command"):
            lines.append(
                f"Durability next: {_short_text(durability.get('next_command'), 220)}"
            )
        gate_reason = promotion_gate.get("reason")
        if gate_reason:
            lines.append(f"Reason: {_short_text(gate_reason, 260)}")
        risk = promotion_gate.get("known_qwopus_failure")
        if risk:
            lines.append(f"Qwopus completion risk: {_short_text(risk, 260)}")
        if promotion_gate.get("baseline_check_command"):
            lines.append(
                f"Baseline check: {_short_text(promotion_gate.get('baseline_check_command'), 220)}"
            )
        if promotion_gate.get("required_command"):
            lines.append(
                f"Promotion check: {_short_text(promotion_gate.get('required_command'), 220)}"
            )
        if (
            local_status.get("vllm_running") is not None
            or local_status.get("vllm_waiting") is not None
        ):
            lines.append(
                f"Node1 vLLM: running={local_status.get('vllm_running')} waiting={local_status.get('vllm_waiting')}"
            )
        recommendation = payload.get("recommendation")
        if recommendation:
            lines.append(f"Next: {_short_text(recommendation, 260)}")
        return "\n".join(lines)
    if contract == "local_node1_goal_model_nontrivial_baseline_check.v1":
        blockers = payload.get("blockers")
        lines = [
            "Qwopus nontrivial baseline check",
            f"Can start now: {payload.get('can_start_baseline_goal_now')}",
            f"Current model: {_short_text(payload.get('current_model_path') or 'unknown', 220)}",
            f"Baseline active: {payload.get('baseline_active')}",
            f"Ornith active: {payload.get('candidate_active')}",
            f"Local-goal lane free: {payload.get('local_goal_lane_free')}",
        ]
        if isinstance(blockers, list) and blockers:
            lines.append("Blockers:")
            lines.extend(f"  - {_short_text(item, 220)}" for item in blockers)
        next_action = payload.get("next_action")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 260)}")
        if payload.get("can_start_baseline_goal_now") is True and payload.get(
            "start_command"
        ):
            lines.append(f"Start: {_short_text(payload.get('start_command'), 260)}")
        return "\n".join(lines)
    if contract == "local_node1_goal_model_nontrivial_baseline_plan.v1":
        run_commands = payload.get("run_commands")
        first_run = ""
        if isinstance(run_commands, list) and run_commands:
            first_run = str(run_commands[0])
        lines = [
            "Qwopus nontrivial baseline plan",
            f"Status: {'service-window-required' if payload.get('requires_service_window') else 'ready'}",
            f"Risk: {_short_text(payload.get('risk'), 260)}",
            f"Goal file: {_short_text(payload.get('goal_file'), 220)}",
        ]
        if first_run:
            lines.append(f"Start after Qwopus is active: {_short_text(first_run, 260)}")
        completion_risk = payload.get("completion_risk_under_test")
        if completion_risk:
            lines.append(f"Completion risk: {_short_text(completion_risk, 260)}")
        return "\n".join(lines)
    if contract == "local_node1_goal_model_eval_next.v1":
        blockers = payload.get("baseline_blockers")
        required = payload.get("next_required_evidence")
        lines = [
            "Ornith/Qwopus eval next",
            f"Status: {payload.get('status')}",
            f"Promotion allowed: {payload.get('promotion_allowed')}",
            f"Compare verdict: {_short_text(payload.get('compare_verdict'), 220)}",
            f"Reason: {_short_text(payload.get('promotion_blocker'), 260)}",
        ]
        if payload.get("status") != "evidence-complete":
            lines.append(
                f"Qwopus baseline can start now: {payload.get('baseline_can_start_now')}"
            )
        if isinstance(blockers, list) and blockers:
            lines.append("Baseline blockers:")
            lines.extend(f"  - {_short_text(item, 220)}" for item in blockers)
        if isinstance(required, list) and required:
            lines.append("Next required evidence:")
            lines.extend(f"  - {_short_text(item, 220)}" for item in required)
        safe_commands = payload.get("safe_commands")
        if isinstance(safe_commands, list) and safe_commands:
            lines.append("Safe commands:")
            lines.extend(
                (
                    f"  - {_short_text(item, 220)}"
                    + (
                        "  # phone-safe preview; does not mutate services"
                        if item == "scripts/local-goal model-promotion-apply"
                        else ""
                    )
                )
                for item in safe_commands[:5]
            )
        next_action = payload.get("next_action")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 260)}")
        return "\n".join(lines)
    if contract == "local_node1_goal_model_promotion_decision.v1":
        required = payload.get("next_required_evidence")
        candidate = payload.get("candidate_identity")
        baseline = payload.get("baseline_identity")
        if not isinstance(candidate, dict):
            candidate = {}
        if not isinstance(baseline, dict):
            baseline = {}
        lines = [
            "Ornith/Qwopus promotion decision",
            f"Status: {payload.get('status')}",
            f"Mutates live service: {payload.get('mutates_live_service')}",
            f"Promotion allowed: {payload.get('promotion_allowed')}",
            (
                "Meaning: "
                + _short_text(
                    payload.get("promotion_allowed_meaning")
                    or (
                        "false means the harness will not promote automatically; "
                        "it does not mean Ornith failed the A/B gate."
                        if payload.get("operator_can_choose_promotion")
                        else "false means promotion is blocked until the listed evidence gate is satisfied."
                    ),
                    260,
                )
            ),
            f"Decision required: {payload.get('decision_required')}",
            f"Operator can choose promotion: {payload.get('operator_can_choose_promotion')}",
            (
                "Model identities: "
                f"candidate={candidate.get('label')} "
                f"baseline={baseline.get('label')}"
            ),
            f"Compare verdict: {_short_text(payload.get('compare_verdict'), 220)}",
            f"Reason: {_short_text(payload.get('promotion_blocker'), 280)}",
        ]
        if isinstance(required, list) and required:
            lines.append("Next required evidence:")
            lines.extend(f"  - {_short_text(item, 220)}" for item in required)
        safe_commands = payload.get("safe_commands")
        if isinstance(safe_commands, list) and safe_commands:
            lines.append("Safe commands:")
            for item in safe_commands[:5]:
                suffix = (
                    "  # phone-safe preview; does not mutate services"
                    if item == "scripts/local-goal model-promotion-apply"
                    else ""
                )
                lines.append(f"  - {_short_text(item, 220)}{suffix}")
        next_action = payload.get("next_action")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 280)}")
        phone_preview = payload.get("phone_safe_preview")
        if phone_preview:
            lines.append(f"Phone-safe preview: {_short_text(phone_preview, 260)}")
        terminal_only = payload.get("terminal_only_mutation")
        if terminal_only:
            lines.append(f"Terminal-only mutation: {_short_text(terminal_only, 260)}")
        approval_preview = payload.get("approval_preview_command")
        if approval_preview:
            lines.append(f"Approval preview: {_short_text(approval_preview, 220)}")
        terminal_approval = payload.get("terminal_approval_command")
        if terminal_approval:
            lines.append(
                f"Terminal-only mutation command: {_short_text(terminal_approval, 260)}"
            )
        return "\n".join(lines)
    if contract == "local_node1_goal_model_promotion_plan.v1":
        commands = payload.get("write_and_persist_commands")
        restart = payload.get("restart_validation_commands")
        rollback = payload.get("rollback_commands")
        lines = [
            "Ornith durable promotion plan",
            f"Status: {payload.get('status')}",
            f"Mutates live service: {payload.get('mutates_live_service')}",
            f"Durable drop-in: {_short_text(payload.get('durable_dropin'), 220)}",
            f"Temporary drop-in: {_short_text(payload.get('temporary_dropin'), 220)}",
        ]
        precondition = payload.get("operator_precondition")
        if precondition:
            lines.append(f"Precondition: {_short_text(precondition, 260)}")
        risk = payload.get("risk")
        if risk:
            lines.append(f"Risk: {_short_text(risk, 260)}")
        if any(
            isinstance(section, list) and section
            for section in (commands, restart, rollback)
        ):
            lines.append(
                "Command sections: terminal-only instructions; viewing this plan does not mutate services."
            )
        if isinstance(commands, list) and commands:
            lines.append(f"First command: {_short_text(commands[0], 260)}")
        if isinstance(restart, list):
            lines.append(f"Restart validation commands: {len(restart)}")
        if isinstance(rollback, list):
            lines.append(f"Rollback commands: {len(rollback)}")
        return "\n".join(lines)
    if contract == "local_node1_goal_model_promotion_apply.v1":
        blockers = payload.get("blockers")
        approval = payload.get("approval_packet")
        if not isinstance(approval, dict):
            approval = {}
        lines = [
            "Ornith durable promotion apply",
            f"Status: {payload.get('status')}",
            f"Ready to execute: {payload.get('ready_to_execute')}",
            f"Mutates live service: {payload.get('mutates_live_service')}",
            f"Would mutate live service: {payload.get('would_mutate_live_service')}",
            f"Executed: {payload.get('executed')}",
            f"Required confirm: {payload.get('required_confirm')}",
            f"Durable drop-in: {_short_text(payload.get('durable_dropin'), 220)}",
        ]
        execute_command = approval.get("execute_command")
        if execute_command:
            lines.append(
                f"Terminal-only mutation command: {_short_text(execute_command, 260)}"
            )
        risk = payload.get("risk")
        if risk:
            lines.append(f"Risk: {_short_text(risk, 260)}")
        if isinstance(blockers, list) and blockers:
            lines.append("Blockers:")
            lines.extend(f"  - {_short_text(item, 220)}" for item in blockers[:5])
        next_action = payload.get("next_action")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 280)}")
        return "\n".join(lines)
    if contract == "local_node1_goal_model_promotion_verify.v1":
        health = payload.get("health")
        if not isinstance(health, dict):
            health = {}
        profile_checks = payload.get("profile_checks")
        if not isinstance(profile_checks, dict):
            profile_checks = {}
        temporary_profile_checks = payload.get("temporary_profile_checks")
        if not isinstance(temporary_profile_checks, dict):
            temporary_profile_checks = {}
        lines = [
            "Ornith promotion verify",
            f"Status: {payload.get('status')}",
            f"OK: {payload.get('ok')}",
            f"Mutates live service: {payload.get('mutates_live_service')}",
            f"Live service: {payload.get('current_service_live')}",
            f"Current model is Ornith: {payload.get('current_model_is_candidate')}",
            f"Durability: {_short_text(payload.get('durability_status'), 180)}",
            f"Durable drop-in ok: {payload.get('durable_dropin_ok')}",
            f"Temporary drop-in removed: {payload.get('temporary_removed')}",
            f"Health: {health.get('ok')}",
            f"Temporary profile ok: {payload.get('temporary_profile_ok')}",
            f"Durable profile ok: {payload.get('profile_ok')}",
        ]
        temporary_gaps = [
            name for name, ok in temporary_profile_checks.items() if not ok
        ]
        if temporary_gaps:
            lines.append("Temporary profile gaps:")
            lines.extend(f"  - {_short_text(item, 140)}" for item in temporary_gaps[:5])
        gaps = [name for name, ok in profile_checks.items() if not ok]
        if gaps:
            lines.append("Durable profile gaps:")
            lines.extend(f"  - {_short_text(item, 140)}" for item in gaps[:5])
        next_action = payload.get("next_action")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 280)}")
        terminal_command = payload.get("terminal_approval_command")
        if terminal_command:
            lines.append(
                f"Terminal-only mutation command: {_short_text(terminal_command, 260)}"
            )
        return "\n".join(lines)
    if contract == "local_node1_goal_model_promotion_waiver.v1":
        lines = [
            "Ornith/Qwopus promotion waiver",
            f"Status: {payload.get('status')}",
            f"Mutates live service: {payload.get('mutates_live_service')}",
            f"Continue developing with Ornith: {payload.get('continue_developing_with_ornith')}",
            f"Durable promotion allowed: {payload.get('durable_promotion_allowed')}",
            f"Candidate active: {payload.get('candidate_active')}",
            f"Service live: {payload.get('service_live')}",
            f"Compare verdict: {_short_text(payload.get('compare_verdict'), 220)}",
            f"Qwopus completion gap is known blocker: {payload.get('qwopus_completion_gap_is_known_blocker')}",
        ]
        waived = payload.get("waived_requirement")
        if waived:
            lines.append(f"Waived requirement: {_short_text(waived, 240)}")
        risk = payload.get("risk")
        if risk:
            lines.append(f"Risk: {_short_text(risk, 260)}")
        next_action = payload.get("next_action")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 300)}")
        return "\n".join(lines)
    if contract == "local_node1_goal_qwopus_completion_risk.v1":
        required = payload.get("next_required_evidence")
        blockers = payload.get("baseline_blockers")
        window_blockers = payload.get("window_blockers")
        historical = payload.get("historical_failure")
        if not isinstance(historical, dict):
            historical = {}
        lines = [
            "Qwopus completion risk",
            f"Status: {payload.get('status')}",
            f"Mutates live service: {payload.get('mutates_live_service')}",
            f"Historical failure: {_short_text(historical.get('summary'), 280)}",
            f"Compare verdict: {_short_text(payload.get('compare_verdict'), 220)}",
            f"Completion evidence missing: {payload.get('completion_evidence_missing')}",
            f"Baseline can start now: {payload.get('baseline_can_start_now')}",
            f"Ready to open window: {payload.get('ready_to_open_qwopus_window')}",
        ]
        if isinstance(required, list) and required:
            lines.append("Next required evidence:")
            lines.extend(f"  - {_short_text(item, 220)}" for item in required)
        if isinstance(blockers, list) and blockers:
            lines.append("Baseline blockers:")
            lines.extend(f"  - {_short_text(item, 220)}" for item in blockers)
        if isinstance(window_blockers, list) and window_blockers:
            lines.append("Window blockers:")
            lines.extend(f"  - {_short_text(item, 220)}" for item in window_blockers)
        next_action = payload.get("next_action")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 260)}")
        next_command = payload.get("next_command")
        if next_command:
            lines.append(f"Command: {_short_text(next_command, 260)}")
        approval_command = payload.get("terminal_approval_command")
        if approval_command and approval_command != next_command:
            lines.append(
                f"Terminal-only mutation command: {_short_text(approval_command, 260)}"
            )
        return "\n".join(lines)
    if contract == "local_node1_goal_model_decision_packet_bundle.v1":
        packets = payload.get("packets")
        packet_count = payload.get("packet_count")
        if packet_count is None and isinstance(packets, list):
            packet_count = len(packets)
        lines = [
            "Local Goal Model Decision Packet Bundle",
            f"Status: {'written' if payload.get('ok') else 'partial'}",
            f"Mutates live service: {str(payload.get('mutates_live_service')).lower()}",
            f"Packets: {packet_count}",
            f"Manifest: {_short_text(payload.get('manifest_path'), 220)}",
        ]
        if isinstance(packets, list):
            for packet in packets[:6]:
                if isinstance(packet, dict):
                    status = packet.get("status") or "unknown"
                    ok = "ok" if packet.get("ok") else "failed"
                    lines.append(
                        f"  - {_short_text(packet.get('name'), 80)}: {ok} status={_short_text(status, 120)}"
                    )
            if len(packets) > 6:
                lines.append(f"  - ... {len(packets) - 6} more packet(s)")
        next_action = payload.get("next_action")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 260)}")
        return "\n".join(lines)
    if contract == "local_node1_goal_model_service_window_check.v1":
        blockers = payload.get("blockers")
        warnings = payload.get("warnings")
        lines = [
            "Qwopus service window check",
            f"Status: {payload.get('status')}",
            f"Ready to open window: {payload.get('ready_to_open_qwopus_window')}",
            f"Requires approval: {payload.get('requires_explicit_operator_approval')}",
            f"Service live: {payload.get('service_live')}",
            f"Local-goal lane free: {payload.get('local_goal_lane_free')}",
            f"Ornith active: {payload.get('candidate_active')}",
            f"Qwopus baseline exists: {payload.get('baseline_exists')}",
            f"Drop-in exists: {payload.get('dropin_exists')}",
        ]
        if isinstance(blockers, list) and blockers:
            lines.append("Blockers:")
            lines.extend(f"  - {_short_text(item, 220)}" for item in blockers)
        if isinstance(warnings, list) and warnings:
            lines.append("Warnings:")
            lines.extend(f"  - {_short_text(item, 220)}" for item in warnings)
        next_action = payload.get("next_action")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 260)}")
        return "\n".join(lines)
    if contract == "local_node1_goal_model_service_window_open.v1":
        capture_commands = payload.get("capture_commands")
        restore_commands = payload.get("restore_commands")
        lines = [
            "Qwopus service window open",
            f"Status: {payload.get('status')}",
            f"Ready to execute: {payload.get('ready_to_execute')}",
            f"Mutates live service: {payload.get('mutates_live_service')}",
            f"Would mutate live service: {payload.get('would_mutate_live_service')}",
            f"Executed: {payload.get('executed')}",
            f"Required confirm: {_short_text(payload.get('required_confirm'), 120)}",
            f"Completion risk status: {payload.get('completion_risk_status')}",
            f"Completion evidence missing: {payload.get('completion_evidence_missing')}",
        ]
        if isinstance(capture_commands, list):
            lines.append(f"Capture commands: {len(capture_commands)}")
        if isinstance(restore_commands, list):
            lines.append(f"Terminal-only restore commands: {len(restore_commands)}")
        approval = payload.get("approval_packet")
        if isinstance(approval, dict):
            execute_command = approval.get("execute_command")
            rollback_command = approval.get("rollback_execute_command")
            start_condition = approval.get("baseline_start_condition")
            post_open = approval.get("post_open_commands")
            lines.append("Approval packet:")
            if execute_command:
                lines.append(
                    f"  terminal-only execute: {_short_text(execute_command, 260)}"
                )
            if rollback_command:
                lines.append(
                    f"  terminal-only rollback execute: {_short_text(rollback_command, 260)}"
                )
            if start_condition:
                lines.append(f"  start condition: {_short_text(start_condition, 260)}")
            if isinstance(post_open, list) and post_open:
                lines.append("  after open:")
                lines.extend(f"    {_short_text(item, 220)}" for item in post_open[:4])
        risk = payload.get("risk")
        if risk:
            lines.append(f"Risk: {_short_text(risk, 260)}")
        next_action = payload.get("next_action")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 260)}")
        return "\n".join(lines)
    if contract == "local_node1_goal_model_service_window_restore.v1":
        restore_commands = payload.get("restore_commands")
        blockers = payload.get("blockers")
        lines = [
            "Qwopus service window restore",
            f"Status: {payload.get('status')}",
            f"Ready to execute: {payload.get('ready_to_execute')}",
            f"Mutates live service: {payload.get('mutates_live_service')}",
            f"Would mutate live service: {payload.get('would_mutate_live_service')}",
            f"Executed: {payload.get('executed')}",
            f"Required confirm: {_short_text(payload.get('required_confirm'), 120)}",
            f"Candidate exists: {payload.get('candidate_exists')}",
            f"Local-goal lane free: {payload.get('local_goal_lane_free')}",
            f"Service live: {payload.get('service_live')}",
        ]
        if isinstance(restore_commands, list):
            lines.append(f"Terminal-only restore commands: {len(restore_commands)}")
        if isinstance(blockers, list) and blockers:
            lines.append("Blockers:")
            lines.extend(f"  - {_short_text(item, 220)}" for item in blockers)
        risk = payload.get("risk")
        if risk:
            lines.append(f"Risk: {_short_text(risk, 260)}")
        next_action = payload.get("next_action")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 260)}")
        return "\n".join(lines)
    if contract == "local_node1_goal_model_service_window_next.v1":
        blockers = payload.get("blockers")
        lines = [
            "Qwopus service window next",
            f"Status: {payload.get('status')}",
            f"Mutates live service: {payload.get('mutates_live_service')}",
            f"Eval next status: {payload.get('eval_next_status')}",
            f"Baseline can start now: {payload.get('baseline_can_start_now')}",
            f"Baseline active: {payload.get('baseline_active')}",
            f"Ornith active: {payload.get('candidate_active')}",
            f"Ready to open window: {payload.get('ready_to_open_qwopus_window')}",
            f"Completion risk status: {payload.get('completion_risk_status')}",
            f"Completion baseline is remaining: {payload.get('completion_baseline_is_remaining')}",
            f"Restore ready: {payload.get('restore_ready')}",
        ]
        if isinstance(blockers, list) and blockers:
            lines.append("Blockers:")
            lines.extend(f"  - {_short_text(item, 220)}" for item in blockers)
        next_action = payload.get("next_action")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 260)}")
        next_command = payload.get("next_command")
        if next_command:
            lines.append(f"Command: {_short_text(next_command, 260)}")
        approval_command = payload.get("terminal_approval_command")
        if approval_command and approval_command != next_command:
            lines.append(
                f"Terminal-only mutation command: {_short_text(approval_command, 260)}"
            )
        return "\n".join(lines)
    if contract == "local_node1_goal_current_truth.v1":
        dirty = payload.get("dirty_operator_summary")
        if not isinstance(dirty, dict):
            dirty = {}
        queue = payload.get("queue")
        if not isinstance(queue, dict):
            queue = {}
        integration = payload.get("integration_audit_latest")
        if not isinstance(integration, dict):
            integration = {}
        model_decision = payload.get("model_promotion_decision")
        if not isinstance(model_decision, dict):
            model_decision = {}
        commands = payload.get("commands")
        if not isinstance(commands, dict):
            commands = {}
        lines = [
            "Local Goal Current Truth",
            f"Status: {payload.get('classification') or 'unknown'}",
            f"Running: {payload.get('running')}",
            f"Accepted: {payload.get('accepted')}",
        ]
        capacity = payload.get("node1_capacity")
        if not isinstance(capacity, dict):
            capacity = {}
        lane_free = payload.get(
            "local_goal_lane_free", capacity.get("local_goal_lane_free")
        )
        vllm_idle = payload.get("node1_vllm_idle", capacity.get("node1_vllm_idle"))
        vllm_other = payload.get(
            "node1_vllm_has_other_activity",
            capacity.get("node1_vllm_has_other_activity"),
        )
        vllm_running = payload.get("node1_vllm_running", capacity.get("vllm_running"))
        vllm_waiting = payload.get("node1_vllm_waiting", capacity.get("vllm_waiting"))
        start_may_wait = payload.get("start_may_wait", capacity.get("start_may_wait"))
        start_guidance = payload.get("start_guidance") or capacity.get("start_guidance")
        lines.extend(
            [
                f"Node1 lane free: {lane_free}",
                (
                    "Node1 vLLM idle: "
                    f"{vllm_idle} other_activity={vllm_other} "
                    f"running={vllm_running if vllm_running is not None else 'unknown'} "
                    f"waiting={vllm_waiting if vllm_waiting is not None else 'unknown'}"
                ),
                f"Start may wait: {bool(start_may_wait)}",
            ]
        )
        if start_guidance:
            lines.append(f"Start guidance: {_short_text(start_guidance, 260)}")
        lines.extend(
            [
                (
                    "Node1 idle legacy: "
                    f"{payload.get('node1_is_idle')} "
                    f"({payload.get('node1_is_idle_scope') or capacity.get('node1_is_idle_scope') or 'legacy lane availability'})"
                ),
                f"Objective: {_short_text(payload.get('objective') or 'none', 220)}",
                f"Recommended action: {_short_text(payload.get('recommended_action') or 'none', 260)}",
                f"Queue: running={queue.get('running') or 0} queued={queue.get('queued') or 0}",
                f"Dirty blocks acceptance: {dirty.get('blocks_acceptance')}",
                f"Dirty blocking count: {dirty.get('blocking_count')}",
                f"Dirty note: {_short_text(dirty.get('note') or 'none', 260)}",
                f"Integration audit: ok={integration.get('ok')} status={integration.get('status')} missing={integration.get('missing') or []}",
            ]
        )
        if model_decision:
            lines.append(
                "Model promotion decision: "
                f"status={model_decision.get('status')} "
                "operator_can_choose_promotion="
                f"{_model_operator_can_choose_promotion(model_decision)}"
            )
            meaning = model_decision.get("promotion_allowed_meaning")
            if meaning:
                lines.append(f"Model promotion meaning: {_short_text(meaning, 260)}")
            terminal_command = model_decision.get("terminal_approval_command")
            if terminal_command:
                lines.append(
                    f"Model promotion terminal command: {_short_text(terminal_command, 260)}"
                )
        report = payload.get("report_path")
        if report:
            lines.append(f"Report: {_short_text(report, 260)}")
        for name in (
            "shortcuts",
            "guide",
            "progress",
            "queue_summary",
            "last_run",
            "last_goal_changed_files",
            "accepted_evidence",
            "verification_passed",
            "dirty_acceptance",
            "brief",
            "next_proof",
            "completion_summary",
            "glm_supervisor",
            "soak_plan",
            "ready_review",
            "can_accept",
            "model_status",
            "model_eval_next",
            "model_promotion_decision",
            "model_promotion_plan",
            "model_promotion_apply_preview",
            "model_promotion_verify",
            "terminal_only_model_promotion_apply_execute",
            "model_promotion_waiver",
            "qwopus_completion_risk",
            "qwopus_safe_harness",
            "qwopus_192k_seq4",
            "model_decision_packet",
            "qwopus_window_check",
            "qwopus_window_next",
            "qwopus_window_open_preview",
            "qwopus_window_restore_preview",
            "telegram_alias_progress",
            "telegram_alias_can_accept",
            "telegram_alias_model_promotion_apply_preview",
        ):
            command = commands.get(name)
            if isinstance(command, str):
                lines.append(f"{name}: {_short_text(command, 260)}")
        return "\n".join(lines)
    human = human_supervisor_summary(payload)
    if human:
        return human
    lines: list[str] = []
    contract = payload.get("contract")
    status = payload.get("status")
    ok = payload.get("ok")
    classification = payload.get("classification")
    phase = payload.get("phase")
    action = payload.get("recommended_action")
    state = status or classification or phase or ("ok" if ok is True else "unknown")
    reason = action or payload.get("reason") or payload.get("detail") or "Hermes returned a local-goal result."
    action_needed = "No"
    exact_phrase = "none"
    if str(state).lower() in {"failed", "blocked", "stuck", "not_accepted"} or ok is False:
        action_needed = "Yes"
        exact_phrase = "supervise local harness"
    elif str(state).lower() in {"reviewing", "needs_review", "waiting_for_review"}:
        state = "waiting for review"
        reason = "Worker stopped and says it is done. Hermes watcher will review it automatically before you start anything new."
    lines.extend(
        [
            f"Status: {state}",
            f"What is happening now: {_short_text(reason, 260)}",
            f"Does Michael need to do anything? {action_needed}",
            f"Exact phrase to send Hermes: {exact_phrase}",
        ]
    )
    missing = payload.get("missing")
    if isinstance(missing, list):
        missing_text = ", ".join(str(item) for item in missing) or "none"
        lines.append(f"Missing: {missing_text}")
    if contract:
        lines.append(f"Details: contract={contract}")
    if status is not None or ok is not None:
        lines.append(f"Details: status={status} ok={ok}")
    if classification or phase:
        lines.append(f"Details: classification={classification} phase={phase}")
    if action:
        lines.append(f"Details: action={_short_text(action, 260)}")
    active_goal = payload.get("active_goal")
    if isinstance(active_goal, dict):
        objective = active_goal.get("objective")
        if objective:
            lines.append(f"Goal: {_short_text(objective, 220)}")
        lines.append(
            "Details: tmux_running="
            f"{active_goal.get('tmux_running')} "
            f"awaiting_review={active_goal.get('awaiting_review')} "
            f"accepted={active_goal.get('accepted')}"
        )
    current_state = payload.get("current_state")
    if isinstance(current_state, dict):
        lines.append(
            "Node1: free="
            f"{current_state.get('node1_is_free')} "
            f"queue_has_active_work={current_state.get('queue_has_active_work')} "
            f"availability_reason={current_state.get('availability_reason')}"
        )
    lanes = payload.get("lanes")
    if isinstance(lanes, dict):
        parts = []
        for name, lane in lanes.items():
            if isinstance(lane, dict):
                parts.append(
                    f"{name}:{lane.get('classification')}"
                    f"/installed={lane.get('installed')}"
                    f"/available={lane.get('available_now')}"
                    f"/reason={lane.get('availability_reason')}"
                )
        if parts:
            lines.append("Details: lanes=" + ",".join(parts))
    queue = payload.get("queue")
    if isinstance(queue, dict):
        lines.append(
            f"Queue: queued={queue.get('queued')} "
            f"queue_running={queue.get('running')} "
            f"old_failed_start_attempts={queue.get('failed_to_start')} "
            f"queue_failed_label={queue.get('failed_to_start_label')}"
        )
    return "\n".join(lines)


def summarize_dry_run(intent: str, reason: str, cmd: list[str]) -> str:
    """Render a phone-readable route preview without executing anything."""
    if cmd[:1] == ["python3"]:
        action = " ".join(cmd[2:]) if len(cmd) > 2 else "status --json"
    else:
        action = " ".join(cmd[1:]) if len(cmd) > 1 else "status --json"
    mutating_intents = {
        "accept",
        "continue",
        "enqueue",
        "enqueue-cloud",
        "glm-supervisor-start",
        "glm-supervisor-stop",
        "mission-create",
        "mission-resume",
        "mission-stop",
        "nudge",
        "premium-start",
        "quick-start",
        "repair-closeout",
        "repair-marker",
        "start",
        "stop",
    }
    mutates = intent in mutating_intents
    if intent == "glm-supervisor":
        mutates = any(part in cmd for part in ("start", "stop"))
    lines = [
        f"Dry run: would route to {intent}",
        f"Reason: {reason}",
        f"Supervisor action: {action}",
        "Mutates live state: false",
    ]
    if mutates:
        lines.append("Would mutate if executed: true")
    lines.append("Executed: no")
    return "\n".join(lines)


def write_artifacts(
    message: str,
    cmd: list[str],
    intent: str,
    reason: str,
    proc: subprocess.CompletedProcess[str],
    supervisor_payload: dict | None = None,
    summary: str = "",
) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "contract": "local_node1_goal_command.v1",
        "generated_at": now(),
        "message": message,
        "intent": intent,
        "reason": reason,
        "command": cmd,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
        "summary": summary,
        "supervisor_payload": supervisor_payload,
    }
    if intent in {"doctor", "trust-boundary", "brief"} and isinstance(
        supervisor_payload, dict
    ):
        envelope = doctor_envelope_status(supervisor_payload.get("doctor_state"))
        payload.update(envelope)
    STATE_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    REPORT_PATH.write_text(
        "\n".join(
            [
                "# Local Node1 Goal Command",
                "",
                f"- Generated: `{payload['generated_at']}`",
                f"- Intent: `{intent}`",
                f"- Reason: {reason}",
                f"- Return code: `{proc.returncode}`",
                f"- Command: `{' '.join(cmd)}`",
                "",
                "## Output",
                "",
                "```text",
                (summary or proc.stdout or proc.stderr or "").strip()[-4000:],
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parse Hermes local-goal control phrases"
    )
    parser.add_argument("message", nargs="*", help="Operator message to parse")
    parser.add_argument("--goal-file")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and return the supervisor command without executing it or writing artifacts.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    message = " ".join(args.message).strip()
    if not message:
        message = sys.stdin.read().strip()
    if not message:
        message = "status local goal"
    if has_explicit_empty_start_intent(message):
        summary = "ERROR: start requires a non-empty goal after `start local goal:` or `/goal:`."
        if args.json:
            print(
                json.dumps(
                    {
                        "contract": "local_node1_goal_command.v1",
                        "intent": "start",
                        "reason": "empty goal rejected",
                        "command": [],
                        "returncode": 2,
                        "summary": summary,
                        "supervisor_payload": None,
                        "stdout": "",
                        "stderr": summary,
                        "state_path": str(STATE_PATH),
                        "report_path": str(REPORT_PATH),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(summary, file=sys.stderr)
        return 2

    cmd, intent, reason = parse_command(message, goal_file=args.goal_file)
    dry_run = args.dry_run or has_dry_run_intent(message)
    if dry_run:
        summary = summarize_dry_run(intent, reason, cmd)
        payload = {
            "contract": "local_node1_goal_command.v1",
            "dry_run": True,
            "intent": intent,
            "reason": reason,
            "command": cmd,
            "returncode": 0,
            "summary": summary,
            "supervisor_payload": None,
            "stdout": "",
            "stderr": "",
            "state_path": str(STATE_PATH),
            "report_path": str(REPORT_PATH),
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(summary)
        return 0

    if intent in {"doctor", "trust-boundary", "brief"}:
        proc, supervisor_payload, summary = run_doctor_command(
            cmd, trust_boundary=intent == "trust-boundary", brief=intent == "brief"
        )
    else:
        proc = run_command(cmd)
        supervisor_payload = parse_supervisor_payload(proc.stdout)
        supervisor_payload = attach_current_truth_model_decision(supervisor_payload)
        summary = summarize_supervisor_payload(supervisor_payload)
        unsafe_artifact_output = looks_like_chat_unsafe_artifact_output(proc.stdout)
        if not summary and unsafe_artifact_output:
            summary = summarize_chat_unsafe_artifact_output(
                proc.stdout,
                intent=intent,
                cmd=cmd,
            )
    try:
        write_artifacts(
            message,
            cmd,
            intent,
            reason,
            proc,
            supervisor_payload=supervisor_payload,
            summary=summary,
        )
    except (OSError, TimeoutError) as exc:
        artifact_error = f"ERROR: command artifact write failed gracefully: {exc}"
        if args.json:
            print(
                json.dumps(
                    {
                        "contract": "local_node1_goal_command.v1",
                        "intent": intent,
                        "reason": reason,
                        "command": chat_safe_command_tokens(cmd),
                        "returncode": proc.returncode or 1,
                        "summary": artifact_error,
                        "doctor_state": None,
                        "supervisor_payload": supervisor_payload,
                        "stdout": chat_safe_command_output(
                            proc.stdout, intent=intent, cmd=cmd
                        ),
                        "stderr": artifact_error,
                        "state_path": str(STATE_PATH),
                        "report_path": str(REPORT_PATH),
                        "artifact_write_error": str(exc),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(artifact_error, file=sys.stderr)
        return proc.returncode or 1

    if args.json:
        doctor_state = (
            doctor_structured_state(supervisor_payload)
            if intent in {"doctor", "trust-boundary", "brief"}
            else None
        )
        envelope = (
            doctor_envelope_status(doctor_state)
            if intent in {"doctor", "trust-boundary", "brief"}
            else {}
        )
        chat_stdout = (
            chat_safe_command_output(proc.stdout, intent=intent, cmd=cmd)
            if "unsafe_artifact_output" in locals() and unsafe_artifact_output
            else proc.stdout
        )
        chat_stderr = (
            chat_safe_command_output(proc.stderr, intent=intent, cmd=cmd)
            if proc.stderr
            and "unsafe_artifact_output" in locals()
            and looks_like_chat_unsafe_artifact_output(proc.stderr)
            else proc.stderr
        )
        artifact_fields = (
            {
                "state_path": "written",
                "report_path": "written",
                "artifact_paths_suppressed": True,
            }
            if "unsafe_artifact_output" in locals() and unsafe_artifact_output
            else {
                "state_path": str(STATE_PATH),
                "report_path": str(REPORT_PATH),
            }
        )
        chat_command = (
            chat_safe_command_tokens(cmd)
            if "unsafe_artifact_output" in locals() and unsafe_artifact_output
            else cmd
        )
        print(
            json.dumps(
                {
                    "contract": "local_node1_goal_command.v1",
                    "intent": intent,
                    "reason": reason,
                    "command": chat_command,
                    "returncode": proc.returncode,
                    **envelope,
                    "summary": summary,
                    "doctor_state": doctor_state,
                    "supervisor_payload": supervisor_payload,
                    "stdout": chat_stdout,
                    "stderr": chat_stderr,
                    **artifact_fields,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        sys.stdout.write((summary or proc.stdout).rstrip() + "\n")
        sys.stderr.write(proc.stderr)
        if intent not in NO_FOOTER_INTENTS:
            if "unsafe_artifact_output" in locals() and unsafe_artifact_output:
                print(f"intent={intent}")
                print("command_artifacts=written")
            else:
                print(f"intent={intent}")
                print(f"command_state={STATE_PATH}")
                print(f"command_report={REPORT_PATH}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
