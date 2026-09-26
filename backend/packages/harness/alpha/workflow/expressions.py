"""Safe expression evaluator for dynamic workflow routing and branching.

Evaluates boolean conditions and property paths against workflow state
without evaluating arbitrary Python code or allowing system access.
"""

from __future__ import annotations

import ast
import operator
from typing import Any


class ExpressionSecurityError(ValueError):
    """Raised when an expression attempts unsafe operations or syntax."""

    pass


class SafeExpressionEvaluator:
    """AST-based safe evaluator for workflow expressions."""

    ALLOWED_OPERATORS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Mod: operator.mod,
        ast.Eq: operator.eq,
        ast.NotEq: operator.ne,
        ast.Lt: operator.lt,
        ast.LtE: operator.le,
        ast.Gt: operator.gt,
        ast.GtE: operator.ge,
        ast.In: lambda a, b: a in b,
        ast.NotIn: lambda a, b: a not in b,
        ast.Not: operator.not_,
        ast.USub: operator.neg,
    }

    DISALLOWED_NAMES = {
        "__class__",
        "__bases__",
        "__subclasses__",
        "__mro__",
        "__globals__",
        "__code__",
        "__closure__",
        "__import__",
        "eval",
        "exec",
        "compile",
        "open",
        "os",
        "sys",
        "subprocess",
    }

    def evaluate(self, expr_str: str, context: dict[str, Any]) -> Any:
        """Safely evaluate an expression string against the provided context dictionary."""
        if not expr_str or not expr_str.strip():
            return True

        normalized = self._normalize_syntax(expr_str)
        try:
            tree = ast.parse(normalized, mode="eval")
        except SyntaxError as e:
            raise ExpressionSecurityError(f"Syntax error in expression: {e}") from e

        return self._eval_node(tree.body, context)

    def _normalize_syntax(self, expr_str: str) -> str:
        """Normalize JS/C-style syntax to valid Python syntax."""
        tokens = expr_str.replace("&&", " and ").replace("||", " or ")
        # Replace lone exclamation mark if used as 'not'
        tokens = tokens.replace("!=", " __NE__ ")
        tokens = tokens.replace("!", " not ")
        tokens = tokens.replace(" __NE__ ", " != ")
        tokens = tokens.replace("true", "True").replace("false", "False").replace("null", "None")
        return tokens

    def _eval_node(self, node: ast.AST, context: dict[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            return node.value

        elif isinstance(node, ast.Name):
            if node.id in self.DISALLOWED_NAMES:
                raise ExpressionSecurityError(f"Access to '{node.id}' is prohibited.")
            return context.get(node.id)

        elif isinstance(node, ast.Attribute):
            val = self._eval_node(node.value, context)
            attr = node.attr
            if attr in self.DISALLOWED_NAMES or attr.startswith("__"):
                raise ExpressionSecurityError(f"Access to attribute '{attr}' is prohibited.")
            if isinstance(val, dict):
                return val.get(attr)
            return getattr(val, attr, None)

        elif isinstance(node, ast.Subscript):
            val = self._eval_node(node.value, context)
            slice_val = self._eval_node(node.slice, context)
            try:
                return val[slice_val]
            except (IndexError, KeyError, TypeError):
                return None

        elif isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                for val in node.values:
                    if not self._eval_node(val, context):
                        return False
                return True
            elif isinstance(node.op, ast.Or):
                for val in node.values:
                    if self._eval_node(val, context):
                        return True
                return False

        elif isinstance(node, ast.UnaryOp):
            op_fn = self.ALLOWED_OPERATORS.get(type(node.op))
            if not op_fn:
                raise ExpressionSecurityError(f"Unsupported unary operator: {type(node.op).__name__}")
            operand = self._eval_node(node.operand, context)
            return op_fn(operand)

        elif isinstance(node, ast.BinOp):
            op_fn = self.ALLOWED_OPERATORS.get(type(node.op))
            if not op_fn:
                raise ExpressionSecurityError(f"Unsupported binary operator: {type(node.op).__name__}")
            left = self._eval_node(node.left, context)
            right = self._eval_node(node.right, context)
            return op_fn(left, right)

        elif isinstance(node, ast.Compare):
            left = self._eval_node(node.left, context)
            for op, comparator in zip(node.ops, node.comparators):
                op_fn = self.ALLOWED_OPERATORS.get(type(op))
                if not op_fn:
                    raise ExpressionSecurityError(f"Unsupported comparison operator: {type(op).__name__}")
                right = self._eval_node(comparator, context)
                if not op_fn(left, right):
                    return False
                left = right
            return True

        elif isinstance(node, ast.List):
            return [self._eval_node(elt, context) for elt in node.elts]

        elif isinstance(node, ast.Dict):
            return {self._eval_node(k, context): self._eval_node(v, context) for k, v in zip(node.keys, node.values)}

        raise ExpressionSecurityError(f"Unsupported expression node: {type(node).__name__}")


def evaluate_condition_strict(condition: str | None, context: dict[str, Any]) -> bool:
    """Evaluate a condition and surface parser/security errors to the caller."""
    if not condition:
        return True
    return bool(SafeExpressionEvaluator().evaluate(condition, context))


def evaluate_condition(condition: str | None, context: dict[str, Any]) -> bool:
    """Evaluate a condition, treating an invalid expression as false.

    Scheduler predicates use this fail-closed form so one malformed branch
    cannot activate work.  Node handlers that need an auditable failure use
    :func:`evaluate_condition_strict` instead.
    """
    try:
        return evaluate_condition_strict(condition, context)
    except Exception:
        return False
