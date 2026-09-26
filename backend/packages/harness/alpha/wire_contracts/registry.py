"""The wire-contract registry and its TypeScript generator.

alpha's gateway contract is hand-maintained on both sides, which is why frontend
typecheck keeps finding fields the backend never heard about.  The fix is to
make one side authoritative: a Pydantic model is the contract, and the
TypeScript the frontend imports is **generated** from it.

A drift between the two is then a *type error* rather than a runtime surprise,
because the generated file is checked in and a test fails when it no longer
matches the models.

The registry is deliberately small and explicit.  A registry that discovers
models by import is a registry that imports the world.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Type

from pydantic import BaseModel

#: Bumped when the generated file's shape changes.
GENERATOR_VERSION = 1

_TS_RESERVED = {
    "break", "case", "catch", "class", "const", "continue", "debugger", "default",
    "delete", "do", "else", "export", "extends", "finally", "for", "function",
    "if", "import", "in", "instanceof", "new", "return", "super", "switch",
    "this", "throw", "try", "typeof", "var", "void", "while", "with", "yield",
    "let", "static", "enum", "await", "implements", "package", "protected",
    "interface", "private", "public", "null", "true", "false",
}

#: JSON-Schema keyword -> TypeScript type.
_SCALAR_TYPES: dict[str, str] = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
    "null": "null",
}


class UnregisteredContract(KeyError):
    """A model was not declared in the registry."""


@dataclass(frozen=True)
class Contract:
    """One wire contract: a name and the Pydantic model that defines it."""

    name: str
    model: Type[BaseModel]
    #: Optional: the module the frontend type should live in.
    module: str = "contracts"

    def json_schema(self) -> dict[str, Any]:
        return self.model.model_json_schema()


def _ts_property_name(name: str) -> str:
    if name in _TS_RESERVED or not name.replace("_", "").isalnum() or name[:1].isdigit():
        return json.dumps(name)
    return name


def _ts_type(schema: dict[str, Any], *, required: bool, indent: int) -> str:
    if not required:
        # Optional in TS too, so a producer that stops sending a field is a type
        # error at the point of use rather than an ``undefined`` at runtime.
        pass
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"])
    for combinator in ("anyOf", "oneOf"):
        if combinator in schema:
            parts = [_ts_type(s, required=required, indent=indent) for s in schema[combinator]]
            unique = list(dict.fromkeys(parts))
            return unique[0] if len(unique) == 1 else " | ".join(unique)
    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(_SCALAR_TYPES.get(k, "unknown") for k in kind)
    if kind == "object" or "properties" in schema:
        return _ts_inline_object(schema, indent=indent)
    if kind == "array":
        return f"Array<{_ts_type(schema.get('items', {}), required=True, indent=indent)}>"
    if kind in _SCALAR_TYPES:
        return _SCALAR_TYPES[kind]
    return "unknown"


def _ts_inline_object(schema: dict[str, Any], *, indent: int) -> str:
    properties = schema.get("properties") or {}
    if not properties:
        return "Record<string, unknown>"
    required = set(schema.get("required") or ())
    pad = "  " * (indent + 1)
    close = "  " * indent
    lines = ["{"]
    for name, sub in properties.items():
        optional = "" if name in required else "?"
        lines.append(
            f"{pad}{_ts_property_name(name)}{optional}: "
            f"{_ts_type(sub, required=name in required, indent=indent + 1)};"
        )
    lines.append(close + "}")
    return "\n".join(lines)


def render_typescript(contracts: Iterable[Contract]) -> str:
    """Render the whole generated TypeScript module."""
    entries = sorted(contracts, key=lambda c: (c.module, c.name))
    header = (
        "// GENERATED FILE - DO NOT EDIT.\n"
        "//\n"
        "// Source of truth: the Pydantic models in the script bridge / gateway\n"
        "// contract registry.  Regenerate with:\n"
        "//\n"
        "//     python -m alpha.wire_contracts.generate --write\n"
        "//\n"
        f"// generator version: {GENERATOR_VERSION}\n"
        "\n"
        "/* eslint-disable */\n"
    )
    by_module: dict[str, list[Contract]] = {}
    for contract in entries:
        by_module.setdefault(contract.module, []).append(contract)

    chunks = [header]
    for module in sorted(by_module):
        chunks.append(f"\n// ---- module: {module} ----\n")
        for contract in sorted(by_module[module], key=lambda c: c.name):
            schema = contract.json_schema()
            chunks.append(
                f"export interface {contract.name} "
                + _ts_inline_object(schema, indent=0)
                + "\n"
            )
    return "".join(chunks)


def contract_fields(contract: Contract) -> set[str]:
    """The top-level field names a consumer may rely on."""
    return set((contract.json_schema().get("properties") or {}).keys())


def required_fields(contract: Contract) -> set[str]:
    return set(contract.json_schema().get("required") or ())


def render_manifest(contracts: Iterable[Contract]) -> dict[str, Any]:
    """A machine-readable summary, for a drift gate that does not read TS."""
    return {
        "generator_version": GENERATOR_VERSION,
        "contracts": {
            c.name: {
                "module": c.module,
                "fields": sorted(contract_fields(c)),
                "required": sorted(required_fields(c)),
            }
            for c in sorted(contracts, key=lambda c: c.name)
        },
    }


#: The registry.  Explicit, small, and checked by a test.
REGISTRY: tuple[Contract, ...] = ()


def register(contract: Contract) -> Contract:
    global REGISTRY
    REGISTRY = tuple(c for c in REGISTRY if c.name != contract.name) + (contract,)
    return contract


def registered() -> tuple[Contract, ...]:
    return tuple(sorted(REGISTRY, key=lambda c: c.name))


def require(name: str) -> Contract:
    for contract in registered():
        if contract.name == name:
            return contract
    raise UnregisteredContract(f"wire contract {name!r} is not registered")
