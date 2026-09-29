"""Alpha's declared Model Context Protocol version contract.

**Why this module exists.** Alpha speaks MCP through two different routes: it
builds adapter server parameters in :mod:`alpha.mcp.client`, and it drives raw
``mcp.ClientSession`` objects itself in :mod:`alpha.mcp.session_pool` and
:mod:`alpha.mcp.task_tool_caller`. The adapter library (``langchain-mcp-
adapters``) resolves the ``mcp`` SDK transitively, so before this module the
wire version Alpha put on the network was decided entirely by whatever
``mcp`` release happened to be resolved. Nothing in the repository recorded
that fact, so a protocol move could only ever surface to a user as a
connection error.

The contract here makes the version explicit, checkable, and failable.

**What the versions mean** (``mcp`` uses ``YYYY-MM-DD`` strings naming the date
of the last backwards-incompatible change; revisions are an enumerated set,
not an ordered scalar -- an unrecognised string is neither newer nor older):

``MINIMUM_MCP_PROTOCOL_VERSION`` (``2025-06-18``)
    The oldest revision Alpha will talk to. Two features Alpha's MCP layer
    *requires* landed here, and both are load-bearing rather than incidental:
    ``tools.py`` reads ``CallToolResult.structuredContent`` and branches on
    ``mcp.types.ResourceLink``, and ``mcp/tasks/ordinary.py`` treats a task
    tool returning no ``structuredContent`` as a protocol error. A server that
    negotiated older than this cannot satisfy those code paths, so honouring it
    would mean connecting successfully and then mis-parsing results.

``DECLARED_MCP_PROTOCOL_VERSION`` (``2025-11-25``)
    The revision Alpha prefers and the one the installed SDK offers as its
    ``LATEST_PROTOCOL_VERSION``. It is the newest handshake-based revision.

``CURRENT_SPEC_MCP_PROTOCOL_VERSION`` (``2026-07-28``)
    The revision published on ``modelcontextprotocol.io/specification``. This is
    **not** something Alpha speaks. It is recorded so that the gap between
    "current" and "declared" is a fact in the repository rather than something a
    reader has to go and discover, and so drift in *either* direction is
    detectable by a test.

**Why the gap is real and deliberate.** ``2026-07-28`` replaced the
``initialize`` handshake with a stateless per-request model: no sessions, no
``Mcp-Session-Id``, a mandatory ``server/discover`` RPC, and a per-request
``MCP-Protocol-Version`` header. Alpha's entire MCP layer is built on
handshake semantics -- ``session_pool._run_session`` blocks on
``session.initialize()`` and only promotes a session into the pool once it
succeeds, and the pool's LRU/lease lifecycle is defined over that handshake.
Moving to the stateless core is a redesign of the session pool, not a version
bump. Alpha therefore stays on the handshake family and says so, rather than
inheriting a version by accident.

**Why this is code, not ``config.yaml``.** ``config.yaml`` is the single source
of truth for *operator choices*, and this repository is strict about that
(``AppConfig`` is ``extra="forbid"``). A protocol version is not an operator
choice: it is a property of the code and of the SDK this code imports. Making
it settable would let an operator declare a revision Alpha cannot speak, and
the config would then be a source of a falsehood rather than of truth -- the
exact failure mode this module exists to remove. An operator who needs a
different behaviour needs a different release, which is auditable in the diff.
See ``docs/PROTOCOLS.md`` for the full reasoning and the review date of the
version strings above.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Every protocol revision Alpha is willing to have negotiated against it,
#: oldest first. Ordered by position in this tuple, never by string comparison.
#:
#: This set *starts at* :data:`MINIMUM_MCP_PROTOCOL_VERSION` rather than listing
#: every revision the SDK can parse. That is deliberate: Alpha can technically
#: decode a ``2024-11-05`` result, but its own ``tools.py`` then requires
#: ``structuredContent`` and branches on ``ResourceLink``, neither of which
#: exists in the revisions below the floor. Admitting them here would mean
#: admitting servers Alpha answers incorrectly for, so the tuple and the floor
#: are the same statement about the same set and must stay consistent.
SUPPORTED_MCP_PROTOCOL_VERSIONS: tuple[str, ...] = (
    "2025-06-18",
    "2025-11-25",
)

#: The oldest revision Alpha accepts. See the module docstring for the two
#: features that make this a floor rather than a preference.
MINIMUM_MCP_PROTOCOL_VERSION: str = "2025-06-18"

#: The revision Alpha prefers and asks for; also the newest handshake-based
#: revision of the specification.
DECLARED_MCP_PROTOCOL_VERSION: str = "2025-11-25"

#: The revision currently published on modelcontextprotocol.io. Alpha does not
#: speak it (see the module docstring). Recorded so the gap is checkable.
CURRENT_SPEC_MCP_PROTOCOL_VERSION: str = "2026-07-28"

#: Every revision this release has heard of, oldest first, used to classify a
#: peer's negotiated version in an error message. Ordering is by position here
#: and never by string comparison, because revisions are an enumerated set
#: rather than an ordered scalar -- the upstream SDK makes the same point, and a
#: peer answering "zzz" must not be reported as being in the future.
#:
#: This deliberately includes revisions *below* the floor (``2024-11-05`` and
#: ``2025-03-26``). A server offering one is a real, dated, recognisable revision
#: that happens to be below what Alpha will accept, and telling its operator
#: "not a protocol revision this Alpha release knows about" would be a lie that
#: sends them looking for a broken server instead of an outdated one.
_KNOWN_REVISIONS: dict[str, int] = {
    version: index
    for index, version in enumerate(
        (
            "2024-11-05",
            "2025-03-26",
            *SUPPORTED_MCP_PROTOCOL_VERSIONS,
            CURRENT_SPEC_MCP_PROTOCOL_VERSION,
        )
    )
}


class McpProtocolVersionError(RuntimeError):
    """A connected MCP server negotiated a protocol revision Alpha will not talk to.

    Raised rather than logged-and-continued, because every path past this point
    parses the negotiated result with assumptions that only hold for
    ``SUPPORTED_MCP_PROTOCOL_VERSIONS``. Connecting to a revision outside that
    set produces tool results Alpha misreads, which surfaces to the user as
    corrupted tool output or a fabricated absence of structured content -- not as
    a connection error, and not safely.
    """


def supports_mcp_protocol_version(version: str) -> bool:
    """Whether Alpha accepts ``version`` as a negotiated protocol revision."""
    return version in SUPPORTED_MCP_PROTOCOL_VERSIONS


def verify_negotiated_protocol_version(server_name: str, negotiated: str) -> str:
    """Check a server's negotiated protocol revision against Alpha's contract.

    Args:
        server_name: The configured MCP server name, used only in the message.
        negotiated: The ``protocolVersion`` the server returned from
            ``initialize``. May be any string, including one Alpha has never
            heard of.

    Returns:
        The negotiated version, unchanged, when it is supported.

    Raises:
        McpProtocolVersionError: The negotiated revision is older than
            :data:`MINIMUM_MCP_PROTOCOL_VERSION`, is newer than
            :data:`DECLARED_MCP_PROTOCOL_VERSION`, or is unrecognised. All
            three fail closed; see the class docstring.
    """
    if supports_mcp_protocol_version(negotiated):
        return negotiated

    known = ", ".join(SUPPORTED_MCP_PROTOCOL_VERSIONS)

    # Distinguish "too old", "newer than we speak", and "unrecognised" in the
    # message. An operator reading this log is trying to answer "is my server
    # too old or is Alpha too old?", and a bare version string does not tell
    # them. Ordering goes through _KNOWN_REVISIONS membership, never a string
    # comparison: revisions are an enumerated set, so a garbage peer string such
    # as "zzz" must not be reported as "newer than we speak".
    position = _KNOWN_REVISIONS.get(negotiated)
    if position is None:
        detail = f"not a protocol revision this Alpha release knows about; the current specification is {CURRENT_SPEC_MCP_PROTOCOL_VERSION}"
    # Compared against _KNOWN_REVISIONS' own index of the floor, not against
    # SUPPORTED_MCP_PROTOCOL_VERSIONS.index(): those are two different
    # coordinate systems, and mixing them made 2024-11-05 -- two positions below
    # the floor -- report itself as "newer than the newest revision Alpha
    # speaks". An operator reading that would conclude the opposite of the truth.
    elif position < _KNOWN_REVISIONS[MINIMUM_MCP_PROTOCOL_VERSION]:
        detail = f"older than the minimum Alpha supports ({MINIMUM_MCP_PROTOCOL_VERSION})"
    else:
        detail = f"newer than the newest revision Alpha speaks ({DECLARED_MCP_PROTOCOL_VERSION})"

    raise McpProtocolVersionError(f"MCP server '{server_name}' negotiated unsupported protocol version {negotiated!r}: it is {detail}. Alpha speaks {known}.")


def describe_mcp_protocol_contract() -> dict[str, object]:
    """A JSON-safe summary of the contract, for diagnostics and tests.

    Deliberately returns the *declared* values rather than reading the installed
    SDK, so it describes what this source claims. Use
    :func:`installed_sdk_protocol_versions` to see what the resolved SDK agrees
    with.
    """
    return {
        "minimum": MINIMUM_MCP_PROTOCOL_VERSION,
        "declared": DECLARED_MCP_PROTOCOL_VERSION,
        "current_spec": CURRENT_SPEC_MCP_PROTOCOL_VERSION,
        "supported": list(SUPPORTED_MCP_PROTOCOL_VERSIONS),
    }


def installed_sdk_protocol_versions() -> dict[str, object]:
    """What the resolved ``mcp`` SDK says it speaks.

    Import errors are reported rather than raised: the point of this function
    is diagnosis, and a caller that cannot import the SDK needs to be told
    that, not to receive a ``ModuleNotFoundError`` from a helper.

    Returns:
        A mapping with ``installed`` (the SDK version string or ``None``),
        ``latest`` (its ``LATEST_PROTOCOL_VERSION`` or ``None``),
        ``default_negotiated`` (its ``DEFAULT_NEGOTIATED_VERSION`` or
        ``None``), ``supported`` (its supported list, or ``None``), and
        ``error`` (a message when the SDK could not be inspected).
    """
    result: dict[str, object] = {
        "installed": None,
        "latest": None,
        "default_negotiated": None,
        "supported": None,
        "error": None,
    }
    try:
        import importlib.metadata as metadata

        from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
        from mcp.types import DEFAULT_NEGOTIATED_VERSION, LATEST_PROTOCOL_VERSION
    except Exception as exc:  # pragma: no cover - depends on the resolved env
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    try:
        result["installed"] = metadata.version("mcp")
    except Exception as exc:  # pragma: no cover - depends on the resolved env
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["latest"] = LATEST_PROTOCOL_VERSION
    result["default_negotiated"] = DEFAULT_NEGOTIATED_VERSION
    result["supported"] = list(SUPPORTED_PROTOCOL_VERSIONS)
    return result
