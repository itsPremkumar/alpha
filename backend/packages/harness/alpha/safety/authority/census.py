"""Static authority census.

The census reads Python source and parses it with :mod:`ast`.  It never imports
an application module, evaluates a literal with ``eval``, starts a process, or
opens a socket.  That is a deliberate security property: a module that raises
on import is still auditable, and a malicious module cannot execute while the
auditor is deciding what authority it exposes.

The detector is intentionally conservative.  A dynamic registration,
reflection call, unrecognised decorator, or parse error becomes an ``unknown``
finding with its path.  Unknowns are not silently skipped and are never
promoted to a safe verdict.  For a destructive operation, a statically visible
default-deny gate counts only when it is attached to the operation's scope (or
to an explicit tool/catalogue name); a global string match is not evidence.

The rule vocabulary is defined in :mod:`alpha.safety.authority.classify`, and
the coverage calculation is kept in :mod:`alpha.safety.authority.coverage` so
the census remains a description of source rather than a policy grant.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .classify import (
    RULE_DYNAMIC_OPERATION,
    Classification,
    capability_from_classification,
    classify_action,
)
from .config import AuthorityAuditConfig
from .coverage import compute_coverage
from .models import (
    AuditGap,
    AuditReport,
    AuthorityRecord,
    Capability,
    Externality,
    Gate,
    GateKind,
    GatePosture,
    Reversibility,
    UnknownFinding,
    Verdict,
)

_ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "trace", "api_route"})
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_HUMAN_COMMAND_WORDS = frozenset(
    {
        "approve",
        "ask",
        "clarify",
        "confirm",
        "deny",
        "human",
        "interrupt",
        "pause",
        "resume-hitl",
        "security approve",
        "security deny",
    }
)
_DESTRUCTIVE_CALL_NAMES = frozenset(
    {
        "delete",
        "destroy",
        "drop",
        "erase",
        "force",
        "kill",
        "remove",
        "rmdir",
        "rmtree",
        "truncate",
        "unlink",
        "wipe",
    }
)
_EXEC_CALL_NAMES = frozenset({"eval", "exec", "popen", "system", "compile"})
_EGRESS_CALL_NAMES = frozenset({"curl", "download", "fetch", "get", "post", "put", "request", "send", "urlopen"})
_SEND_CALL_NAMES = frozenset({"broadcast", "email", "message", "notify", "publish", "send_message"})
_SECRET_NAMES = frozenset({"api_key", "apikey", "credential", "credentials", "password", "private_key", "secret", "token"})
_FLAG_NAMES = frozenset(
    {
        "allow_all",
        "allow_host_bash",
        "auto_promote",
        "auth_disabled",
        "default_allow",
        "default_deny",
        "disable_clarification",
        "enabled",
        "fail_closed",
        "human_approved",
        "is_autonomous_trigger",
        "non_interactive",
        "operator_token",
        "requires_approval",
        "security_fail_closed",
    }
)
_DYNAMIC_CALL_NAMES = frozenset(
    {
        "__import__",
        "add_api_route",
        "add_middleware",
        "add_route",
        "add_tool",
        "getattr",
        "globals",
        "import_module",
        "include_router",
        "locals",
        "register",
        "register_tool",
        "resolve_variable",
        "setattr",
    }
)
_ALLOWLIST_NAME_RE = re.compile(r"(?:allow_?list|allow(?:ed|able)?|authorized_?tools?|permitted_?tools?|disallowed_?tools?|tool_?names?|scope|scopes)", re.I)
_APPROVAL_NAME_RE = re.compile(r"(?:approv|confirm|clarif|human|operator|ask_?human)", re.I)
_MIDDLEWARE_NAME_RE = re.compile(r"middleware", re.I)
_GUARD_NAME_RE = re.compile(r"(?:allowlist|authorization|authorize|check_permission|(?:^|_)(?:guard|allow|permits?)(?:$|_)|guardrail|tool_permission|is_allowed|is_pat_allowed)", re.I)
_POLICY_CLASS_RE = re.compile(r"policy|guard|authorization", re.I)
_SHELL_DESTRUCTIVE_RE = re.compile(
    r"(?:\brm\s+-[rf]{1,2}\b|\bgit\s+branch\s+-D\b|\bbranch\b[^A-Za-z0-9]{0,20}-D\b|\bgit\s+push\b|\bdrop\s+(?:table|database)\b|\btruncate\s+table\b|\bcurl\b|\bwget\b|\bkubectl\b|\bterraform\s+apply\b|\bnpm\s+publish\b|\bssh\b|\bscp\b)",
    re.I,
)


@dataclass(slots=True)
class _RawOperation:
    path: str
    line: int
    action: str
    description: str
    scope: str | None
    dynamic: bool = False
    tags: tuple[str, ...] = ()
    tool_name: str | None = None
    order: int = 0
    associated_gate_ids: tuple[str, ...] = ()
    chosen_gate: str | None = None


@dataclass(slots=True)
class _RawGate:
    gate: Gate
    path: str
    scope: str | None
    order: int
    tool_names: frozenset[str] = frozenset()
    broad: bool = False


@dataclass(slots=True)
class _FileState:
    path: str
    operations: list[_RawOperation] = field(default_factory=list)
    gates: list[_RawGate] = field(default_factory=list)
    unknowns: list[UnknownFinding] = field(default_factory=list)
    gaps: list[AuditGap] = field(default_factory=list)
    tool_scopes: dict[str, str] = field(default_factory=dict)
    order: int = 0


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix().replace("\\", "/")


def _literal(node: ast.AST | None) -> Any:
    """Return a literal value without executing arbitrary Python."""

    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None


def _dotted_name(node: ast.AST | None) -> str:
    """Resolve decorator/call names to a dotted name without importing them."""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _dotted_name(node.func)
    return ""


def _target_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, (ast.Tuple, ast.List)):
        names: list[str] = []
        for item in node.elts:
            names.extend(_target_names(item))
        return tuple(names)
    if isinstance(node, ast.Attribute):
        return (node.attr,)
    return ()


def _scope_key(path: str, name: str, line: int) -> str:
    return f"{path}::{name}:{line}"


def _body_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Yield a function/class body once, without re-walking nested defs.

    Nested functions are visited by the outer visitor in their own scope.  A
    plain ``ast.walk`` here would re-scan their entire subtree for every
    ancestor, which turns a large router into quadratic work.
    """

    body = getattr(node, "body", None)
    if not isinstance(body, list):
        return
    stack = list(body)
    while stack:
        child = stack.pop()
        yield child
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(child))


