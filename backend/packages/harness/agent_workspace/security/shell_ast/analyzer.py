"""ShellASTSecurityAnalyzer: Performs deep semantic inspection and risk scoring of shell ASTs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent_workspace.security.shell_ast.ast_nodes import (
    ASTNode,
    CommandNode,
    CompoundNode,
    NodeType,
    PipelineNode,
    SubshellNode,
)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKED = "blocked"


@dataclass
class SecurityViolation:
    rule_id: str
    risk_level: RiskLevel
    message: str
    command_snippet: str
    remediation: str | None = None


@dataclass
class AnalysisReport:
    risk_level: RiskLevel
    violations: list[SecurityViolation] = field(default_factory=list)
    ast_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def is_safe(self) -> bool:
        return self.risk_level in {RiskLevel.LOW, RiskLevel.MEDIUM}

    @property
    def is_blocked(self) -> bool:
        return self.risk_level == RiskLevel.BLOCKED


class ShellASTSecurityAnalyzer:
    """Walks the Shell AST, analyzing commands, pipelines, and subshells for security hazards."""

    FETCH_COMMANDS = {"curl", "wget", "fetch", "http", "aria2c"}
    INTERPRETER_COMMANDS = {"bash", "sh", "zsh", "python", "python3", "perl", "ruby", "node"}
    DESTRUCTIVE_ROOT_PATHS = {"/", "/*", "~", "~/*", "/etc", "/usr", "/var", "/bin", "/sbin", "C:\\", "C:\\Windows"}

    def analyze(self, root_node: ASTNode) -> AnalysisReport:
        violations: list[SecurityViolation] = []
        self._traverse(root_node, violations)

        # Global pattern checks across reconstructed tree
        reconstructed = self._node_to_string(root_node)
        stripped = reconstructed.replace(" ", "")
        if ":(){" in reconstructed or ":(){:|:&};:" in stripped or ":(){:&}" in stripped:
            violations.append(
                SecurityViolation(
                    rule_id="SEC_FORK_BOMB",
                    risk_level=RiskLevel.BLOCKED,
                    message="Classic shell fork bomb detected.",
                    command_snippet=reconstructed,
                )
            )

        # Determine highest risk level
        overall_risk = RiskLevel.LOW
        if any(v.risk_level == RiskLevel.BLOCKED for v in violations):
            overall_risk = RiskLevel.BLOCKED
        elif any(v.risk_level == RiskLevel.HIGH for v in violations):
            overall_risk = RiskLevel.HIGH
        elif any(v.risk_level == RiskLevel.MEDIUM for v in violations):
            overall_risk = RiskLevel.MEDIUM

        return AnalysisReport(
            risk_level=overall_risk,
            violations=violations,
            ast_summary=root_node.to_dict(),
        )

    def _node_to_string(self, node: ASTNode) -> str:
        """Serialize ASTNode back to a normalized shell command string."""
        if isinstance(node, CommandNode):
            parts = [node.command] + node.args
            return " ".join(p for p in parts if p)
        elif isinstance(node, PipelineNode):
            return " | ".join(self._node_to_string(s) for s in node.stages)
        elif isinstance(node, CompoundNode):
            return f"{self._node_to_string(node.left)} {node.operator} {self._node_to_string(node.right)}"
        elif isinstance(node, SubshellNode):
            return f"$({self._node_to_string(node.body)})"
        return ""

    def _traverse(self, node: ASTNode, violations: list[SecurityViolation]) -> None:
        if node.node_type == NodeType.COMMAND:
            cmd_node = node
            assert isinstance(cmd_node, CommandNode)
            self._check_command(cmd_node, violations)
            for sub in cmd_node.subshells:
                self._traverse(sub, violations)

        elif node.node_type == NodeType.PIPELINE:
            pipe_node = node
            assert isinstance(pipe_node, PipelineNode)
            self._check_pipeline(pipe_node, violations)
            for stage in pipe_node.stages:
                self._traverse(stage, violations)

        elif node.node_type == NodeType.SUBSHELL:
            sub_node = node
            assert isinstance(sub_node, SubshellNode)
            self._traverse(sub_node.body, violations)

        elif node.node_type == NodeType.COMPOUND:
            comp_node = node
            assert isinstance(comp_node, CompoundNode)
            self._traverse(comp_node.left, violations)
            self._traverse(comp_node.right, violations)

    def _check_command(self, node: CommandNode, violations: list[SecurityViolation]) -> None:
        cmd = node.command.lower()
        args = [a.lower() for a in node.args]
        full_line = f"{node.command} {' '.join(node.args)}".strip()

        # 1. Catastrophic root deletion: rm -rf / or rm -rf ~
        if cmd == "rm":
            has_rf = any(arg in {"-r", "-rf", "-fr", "-rfi", "-rI"} or ("r" in arg and "f" in arg and arg.startswith("-")) for arg in args)
            targets = [a for a in node.args if not a.startswith("-")]

            if has_rf:
                for target in targets:
                    norm_target = target.rstrip("/").strip()
                    if norm_target in self.DESTRUCTIVE_ROOT_PATHS or norm_target == "":
                        violations.append(
                            SecurityViolation(
                                rule_id="SEC_CATASTROPHIC_DELETION",
                                risk_level=RiskLevel.BLOCKED,
                                message=f"Attempted destructive deletion of system root: 'rm {target}'",
                                command_snippet=full_line,
                                remediation="Target specific workspace directories instead of system roots.",
                            )
                        )
                    elif target.startswith("$") and not (target.startswith('"${') or target.startswith('"$')):
                        violations.append(
                            SecurityViolation(
                                rule_id="SEC_UNQUOTED_PARAM_DELETION",
                                risk_level=RiskLevel.HIGH,
                                message=f"Dangerous unquoted variable expansion in deletion: '{target}'",
                                command_snippet=full_line,
                                remediation=f'Enclose variable in double quotes: \'"{target}"\'.',
                            )
                        )

        # 2. Raw disk formatting or writing
        if cmd in {"mkfs", "fdisk", "parted", "dd"}:
            if cmd == "dd" and any("of=/dev/sd" in a or "of=/dev/nvme" in a for a in args):
                violations.append(
                    SecurityViolation(
                        rule_id="SEC_RAW_DISK_OVERWRITE",
                        risk_level=RiskLevel.BLOCKED,
                        message="Direct write to raw block device detected.",
                        command_snippet=full_line,
                    )
                )
            elif cmd in {"mkfs", "fdisk", "parted"}:
                violations.append(
                    SecurityViolation(
                        rule_id="SEC_FILESYSTEM_MODIFICATION",
                        risk_level=RiskLevel.BLOCKED,
                        message=f"Disk formatting utility '{cmd}' blocked.",
                        command_snippet=full_line,
                    )
                )

        # 3. Privilege escalation & system permissions
        if cmd == "chmod":
            if any("777" in a for a in args) and any(a in {"/", "/etc", "/var", "/bin", "/usr"} for a in args):
                violations.append(
                    SecurityViolation(
                        rule_id="SEC_SYSTEM_CHMOD_777",
                        risk_level=RiskLevel.HIGH,
                        message="Broad system permission modification (chmod 777) on system path.",
                        command_snippet=full_line,
                        remediation="Apply restricted permissions specifically to project files.",
                    )
                )

        # 4. Dangerous fork bomb pattern
        if ":(){ :|:& };:" in full_line or ":(){:|:&};:" in full_line:
            violations.append(
                SecurityViolation(
                    rule_id="SEC_FORK_BOMB",
                    risk_level=RiskLevel.BLOCKED,
                    message="Classic shell fork bomb detected.",
                    command_snippet=full_line,
                )
            )

        # 5. Potential credential exfiltration
        if any(kw in full_line for kw in ["id_rsa", ".ssh/id_", "/etc/shadow"]) and any(
            t in cmd for t in ["curl", "nc", "ncat", "telnet", "wget"]
        ):
            violations.append(
                SecurityViolation(
                    rule_id="SEC_CREDENTIAL_EXFILTRATION",
                    risk_level=RiskLevel.BLOCKED,
                    message="Potential private key or shadow credential network transmission.",
                    command_snippet=full_line,
                )
            )

    def _check_pipeline(self, node: PipelineNode, violations: list[SecurityViolation]) -> None:
        """Inspect pipeline connections, specifically remote execution patterns (curl ... | bash)."""
        stage_cmds = []
        for stage in node.stages:
            if isinstance(stage, CommandNode):
                stage_cmds.append(stage.command.lower())
            else:
                stage_cmds.append("unknown")

        for i in range(len(stage_cmds) - 1):
            left_cmd = stage_cmds[i]
            right_cmd = stage_cmds[i + 1]

            if left_cmd in self.FETCH_COMMANDS and right_cmd in self.INTERPRETER_COMMANDS:
                violations.append(
                    SecurityViolation(
                        rule_id="SEC_REMOTE_EXECUTION_PIPELINE",
                        risk_level=RiskLevel.HIGH,
                        message=f"Direct pipe from network downloader ('{left_cmd}') into shell interpreter ('{right_cmd}').",
                        command_snippet=f"{left_cmd} | {right_cmd}",
                        remediation="Download script to a temporary file, inspect contents, and execute explicitly.",
                    )
                )
