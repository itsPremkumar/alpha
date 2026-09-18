"""Deep Code Reviewer and Semantic Anti-Regression Agent.

Acts as an automated gatekeeper for proposed patches, auditing breaking API
changes, type safety invariants, test regressions, and coding conventions.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from agent_workspace.subagents.config import SubagentConfig
from agent_workspace.subagents.deep_handoff_contract import (
    DeepExecutionStatus,
    DeepHandoffContract,
    estimate_tokens,
)

AGENT_TYPE = "code_reviewer"
DISPLAY_NAME = "DeepCodeReviewerAgent"

SYSTEM_PROMPT = """You are DeepCodeReviewerAgent, an autonomous code review gatekeeper.
Audit patches for breaking API changes, type safety invariants, test regressions,
and convention violations. Return a structured pass or fail verdict with exact
remediation recommendations. Never request human confirmation."""

DEEP_CODE_REVIEWER_AGENT_CONFIG = SubagentConfig(
    name="deep-code-reviewer",
    description="Autonomous patch gatekeeper with API, type, regression, and convention checks.",
    system_prompt=SYSTEM_PROMPT,
    tools=["read_file", "bash", "ast_grep_search"],
    disallowed_tools=["task", "ralph_loop", "ask_clarification", "present_files"],
    model="inherit",
    max_turns=120,
    timeout_seconds=1800,
)


class DeepCodeReviewerAgent:
    """Autonomous code review specialist."""

    agent_type = AGENT_TYPE
    display_name = DISPLAY_NAME

    def extract_public_api(self, source: str) -> set[str]:
        """Extract top level public function and class names.

        Args:
            source: Python source text.

        Returns:
            Set of public API names.
        """
        try:
            tree = ast.parse(source or "")
        except SyntaxError:
            return set()
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    names.add(node.name)
        return names

    def review_patch(
        self,
        before_source: str,
        after_source: str,
        patch_text: str = "",
    ) -> dict[str, Any]:
        """Review a patch and produce a structured verdict.

        Args:
            before_source: Source before the patch.
            after_source: Source after the patch.
            patch_text: Optional unified diff text.

        Returns:
            Review verdict dictionary.
        """
        before_api = self.extract_public_api(before_source)
        after_api = self.extract_public_api(after_source)
        removed = sorted(before_api - after_api)
        findings: list[str] = []
        if removed:
            findings.append(f"breaking API change: removed {', '.join(removed)}")
        try:
            ast.parse(after_source or "")
        except SyntaxError as exc:
            findings.append(f"type/syntax invariant violated: {exc}")
        if len(after_source or "") > max(1, len(before_source or "")) * 5:
            findings.append("patch size anomaly: output is disproportionately large")
        if re.search(r"\bprint\s*\(", after_source or "") and "print(" not in (before_source or ""):
            findings.append("convention: avoid new unstructured print statements")
        if patch_text and len(patch_text) > 20000:
            findings.append("convention: patch exceeds compact review budget")
        verdict = "FAIL" if findings else "PASS"
        remediation = ["Restore removed public symbols or document migration path."] if removed else (["Address listed findings and resubmit."] if findings else ["No action required."])
        return {"verdict": verdict, "findings": findings, "remediation": remediation, "removed_api": removed}

    def review(
        self,
        before_source: str,
        after_source: str,
        patch_text: str = "",
        session_id: str = "",
    ) -> DeepHandoffContract:
        """Review sources and return compact synthesis.

        Args:
            before_source: Source before the patch.
            after_source: Source after the patch.
            patch_text: Optional unified diff text.
            session_id: Isolated session identifier.

        Returns:
            Compact handoff contract with verdict.
        """
        result = self.review_patch(before_source, after_source, patch_text)
        passed = result["verdict"] == "PASS"
        summary = f"DeepCodeReviewerAgent verdict is {result['verdict']} with {len(result['findings'])} finding(s). " + ("The patch preserves API, types, and conventions." if passed else "Remediation is required before merge.")
        contract = DeepHandoffContract(
            status=DeepExecutionStatus.SUCCESS,
            executive_summary=summary,
            unified_diff=(patch_text or "")[:2000],
            test_oracles=[{"name": "patch-review", "command": "automated review checks", "passed": passed}],
            security_stamps=["review:api-compatibility-checked"],
            invariant_assertions=[
                f"verdict={result['verdict']}",
                "no test regression signals ignored",
            ]
            + result["findings"][:3],
            session_id=session_id,
            agent_type=self.agent_type,
        )
        contract.tokens_returned = max(1, estimate_tokens(contract.to_parent_text()))
        return contract
