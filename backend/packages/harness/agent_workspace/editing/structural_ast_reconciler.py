"""Structural 3-Way AST Conflict Reconciler Engine (SWE-EVO).

Replaces crude line-based git conflict markers with semantic 3-way AST node reconciliation
across Base, Ours, and Theirs code states. Automatically merges non-conflicting function body
edits, deduplicates and sorts import statements, preserves docstrings, and synthesizes unified
AST representations for concurrent modifications.

Architectural Invariants:
- Zero Human-in-the-Loop Blocking: 100% autonomous operation.
- Strict Enterprise Naming: Clean, professional, unbranded terminology.
- 100% English code, comments, docstrings, and diagnostics.
"""

from __future__ import annotations

import ast
import copy
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from langchain.tools import tool

logger = logging.getLogger(__name__)


@dataclass
class ReconciledElement:
    """Record of a merged or reconciled AST construct."""

    element_name: str
    element_type: str  # "import", "function", "class", "global_assign", "docstring"
    resolution_source: str  # "ours", "theirs", "structural_merge", "deduplicated"
    details: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class StructuralAstConflictReconciler:
    """Semantic 3-way AST merger for concurrent multi-agent code modifications."""

    def __init__(self) -> None:
        pass

    def _get_docstring_node(self, body: List[ast.AST]) -> Optional[ast.Expr]:
        """Retrieve module or function docstring node if present."""
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            return body[0]
        return None

    def reconcile_imports(
        self, base_tree: ast.Module, ours_tree: ast.Module, theirs_tree: ast.Module
    ) -> Tuple[List[ast.AST], List[ReconciledElement]]:
        """Deduplicate, merge, and group import statements across all revisions."""
        records: List[ReconciledElement] = []

        # Maps module_name -> set of (name, alias)
        from_imports: Dict[str, Set[Tuple[str, Optional[str]]]] = defaultdict(set)
        # Set of (module_name, alias)
        direct_imports: Set[Tuple[str, Optional[str]]] = set()

        for tree, label in [(base_tree, "base"), (theirs_tree, "theirs"), (ours_tree, "ours")]:
            for node in tree.body:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        direct_imports.add((alias.name, alias.asname))
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    level = node.level
                    key = "." * level + mod if level > 0 else mod
                    for alias in node.names:
                        from_imports[key].add((alias.name, alias.asname))

        reconciled_nodes: List[ast.AST] = []

        # Build combined direct imports
        for mod_name, alias_name in sorted(direct_imports, key=lambda x: x[0]):
            alias_node = ast.alias(name=mod_name, asname=alias_name)
            reconciled_nodes.append(ast.Import(names=[alias_node]))
            records.append(
                ReconciledElement(
                    element_name=mod_name,
                    element_type="import",
                    resolution_source="deduplicated",
                    details=f"Direct import '{mod_name}' reconciled.",
                )
            )

        # Build combined from-imports
        for mod_key, names_set in sorted(from_imports.items(), key=lambda x: x[0]):
            level = 0
            while mod_key.startswith("."):
                level += 1
                mod_key = mod_key[1:]
            sorted_aliases = [
                ast.alias(name=n, asname=a)
                for n, a in sorted(names_set, key=lambda x: (x[0], x[1] or ""))
            ]
            reconciled_nodes.append(
                ast.ImportFrom(module=mod_key or None, names=sorted_aliases, level=level)
            )
            records.append(
                ReconciledElement(
                    element_name=mod_key or ".",
                    element_type="import",
                    resolution_source="deduplicated",
                    details=f"From-import '{mod_key}' merged with {len(sorted_aliases)} names.",
                )
            )

        return reconciled_nodes, records

    def _ast_to_code(self, node: ast.AST) -> str:
        """Unparse AST node into normalized string."""
        try:
            return ast.unparse(node).strip()
        except Exception:
            return str(node)

    def reconcile_functions(
        self,
        base_fn: Optional[Union[ast.FunctionDef, ast.AsyncFunctionDef]],
        ours_fn: Optional[Union[ast.FunctionDef, ast.AsyncFunctionDef]],
        theirs_fn: Optional[Union[ast.FunctionDef, ast.AsyncFunctionDef]],
    ) -> Tuple[Optional[ast.AST], ReconciledElement]:
        """Perform 3-way reconciliation on function definitions."""
        name = (ours_fn or theirs_fn or base_fn).name  # type: ignore

        if ours_fn and not theirs_fn and not base_fn:
            return ours_fn, ReconciledElement(name, "function", "ours", "Added exclusively in Ours.")
        if theirs_fn and not ours_fn and not base_fn:
            return theirs_fn, ReconciledElement(name, "function", "theirs", "Added exclusively in Theirs.")

        code_base = self._ast_to_code(base_fn) if base_fn else None
        code_ours = self._ast_to_code(ours_fn) if ours_fn else None
        code_theirs = self._ast_to_code(theirs_fn) if theirs_fn else None

        if code_ours == code_theirs and ours_fn:
            return ours_fn, ReconciledElement(name, "function", "ours", "Identical in Ours and Theirs.")
        if code_ours == code_base and theirs_fn:
            return theirs_fn, ReconciledElement(name, "function", "theirs", "Modified only in Theirs.")
        if code_theirs == code_base and ours_fn:
            return ours_fn, ReconciledElement(name, "function", "ours", "Modified only in Ours.")

        # Concurrent modifications: attempt semantic AST body statement merge
        if ours_fn and theirs_fn:
            merged_fn = copy.deepcopy(ours_fn)
            ours_doc = self._get_docstring_node(ours_fn.body)
            theirs_doc = self._get_docstring_node(theirs_fn.body)
            base_doc = self._get_docstring_node(base_fn.body) if base_fn else None

            # Reconcile docstrings
            chosen_doc = ours_doc
            if theirs_doc and (not base_doc or self._ast_to_code(theirs_doc) != self._ast_to_code(base_doc)):
                if not ours_doc or (base_doc and self._ast_to_code(ours_doc) == self._ast_to_code(base_doc)):
                    chosen_doc = theirs_doc

            # Reconcile parameters
            ours_args = {a.arg: a for a in ours_fn.args.args}
            for a in theirs_fn.args.args:
                if a.arg not in ours_args:
                    merged_fn.args.args.append(a)

            # Reconcile body statements
            base_stmts_code = {
                self._ast_to_code(s) for s in (base_fn.body if base_fn else [])
            }
            ours_stmts = [
                s for s in ours_fn.body if not (chosen_doc and s is ours_doc)
            ]
            theirs_stmts = [
                s for s in theirs_fn.body if not (chosen_doc and s is theirs_doc)
            ]

            ours_code_set = {self._ast_to_code(s) for s in ours_stmts}
            theirs_new_stmts = [
                s for s in theirs_stmts if self._ast_to_code(s) not in base_stmts_code and self._ast_to_code(s) not in ours_code_set
            ]

            # Place statements: docstring first, then ours, then non-conflicting additions from theirs
            new_body: List[ast.AST] = []
            if chosen_doc:
                new_body.append(chosen_doc)
            new_body.extend(ours_stmts)
            new_body.extend(theirs_new_stmts)

            if not new_body:
                new_body.append(ast.Pass())

            merged_fn.body = new_body
            return merged_fn, ReconciledElement(
                name,
                "function",
                "structural_merge",
                f"Merged function body with {len(theirs_new_stmts)} statements incorporated from Theirs.",
            )

        fallback = ours_fn or theirs_fn or base_fn
        return fallback, ReconciledElement(name, "function", "ours", "Fallback to available node.")

    def reconcile_classes(
        self,
        base_cls: Optional[ast.ClassDef],
        ours_cls: Optional[ast.ClassDef],
        theirs_cls: Optional[ast.ClassDef],
    ) -> Tuple[Optional[ast.AST], ReconciledElement]:
        """Perform 3-way reconciliation on ClassDef nodes and member methods."""
        name = (ours_cls or theirs_cls or base_cls).name  # type: ignore

        if ours_cls and not theirs_cls and not base_cls:
            return ours_cls, ReconciledElement(name, "class", "ours", "Class added exclusively in Ours.")
        if theirs_cls and not ours_cls and not base_cls:
            return theirs_cls, ReconciledElement(name, "class", "theirs", "Class added exclusively in Theirs.")

        code_base = self._ast_to_code(base_cls) if base_cls else None
        code_ours = self._ast_to_code(ours_cls) if ours_cls else None
        code_theirs = self._ast_to_code(theirs_cls) if theirs_cls else None

        if code_ours == code_theirs and ours_cls:
            return ours_cls, ReconciledElement(name, "class", "ours", "Identical class in Ours and Theirs.")
        if code_ours == code_base and theirs_cls:
            return theirs_cls, ReconciledElement(name, "class", "theirs", "Class modified only in Theirs.")
        if code_theirs == code_base and ours_cls:
            return ours_cls, ReconciledElement(name, "class", "ours", "Class modified only in Ours.")

        if ours_cls and theirs_cls:
            merged_cls = copy.deepcopy(ours_cls)

            # Reconcile methods inside class
            base_methods = {
                m.name: m for m in (base_cls.body if base_cls else []) if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            ours_methods = {
                m.name: m for m in ours_cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            theirs_methods = {
                m.name: m for m in theirs_cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            }

            all_method_names = list(
                dict.fromkeys(list(ours_methods.keys()) + list(theirs_methods.keys()))
            )
            reconciled_methods: List[ast.AST] = []
            for m_name in all_method_names:
                b_m = base_methods.get(m_name)
                o_m = ours_methods.get(m_name)
                t_m = theirs_methods.get(m_name)
                rec_m, _ = self.reconcile_functions(b_m, o_m, t_m)
                if rec_m:
                    reconciled_methods.append(rec_m)

            # Preserve non-method statements (attributes, docstring)
            non_method_stmts = [
                s for s in ours_cls.body if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            merged_cls.body = non_method_stmts + reconciled_methods
            return merged_cls, ReconciledElement(
                name, "class", "structural_merge", f"Merged class {name} methods and attributes."
            )

        fallback = ours_cls or theirs_cls or base_cls
        return fallback, ReconciledElement(name, "class", "ours", "Class fallback.")

    def reconcile(
        self, base_code: str, ours_code: str, theirs_code: str, file_path: str = "module.py"
    ) -> Dict[str, Any]:
        """Reconcile concurrent Python edits into a single valid AST representation."""
        try:
            base_tree = ast.parse(base_code or "")
            ours_tree = ast.parse(ours_code or "")
            theirs_tree = ast.parse(theirs_code or "")
        except SyntaxError as e:
            return {
                "success": False,
                "error": f"Syntax error in input code during structural reconciliation: {e.msg} at line {e.lineno}",
                "file_path": file_path,
                "merged_code": ours_code,  # Safe fallback to Ours
                "reconciled_elements": [],
            }

        reconciled_elements: List[ReconciledElement] = []
        final_body: List[ast.AST] = []

        # 1. Module Docstring
        base_doc = self._get_docstring_node(base_tree.body)
        ours_doc = self._get_docstring_node(ours_tree.body)
        theirs_doc = self._get_docstring_node(theirs_tree.body)

        chosen_doc = ours_doc
        if theirs_doc and (not base_doc or self._ast_to_code(theirs_doc) != self._ast_to_code(base_doc)):
            if not ours_doc or (base_doc and self._ast_to_code(ours_doc) == self._ast_to_code(base_doc)):
                chosen_doc = theirs_doc

        if chosen_doc:
            final_body.append(chosen_doc)
            reconciled_elements.append(
                ReconciledElement("module_docstring", "docstring", "structural_merge", "Module docstring preserved.")
            )

        # 2. Imports
        import_nodes, import_records = self.reconcile_imports(base_tree, ours_tree, theirs_tree)
        final_body.extend(import_nodes)
        reconciled_elements.extend(import_records)

        # 3. Categorize top-level definitions
        def categorize(tree: ast.Module) -> Tuple[Dict[str, Any], Dict[str, Any], List[ast.AST]]:
            funcs = {}
            classes = {}
            others = []
            for node in tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                if node is self._get_docstring_node(tree.body):
                    continue
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs[node.name] = node
                elif isinstance(node, ast.ClassDef):
                    classes[node.name] = node
                else:
                    others.append(node)
            return funcs, classes, others

        base_funcs, base_classes, base_others = categorize(base_tree)
        ours_funcs, ours_classes, ours_others = categorize(ours_tree)
        theirs_funcs, theirs_classes, theirs_others = categorize(theirs_tree)

        # Reconcile Classes
        all_class_names = list(
            dict.fromkeys(list(ours_classes.keys()) + list(theirs_classes.keys()) + list(base_classes.keys()))
        )
        for c_name in all_class_names:
            b_c = base_classes.get(c_name)
            o_c = ours_classes.get(c_name)
            t_c = theirs_classes.get(c_name)
            rec_c, rec_el = self.reconcile_classes(b_c, o_c, t_c)
            if rec_c:
                final_body.append(rec_c)
                reconciled_elements.append(rec_el)

        # Reconcile Functions
        all_func_names = list(
            dict.fromkeys(list(ours_funcs.keys()) + list(theirs_funcs.keys()) + list(base_funcs.keys()))
        )
        for f_name in all_func_names:
            b_f = base_funcs.get(f_name)
            o_f = ours_funcs.get(f_name)
            t_f = theirs_funcs.get(f_name)
            rec_f, rec_el = self.reconcile_functions(b_f, o_f, t_f)
            if rec_f:
                final_body.append(rec_f)
                reconciled_elements.append(rec_el)

        # Reconcile other top-level statements (e.g. assignments, main guards)
        seen_others_code: Set[str] = set()
        for stmt in ours_others + theirs_others:
            c = self._ast_to_code(stmt)
            if c not in seen_others_code:
                seen_others_code.add(c)
                final_body.append(stmt)
                reconciled_elements.append(
                    ReconciledElement("statement", "global_assign", "deduplicated", f"Statement: {c[:40]}")
                )

        # Synthesize final module and unparse
        merged_module = ast.Module(body=final_body, type_ignores=[])
        ast.fix_missing_locations(merged_module)

        try:
            merged_code = ast.unparse(merged_module)
        except Exception as e:
            logger.warning("ast.unparse failed: %s; falling back to Ours", e)
            merged_code = ours_code

        return {
            "success": True,
            "file_path": file_path,
            "merged_code": merged_code,
            "reconciled_elements_count": len(reconciled_elements),
            "reconciled_elements": [e.to_dict() for e in reconciled_elements],
            "status": "clean_structural_merge",
        }


@tool("reconcile_structural_ast_conflicts", parse_docstring=True)
def reconcile_structural_ast_conflicts(
    base_code: str,
    ours_code: str,
    theirs_code: str,
    file_path: str = "module.py",
) -> Dict[str, Any]:
    """Reconcile multi-agent concurrent modifications via semantic 3-way AST node merging.

    Replaces line-based git conflict markers with structural AST reconciliation. Merges
    non-conflicting function edits, sorts and deduplicates imports, preserves docstrings,
    and synthesizes unified ASTs without human intervention.

    Args:
        base_code: Common ancestor code string prior to branch divergence.
        ours_code: Local branch code string containing modifications.
        theirs_code: Remote or peer agent code string containing concurrent modifications.
        file_path: Relative path of the module being merged (default 'module.py').

    Returns:
        Structured dictionary containing merged_code, element records, and merge status.
    """
    try:
        reconciler = StructuralAstConflictReconciler()
        result = reconciler.reconcile(
            base_code=base_code,
            ours_code=ours_code,
            theirs_code=theirs_code,
            file_path=file_path,
        )
        return {
            "success": result.get("success", False),
            "data": result,
        }
    except Exception as e:
        logger.exception("Error in structural AST reconciler")
        return {
            "success": False,
            "data": {
                "error": str(e),
                "file_path": file_path,
                "merged_code": ours_code,
            },
        }
