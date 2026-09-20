"""AST Dynamic Program Slicing and Blast-Radius Engine.

Constructs Program Dependence Graphs (PDG) combining Control Dependence Graphs (CDG)
and Data Dependence Graphs (DDG) across Python functions, classes, and modules.

Supports:
- Backward Slicing: Computes minimal causal statement slice leading to a crash or assertion line.
- Forward Slicing: Computes blast-radius and affected downstream call/def sites for planned edits.
- Surgical Slicing Reports: Identifies precise causal lines to prevent accidental regressions.

Architectural Invariants:
- Zero Human-in-the-Loop Blocking: 100% autonomous operation.
- Strict Enterprise Naming: Clean, professional, unbranded terminology.
- 100% English code, comments, docstrings, and diagnostics.
"""

from __future__ import annotations

import ast
import logging
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain.tools import tool

logger = logging.getLogger(__name__)


@dataclass
class StatementNode:
    """Representation of an individual statement within the AST."""

    line_number: int
    node_type: str
    code_snippet: str
    defined_vars: Set[str] = field(default_factory=set)
    used_vars: Set[str] = field(default_factory=set)
    control_parents: Set[int] = field(default_factory=set)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "line_number": self.line_number,
            "node_type": self.node_type,
            "code_snippet": self.code_snippet,
            "defined_vars": sorted(list(self.defined_vars)),
            "used_vars": sorted(list(self.used_vars)),
            "control_parents": sorted(list(self.control_parents)),
        }


@dataclass
class ProgramDependenceGraph:
    """Combines Data Dependence Graph (DDG) and Control Dependence Graph (CDG)."""

    statements: Dict[int, StatementNode] = field(default_factory=dict)
    # data_edges: def_line -> set of (use_line, var_name)
    data_edges: Dict[int, Set[Tuple[int, str]]] = field(default_factory=lambda: defaultdict(set))
    # rev_data_edges: use_line -> set of (def_line, var_name)
    rev_data_edges: Dict[int, Set[Tuple[int, str]]] = field(default_factory=lambda: defaultdict(set))
    # control_edges: parent_line -> set of child_lines
    control_edges: Dict[int, Set[int]] = field(default_factory=lambda: defaultdict(set))
    # rev_control_edges: child_line -> set of parent_lines
    rev_control_edges: Dict[int, Set[int]] = field(default_factory=lambda: defaultdict(set))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "statements_count": len(self.statements),
            "data_edges_count": sum(len(v) for v in self.data_edges.values()),
            "control_edges_count": sum(len(v) for v in self.control_edges.values()),
            "statement_lines": sorted(list(self.statements.keys())),
        }


class ASTVariableExtractor(ast.NodeVisitor):
    """Extracts defined and used variable names strictly from the current statement header."""

    def __init__(self) -> None:
        self.defined_vars: Set[str] = set()
        self.used_vars: Set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.defined_vars.add(node.id)
        elif isinstance(node.ctx, ast.Load):
            self.used_vars.add(node.id)
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        self.defined_vars.add(node.arg)
        if node.annotation:
            self.visit(node.annotation)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            # Record base object or composite attribute
            if isinstance(node.value, ast.Name):
                self.defined_vars.add(f"{node.value.id}.{node.attr}")
                self.used_vars.add(node.value.id)
        elif isinstance(node.ctx, ast.Load):
            if isinstance(node.value, ast.Name):
                self.used_vars.add(f"{node.value.id}.{node.attr}")
                self.used_vars.add(node.value.id)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.defined_vars.add(node.name)
        for dec in node.decorator_list:
            self.visit(dec)
        if node.returns:
            self.visit(node.returns)
        all_args = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
        for a in all_args:
            self.defined_vars.add(a.arg)
            if a.annotation:
                self.visit(a.annotation)
        if node.args.vararg:
            self.defined_vars.add(node.args.vararg.arg)
        if node.args.kwarg:
            self.defined_vars.add(node.args.kwarg.arg)
        for d in node.args.defaults + [x for x in node.args.kw_defaults if x]:
            self.visit(d)
        # Deliberately do NOT visit node.body!

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)  # type: ignore

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.defined_vars.add(node.name)
        for dec in node.decorator_list:
            self.visit(dec)
        for b in node.bases:
            self.visit(b)
        for kw in node.keywords:
            self.visit(kw.value)
        # Deliberately do NOT visit node.body!

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        # Deliberately do NOT visit node.body or node.orelse!

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        # Deliberately do NOT visit node.body or node.orelse!

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.target)
        self.visit(node.iter)
        # Deliberately do NOT visit node.body or node.orelse!

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit_For(node)  # type: ignore

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars:
                self.visit(item.optional_vars)
        # Deliberately do NOT visit node.body!

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.visit_With(node)  # type: ignore

    def visit_Try(self, node: ast.Try) -> None:
        pass


