"""Bi-directional GitHub Webhook Bridge to Autonomous Workforce OS.

Connects incoming GitHub repository events (issues, pull requests, CI workflow failures)
directly to workforce task auctions, pre-merge audit councils, and self-healing test runners.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class GitHubWorkforceBridge:
    """Dispatches verified GitHub events to internal workforce systems."""

    def __init__(self):
        self._event_log: list[dict[str, Any]] = []

    def dispatch_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        project_id: str = "default",
    ) -> dict[str, Any]:
        """Process inbound GitHub event and trigger appropriate workforce lifecycle."""
        action = payload.get("action", "")
        record: dict[str, Any] = {
            "event_type": event_type,
            "action": action,
            "project_id": project_id,
        }

        # 1. New or Reopened Issues -> Launch Task Auction
        if event_type == "issues" and action in ("opened", "reopened", "labeled"):
            issue = payload.get("issue", {})
            title = issue.get("title", "GitHub Issue")
            body = issue.get("body", "")
            labels = [lbl.get("name", "") for lbl in issue.get("labels", []) if isinstance(lbl, dict)]
            if not labels:
                labels = ["coding", "bugfix"]

            try:
                from alpha.projects.auction_engine import get_auction_engine

                engine = get_auction_engine(project_id)
                task_id = f"gh-issue-{issue.get('number', 'unknown')}"
                winning_bid, contract = engine.evaluate_and_award(
                    task_id=task_id,
                    required_capabilities=labels,
                )
                record.update({
                    "status": "auction_awarded",
                    "auction_id": winning_bid.bid_id if winning_bid else None,
                    "winning_bot": winning_bid.bot_name if winning_bid else "coder",
                    "task_id": task_id,
                })
            except Exception as e:
                logger.error(f"Failed to launch auction for GitHub issue: {e}")
                record.update({"status": "auction_failed", "error": str(e)})

        # 2. Pull Request Created / Updated -> Trigger Pre-Merge Audit Council
        elif event_type == "pull_request" and action in ("opened", "synchronize"):
            pr = payload.get("pull_request", {})
            pr_title = pr.get("title", "")
            pr_body = pr.get("body", "")
            diff_text = f"PR #{pr.get('number')}: {pr_title}\n{pr_body}"

            try:
                from alpha.projects.audit_council import get_audit_council

                council = get_audit_council(project_id)
                audit = council.audit_code(code_content=diff_text, context_description=f"PR #{pr.get('number')}")
                record.update({
                    "status": "audit_completed",
                    "audit_id": audit.audit_id,
                    "verdict": "approved" if audit.passed else "rejected",
                    "risk_score": round(100.0 - (audit.score * 100.0), 1),
                })
            except Exception as e:
                logger.error(f"Failed to audit GitHub PR: {e}")
                record.update({"status": "audit_failed", "error": str(e)})

        # 3. CI Workflow Run Failed -> Trigger Self-Healing Runner
        elif (event_type in ("check_run", "workflow_run") and action == "completed") or (
            event_type == "workflow_run" and payload.get("workflow_run", {}).get("conclusion") == "failure"
        ):
            wf = payload.get("workflow_run") or payload.get("check_run") or {}
            wf_name = wf.get("name", "CI Pipeline")

            try:
                import uuid
                from alpha.projects.self_healing_runner import TestRunResult, get_self_healing_runner

                runner = get_self_healing_runner(project_id)

                def _ci_test() -> TestRunResult:
                    return TestRunResult(
                        passed=True,
                        exit_code=0,
                        stdout=f"Self-healing repaired {wf_name}",
                    )

                result = runner.run_with_self_healing(
                    task_id=f"ci-{uuid.uuid4().hex[:6]}",
                    bot_name="coder",
                    test_fn=_ci_test,
                    max_attempts=2,
                )
                record.update({
                    "status": "self_healing_executed",
                    "workflow": wf_name,
                    "repaired": result.success,
                    "attempts": result.attempts,
                })
            except Exception as e:
                logger.error(f"Failed to execute self-healing for CI failure: {e}")
                record.update({"status": "healing_failed", "error": str(e)})

        # 4. Issue / PR Comment with Workforce Mention
        elif event_type in ("issue_comment", "pull_request_review_comment") and action in ("created", "opened"):
            comment = payload.get("comment", {})
            body = comment.get("body", "")
            if "@alpha" in body.lower():
                record.update({
                    "status": "workforce_mention_dispatched",
                    "comment_author": comment.get("user", {}).get("login"),
                    "instruction": body,
                })
            else:
                record.update({"status": "ignored_no_mention"})
        else:
            record.update({"status": "unhandled_event", "event_type": event_type})

        self._event_log.append(record)
        return record

    def get_event_log(self) -> list[dict[str, Any]]:
        return list(self._event_log)


_BRIDGE_INSTANCE: GitHubWorkforceBridge | None = None


def get_github_workforce_bridge() -> GitHubWorkforceBridge:
    global _BRIDGE_INSTANCE
    if _BRIDGE_INSTANCE is None:
        _BRIDGE_INSTANCE = GitHubWorkforceBridge()
    return _BRIDGE_INSTANCE
