"""AST static security auditor for executable skills inspired by Hermes Agent."""

import ast
from dataclasses import dataclass, field


@dataclass
class ASTAuditResult:
    is_safe: bool
    violations: list[str] = field(default_factory=list)


class SkillASTAuditor:
    """Statically analyzes Python code to prevent malicious code execution in skills."""

    PROHIBITED_MODULES = {
        "subprocess",
        "ctypes",
        "pty",
        "socket",
        "fcntl",
        "posix",
    }

    PROHIBITED_CALLS = {
        "eval",
        "exec",
        "__import__",
        "compile",
    }

    def audit_code(self, python_code: str) -> ASTAuditResult:
        """Parse and verify Python source AST for prohibited operations."""
        try:
            tree = ast.parse(python_code)
        except SyntaxError as e:
            return ASTAuditResult(is_safe=False, violations=[f"Syntax error in skill code: {e}"])

        violations = []

        for node in ast.walk(tree):
            # Check imports: import X
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if root_mod in self.PROHIBITED_MODULES:
                        violations.append(f"Prohibited module import: '{alias.name}' (line {node.lineno})")

            # Check from X import Y
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_mod = node.module.split(".")[0]
                    if root_mod in self.PROHIBITED_MODULES:
                        violations.append(f"Prohibited module import: 'from {node.module} import ...' (line {node.lineno})")

            # Check prohibited calls: eval(), exec(), os.system()
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in self.PROHIBITED_CALLS:
                        violations.append(f"Prohibited builtin call: '{node.func.id}()' (line {node.lineno})")
                elif isinstance(node.func, ast.Attribute):
                    # Check os.system, os.popen
                    if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                        if node.func.attr in ("system", "popen", "spawn", "execl", "execv"):
                            violations.append(f"Prohibited OS process call: 'os.{node.func.attr}()' (line {node.lineno})")

        return ASTAuditResult(is_safe=len(violations) == 0, violations=violations)


_global_auditor = SkillASTAuditor()


def get_skill_ast_auditor() -> SkillASTAuditor:
    return _global_auditor
