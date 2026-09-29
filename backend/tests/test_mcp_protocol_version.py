"""The MCP protocol-version contract, pinned.

Before this module existed, Alpha's MCP layer contained no protocol-version
reference at all: `alpha/mcp/client.py` built adapter parameter dicts and
nothing more, and the wire revision was decided by whatever `mcp` release the
transitive dependency on `langchain-mcp-adapters` happened to resolve to. These
tests make that value a stated, checkable fact.

What each test is for:

- `test_declared_version_matches_pinned_expectation` is the tripwire. It reads
  the real constant out of `alpha.mcp.protocol_version` and compares it against a
  literal written here. Editing the declared version without updating this file
  fails CI, and the failure message says which side moved. That is the property
  the constant-alone assertion would not have.
- `test_declared_version_is_supported` keeps the declared value inside the
  supported set, so a declared version that nothing can actually speak is caught
  separately from a version that simply changed.
- `test_declared_version_agrees_with_installed_sdk` is the drift detector: it
  compares Alpha's declaration against the resolved `mcp` SDK rather than against
  a lockfile, because a lockfile records what was resolved once and the SDK on
  disk is what will actually put bytes on the wire.
- The remaining tests pin the *behaviour* -- that an unsupported revision is
  refused rather than tolerated, that the error names the server, and that the
  refusal happens before a session is ever handed to a caller.

Sources for the version strings, all checked 2026-09-29:
  https://modelcontextprotocol.io/specification/2026-07-28
  https://modelcontextprotocol.io/docs/2026-07-28/learn/versioning
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from alpha.mcp import protocol_version as pv
from alpha.mcp.protocol_version import (
    CURRENT_SPEC_MCP_PROTOCOL_VERSION,
    DECLARED_MCP_PROTOCOL_VERSION,
    MINIMUM_MCP_PROTOCOL_VERSION,
    SUPPORTED_MCP_PROTOCOL_VERSIONS,
    McpProtocolVersionError,
    supports_mcp_protocol_version,
    verify_negotiated_protocol_version,
)

# The literal this file exists to protect. If a future change to the
# specification, or to the SDK Alpha depends on, moves the revision Alpha
# speaks, this line has to be edited in the same change set as
# DECLARED_MCP_PROTOCOL_VERSION -- with the reason recorded in docs/PROTOCOLS.md.
#
# Why "2025-11-25" and not the current "2026-07-28": 2026-07-28 replaced the
# initialize handshake with a stateless per-request model, and Alpha's MCP
# layer is built on handshake semantics -- MCPSessionPool promotes a session
# into its pool only once session.initialize() has returned, and the pool's LRU
# and lease lifecycle is defined over that handshake. Supporting the stateless
# core is a redesign of the session pool, not a version bump, so this release
# stays on the handshake family and says so rather than inheriting a version by
# accident.
EXPECTED_DECLARED_VERSION = "2025-11-25"

EXPECTED_MINIMUM_VERSION = "2025-06-18"

EXPECTED_CURRENT_SPEC_VERSION = "2026-07-28"


class TestPinnedExpectations:
    """The declarations, compared against literals written in this file."""

    def test_declared_version_matches_pinned_expectation(self):
        assert DECLARED_MCP_PROTOCOL_VERSION == EXPECTED_DECLARED_VERSION, (
            f"Alpha's declared MCP protocol version moved: "
            f"alpha/mcp/protocol_version.py declares {DECLARED_MCP_PROTOCOL_VERSION!r} "
            f"but this test pins {EXPECTED_DECLARED_VERSION!r}. One of the two was "
            f"changed without the other. Moving it deliberately means updating both, "
            f"with the reason recorded in docs/PROTOCOLS.md -- an MCP revision is not "
            f"something to change silently, because it decides what Alpha can read."
        )

    def test_minimum_version_matches_pinned_expectation(self):
        assert MINIMUM_MCP_PROTOCOL_VERSION == EXPECTED_MINIMUM_VERSION, (
            f"The MCP protocol floor moved: declared "
            f"{MINIMUM_MCP_PROTOCOL_VERSION!r}, pinned {EXPECTED_MINIMUM_VERSION!r}. "
            f"The floor is justified in alpha/mcp/protocol_version.py by two features "
            f"Alpha's tool-result parsing requires (structuredContent and ResourceLink). "
            f"Lowering it without removing that dependency would admit servers whose "
            f"results this code cannot parse."
        )

    def test_current_spec_version_matches_pinned_expectation(self):
        assert CURRENT_SPEC_MCP_PROTOCOL_VERSION == EXPECTED_CURRENT_SPEC_VERSION, (
            f"The recorded current specification revision moved: declared "
            f"{CURRENT_SPEC_MCP_PROTOCOL_VERSION!r}, pinned {EXPECTED_CURRENT_SPEC_VERSION!r}. "
            f"This constant records where modelcontextprotocol.io is, which Alpha does "
            f"NOT speak. If the specification has moved on, re-verify it at "
            f"https://modelcontextprotocol.io/specification/ and decide whether this "
            f"release can move with it; do not just update the literal."
        )

    def test_declared_version_is_inside_the_supported_set(self):
        """A declared version nothing can speak is a different bug from a moved one."""
        assert DECLARED_MCP_PROTOCOL_VERSION in SUPPORTED_MCP_PROTOCOL_VERSIONS, (
            f"{DECLARED_MCP_PROTOCOL_VERSION!r} is declared but absent from SUPPORTED_MCP_PROTOCOL_VERSIONS {list(SUPPORTED_MCP_PROTOCOL_VERSIONS)!r}, so Alpha would refuse every server that agreed with its own declaration."
        )

    def test_minimum_is_not_newer_than_declared(self):
        assert MINIMUM_MCP_PROTOCOL_VERSION <= DECLARED_MCP_PROTOCOL_VERSION, "The supported set is ordered oldest-first, so a floor newer than the declared version would make the declaration unsatisfiable."

    def test_supported_set_is_ordered_and_unique(self):
        assert list(SUPPORTED_MCP_PROTOCOL_VERSIONS) == sorted(set(SUPPORTED_MCP_PROTOCOL_VERSIONS)), (
            "SUPPORTED_MCP_PROTOCOL_VERSIONS must be unique and oldest-first: the error message for a too-old peer indexes it by position, and a duplicate or unordered tuple would misreport which side is out of date."
        )


class TestAgreesWithInstalledSdk:
    """Drift against the SDK that actually does the talking."""

    def test_declared_version_agrees_with_installed_sdk(self):
        """The declared version must be one the resolved `mcp` SDK offers.

        This is the check that replaces reading a lockfile. A lockfile records a
        resolution made once; `installed_sdk_protocol_versions()` inspects the
        package that is installed in this environment and is about to send
        `initialize`. A `uv sync` that moves `mcp` without moving this
        declaration therefore fails here rather than in production.
        """
        sdk = pv.installed_sdk_protocol_versions()
        assert sdk["error"] is None, f"Could not inspect the installed mcp SDK: {sdk['error']}"

        latest = sdk["latest"]
        assert latest == DECLARED_MCP_PROTOCOL_VERSION, (
            f"The installed mcp SDK {sdk['installed']} speaks LATEST_PROTOCOL_VERSION "
            f"{latest!r}, but Alpha declares {DECLARED_MCP_PROTOCOL_VERSION!r}. Alpha "
            f"asks for the SDK's own latest version in initialize(), so a divergence "
            f"here means Alpha is silently negotiating something other than what it "
            f"claims to support. Fix by moving the declaration to the value the SDK "
            f"reports (and to this test's literal), or by pinning the SDK."
        )

    def test_every_supported_version_is_known_to_the_installed_sdk(self):
        """Alpha must not accept a revision its SDK cannot negotiate at all.

        Accepting one would mean refusing nothing in particular and trusting a
        revision the transport has no support for.
        """
        sdk = pv.installed_sdk_protocol_versions()
        assert sdk["error"] is None, f"Could not inspect the installed mcp SDK: {sdk['error']}"

        sdk_supported = set(sdk["supported"] or ())
        # Alpha only *asks* for the latest revision, so accepting an older one is
        # about tolerating what a server answers with rather than about the SDK
        # being able to originate it. But every revision Alpha tolerates must
        # still be one the SDK recognises, or the negotiated value is meaningless.
        unknown = [v for v in SUPPORTED_MCP_PROTOCOL_VERSIONS if v not in sdk_supported]
        assert not unknown, (
            f"Alpha tolerates MCP revisions the installed SDK does not know: {unknown}. "
            f"The SDK's supported set is {sorted(sdk_supported)}. Either the SDK moved "
            f"and Alpha should stop tolerating a revision it can no longer negotiate, "
            f"or the declaration is wrong."
        )

    def test_contract_summary_is_json_safe_and_self_consistent(self):
        described = pv.describe_mcp_protocol_contract()
        assert described == {
            "minimum": MINIMUM_MCP_PROTOCOL_VERSION,
            "declared": DECLARED_MCP_PROTOCOL_VERSION,
            "current_spec": CURRENT_SPEC_MCP_PROTOCOL_VERSION,
            "supported": list(SUPPORTED_MCP_PROTOCOL_VERSIONS),
        }, "describe_mcp_protocol_contract() must report the declared values, not something recomputed."


class TestVerifyNegotiatedProtocolVersion:
    """Behaviour on a version Alpha does not speak."""

    @pytest.mark.parametrize("version", SUPPORTED_MCP_PROTOCOL_VERSIONS)
    def test_supported_versions_pass_through_unchanged(self, version):
        assert supports_mcp_protocol_version(version)
        assert verify_negotiated_protocol_version("srv", version) == version

    def test_version_older_than_the_minimum_is_refused(self):
        """A server too old for Alpha's parsing cannot be tolerated.

        `2024-11-05` and `2025-03-26` are revisions the SDK can decode but Alpha
        must not accept, because its tool-result parsing requires
        `structuredContent` and `ResourceLink`. This is the test that would fail
        if someone "helpfully" added them back to the supported set.
        """
        for too_old in ("2024-11-05", "2025-03-26"):
            with pytest.raises(McpProtocolVersionError) as excinfo:
                verify_negotiated_protocol_version("srv", too_old)

            message = str(excinfo.value)
            assert "srv" in message, "the error must name the server, so an operator knows which entry to fix"
            assert "older than the minimum" in message
            assert MINIMUM_MCP_PROTOCOL_VERSION in message

        message = str(excinfo.value)
        assert "srv" in message, "the error must name the server, so an operator knows which entry to fix"
        assert "older than the minimum" in message
        assert MINIMUM_MCP_PROTOCOL_VERSION in message

    def test_current_spec_revision_is_refused_and_says_so(self):
        """2026-07-28 is a real revision this release genuinely does not speak.

        The message has to distinguish that from garbage: the operator's real
        question is "is my server ahead of Alpha, or is this a broken server?",
        and the answer changes what they do about it.
        """
        with pytest.raises(McpProtocolVersionError) as excinfo:
            verify_negotiated_protocol_version("srv", CURRENT_SPEC_MCP_PROTOCOL_VERSION)

        assert "newer than the newest revision Alpha speaks" in str(excinfo.value)

    def test_unknown_string_is_not_reported_as_being_in_the_future(self):
        """Revisions are an enumerated set, not an ordered scalar.

        `"zzz"` sorts above every date-shaped revision, so a naive string
        comparison would tell an operator their server is from the future. It is
        not: it is not a revision at all.
        """
        with pytest.raises(McpProtocolVersionError) as excinfo:
            verify_negotiated_protocol_version("srv", "zzz")

        message = str(excinfo.value)
        assert "not a protocol revision this Alpha release knows about" in message
        assert "newer than" not in message

    def test_missing_version_is_refused_not_tolerated(self):
        """An absent protocolVersion is a refusal, not a pass.

        This is the case a mocked session hits, and it is the reason the gate
        fails closed: a server that will not say which revision it speaks has
        not been shown to speak one this release can read.
        """
        with pytest.raises(McpProtocolVersionError):
            verify_negotiated_protocol_version("srv", "")

    def test_error_message_lists_every_revision_alpha_speaks(self):
        with pytest.raises(McpProtocolVersionError) as excinfo:
            verify_negotiated_protocol_version("srv", "2024-11-05")

        for version in SUPPORTED_MCP_PROTOCOL_VERSIONS:
            assert version in str(excinfo.value)

    def test_error_is_a_runtime_error(self):
        """Callers that already catch RuntimeError keep working.

        Session-pool teardown unwinds on BaseException, so the new gate must not
        introduce a base class that escapes that handling.
        """
        assert issubclass(McpProtocolVersionError, RuntimeError)


class TestPooledSessionRefusesBeforeCommitting:
    """The gate is wired into the session pool, ahead of the commit point."""

    @pytest.mark.asyncio
    async def test_unsupported_version_prevents_a_session_reaching_a_caller(self, monkeypatch):
        """A refused server must never produce a session for anyone to use.

        This is the end-to-end property the constant tests cannot reach: it
        exercises MCPSessionPool.get_session with a server double that answers
        `initialize` with a revision Alpha does not speak, and asserts the caller
        sees the protocol error rather than a session object.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from alpha.mcp.session_pool import MCPSessionPool, reset_session_pool

        reset_session_pool()
        try:
            pool = MCPSessionPool()
            session = AsyncMock()
            session.initialize = AsyncMock(return_value=SimpleNamespace(protocolVersion=CURRENT_SPEC_MCP_PROTOCOL_VERSION))
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=session)
            cm.__aexit__ = AsyncMock(return_value=False)

            with (
                patch("langchain_mcp_adapters.sessions.create_session", return_value=cm),
                pytest.raises(McpProtocolVersionError),
            ):
                await pool.get_session(
                    "server",
                    "thread-1",
                    {"transport": "stdio", "command": "srv", "args": []},
                )
        finally:
            reset_session_pool()

    @pytest.mark.asyncio
    async def test_supported_version_admits_the_session(self, monkeypatch):
        """The gate must not be a blanket refusal: a supported server connects."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from alpha.mcp.session_pool import MCPSessionPool, reset_session_pool

        reset_session_pool()
        try:
            pool = MCPSessionPool()
            session = AsyncMock()
            session.initialize = AsyncMock(return_value=SimpleNamespace(protocolVersion=DECLARED_MCP_PROTOCOL_VERSION))
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=session)
            cm.__aexit__ = AsyncMock(return_value=False)

            with patch("langchain_mcp_adapters.sessions.create_session", return_value=cm):
                got = await pool.get_session(
                    "server",
                    "thread-1",
                    {"transport": "stdio", "command": "srv", "args": []},
                )

            assert got is session
        finally:
            reset_session_pool()