def _has_return(node: ast.AST | Iterable[ast.AST], value: bool) -> bool:
    nodes: Iterable[ast.AST]
    if isinstance(node, ast.AST):
        nodes = ast.walk(node)
    else:
        nodes = (child for statement in node for child in ast.walk(statement))
    for child in nodes:
        if isinstance(child, ast.Return) and isinstance(child.value, ast.Constant):
            if child.value.value is value:
                return True
    return False


def _is_constant_true(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return node.value is True
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.Eq, ast.Is)):
        left = _literal(node.left)
        right = _literal(node.comparators[0])
        return left is not None and left == right
    return False


def _gate_id(kind: str, path: str, line: int, name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "gate"
    return f"gate:{kind}:{path}:{line}:{clean}"


def _capability_id(kind: str, path: str, line: int, name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "capability"
    return f"cap:{kind}:{path}:{line}:{clean}"


def _posture_for_guard(node: ast.AST, allowlist_names: set[str]) -> tuple[GatePosture, bool, str]:
    """Infer a guard's posture and whether it has any deny path.

    This is intentionally a small structural analysis.  If it cannot see a
    deny branch, it reports a non-failing gate rather than assuming safety.
    """

    for child in _body_nodes(node):
        if not isinstance(child, ast.If):
            continue
        test = child.test
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            names = {item.id for item in ast.walk(test.operand) if isinstance(item, ast.Name)}
            if names & allowlist_names and _has_return(child.body, True):
                return GatePosture.DEFAULT_ALLOW, False, "empty/false allowlist branch returns True (fail-open)"
            if _is_constant_true(test) and _has_return(child.body, True):
                return GatePosture.DEFAULT_ALLOW, False, "constant-true guard branch returns True"
        if isinstance(test, ast.Compare) and len(test.ops) == 1:
            op = test.ops[0]
            left_names = {item.id for item in ast.walk(test.left) if isinstance(item, ast.Name)}
            right_names = {item.id for item in ast.walk(test.comparators[0]) if isinstance(item, ast.Name)}
            names_in_test = left_names | right_names
            membership = isinstance(op, (ast.In, ast.NotIn))
            allowlist_compare = bool(names_in_test & allowlist_names)
            if membership or allowlist_compare:
                if isinstance(op, ast.NotIn) and _has_return(child.body, True):
                    return GatePosture.DEFAULT_ALLOW, False, "membership test returns True for a non-member (fail-open)"
                if isinstance(op, ast.IsNot) and allowlist_compare and _has_return(child.body, True):
                    return GatePosture.DEFAULT_ALLOW, False, "allowlist comparison returns True for a non-member (fail-open)"
                if isinstance(op, (ast.NotIn, ast.IsNot)) and _has_return(child.body, False):
                    return GatePosture.DEFAULT_DENY, True, "allowlist membership denies a non-member"
                if isinstance(op, (ast.In, ast.Is)) and _has_return(child.body, True):
                    return GatePosture.DEFAULT_DENY, True, "allowlist membership is the decision"
    for statement in _body_nodes(node):
        if isinstance(statement, ast.Return) and statement.value is not None and not isinstance(statement.value, ast.Constant):
            return GatePosture.DEFAULT_DENY, True, "non-constant boolean result can be false"
    body = getattr(node, "body", None)
    if isinstance(body, list):
        for index, statement in enumerate(body):
            if isinstance(statement, ast.Return) and isinstance(statement.value, ast.Constant) and statement.value.value is True:
                prior = body[:index]
                if not any(_has_return(item, False) for item in prior):
                    return GatePosture.DEFAULT_ALLOW, False, "guard has an unconditional True return before any deny path"
    if _has_return(node, True) and not _has_return(node, False):
        return GatePosture.DEFAULT_ALLOW, False, "guard has only an unconditional True return and no deny path"
    return GatePosture.DEFAULT_DENY, True, "a conditional deny path is visible"


def _declared_default(node: ast.AST | None) -> Any:
    """Return a literal assignment default, including ``Field(default=...)``."""

    direct = _literal(node)
    if direct is not None:
        return direct
    if isinstance(node, ast.Call):
        for keyword in node.keywords:
            if keyword.arg in {"default", "default_factory"}:
                return _literal(keyword.value)
    return None


def _literal_collection(node: ast.AST | None) -> tuple[list[Any], bool] | None:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    values = [_literal(item) for item in node.elts]
    return values, True


class _SourceVisitor(ast.NodeVisitor):
    """Collect operations, gates, and unknowns from one parsed module."""

    def __init__(self, state: _FileState, source: str, custom_rules: Mapping[str, Any] | None) -> None:
        self.state = state
        self.source = source
        self.source_lines = source.splitlines()
        self.custom_rules = custom_rules
        self.scopes: list[str] = []
        self.allowlist_names: set[str] = set()

    @property
    def current_scope(self) -> str | None:
        return self.scopes[-1] if self.scopes else None

    def _next_order(self) -> int:
        self.state.order += 1
        return self.state.order

    def _unknown(self, line: int, kind: str, reason: str, rule_id: str = RULE_DYNAMIC_OPERATION) -> None:
        self.state.unknowns.append(
            UnknownFinding(
                path=self.state.path,
                line=max(0, line),
                kind=kind,
                reason=reason,
                rule_id=rule_id,
            )
        )

    def _add_operation(
        self,
        node: ast.AST,
        action: str,
        description: str,
        *,
        tags: Iterable[str] = (),
        dynamic: bool = False,
        tool_name: str | None = None,
    ) -> None:
        effective_tags = set(tags)
        lowered_path = self.state.path.casefold()
        if any(marker in lowered_path for marker in ("/rsi/", "/evolution/", "/skills/", "/config/")) and any(marker in action.casefold() for marker in ("write", "promote", "rollback", "install", "update", "evolve", "skill")):
            effective_tags.add("self_modification")
        self.state.operations.append(
            _RawOperation(
                path=self.state.path,
                line=max(0, int(getattr(node, "lineno", 0) or 0)),
                action=action,
                description=description,
                scope=self.current_scope,
                dynamic=dynamic,
                tags=tuple(sorted(str(tag) for tag in effective_tags)),
                tool_name=tool_name,
                order=self._next_order(),
            )
        )

    def _add_gate(
        self,
        node: ast.AST,
        *,
        kind: GateKind,
        name: str,
        posture: GatePosture,
        description: str,
        rule_id: str,
        protects: Iterable[str] = (),
        can_fail: bool = True,
        ineffective_reason: str | None = None,
        tool_names: Iterable[str] = (),
        broad: bool = False,
    ) -> None:
        line = max(0, int(getattr(node, "lineno", 0) or 0))
        gate = Gate(
            id=_gate_id(kind.value, self.state.path, line, name),
            enforcement_point=name,
            kind=kind,
            default_posture=posture,
            protects=tuple(sorted({str(item) for item in protects})),
            source_file=self.state.path,
            source_line=line,
            rule_id=rule_id,
            description=description,
            can_fail=can_fail,
            ineffective_reason=ineffective_reason,
            metadata={"scope": self.current_scope or "", "broad": broad},
        )
        for existing in self.state.gates:
            if existing.gate.id == gate.id:
                merged = tuple(sorted(set(existing.gate.protects) | set(gate.protects)))
                existing.gate = existing.gate.model_copy(update={"protects": merged})
                return
        self.state.gates.append(
            _RawGate(
                gate=gate,
                path=self.state.path,
                scope=self.current_scope,
                order=self._next_order(),
                tool_names=frozenset(str(item) for item in tool_names),
                broad=broad,
            )
        )

    def _decorator_info(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, ast.AST | None]]:
        result: list[tuple[str, ast.AST | None]] = []
        for decorator in node.decorator_list:
            name = _dotted_name(decorator)
            if not name:
                self._unknown(node.lineno, "unrecognised_decorator", "decorator could not be resolved to a name")
                continue
            result.append((name, decorator))
        return result

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _parameter_tool_gates(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        arguments = node.args
        positional = [*getattr(arguments, "posonlyargs", []), *arguments.args, *arguments.kwonlyargs]
        defaults: list[ast.AST | None] = [None] * max(0, len(positional) - len(arguments.defaults))
        defaults.extend(arguments.defaults)
        defaults.extend(arguments.kw_defaults)
        for argument, default in zip(positional, defaults, strict=False):
            lowered = argument.arg.casefold()
            if not any(marker in lowered for marker in ("tool_names", "allowed_tools", "allowlist", "authorization_infrastructure")):
                continue
            collection = _literal_collection(default)
            if collection is not None:
                posture = GatePosture.DEFAULT_DENY if collection[0] else GatePosture.DEFAULT_ALLOW
                names = frozenset(str(item) for item in collection[0] if isinstance(item, str))
            elif isinstance(default, ast.Call) and _dotted_name(default.func).rsplit(".", 1)[-1] in {"list", "set", "frozenset", "tuple"} and not default.args:
                posture = GatePosture.DEFAULT_ALLOW
                names = frozenset()
            elif default is None:
                posture = GatePosture.DEFAULT_ALLOW
                names = frozenset()
            else:
                posture = GatePosture.DEFAULT_DENY
                names = frozenset()
            self._add_gate(
                node,
                kind=GateKind.TOOL_GUARD,
                name=argument.arg,
                posture=posture,
                description=f"tool authorization parameter {argument.arg}",
                rule_id="GATE-TOOL-003",
                tool_names=names,
            )

    def _may_contain_flags(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        end_line = getattr(node, "end_lineno", None)
        if not isinstance(end_line, int):
            return True
        segment = "\n".join(self.source_lines[max(0, node.lineno - 1) : end_line]).casefold()
        markers = (
            "approval",
            "human_approved",
            "requires_approval",
            "is_autonomous_trigger",
            "operator_token",
            "allowlist",
            "allowed",
            "scopes",
            "tool_names",
            "fail_closed",
            "allow_all",
            "allow_host_bash",
            "auto_promote",
            "non_interactive",
            "disable_clarification",
            "auth_disabled",
            "enabled",
            "security_fail_closed",
            "default_allow",
            "default_deny",
        )
        return any(marker in segment for marker in markers)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        decorators = self._decorator_info(node)
        scope = _scope_key(self.state.path, node.name, node.lineno)
        self.scopes.append(scope)
        self._parameter_tool_gates(node)
        approval_marker, allowlist_marker = self._scan_body_flags(node) if self._may_contain_flags(node) else (False, False)
        route_capability_id: str | None = None
        for name, decorator in decorators:
            leaf = name.rsplit(".", 1)[-1]
            lower = name.lower()
            call = decorator if isinstance(decorator, ast.Call) else None
            if leaf in _ROUTE_METHODS and ("router" in lower or "app" in lower or "route" in lower):
                method = leaf.upper()
                if method == "API_ROUTE":
                    method = "ANY"
                path_value = _literal(call.args[0]) if call is not None and call.args else "/"
                path = str(path_value or "/")
                action = _route_action(method, path)
                capability_id = _capability_id("route", self.state.path, node.lineno, f"{method}-{path}")
                route_capability_id = capability_id
                self._add_operation(node, action, f"HTTP {method} route {path}", tags=("route", method.lower()))
                self._add_gate(
                    node,
                    kind=GateKind.AUTHORIZATION,
                    name=name,
                    posture=GatePosture.DEFAULT_DENY,
                    description=f"route decorator {name} on {node.name}",
                    rule_id="GATE-ROUTE-001",
                    protects=(capability_id,),
                )
            if leaf in {"require_permission", "require_auth"}:
                args = [_literal(item) for item in call.args] if call is not None else []
                keywords = {item.arg: _literal(item.value) for item in call.keywords} if call is not None else {}
                if leaf == "require_permission":
                    permission_parts = [str(item) for item in args[:2] if item is not None]
                    if not permission_parts:
                        permission_parts = [str(keywords[name]) for name in ("resource", "action") if name in keywords]
                    permission = ":".join(permission_parts) or "unspecified"
                    self._add_gate(
                        node,
                        kind=GateKind.AUTHORIZATION,
                        name=name,
                        posture=GatePosture.DEFAULT_DENY,
                        description=f"permission decorator {name}({permission})" + (" owner_check" if keywords.get("owner_check") else ""),
                        rule_id="GATE-PERM-001",
                        protects=(route_capability_id,) if route_capability_id else (),
                    )
                else:
                    self._add_gate(
                        node,
                        kind=GateKind.AUTHORIZATION,
                        name=name,
                        posture=GatePosture.DEFAULT_DENY,
                        description=f"authentication decorator {name}",
                        rule_id="GATE-AUTH-001",
                        protects=(route_capability_id,) if route_capability_id else (),
                    )
            if leaf == "tool":
                explicit = _literal(call.args[0]) if call is not None and call.args else None
                tool_name = str(explicit or node.name)
                self.state.tool_scopes[tool_name] = scope
                self._add_operation(
                    node,
                    f"tool call {tool_name}",
                    f"registered model-visible tool {tool_name}",
                    tags=("tool",),
                    tool_name=tool_name,
                )
        if re.match(r"^(?:charge|payment|purchase|refund|billing|transfer|deploy|publish)_", node.name, re.I):
            self._add_operation(
                node,
                f"authority function {node.name}",
                f"function {node.name} is a financial/deployment surface",
                tags=("financial", "deployment"),
            )
        if _GUARD_NAME_RE.search(node.name) or allowlist_marker:
            posture, can_fail, reason = _posture_for_guard(node, self.allowlist_names)
            names: set[str] = set()
            stack = list(getattr(node, "body", ()) or ())
            while stack:
                literal = stack.pop()
                if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
                    names.add(literal.value)
                if isinstance(literal, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    continue
                stack.extend(ast.iter_child_nodes(literal))
            self._add_gate(
                node,
                kind=GateKind.TOOL_GUARD,
                name=node.name,
                posture=posture,
                description=f"tool/authorization guard {node.name}",
                rule_id="COV-002" if not can_fail else "GATE-TOOL-002",
                can_fail=can_fail,
                ineffective_reason=None if can_fail else reason,
                tool_names=names,
            )
        if _APPROVAL_NAME_RE.search(node.name) or approval_marker:
            self._add_gate(
                node,
                kind=GateKind.APPROVAL,
                name=node.name,
                posture=GatePosture.DEFAULT_DENY,
                description=f"human/operator approval surface {node.name}",
                rule_id="GATE-APPROVAL-001",
            )
        self.generic_visit(node)
        self.scopes.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        scope = _scope_key(self.state.path, node.name, node.lineno)
        self.scopes.append(scope)
        base_names = tuple(_dotted_name(base).rsplit(".", 1)[-1] for base in node.bases)
        if _MIDDLEWARE_NAME_RE.search(node.name) or any("Middleware" in base for base in base_names):
            self._add_gate(
                node,
                kind=GateKind.MIDDLEWARE,
                name=node.name,
                posture=GatePosture.DEFAULT_DENY,
                description=f"middleware class {node.name}",
                rule_id="GATE-MIDDLEWARE-001",
            )
        elif _GUARD_NAME_RE.search(node.name) or _POLICY_CLASS_RE.search(node.name):
            self._add_gate(
                node,
                kind=GateKind.TOOL_GUARD,
                name=node.name,
                posture=GatePosture.DEFAULT_DENY,
                description=f"guard class {node.name}",
                rule_id="GATE-TOOL-001",
            )
        self.generic_visit(node)
        self.scopes.pop()

    def _scan_body_flags(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[bool, bool]:
        approval_marker = False
        allowlist_marker = False
        for child in _body_nodes(node):
            if isinstance(child, ast.Name) and child.id in {"requires_approval", "human_approved", "operator_token"}:
                approval_marker = True
            if isinstance(child, ast.Attribute) and child.attr in {"requires_approval", "human_approved", "operator_token"}:
                approval_marker = True
            targets: tuple[str, ...] = ()
            value: ast.AST | None = None
            if isinstance(child, ast.Assign):
                targets = tuple(name for target in child.targets for name in _target_names(target))
                value = child.value
            elif isinstance(child, ast.AnnAssign):
                targets = _target_names(child.target)
                value = child.value
            if not targets or value is None:
                continue
            for target in targets:
                if target in self.allowlist_names or _ALLOWLIST_NAME_RE.search(target):
                    allowlist_marker = True
                    collection = _literal_collection(value)
                    if isinstance(value, ast.Constant) and value.value is None:
                        posture, can_fail, reason = GatePosture.DEFAULT_ALLOW, True, "None allowlist means no allowlist (allow all)"
                    elif collection is not None and not collection[0]:
                        posture, can_fail, reason = GatePosture.DEFAULT_DENY, True, "explicit empty allowlist denies non-members"
                    else:
                        posture, can_fail, reason = GatePosture.DEFAULT_DENY, True, "membership allowlist denies non-members"
                    self.allowlist_names.add(target)
                    self._add_gate(
                        child,
                        kind=GateKind.TOOL_GUARD,
                        name=target,
                        posture=posture,
                        description=f"allowlist/denylist declaration {target}",
                        rule_id="GATE-ALLOWLIST-001",
                        can_fail=can_fail,
                        ineffective_reason=None if can_fail else reason,
                        broad=True,
                    )
        return approval_marker, allowlist_marker

    def visit_Call(self, node: ast.Call) -> None:
        name = _dotted_name(node.func)
        leaf = name.rsplit(".", 1)[-1].lower()
        lowered = name.lower()
        if leaf in _DYNAMIC_CALL_NAMES or lowered in {"importlib.import_module", "reflection.resolve_variable"}:
            self._unknown(node.lineno, "dynamic_registration", f"dynamic authority surface via {name or 'unresolved call'}")
            self._add_operation(
                node,
                f"dynamic registration {name or 'unresolved call'}",
                "authority is selected through reflection or runtime registration",
                tags=("dynamic",),
                dynamic=True,
            )
        if _is_authority_call(name, node):
            action = _operation_action(name, node)
            tags = _operation_tags(name, leaf)
            self._add_operation(
                node,
                action,
                f"{name} performs an authority-bearing operation",
                tags=tags,
            )
        literal_values = list(_strings_outside_nested_calls((*node.args, *(item.value for item in node.keywords))))
        shell_text = " ".join(_safe_literal(value, action_name="shell command") for value in literal_values)
        branch_delete = "branch" in literal_values and any(value in {"-D", "--delete"} for value in literal_values)
        if branch_delete:
            shell_text = f"git branch -D {shell_text}"
        if _SHELL_DESTRUCTIVE_RE.search(shell_text):
            action_text = "git branch delete" if branch_delete else f"shell command {shell_text.strip()[:120]}"
            self._add_operation(
                node,
                action_text,
                "literal shell command with destructive or external effect",
                tags=("shell",),
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._visit_assignment(node, node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._visit_assignment(node, (node.target,), node.value)
        self.generic_visit(node)

    def _visit_assignment(self, node: ast.AST, targets: Iterable[ast.AST], value: ast.AST | None) -> None:
        if value is None:
            return
        names = tuple(name for target in targets for name in _target_names(target))
        literal_value = _declared_default(value)
        for name in names:
            if name == "mode" and isinstance(literal_value, str) and literal_value in {"open", "isolated", "allowlist"}:
                network_posture = GatePosture.DEFAULT_ALLOW if literal_value == "open" else GatePosture.DEFAULT_DENY
                self._add_gate(
                    node,
                    kind=GateKind.SANDBOX,
                    name="sandbox.network.mode",
                    posture=network_posture,
                    description=f"sandbox network mode default {literal_value}",
                    rule_id="GATE-NETWORK-001",
                )
            if name == "approval" and isinstance(literal_value, str) and literal_value in {"prompt", "deny"}:
                self._add_gate(
                    node,
                    kind=GateKind.APPROVAL,
                    name="sandbox.network.approval",
                    posture=GatePosture.DEFAULT_DENY,
                    description=f"sandbox network approval default {literal_value}",
                    rule_id="GATE-NETWORK-APPROVAL-001",
                )
        for name in names:
            lowered = name.lower()
            if lowered in _SECRET_NAMES or any(secret in lowered for secret in ("secret", "credential", "password", "token")):
                self._add_operation(
                    node,
                    f"secret access {name}",
                    f"reads or stores sensitive value {name}",
                    tags=("privacy",),
                )
            if lowered in _FLAG_NAMES:
                constant = _declared_default(value)
                posture: GatePosture | None = None
                if isinstance(constant, bool):
                    if constant is False or lowered in {
                        "allow_all",
                        "allow_host_bash",
                        "auto_promote",
                        "non_interactive",
                        "disable_clarification",
                        "auth_disabled",
                        "default_allow",
                    }:
                        posture = GatePosture.DEFAULT_ALLOW
                    else:
                        posture = GatePosture.DEFAULT_DENY
                    if lowered in {"fail_closed", "security_fail_closed", "default_deny"} and constant is False:
                        posture = GatePosture.DEFAULT_ALLOW
                elif isinstance(constant, str) and constant in {item.value for item in GatePosture}:
                    posture = GatePosture(constant)
                if posture is not None:
                    self._add_gate(
                        node,
                        kind=GateKind.CONFIG_FLAG,
                        name=name,
                        posture=posture,
                        description=f"configuration flag {name}={constant}",
                        rule_id="GATE-FLAG-001",
                    )
        if any(_ALLOWLIST_NAME_RE.search(name) for name in names):
            collection = _literal_collection(value)
            literal_names = frozenset(str(item) for item in (collection[0] if collection else []) if isinstance(item, str))
            for name in names:
                self.allowlist_names.add(name)
                if isinstance(value, ast.Constant) and value.value is None:
                    posture, can_fail, reason = GatePosture.DEFAULT_ALLOW, True, "None allowlist means no allowlist (allow all)"
                elif collection is not None and not collection[0]:
                    posture, can_fail, reason = GatePosture.DEFAULT_DENY, True, "explicit empty allowlist denies non-members"
                else:
                    posture, can_fail, reason = GatePosture.DEFAULT_DENY, True, "membership allowlist denies non-members"
                self._add_gate(
                    node,
                    kind=GateKind.TOOL_GUARD,
                    name=name,
                    posture=posture,
                    description=f"allowlist/denylist declaration {name}",
                    rule_id="GATE-ALLOWLIST-001",
                    can_fail=can_fail,
                    ineffective_reason=None if can_fail else reason,
                    tool_names=literal_names,
                    broad=True,
                )
        if any(name in {"BUILTIN_TOOLS", "SUBAGENT_TOOLS", "TOOL_ALLOWLIST", "TOOLS"} for name in names):
            self._add_gate(
                node,
                kind=GateKind.TOOL_GUARD,
                name=names[0],
                posture=GatePosture.DEFAULT_ALLOW,
                description=f"tool catalogue declaration {names[0]} (registration is not enforcement)",
                rule_id="GATE-CATALOG-001",
                broad=True,
            )
        if any("entries" in name.lower() or "catalog" in name.lower() for name in names):
            self._scan_command_catalog(value)

    def _scan_command_catalog(self, value: ast.AST) -> None:
        for element in ast.walk(value):
            if not isinstance(element, (ast.Tuple, ast.List)) or not element.elts:
                continue
            command = _literal(element.elts[0])
            if not isinstance(command, str) or not command.startswith("/"):
                continue
            command_lower = command.lower().lstrip("/")
            approval_index_present = len(element.elts) >= 7 and _literal(element.elts[6]) is True
            human_word = any(word in command_lower for word in _HUMAN_COMMAND_WORDS)
            if not (approval_index_present or human_word):
                continue
            self._add_operation(
                element,
                f"human command {command}",
                f"channel command {command} pauses for a human decision",
                tags=("command", "approval"),
            )
            self._add_gate(
                element,
                kind=GateKind.APPROVAL,
                name=command,
                posture=GatePosture.DEFAULT_DENY,
                description=f"command approval/clarification surface {command}",
                rule_id="GATE-COMMAND-001",
            )


def _route_action(method: str, path: str) -> str:
    if method in _READ_METHODS:
        return f"read route {method} {path}"
    if method == "DELETE":
        return f"delete route {method} {path}"
    return f"update route {method} {path}"


def _operation_tags(name: str, leaf: str) -> tuple[str, ...]:
    lowered = name.lower()
    tags: set[str] = set()
    if leaf in _DESTRUCTIVE_CALL_NAMES or any(word in lowered for word in ("remove", "delete", "drop", "truncate", "unlink")):
        tags.add("destructive")
    if leaf in _EXEC_CALL_NAMES or any(word in lowered for word in ("subprocess", "system", "popen", "eval", "exec")):
        tags.add("execution")
    if any(word in lowered for word in ("http", "request", "socket", "urlopen", "fetch", "download", "web")):
        tags.add("network")
    if leaf in _SEND_CALL_NAMES or any(word in lowered for word in ("send", "publish", "notify", "message")):
        tags.add("external")
    return tuple(sorted(tags))


def _safe_literal(value: Any, *, action_name: str = "") -> str:
    """Render a literal for a report without copying credential material."""

    text = str(value)
    lowered = f"{action_name} {text}".lower()
    if any(marker in lowered for marker in ("password", "passwd", "secret", "token", "api_key", "apikey", "private_key", "authorization", "bearer")):
        return "<redacted>"
    return text[:80]


def _operation_action(name: str, node: ast.Call) -> str:
    leaf = name.rsplit(".", 1)[-1]
    args: list[str] = []
    for argument in node.args[:2]:
        value = _literal(argument)
        if value is not None:
            args.append(_safe_literal(value, action_name=name))
    suffix = f" {args[0]}" if args else ""
    return f"{name}{suffix}" if name else leaf + suffix


def _strings_outside_nested_calls(nodes: Iterable[ast.AST]) -> Iterator[str]:
    """Yield string literals under *nodes* without descending into calls.

    Each nested call is scanned by its own ``visit_Call`` invocation.  Not
    descending here prevents an outer call from repeatedly traversing the
    whole inner call tree.
    """

    stack = list(nodes)
    while stack:
        child = stack.pop()
        if isinstance(child, ast.Call):
            continue
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            yield child.value
        stack.extend(ast.iter_child_nodes(child))


def _is_authority_call(name: str, node: ast.Call) -> bool:
    if not name:
        return False
    leaf = name.rsplit(".", 1)[-1].lower()
    lowered = name.lower()
    qualified_prefixes = (
        "os.",
        "pathlib.",
        "shutil.",
        "subprocess.",
        "httpx.",
        "requests.",
        "urllib.",
        "socket.",
        "session.",
        "db.",
        "database.",
        "git.",
        "stripe.",
        "boto3.",
        "kubernetes.",
    )
    if any(lowered.startswith(prefix) for prefix in qualified_prefixes):
        return True
    if leaf in _EXEC_CALL_NAMES:
        return True
    if leaf in _DESTRUCTIVE_CALL_NAMES:
        return True
    if leaf in {"write_text", "write_bytes", "mkdir", "touch"}:
        return True
    if leaf in _SEND_CALL_NAMES:
        return True
    if any(marker in leaf for marker in ("secret", "credential", "password", "api_key", "private_key", "get_token")):
        return True
    if leaf in {"charge", "checkout", "purchase", "refund", "transfer", "billing", "deploy", "release", "promote", "rollback"}:
        return True
    if any(word in leaf for word in ("delete", "remove", "destroy", "drop", "truncate", "unlink", "deploy", "publish", "revoke", "wipe")):
        return True
    if leaf in {"execute", "commit", "insert", "update", "upsert", "delete", "add", "merge", "save"} and any(word in lowered for word in ("session", "db", "database", "sql", "conn", "connection")):
        return True
    if leaf in _EGRESS_CALL_NAMES and any(word in lowered for word in ("http", "request", "url", "session", "client", "socket", "web", "fetch")):
        return True
    if leaf in {"get", "post", "put", "patch", "delete"} and any(word in lowered for word in ("client", "request", "http", "session", "api", "web", "remote")):
        return True
    return False


def _gate_covers_operation(raw_gate: _RawGate, operation: _RawOperation, file_state: _FileState) -> bool:
    if raw_gate.path != operation.path:
        return False
    if raw_gate.scope and operation.scope and (operation.scope == raw_gate.scope or operation.scope.startswith(raw_gate.scope + "::")):
        return True
    if operation.tool_name and raw_gate.tool_names and operation.tool_name in raw_gate.tool_names:
        return True
    if raw_gate.tool_names and any(name and ((operation.scope is not None and name in operation.scope) or name in operation.action) for name in raw_gate.tool_names):
        return True
    if raw_gate.scope and not operation.scope:
        return False
    if raw_gate.broad and operation.tool_name:
        return True
    # A file-level allowlist is associated with a literal tool name or with a
    # sole operation in that file.  This keeps an unrelated module-level list
    # from silently claiming every later capability.
    if raw_gate.broad and operation.action.startswith("tool call"):
        return True
    if raw_gate.broad:
        same_file_ops = [item for item in file_state.operations if item.path == operation.path]
        return len(same_file_ops) == 1
    return False


def _associate(state: _FileState) -> None:
    for operation in state.operations:
        candidates = [raw for raw in state.gates if _gate_covers_operation(raw, operation, state)]
        if not candidates:
            continue
        candidates.sort(key=lambda raw: (0 if raw.gate.default_posture is GatePosture.DEFAULT_DENY else 1, raw.order))
        chosen = candidates[0].gate
        ids = {candidate.gate.id for candidate in candidates}
        for candidate in candidates:
            updated = list(candidate.gate.protects)
            if _capability_for_operation(operation) not in updated:
                updated.append(_capability_for_operation(operation))
            candidate.gate = candidate.gate.model_copy(update={"protects": tuple(sorted(set(updated)))})
        operation.associated_gate_ids = tuple(sorted(ids))
        operation.chosen_gate = chosen.id


def _capability_for_operation(operation: _RawOperation) -> str:
    return _capability_id("operation", operation.path, operation.line, operation.action)


def _make_record(
    operation: _RawOperation,
    gate: Gate | None,
    custom_rules: Mapping[str, Any] | None,
) -> tuple[AuthorityRecord, Capability, UnknownFinding | None]:
    effective_gate = gate is not None and gate.can_fail and gate.default_posture is GatePosture.DEFAULT_DENY
    classification: Classification = classify_action(
        operation.action,
        currently_gated=effective_gate,
        gate=gate,
        dynamic=operation.dynamic,
        custom_rules=custom_rules,
    )
    capability = capability_from_classification(
        capability_id=_capability_for_operation(operation),
        description=operation.description,
        source_file=operation.path,
        source_line=operation.line,
        action=operation.action,
        classification=classification,
        currently_gated=effective_gate,
        gating=(f"{gate.description} ({gate.default_posture.value}, can_fail={gate.can_fail})" if gate is not None else "no statically associated default-deny gate"),
        tags=operation.tags,
        metadata={"scope": operation.scope or "", "gate_ids": operation.associated_gate_ids},
    )
    unknown = None
    if classification.verdict is Verdict.UNKNOWN or operation.dynamic:
        unknown = UnknownFinding(
            path=operation.path,
            line=operation.line,
            kind="dynamic_operation" if operation.dynamic else "unclassified_capability",
            reason=classification.verdict_reason,
            rule_id=classification.rule_id,
        )
    reason = classification.verdict_reason
    if gate is not None:
        reason = f"{reason}; gate {gate.id} ({gate.default_posture.value}, can_fail={gate.can_fail})"
    record = AuthorityRecord(
        capability=capability,
        gate=gate,
        verdict=classification.verdict,
        verdict_rule_id=classification.verdict_rule_id,
        reason=reason,
    )
    return record, capability, unknown


def _parse_source(path: Path, relative: str) -> tuple[str, ast.Module | None, UnknownFinding | None]:
    try:
        source = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        return (
            "",
            None,
            UnknownFinding(
                path=relative,
                line=0,
                kind="read_error",
                reason=f"source could not be read as UTF-8: {exc}",
                rule_id="UNK-001",
            ),
        )
    try:
        return source, ast.parse(source, filename=relative), None
    except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError) as exc:
        line = int(getattr(exc, "lineno", 0) or 0)
        return (
            source,
            None,
            UnknownFinding(
                path=relative,
                line=line,
                kind="parse_error",
                reason=f"file did not parse and is an audit gap: {exc}",
                rule_id="UNK-001",
            ),
        )


def iter_source_files(root: str | Path, config: AuthorityAuditConfig | None = None) -> list[Path]:
    """Enumerate deterministic Python source files using the config readers."""

    base = Path(root).resolve()
    settings = config or AuthorityAuditConfig()
    files: set[Path] = set()
    for scanned_root in settings.resolve_scanned_roots(base):
        if scanned_root.is_file():
            if scanned_root.suffix == ".py" and not settings.is_excluded(scanned_root, base):
                files.add(scanned_root)
            continue
        for candidate in scanned_root.rglob("*.py"):
            if not candidate.is_file() or settings.is_excluded(candidate, base):
                continue
            files.add(candidate)
    return sorted(files, key=lambda item: _relative(item, base))


def _choose_gate(operation: _RawOperation, state: _FileState) -> Gate | None:
    chosen_id = operation.chosen_gate
    if chosen_id is None:
        return None
    for raw in state.gates:
        if raw.gate.id == chosen_id:
            return raw.gate
    return None


def _unknown_authority_record(finding: UnknownFinding) -> AuthorityRecord:
    capability = Capability(
        id=_capability_id("unknown", finding.path, finding.line, finding.kind),
        description=f"unclassified authority surface: {finding.reason}",
        source_file=finding.path,
        source_line=finding.line,
        concrete_action="unknown authority surface",
        reversibility_class=Reversibility.IRREVERSIBLE,
        currently_gated=False,
        gating="unknown; no enforcement association may be assumed",
        externality=Externality.UNKNOWN,
        classification_rule_id=finding.rule_id,
        tags=("unknown", finding.kind),
    )
    return AuthorityRecord(
        capability=capability,
        gate=None,
        verdict=Verdict.UNKNOWN,
        verdict_rule_id="VERDICT-UNK-001",
        reason=finding.reason,
    )


def scan(
    root: str | Path,
    *,
    config: AuthorityAuditConfig | None = None,
    revision: str = "unrevised",
) -> AuditReport:
    """Run a static census of ``root`` and return an :class:`AuditReport`.

    The caller may pass an injected ``revision`` (normally ``git rev-parse
    HEAD``).  No wall-clock value is read here, so two scans of identical bytes
    produce byte-identical JSON and Markdown.
    """

    base = Path(root).resolve()
    settings = config or AuthorityAuditConfig()
    # Explicit scan is an explicit request.  Reading the enabled key here keeps
    # the default-OFF contract visible to embedders without making the CLI a
    # silent no-op.
    settings.should_run(explicitly_requested=True)
    custom_rules = settings.read_rule_set(base)
    states: list[_FileState] = []
    all_unknowns: list[UnknownFinding] = []
    all_unknown_keys: set[tuple[str, int, str, str, str]] = set()
    all_gaps: list[AuditGap] = []
    scanned_files: list[str] = []

    def remember_unknown(finding: UnknownFinding) -> None:
        key = (finding.path, finding.line, finding.kind, finding.reason, finding.rule_id)
        if key not in all_unknown_keys:
            all_unknown_keys.add(key)
            all_unknowns.append(finding)

    explicit_root_values = [Path(item) if Path(item).is_absolute() else base / item for item in settings.scanned_roots]
    if any(item.exists() for item in explicit_root_values):
        for missing in explicit_root_values:
            if not missing.exists():
                all_gaps.append(
                    AuditGap(
                        kind="missing_scanned_root",
                        path=_relative(missing, base),
                        line=0,
                        reason="configured scanned root does not exist; coverage may be partial",
                        rule_id="COV-ROOT-001",
                    )
                )

    for source_path in iter_source_files(base, settings):
        relative = _relative(source_path, base)
        scanned_files.append(relative)
        source, tree, parse_unknown = _parse_source(source_path, relative)
        state = _FileState(path=relative)
        states.append(state)
        if parse_unknown is not None:
            state.unknowns.append(parse_unknown)
            remember_unknown(parse_unknown)
            continue
        if tree is None:  # pragma: no cover - parse_unknown covers all failures
            continue
        visitor = _SourceVisitor(state, source, custom_rules)
        try:
            visitor.visit(tree)
        except (RecursionError, MemoryError) as exc:
            unknown = UnknownFinding(
                path=relative,
                line=0,
                kind="analysis_error",
                reason=f"AST traversal could not complete: {exc}",
                rule_id="UNK-001",
            )
            state.unknowns.append(unknown)
            remember_unknown(unknown)
        _associate(state)
        for finding in state.unknowns:
            remember_unknown(finding)
        all_gaps.extend(state.gaps)

    records: list[AuthorityRecord] = []
    all_gates: dict[str, Gate] = {}
    for state in states:
        state_records: list[AuthorityRecord] = []
        for operation in sorted(state.operations, key=lambda item: (item.line, item.order, item.action)):
            gate = _choose_gate(operation, state)
            record, _capability, unknown = _make_record(operation, gate, custom_rules)
            state_records.append(record)
            if unknown is not None:
                remember_unknown(unknown)
        recorded_locations = {(record.capability.source_file, record.capability.source_line) for record in state_records}
        for finding in state.unknowns:
            if (finding.path, finding.line) not in recorded_locations:
                state_records.append(_unknown_authority_record(finding))
        records.extend(state_records)

    for state in states:
        for raw in state.gates:
            all_gates[raw.gate.id] = raw.gate
    records.sort(key=lambda item: (item.capability.source_file, item.capability.source_line, item.capability.id))
    gates = tuple(sorted(all_gates.values(), key=lambda item: (item.source_file, item.source_line, item.id)))
    unknowns = tuple(sorted({(item.path, item.line, item.kind, item.reason, item.rule_id): item for item in all_unknowns}.values(), key=lambda item: (item.path, item.line, item.kind, item.reason)))
    gaps = tuple(sorted({(item.kind, item.path, item.line, item.reason, item.rule_id): item for item in all_gaps}.values(), key=lambda item: (item.path, item.line, item.kind)))

    counts = {verdict.value: 0 for verdict in Verdict}
    for record in records:
        counts[record.verdict.value] += 1
    coverage = compute_coverage(records, gates)
    report = AuditReport(
        revision=str(revision or "unrevised"),
        records=tuple(records),
        gates=gates,
        unknowns=unknowns,
        gaps=gaps,
        verdict_counts=counts,
        coverage=coverage,
        ungated_destructive_capabilities=coverage.ungated_destructive,
        ineffective_gates=coverage.ineffective_gates,
        scanned_files=tuple(sorted(set(scanned_files))),
    )
    return report


def scan_tree(
    root: str | Path,
    *,
    config: AuthorityAuditConfig | None = None,
    revision: str = "unrevised",
) -> AuditReport:
    """Compatibility alias for :func:`scan`."""

    return scan(root, config=config, revision=revision)


def census(
    root: str | Path,
    *,
    config: AuthorityAuditConfig | None = None,
    revision: str = "unrevised",
) -> AuditReport:
    """Short alias for :func:`scan` used by integrations."""

    return scan(root, config=config, revision=revision)


__all__ = [
    "census",
    "iter_source_files",
    "scan",
    "scan_tree",
]
