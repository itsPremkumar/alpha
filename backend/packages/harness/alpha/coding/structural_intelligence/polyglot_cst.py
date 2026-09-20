"""Polyglot Concrete Syntax Tree (CST) and Symbol Parser.

Supports Python (via ast), TypeScript/JavaScript, Go, Rust, Java, and C/C++
with concrete symbol extraction (DEFINES, CALLS, IMPORTS, INHERITS) and
optional Tree-Sitter grammars when available.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class SymbolKind(str, Enum):
    MODULE = "module"
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    INTERFACE = "interface"
    STRUCT = "struct"
    TRAIT = "trait"
    TYPE_ALIAS = "type_alias"
    VARIABLE = "variable"
    IMPORT = "import"


@dataclass
class SymbolNode:
    id: str  # Unique qualified identifier, e.g. "path/to/file.py:ClassName.method_name"
    name: str
    kind: SymbolKind
    file_path: str
    start_line: int
    end_line: int
    docstring: str = ""
    parameters: list[str] = field(default_factory=list)
    return_type: Optional[str] = None
    modifiers: list[str] = field(default_factory=list)
    parent_symbol: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "docstring": self.docstring,
            "parameters": self.parameters,
            "return_type": self.return_type,
            "modifiers": self.modifiers,
            "parent_symbol": self.parent_symbol,
        }


@dataclass
class ParsedFile:
    file_path: str
    language: str
    symbols: list[SymbolNode] = field(default_factory=list)
    defines_edges: list[tuple[str, str]] = field(default_factory=list)  # (source_id, target_id)
    calls_edges: list[tuple[str, str]] = field(default_factory=list)  # (caller_id, callee_name)
    imports_edges: list[tuple[str, str]] = field(default_factory=list)  # (file_path, imported_module)
    inherits_edges: list[tuple[str, str]] = field(default_factory=list)  # (subclass_id, base_class_name)


class _PythonASTVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str, code_lines: list[str]) -> None:
        self.file_path = file_path
        self.code_lines = code_lines
        self.symbols: list[SymbolNode] = []
        self.defines_edges: list[tuple[str, str]] = []
        self.calls_edges: list[tuple[str, str]] = []
        self.imports_edges: list[tuple[str, str]] = []
        self.inherits_edges: list[tuple[str, str]] = []
        self._current_scope: list[str] = []
        self._current_class: Optional[str] = None

    def _get_call_name(self, func_node: ast.AST) -> Optional[str]:
        if isinstance(func_node, ast.Name):
            return func_node.id
        elif isinstance(func_node, ast.Attribute):
            return func_node.attr
        return None

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports_edges.append((self.file_path, alias.name))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            target = f"{module}.{alias.name}" if module else alias.name
            self.imports_edges.append((self.file_path, target))
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        class_id = f"{self.file_path}:{node.name}"
        end_line = getattr(node, "end_lineno", node.lineno)
        doc = ast.get_docstring(node) or ""
        
        # Base inheritance
        for base in node.bases:
            if isinstance(base, ast.Name):
                self.inherits_edges.append((class_id, base.id))
            elif isinstance(base, ast.Attribute):
                self.inherits_edges.append((class_id, base.attr))

        sym = SymbolNode(
            id=class_id,
            name=node.name,
            kind=SymbolKind.CLASS,
            file_path=self.file_path,
            start_line=node.lineno,
            end_line=end_line,
            docstring=doc,
            parent_symbol=self.file_path,
        )
        self.symbols.append(sym)
        self.defines_edges.append((self.file_path, class_id))

        prev_class = self._current_class
        self._current_class = class_id
        self._current_scope.append(node.name)

        # Visit inner nodes
        self.generic_visit(node)

        self._current_scope.pop()
        self._current_class = prev_class

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        is_method = self._current_class is not None
        kind = SymbolKind.METHOD if is_method else SymbolKind.FUNCTION
        
        if is_method and self._current_class:
            sym_id = f"{self._current_class}.{node.name}"
            parent_id = self._current_class
        else:
            sym_id = f"{self.file_path}:{node.name}"
            parent_id = self.file_path

        end_line = getattr(node, "end_lineno", node.lineno)
        doc = ast.get_docstring(node) or ""
        params = [arg.arg for arg in node.args.args]
        
        return_annotation = None
        if node.returns:
            try:
                return_annotation = ast.unparse(node.returns)
            except Exception:
                return_annotation = None

        modifiers = []
        if isinstance(node, ast.AsyncFunctionDef):
            modifiers.append("async")
        for deco in node.decorator_list:
            if isinstance(deco, ast.Name):
                modifiers.append(f"@{deco.id}")

        sym = SymbolNode(
            id=sym_id,
            name=node.name,
            kind=kind,
            file_path=self.file_path,
            start_line=node.lineno,
            end_line=end_line,
            docstring=doc,
            parameters=params,
            return_type=return_annotation,
            modifiers=modifiers,
            parent_symbol=parent_id,
        )
        self.symbols.append(sym)
        self.defines_edges.append((parent_id, sym_id))

        prev_scope = list(self._current_scope)
        self._current_scope.append(node.name)

        # Track calls within this function/method body
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                call_name = self._get_call_name(child.func)
                if call_name:
                    self.calls_edges.append((sym_id, call_name))

        # Do not recurse into generic_visit since we walked child nodes,
        # but check for nested functions
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.visit(item)

        self._current_scope = prev_scope

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)


class PolyglotCSTParser:
    """Polyglot parser for extracting AST symbols, definitions, calls, imports, and inheritance."""

    SUPPORTED_EXTENSIONS: dict[str, str] = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c",
        ".hpp": "cpp",
    }

    def detect_language(self, file_path: str) -> str:
        ext = Path(file_path).suffix.lower()
        return self.SUPPORTED_EXTENSIONS.get(ext, "text")

    def parse_source(self, file_path: str, code: str) -> ParsedFile:
        """Parse source code string into structured AST symbols and dependency relations."""
        norm_path = str(Path(file_path)).replace("\\", "/")
        language = self.detect_language(file_path)
        
        parsed = ParsedFile(file_path=norm_path, language=language)

        if language == "python":
            self._parse_python(norm_path, code, parsed)
        elif language in ("typescript", "javascript"):
            self._parse_typescript_javascript(norm_path, code, parsed)
        elif language == "go":
            self._parse_go(norm_path, code, parsed)
        elif language == "rust":
            self._parse_rust(norm_path, code, parsed)
        elif language == "java":
            self._parse_java(norm_path, code, parsed)
        elif language in ("c", "cpp"):
            self._parse_c_cpp(norm_path, code, parsed)
        else:
            # Generic fallback: search for top-level definitions
            self._parse_generic(norm_path, code, parsed)

        return parsed

    def _parse_python(self, file_path: str, code: str, parsed: ParsedFile) -> None:
        lines = code.splitlines()
        try:
            tree = ast.parse(code, filename=file_path)
        except Exception:
            # Fall back to regex parsing on syntax errors
            self._parse_generic(file_path, code, parsed)
            return

        visitor = _PythonASTVisitor(file_path, lines)
        visitor.visit(tree)

        parsed.symbols.extend(visitor.symbols)
        parsed.defines_edges.extend(visitor.defines_edges)
        parsed.calls_edges.extend(visitor.calls_edges)
        parsed.imports_edges.extend(visitor.imports_edges)
        parsed.inherits_edges.extend(visitor.inherits_edges)

    def _parse_typescript_javascript(self, file_path: str, code: str, parsed: ParsedFile) -> None:
        lines = code.splitlines()

        # 1. Imports
        import_pattern = re.compile(r'import\s+(?:(?:\{[^}]*\}|\*\s+as\s+[\w$]+|[\w$]+)\s+from\s+)?[\'"]([^\'"]+)[\'"]|require\([\'"]([^\'"]+)[\'"]')
        for line in lines:
            for match in import_pattern.finditer(line):
                target = match.group(1) or match.group(2)
                if target:
                    parsed.imports_edges.append((file_path, target))

        # 2. Classes & Interfaces
        class_pattern = re.compile(r'(?:export\s+)?(?:default\s+)?class\s+([\w$]+)(?:\s+extends\s+([\w$]+))?(?:\s+implements\s+([\w$,\s]+))?')
        interface_pattern = re.compile(r'(?:export\s+)?interface\s+([\w$]+)(?:\s+extends\s+([\w$,\s]+))?')
        
        # 3. Functions & Methods
        func_pattern = re.compile(r'(?:export\s+)?(?:async\s+)?function\s+([\w$]+)(?:<[^>]+>)?\s*\(([^)]*)\)')
        arrow_func_pattern = re.compile(r'(?:export\s+)?(?:const|let|var)\s+([\w$]+)\s*=\s*(?:async\s*)?(?:<[^>]+>)?\s*\(([^)]*)\)\s*(?::\s*[^=]+)?\s*=>')
        method_pattern = re.compile(r'^\s*(?:public|private|protected|static|async|\s)*\s*([\w$]+)(?:<[^>]+>)?\s*\(([^)]*)\)\s*(?::\s*[^{]+)?\s*\{?')

        current_class: Optional[str] = None
        brace_depth = 0

        for idx, line in enumerate(lines, 1):
            brace_depth += line.count("{") - line.count("}")
            if brace_depth <= 0:
                current_class = None

            # Class
            cm = class_pattern.search(line)
            if cm:
                name = cm.group(1)
                extends = cm.group(2)
                implements = cm.group(3)
                class_id = f"{file_path}:{name}"
                current_class = class_id
                parsed.symbols.append(
                    SymbolNode(
                        id=class_id,
                        name=name,
                        kind=SymbolKind.CLASS,
                        file_path=file_path,
                        start_line=idx,
                        end_line=idx,
                        parent_symbol=file_path,
                    )
                )
                parsed.defines_edges.append((file_path, class_id))
                if extends:
                    parsed.inherits_edges.append((class_id, extends.strip()))
                if implements:
                    for iface in implements.split(","):
                        if iface.strip():
                            parsed.inherits_edges.append((class_id, iface.strip()))
                continue

            # Interface
            im = interface_pattern.search(line)
            if im:
                name = im.group(1)
                extends = im.group(2)
                iface_id = f"{file_path}:{name}"
                parsed.symbols.append(
                    SymbolNode(
                        id=iface_id,
                        name=name,
                        kind=SymbolKind.INTERFACE,
                        file_path=file_path,
                        start_line=idx,
                        end_line=idx,
                        parent_symbol=file_path,
                    )
                )
                parsed.defines_edges.append((file_path, iface_id))
                if extends:
                    for ext in extends.split(","):
                        if ext.strip():
                            parsed.inherits_edges.append((iface_id, ext.strip()))
                continue

            # Function
            fm = func_pattern.search(line) or arrow_func_pattern.search(line)
            if fm:
                name = fm.group(1)
                params = [p.strip().split(":")[0].strip() for p in fm.group(2).split(",") if p.strip()]
                fn_id = f"{file_path}:{name}"
                parsed.symbols.append(
                    SymbolNode(
                        id=fn_id,
                        name=name,
                        kind=SymbolKind.FUNCTION,
                        file_path=file_path,
                        start_line=idx,
                        end_line=idx,
                        parameters=params,
                        parent_symbol=file_path,
                    )
                )
                parsed.defines_edges.append((file_path, fn_id))
                continue

            # Method inside class
            if current_class:
                mm = method_pattern.search(line)
                if mm:
                    mname = mm.group(1)
                    if mname not in ("if", "for", "while", "switch", "catch", "return", "throw"):
                        params = [p.strip().split(":")[0].strip() for p in mm.group(2).split(",") if p.strip()]
                        mid = f"{current_class}.{mname}"
                        parsed.symbols.append(
                            SymbolNode(
                                id=mid,
                                name=mname,
                                kind=SymbolKind.METHOD,
                                file_path=file_path,
                                start_line=idx,
                                end_line=idx,
                                parameters=params,
                                parent_symbol=current_class,
                            )
                        )
                        parsed.defines_edges.append((current_class, mid))

        # Calls detection
        call_pattern = re.compile(r'([\w$]+)\s*\(')
        for line in lines:
            for match in call_pattern.finditer(line):
                call_name = match.group(1)
                if call_name not in ("if", "for", "while", "switch", "catch", "function"):
                    parsed.calls_edges.append((file_path, call_name))

    def _parse_go(self, file_path: str, code: str, parsed: ParsedFile) -> None:
        lines = code.splitlines()
        import_single = re.compile(r'import\s+"([^"]+)"')
        type_struct = re.compile(r'type\s+(\w+)\s+struct')
        type_interface = re.compile(r'type\s+(\w+)\s+interface')
        func_decl = re.compile(r'func\s+(?:\((?:[\w*]+\s+)?(\*?[\w]+)\)\s+)?(\w+)\s*\(([^)]*)\)')

        in_import_block = False
        for idx, line in enumerate(lines, 1):
            s = line.strip()
            if s.startswith("import ("):
                in_import_block = True
                continue
            if in_import_block:
                if s == ")":
                    in_import_block = False
                else:
                    m = re.search(r'"([^"]+)"', s)
                    if m:
                        parsed.imports_edges.append((file_path, m.group(1)))
                continue

            m = import_single.search(line)
            if m:
                parsed.imports_edges.append((file_path, m.group(1)))

            st = type_struct.search(line)
            if st:
                name = st.group(1)
                sid = f"{file_path}:{name}"
                parsed.symbols.append(SymbolNode(id=sid, name=name, kind=SymbolKind.STRUCT, file_path=file_path, start_line=idx, end_line=idx))
                parsed.defines_edges.append((file_path, sid))
                continue

            it = type_interface.search(line)
            if it:
                name = it.group(1)
                sid = f"{file_path}:{name}"
                parsed.symbols.append(SymbolNode(id=sid, name=name, kind=SymbolKind.INTERFACE, file_path=file_path, start_line=idx, end_line=idx))
                parsed.defines_edges.append((file_path, sid))
                continue

            fn = func_decl.search(line)
            if fn:
                receiver = fn.group(1).lstrip("*") if fn.group(1) else None
                name = fn.group(2)
                kind = SymbolKind.METHOD if receiver else SymbolKind.FUNCTION
                sid = f"{file_path}:{receiver}.{name}" if receiver else f"{file_path}:{name}"
                parent = f"{file_path}:{receiver}" if receiver else file_path
                parsed.symbols.append(SymbolNode(id=sid, name=name, kind=kind, file_path=file_path, start_line=idx, end_line=idx, parent_symbol=parent))
                parsed.defines_edges.append((parent, sid))

    def _parse_rust(self, file_path: str, code: str, parsed: ParsedFile) -> None:
        lines = code.splitlines()
        use_pattern = re.compile(r'use\s+([^;]+);')
        struct_pattern = re.compile(r'(?:pub\s+)?struct\s+(\w+)')
        trait_pattern = re.compile(r'(?:pub\s+)?trait\s+(\w+)')
        impl_pattern = re.compile(r'impl(?:\s*<[^>]+>)?\s+(?:(\w+)\s+for\s+)?(\w+)')
        fn_pattern = re.compile(r'(?:pub(?:\([^)]+\))?\s+)?(?:async\s+)?fn\s+(\w+)\s*\(([^)]*)\)')

        for idx, line in enumerate(lines, 1):
            um = use_pattern.search(line)
            if um:
                parsed.imports_edges.append((file_path, um.group(1).strip()))

            sm = struct_pattern.search(line)
            if sm:
                name = sm.group(1)
                sid = f"{file_path}:{name}"
                parsed.symbols.append(SymbolNode(id=sid, name=name, kind=SymbolKind.STRUCT, file_path=file_path, start_line=idx, end_line=idx))
                parsed.defines_edges.append((file_path, sid))
                continue

            tm = trait_pattern.search(line)
            if tm:
                name = tm.group(1)
                sid = f"{file_path}:{name}"
                parsed.symbols.append(SymbolNode(id=sid, name=name, kind=SymbolKind.TRAIT, file_path=file_path, start_line=idx, end_line=idx))
                parsed.defines_edges.append((file_path, sid))
                continue

            im = impl_pattern.search(line)
            if im:
                trait_name = im.group(1)
                struct_name = im.group(2)
                if trait_name and struct_name:
                    parsed.inherits_edges.append((f"{file_path}:{struct_name}", trait_name))

            fm = fn_pattern.search(line)
            if fm:
                name = fm.group(1)
                sid = f"{file_path}:{name}"
                parsed.symbols.append(SymbolNode(id=sid, name=name, kind=SymbolKind.FUNCTION, file_path=file_path, start_line=idx, end_line=idx))
                parsed.defines_edges.append((file_path, sid))

    def _parse_java(self, file_path: str, code: str, parsed: ParsedFile) -> None:
        lines = code.splitlines()
        import_pattern = re.compile(r'import\s+(?:static\s+)?([\w.]+);')
        class_pattern = re.compile(r'(?:public|protected|private)?\s*(?:abstract|final)?\s*(class|interface|enum)\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([\w,\s]+))?')
        method_pattern = re.compile(r'(?:public|protected|private)\s+(?:static\s+)?[<\w>,\[\]]+\s+(\w+)\s*\(([^)]*)\)')

        for idx, line in enumerate(lines, 1):
            im = import_pattern.search(line)
            if im:
                parsed.imports_edges.append((file_path, im.group(1)))

            cm = class_pattern.search(line)
            if cm:
                kind_str = cm.group(1)
                name = cm.group(2)
                extends = cm.group(3)
                implements = cm.group(4)
                kind = SymbolKind.INTERFACE if kind_str == "interface" else SymbolKind.CLASS
                sid = f"{file_path}:{name}"
                parsed.symbols.append(SymbolNode(id=sid, name=name, kind=kind, file_path=file_path, start_line=idx, end_line=idx))
                parsed.defines_edges.append((file_path, sid))
                if extends:
                    parsed.inherits_edges.append((sid, extends))
                if implements:
                    for iface in implements.split(","):
                        if iface.strip():
                            parsed.inherits_edges.append((sid, iface.strip()))
                continue

            mm = method_pattern.search(line)
            if mm:
                name = mm.group(1)
                sid = f"{file_path}:{name}"
                parsed.symbols.append(SymbolNode(id=sid, name=name, kind=SymbolKind.METHOD, file_path=file_path, start_line=idx, end_line=idx))
                parsed.defines_edges.append((file_path, sid))

    def _parse_c_cpp(self, file_path: str, code: str, parsed: ParsedFile) -> None:
        lines = code.splitlines()
        include_pattern = re.compile(r'#include\s+[<"]([^>"]+)[>"]')
        class_pattern = re.compile(r'(?:class|struct)\s+(\w+)(?:\s*:\s*(?:public|protected|private)?\s*(\w+))?')
        func_pattern = re.compile(r'(?:[\w:*&]+)\s+(\w+)\s*\(([^)]*)\)\s*\{?')

        for idx, line in enumerate(lines, 1):
            inc = include_pattern.search(line)
            if inc:
                parsed.imports_edges.append((file_path, inc.group(1)))

            cm = class_pattern.search(line)
            if cm:
                name = cm.group(1)
                base = cm.group(2)
                sid = f"{file_path}:{name}"
                parsed.symbols.append(SymbolNode(id=sid, name=name, kind=SymbolKind.CLASS, file_path=file_path, start_line=idx, end_line=idx))
                parsed.defines_edges.append((file_path, sid))
                if base:
                    parsed.inherits_edges.append((sid, base))
                continue

            fm = func_pattern.search(line)
            if fm:
                name = fm.group(1)
                if name not in ("if", "for", "while", "switch"):
                    sid = f"{file_path}:{name}"
                    parsed.symbols.append(SymbolNode(id=sid, name=name, kind=SymbolKind.FUNCTION, file_path=file_path, start_line=idx, end_line=idx))
                    parsed.defines_edges.append((file_path, sid))

    def _parse_generic(self, file_path: str, code: str, parsed: ParsedFile) -> None:
        lines = code.splitlines()
        def_pattern = re.compile(r'(?:def|function|fn|func|class)\s+([\w$]+)')
        for idx, line in enumerate(lines, 1):
            m = def_pattern.search(line)
            if m:
                name = m.group(1)
                sid = f"{file_path}:{name}"
                parsed.symbols.append(SymbolNode(id=sid, name=name, kind=SymbolKind.FUNCTION, file_path=file_path, start_line=idx, end_line=idx))
                parsed.defines_edges.append((file_path, sid))