class ProgramSlicingEngine:
    """Builds PDGs and computes minimal backward and forward causal slices."""

    def __init__(self) -> None:
        pass

    def build_pdg(self, source_code: str) -> ProgramDependenceGraph:
        """Parse source code and construct a combined Control and Data Dependence Graph."""
        pdg = ProgramDependenceGraph()
        lines = source_code.splitlines()

        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            logger.warning("SyntaxError while building PDG: %s", e)
            return pdg

        # Pass 1: Extract statements and their defined/used variables
        def get_snippet(lineno: int) -> str:
            if 1 <= lineno <= len(lines):
                return lines[lineno - 1].strip()
            return ""

        def record_statement(node: ast.AST, parent_line: Optional[int] = None) -> None:
            lineno = getattr(node, "lineno", None)
            if lineno is None:
                return

            extractor = ASTVariableExtractor()
            extractor.visit(node)

            if lineno not in pdg.statements:
                pdg.statements[lineno] = StatementNode(
                    line_number=lineno,
                    node_type=node.__class__.__name__,
                    code_snippet=get_snippet(lineno),
                    defined_vars=set(extractor.defined_vars),
                    used_vars=set(extractor.used_vars),
                )
            else:
                # Merge multiple nodes on the same line
                pdg.statements[lineno].defined_vars.update(extractor.defined_vars)
                pdg.statements[lineno].used_vars.update(extractor.used_vars)

            if parent_line is not None and parent_line != lineno:
                pdg.statements[lineno].control_parents.add(parent_line)
                pdg.control_edges[parent_line].add(lineno)
                pdg.rev_control_edges[lineno].add(parent_line)

        def walk_body(body_nodes: List[ast.AST], parent_line: Optional[int]) -> None:
            for child in body_nodes:
                record_statement(child, parent_line)

                child_lineno = getattr(child, "lineno", parent_line)
                # Control flow statements create control dependence for their body
                if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    nested_parent = child_lineno
                    if hasattr(child, "body"):
                        walk_body(child.body, nested_parent)
                    if hasattr(child, "orelse") and child.orelse:
                        walk_body(child.orelse, nested_parent)
                    if hasattr(child, "handlers") and child.handlers:
                        for handler in child.handlers:
                            h_lineno = getattr(handler, "lineno", nested_parent)
                            record_statement(handler, nested_parent)
                            walk_body(handler.body, h_lineno)
                    if hasattr(child, "finalbody") and child.finalbody:
                        walk_body(child.finalbody, nested_parent)

        walk_body(tree.body, None)

        # Pass 2: Reaching Definitions Analysis across Control Flow
        def compute_data_flow(
            nodes: List[ast.AST],
            in_defs: Dict[str, Set[int]],
        ) -> Dict[str, Set[int]]:
            curr_defs = defaultdict(set, {k: set(v) for k, v in in_defs.items()})

            for child in nodes:
                lineno = getattr(child, "lineno", None)
                if lineno and lineno in pdg.statements:
                    stmt = pdg.statements[lineno]
                    # Connect uses to all active reaching definitions
                    for u_var in stmt.used_vars:
                        base_var = u_var.split(".")[0]
                        candidate_def_lines = curr_defs.get(u_var) or curr_defs.get(base_var) or set()
                        for def_line in candidate_def_lines:
                            if def_line != lineno:
                                pdg.data_edges[def_line].add((lineno, u_var))
                                pdg.rev_data_edges[lineno].add((def_line, u_var))

                    # Update definitions
                    for d_var in stmt.defined_vars:
                        curr_defs[d_var] = {lineno}
                        base_var = d_var.split(".")[0]
                        curr_defs[base_var] = {lineno}

                # Handle branching and sub-scopes
                if isinstance(child, ast.If):
                    out_if = compute_data_flow(child.body, curr_defs)
                    out_else = compute_data_flow(child.orelse, curr_defs) if child.orelse else curr_defs
                    joined: Dict[str, Set[int]] = defaultdict(set)
                    for k in set(out_if.keys()) | set(out_else.keys()):
                        joined[k] = out_if.get(k, set()) | out_else.get(k, set())
                    curr_defs = joined

                elif isinstance(child, (ast.For, ast.AsyncFor, ast.While)):
                    body_nodes = list(child.body)
                    out_loop = compute_data_flow(body_nodes, curr_defs)
                    loop_joined: Dict[str, Set[int]] = defaultdict(set)
                    for k in set(curr_defs.keys()) | set(out_loop.keys()):
                        loop_joined[k] = curr_defs.get(k, set()) | out_loop.get(k, set())
                    compute_data_flow(body_nodes, loop_joined)
                    if child.orelse:
                        out_else = compute_data_flow(child.orelse, loop_joined)
                        for k in set(out_else.keys()):
                            loop_joined[k] |= out_else[k]
                    curr_defs = loop_joined

                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    compute_data_flow(child.body, curr_defs)

                elif isinstance(child, ast.ClassDef):
                    compute_data_flow(child.body, curr_defs)

                elif isinstance(child, ast.Try):
                    out_try = compute_data_flow(child.body, curr_defs)
                    handler_outs = [out_try]
                    for h in child.handlers:
                        h_out = compute_data_flow(h.body, curr_defs)
                        handler_outs.append(h_out)
                    if child.orelse:
                        handler_outs.append(compute_data_flow(child.orelse, out_try))
                    joined_try: Dict[str, Set[int]] = defaultdict(set)
                    for o in handler_outs:
                        for k, v in o.items():
                            joined_try[k] |= v
                    if child.finalbody:
                        joined_try = compute_data_flow(child.finalbody, joined_try)
                    curr_defs = joined_try

                elif isinstance(child, (ast.With, ast.AsyncWith)):
                    curr_defs = compute_data_flow(child.body, curr_defs)

            return curr_defs

        compute_data_flow(tree.body, defaultdict(set))
        return pdg

    def backward_slice(
        self,
        source_code: str,
        target_line: int,
        target_variable: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute the minimal backward slice of statements influencing the target."""
        pdg = self.build_pdg(source_code)
        lines = source_code.splitlines()

        if not pdg.statements:
            return {
                "slicing_mode": "backward",
                "target_line": target_line,
                "target_variable": target_variable,
                "slice_lines": [target_line] if 1 <= target_line <= len(lines) else [],
                "slice_code": lines[target_line - 1] if 1 <= target_line <= len(lines) else "",
                "causal_statements": [],
                "summary": "Empty or unparseable source code; returned fallback target line.",
            }

        # Resolve closest available statement line <= target_line
        candidate_lines = [l for l in pdg.statements if l <= target_line]
        resolved_line = max(candidate_lines) if candidate_lines else min(pdg.statements.keys())

        visited_lines: Set[int] = set()
        queue: deque[Tuple[int, Optional[str]]] = deque()

        start_stmt = pdg.statements[resolved_line]
        if target_variable:
            queue.append((resolved_line, target_variable))
        elif start_stmt.used_vars:
            for v in start_stmt.used_vars:
                queue.append((resolved_line, v))
        else:
            queue.append((resolved_line, None))

        visited_lines.add(resolved_line)

        while queue:
            curr_line, needed_var = queue.popleft()

            # Follow reverse data edges (definitions influencing this line)
            for def_line, edge_var in pdg.rev_data_edges.get(curr_line, set()):
                if needed_var is None or edge_var == needed_var or edge_var.split(".")[0] == needed_var:
                    if def_line not in visited_lines:
                        visited_lines.add(def_line)
                        def_stmt = pdg.statements.get(def_line)
                        if def_stmt:
                            for prior_used in def_stmt.used_vars:
                                queue.append((def_line, prior_used))
                        else:
                            queue.append((def_line, None))

            # Follow reverse control edges (controlling conditions)
            for ctrl_parent in pdg.rev_control_edges.get(curr_line, set()):
                if ctrl_parent not in visited_lines:
                    visited_lines.add(ctrl_parent)
                    parent_stmt = pdg.statements.get(ctrl_parent)
                    if parent_stmt:
                        for ctrl_used in parent_stmt.used_vars:
                            queue.append((ctrl_parent, ctrl_used))
                    else:
                        queue.append((ctrl_parent, None))

        sorted_slice_lines = sorted(list(visited_lines))
        causal_statements = [
            pdg.statements[l].to_dict() for l in sorted_slice_lines if l in pdg.statements
        ]

        slice_code = "\n".join(
            lines[l - 1] for l in sorted_slice_lines if 1 <= l <= len(lines)
        )

        return {
            "slicing_mode": "backward",
            "target_line": target_line,
            "resolved_line": resolved_line,
            "target_variable": target_variable,
            "slice_line_count": len(sorted_slice_lines),
            "slice_lines": sorted_slice_lines,
            "slice_code": slice_code,
            "causal_statements": causal_statements,
            "summary": f"Backward slice identified {len(sorted_slice_lines)} causal statements affecting line {resolved_line}.",
        }

    def forward_slice(
        self,
        source_code: str,
        target_line: int,
        target_variable: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute the downstream blast radius and affected lines for a modified statement."""
        pdg = self.build_pdg(source_code)
        lines = source_code.splitlines()

        if not pdg.statements:
            return {
                "slicing_mode": "forward",
                "target_line": target_line,
                "target_variable": target_variable,
                "impacted_lines": [target_line] if 1 <= target_line <= len(lines) else [],
                "impacted_code": lines[target_line - 1] if 1 <= target_line <= len(lines) else "",
                "blast_radius_risk": "Low",
                "summary": "Empty or unparseable source code; returned fallback target line.",
            }

        candidate_lines = [l for l in pdg.statements if l >= target_line]
        resolved_line = min(candidate_lines) if candidate_lines else max(pdg.statements.keys())

        visited_lines: Set[int] = set()
        queue: deque[Tuple[int, Optional[str]]] = deque()

        start_stmt = pdg.statements[resolved_line]
        if target_variable:
            queue.append((resolved_line, target_variable))
        elif start_stmt.defined_vars:
            for v in start_stmt.defined_vars:
                queue.append((resolved_line, v))
        else:
            queue.append((resolved_line, None))

        visited_lines.add(resolved_line)

        while queue:
            curr_line, active_var = queue.popleft()

            # Follow forward data edges (usages dependent on this definition)
            for use_line, edge_var in pdg.data_edges.get(curr_line, set()):
                if active_var is None or edge_var == active_var or edge_var.split(".")[0] == active_var:
                    if use_line not in visited_lines:
                        visited_lines.add(use_line)
                        use_stmt = pdg.statements.get(use_line)
                        if use_stmt:
                            for downstream_def in use_stmt.defined_vars:
                                queue.append((use_line, downstream_def))
                        else:
                            queue.append((use_line, None))

            # Follow forward control edges (statements governed by this control node)
            for ctrl_child in pdg.control_edges.get(curr_line, set()):
                if ctrl_child not in visited_lines:
                    visited_lines.add(ctrl_child)
                    queue.append((ctrl_child, None))

        sorted_impacted = sorted(list(visited_lines))
        impacted_statements = [
            pdg.statements[l].to_dict() for l in sorted_impacted if l in pdg.statements
        ]

        impacted_code = "\n".join(
            lines[l - 1] for l in sorted_impacted if 1 <= l <= len(lines)
        )

        # Risk scoring
        total_stmts = len(pdg.statements)
        ratio = len(sorted_impacted) / total_stmts if total_stmts > 0 else 0.0

        if ratio >= 0.6 or len(sorted_impacted) > 25:
            risk = "Critical"
        elif ratio >= 0.35 or len(sorted_impacted) > 12:
            risk = "High"
        elif ratio >= 0.15 or len(sorted_impacted) > 5:
            risk = "Medium"
        else:
            risk = "Low"

        return {
            "slicing_mode": "forward",
            "target_line": target_line,
            "resolved_line": resolved_line,
            "target_variable": target_variable,
            "blast_radius_risk": risk,
            "impact_ratio": round(ratio, 4),
            "impacted_line_count": len(sorted_impacted),
            "impacted_lines": sorted_impacted,
            "impacted_code": impacted_code,
            "impacted_statements": impacted_statements,
            "summary": f"Forward blast radius identified {len(sorted_impacted)} downstream statements (Risk: {risk}).",
        }


@tool("compute_program_slice", parse_docstring=True)
def compute_program_slice(
    source_code: Optional[str] = None,
    file_path: Optional[str] = None,
    slicing_mode: str = "backward",
    target_line: int = 1,
    target_variable: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute backward or forward program slices across Python code using AST and PDG graphs.

    Enables surgical debugging and blast-radius analysis:
    - Backward slicing isolates the minimal preceding statements that caused a crash or assertion.
    - Forward slicing computes the downstream blast radius of a planned variable or signature edit.

    Args:
        source_code: Python code string to analyze.
        file_path: Optional file path to read code from if source_code is not provided.
        slicing_mode: Slicing direction, either 'backward' (causal fault localization) or 'forward' (blast radius).
        target_line: Target statement line number (1-indexed).
        target_variable: Optional variable name of interest at target line.

    Returns:
        Structured dictionary containing slice lines, extracted code, and dependence metrics.
    """
    try:
        engine = ProgramSlicingEngine()

        code = source_code or ""
        if not code and file_path:
            p = Path(file_path)
            if p.is_file():
                code = p.read_text(encoding="utf-8")
            else:
                return {
                    "success": False,
                    "data": {"error": f"File not found: {file_path}"},
                }

        if not code:
            return {
                "success": False,
                "data": {"error": "No source_code or valid file_path provided."},
            }

        mode = slicing_mode.strip().lower()
        if mode == "forward":
            result = engine.forward_slice(code, target_line, target_variable)
        else:
            result = engine.backward_slice(code, target_line, target_variable)

        return {
            "success": True,
            "data": result,
        }
    except Exception as e:
        logger.exception("Error computing program slice")
        return {
            "success": False,
            "data": {
                "error": str(e),
                "slicing_mode": slicing_mode,
                "target_line": target_line,
            },
        }
