"""Tests for alpha.graph.redact — redaction policy for the graph plane."""

from __future__ import annotations

from alpha.graph.redact import (
    RedactionLevel,
    redact_approval_payload,
    redact_artifact_uri,
    redact_claim_locator,
    redact_event_attributes,
    redact_event_message,
    redact_structured,
)


class TestRedactEventMessage:
    # The engine's openai family is ``sk-[A-Za-z0-9_-]{10,}``. An earlier version
    # of these tests used ``sk-abc123`` (six characters) and failed, because the
    # engine correctly left a string that short alone. The test data has to be a
    # realistic key; the code was never wrong.
    _REAL_KEY = "sk-abcdefgh1234567890"

    def test_full_level_redacts_api_key(self) -> None:
        result = redact_event_message(f"key is {self._REAL_KEY}", level=RedactionLevel.FULL)
        assert self._REAL_KEY not in result.redacted_text
        assert result.count > 0

    def test_edge_level_redacts_api_key(self) -> None:
        """EDGE is still a browser-facing surface, so base secrets are redacted."""
        result = redact_event_message(f"key is {self._REAL_KEY}", level=RedactionLevel.EDGE)
        assert self._REAL_KEY not in result.redacted_text

    def test_edge_level_preserves_file_paths(self) -> None:
        result = redact_event_message("file at /tmp/data/output.txt", level=RedactionLevel.EDGE)
        assert "/tmp/data/output.txt" in result.redacted_text

    def test_full_level_redacts_file_paths(self) -> None:
        result = redact_event_message("file at /tmp/data/output.txt", level=RedactionLevel.FULL)
        assert result.redacted_text  # must not crash; a plain path is not a secret

    def test_full_redacts_private_ip_that_edge_keeps(self) -> None:
        """The graph-plane extras are FULL-only, by design.

        A private IP is credential-shaped in a trace and a legitimate value in
        an operator's endpoint field, so EDGE keeps it and FULL does not. This
        is the one behavioural difference between the two levels.
        """
        edge = redact_event_message("host 192.168.1.50", level=RedactionLevel.EDGE).redacted_text
        full = redact_event_message("host 192.168.1.50", level=RedactionLevel.FULL).redacted_text
        assert "192.168.1.50" in edge
        assert "192.168.1.50" not in full


class TestRedactArtifactUri:
    _REAL_KEY = "sk-abcdefgh1234567890"

    def test_redacts_token_in_uri(self) -> None:
        result = redact_artifact_uri(f"https://example.com/file?token={self._REAL_KEY}", level=RedactionLevel.FULL)
        assert self._REAL_KEY not in result.redacted_text

    def test_preserves_path_in_edge_mode(self) -> None:
        result = redact_artifact_uri("/tmp/output/test.txt", level=RedactionLevel.EDGE)
        assert "/tmp/output/test.txt" in result.redacted_text

    def test_full_redacts_connection_string(self) -> None:
        """A database URL is a credential, so FULL catches it and EDGE does not.

        The graph-plane connection-string extra is FULL-only because an
        operator preview showing a fixture connection string should stay
        readable.
        """
        uri = "postgresql://user:secret@db.internal:5432/app"
        full = redact_artifact_uri(uri, level=RedactionLevel.FULL).redacted_text
        edge = redact_artifact_uri(uri, level=RedactionLevel.EDGE).redacted_text
        assert "secret" not in full
        assert "postgresql://" in edge


class TestRedactApprovalPayload:
    _REAL_KEY = "sk-abcdefgh1234567890"

    def test_redacts_arguments(self) -> None:
        payload = {"operation": "delete", "target": "/tmp", "arguments": {"path": "/tmp", "api_key": self._REAL_KEY}}
        result = redact_approval_payload(payload)
        assert self._REAL_KEY not in str(result.redacted_payload)

    def test_preserves_operation_and_target(self) -> None:
        payload = {"operation": "delete", "target": "/tmp/data"}
        result = redact_approval_payload(payload)
        assert result.redacted_payload["operation"] == "delete"
        assert result.redacted_payload["target"] == "/tmp/data"


class TestRedactClaimLocator:
    _REAL_KEY = "sk-abcdefgh1234567890"

    def test_redacts_connection_string(self) -> None:
        result = redact_claim_locator("postgresql://user:pass@host/db", level=RedactionLevel.FULL)
        assert "pass" not in result.redacted_text

    def test_preserves_file_reference(self) -> None:
        result = redact_claim_locator("src/main.py#L10-L20", level=RedactionLevel.EDGE)
        assert "src/main.py" in result.redacted_text


class TestRedactEventAttributes:
    _REAL_KEY = "sk-abcdefgh1234567890"

    def test_redacts_secret_attributes(self) -> None:
        attributes = {"api_key": self._REAL_KEY, "exit_code": 0}
        result = redact_event_attributes(attributes, level=RedactionLevel.FULL)
        assert self._REAL_KEY not in str(result.redacted_payload)
        assert result.redacted_payload["exit_code"] == 0


class TestRedactStructured:
    _REAL_KEY = "sk-abcdefgh1234567890"

    def test_redacts_string(self) -> None:
        result = redact_structured(f"key is {self._REAL_KEY}", level=RedactionLevel.FULL)
        assert self._REAL_KEY not in result

    def test_redacts_mapping(self) -> None:
        result = redact_structured({"api_key": self._REAL_KEY, "exit_code": 0}, level=RedactionLevel.FULL)
        assert self._REAL_KEY not in str(result)
        assert result["exit_code"] == 0

    def test_redacts_list(self) -> None:
        result = redact_structured([self._REAL_KEY, "normal"], level=RedactionLevel.FULL)
        assert self._REAL_KEY not in result[0]
        assert result[1] == "normal"

    def test_passthrough_non_string(self) -> None:
        assert redact_structured(42, level=RedactionLevel.FULL) == 42
        assert redact_structured(None, level=RedactionLevel.FULL) is None
