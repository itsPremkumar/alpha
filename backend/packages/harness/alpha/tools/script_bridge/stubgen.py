"""Generator for the script-bridge tool stub.

The stub is **generated from the actual tool registry**, never hand-maintained.
:func:`check_stub_drift` compares the live registry fingerprint against the one
baked into the generated module and raises :class:`StaleStubError` on any
mismatch, so a registry change underneath the stub is a loud failure rather than
a script that calls a tool that no longer exists (or, worse, keeps calling a tool
that now means something else).

Regenerate with::

    python -m alpha.tools.script_bridge.stubgen --write
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from .errors import StaleStubError
from .policy import FORBIDDEN_TOOL_NAMES, FORBIDDEN_TOOL_PREFIXES

GENERATED_DIR = Path(__file__).resolve().parents[2] / "script_bridge_child"
STUB_PATH = GENERATED_DIR / "generated.py"
FINGERPRINT_ATTR = "STUB_REGISTRY_SHA256"

_HEADER = '''\
"""GENERATED FILE - DO NOT EDIT.

Regenerate with::

    python -m alpha.tools.script_bridge.stubgen --write

Generated from the live tool registry by
``alpha.tools.script_bridge.stubgen``.  Every function below is a thin RPC shim:
it sends the call over the bridge socket and returns **only** what the tool
returned.  The parent-side dispatcher re-derives the allowlist, the limits and
the authorisation decision for every call, so nothing here is trusted.
"""

from __future__ import annotations

from .client import get_client, verify_stub_fingerprint

#: Fingerprint of the tool registry this file was generated from.
STUB_REGISTRY_SHA256 = "{fingerprint}"

#: Registry snapshot the generator saw: name -> {"description": str}
STUB_TOOL_INDEX = {index}

_DRIFT = verify_stub_fingerprint()
if _DRIFT is not None:  # pragma: no cover - fires only on real registry drift
    raise RuntimeError(_DRIFT)


def available_tool_names() -> list[str]:
    """Every tool this stub can name.  Whether a given call is *permitted* is
    decided parent-side; this list is a naming convenience only."""
    return sorted(STUB_TOOL_INDEX)


def tool(name: str, **kwargs) -> object:
    """Call any allowed tool by name.

    The allowlist is enforced parent-side, so a name outside it is refused with
    a reason rather than silently ignored.
    """
    return get_client().call(name, dict(kwargs))


'''

_FUNC = '''
def {name}(**kwargs) -> object:
    """{doc}

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``{name}`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("{name}", dict(kwargs))

'''


def registry_tools() -> list[Any]:
    """Return the live ``BUILTIN_TOOLS`` list from the real registry."""
    from alpha.tools import tools as tools_module

    return list(tools_module.BUILTIN_TOOLS)


def registry_names(tools: Iterable[Any] | None = None) -> list[str]:
    """Every callable tool name in the live registry, sorted."""
    resolved = list(tools) if tools is not None else registry_tools()
    names: set[str] = set()
    for tool in resolved:
        name = getattr(tool, "name", None)
        if isinstance(name, str) and name:
            names.add(name)
    return sorted(names)


def _description(tool: Any) -> str:
    text = getattr(tool, "description", "") or ""
    first = text.strip().split("\n", 1)[0].strip()
    return first[:200]


def stub_index(tools: Iterable[Any] | None = None) -> dict[str, dict[str, Any]]:
    """name -> metadata for every tool a script is *allowed to name*.

    MCP tools and the forbidden surfaces are excluded at generation time so the
    stub cannot even spell them; the parent-side policy re-checks anyway.
    """
    resolved = list(tools) if tools is not None else registry_tools()
    index: dict[str, dict[str, Any]] = {}
    for tool in resolved:
        name = getattr(tool, "name", None)
        if not isinstance(name, str) or not name:
            continue
        if name in FORBIDDEN_TOOL_NAMES:
            continue
        if any(name.startswith(prefix) for prefix in FORBIDDEN_TOOL_PREFIXES):
            continue
        index[name] = {"description": _description(tool)}
    return dict(sorted(index.items()))


def registry_fingerprint(tools: Iterable[Any] | None = None) -> str:
    """A stable SHA-256 over the registry names the stub is generated from.

    Includes names *and* descriptions, so a semantic change to a tool is drift
    even when the name is unchanged.
    """
    index = stub_index(tools)
    payload = json.dumps(index, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_stub(tools: Iterable[Any] | None = None) -> str:
    """Render the full stub module source for the given registry state."""
    index = stub_index(tools)
    fingerprint = hashlib.sha256(
        json.dumps(index, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    # Explicit substitution, not str.format: the index is JSON and contains
    # braces that format() would try to interpret.
    body = _HEADER.replace("{fingerprint}", fingerprint).replace(
        "{index}", json.dumps(index, indent=4, sort_keys=True, ensure_ascii=False)
    )
    for name, meta in index.items():
        doc = (meta["description"] or name).replace('"""', "'''").replace("\\", "\\\\")
        body += _FUNC.format(name=name, doc=doc)
    return body


def read_generated_fingerprint(path: Path | None = None) -> str | None:
    """Read the fingerprint baked into the checked-in stub, if any."""
    target = path or STUB_PATH
    if not target.exists():
        return None
    match = re.search(rf'^{FINGERPRINT_ATTR} = "([0-9a-f]{{64}})"', target.read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else None


def drift_report_text(expected: str, live: str) -> str:
    return (
        "generated tool stub is stale: it was generated from registry fingerprint "
        f"{expected[:12]}... but the live registry is {live[:12]}.... "
        "Regenerate with `python -m alpha.tools.script_bridge.stubgen --write`."
    )


def check_stub_drift(*, path: Path | None = None, tools: Iterable[Any] | None = None) -> str | None:
    """Return a drift reason, or ``None`` when the stub matches the registry."""
    generated = read_generated_fingerprint(path)
    if generated is None:
        return (
            "generated tool stub is missing or has no fingerprint; regenerate with "
            "`python -m alpha.tools.script_bridge.stubgen --write`"
        )
    live = registry_fingerprint(tools)
    if live != generated:
        return drift_report_text(generated, live)
    return None


def assert_stub_current(*, path: Path | None = None, tools: Iterable[Any] | None = None) -> None:
    """Raise :class:`StaleStubError` when the stub has drifted."""
    reason = check_stub_drift(path=path, tools=tools)
    if reason is not None:
        raise StaleStubError(reason)


def write_stub(*, path: Path | None = None, tools: Iterable[Any] | None = None) -> Path:
    target = path or STUB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_stub(tools), encoding="utf-8", newline="\n")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the script-bridge tool stub.")
    parser.add_argument("--write", action="store_true", help="write the stub to disk")
    parser.add_argument("--check", action="store_true", help="exit non-zero when the stub drifted")
    args = parser.parse_args(argv)
    if args.write:
        written = write_stub()
        print(f"wrote {written}")
        return 0
    reason = check_stub_drift()
    if reason:
        print(f"DRIFT: {reason}")
        return 1 if args.check else 0
    print("stub is current")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())
