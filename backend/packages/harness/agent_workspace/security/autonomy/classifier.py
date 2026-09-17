from __future__ import annotations

import re

from .profiles import ActionRiskLevel


class ActionRiskClassifier:
    """
    Classifies proposed actions, tool invocations, or shell command strings
    into ActionRiskLevel (LOW_SAFE, MEDIUM_RISK, HIGH_RISK_DESTRUCTIVE).
    """

    DANGEROUS_COMMAND_PATTERNS = [
        r"\brm\s+-[rf]{1,2}\b",
        r"\bdrop\s+(database|table)\b",
        r"\bpush\s+.*--force\b",
        r"\bformat\s+[a-z]:\b",
        r"\bchmod\s+777\b",
        r"\bchown\s+root\b",
        r"\bkill\s+-9\s+1\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bcurl\s+.*\|\s*(bash|sh)\b",
    ]

    READ_ONLY_TOOLS = {
        "view_file",
        "list_dir",
        "grep_search",
        "find_by_name",
        "read_url_content",
        "search_web",
        "session_search_tool",
        "list_uploaded_files",
        "list_background_tasks",
        "batch_status",
    }

    WRITE_EDIT_TOOLS = {
        "write_to_file",
        "replace_file_content",
        "hashline_edit",
        "ast_grep_rewrite",
    }

    def classify_tool_call(self, tool_name: str, args: dict | None = None) -> ActionRiskLevel:
        if tool_name in self.READ_ONLY_TOOLS:
            return ActionRiskLevel.LOW_SAFE

        if tool_name == "run_command":
            cmd = args.get("CommandLine", "") if args else ""
            return self.classify_shell_command(cmd)

        if tool_name in self.WRITE_EDIT_TOOLS:
            return ActionRiskLevel.MEDIUM_RISK

        return ActionRiskLevel.MEDIUM_RISK

    def classify_shell_command(self, command: str) -> ActionRiskLevel:
        cmd_clean = command.strip().lower()

        # Check for high-risk destructive patterns
        for pattern in self.DANGEROUS_COMMAND_PATTERNS:
            if re.search(pattern, cmd_clean):
                return ActionRiskLevel.HIGH_RISK_DESTRUCTIVE

        # Check for standard read/safe shell commands
        safe_prefixes = [
            "ls", "dir", "cat", "echo", "pwd", "git status", "git diff", "git log",
            "pytest", "python -m pytest", "cargo test", "npm test", "node -v", "python --version"
        ]
        if any(cmd_clean.startswith(p) for p in safe_prefixes):
            return ActionRiskLevel.LOW_SAFE

        return ActionRiskLevel.MEDIUM_RISK
