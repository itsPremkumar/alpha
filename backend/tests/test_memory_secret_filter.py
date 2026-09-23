"""Sensitive-memory filtering: pattern redaction + the single memory write boundary.

Covers two layers:

1. ``alpha.security.memory_redaction`` -- one test per pattern family, the
   documented benign non-matches, overlap merging, ``extra_patterns``,
   ``redact_mapping``, documented coverage limits, idempotency, and the honest
   docstring claims (every kind named, limits stated).
2. The :class:`MemoryManager` write boundary -- every write path (``add`` /
   ``add_nowait`` / ``aadd`` / ``create_fact`` / ``update_fact`` /
   ``import_memory``) funnels through the module-level seam
   ``redact_for_memory_write`` (one test monkeypatches exactly that one
   function), attaches honest ``memory_redaction`` metadata where a slot
   exists, never mutates caller messages, and never stores or logs secret text.

All fixtures are OBVIOUSLY FAKE (``sk-test-...``, ``TESTFAKE`` bodies); no
fixture here is a usable credential.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import PrivateAttr

from alpha.agents.memory import MemoryManager, get_memory_manager, reset_memory_manager
from alpha.agents.memory.manager import MEMORY_REDACTION_METADATA_KEY
from alpha.config.memory_config import MemoryConfig, get_memory_config, set_memory_config
from alpha.security import memory_redaction
from alpha.security.memory_redaction import Redaction, redact_for_memory, redact_mapping

# ── Obviously fake fixtures (never real credentials) ───────────────────────
FAKE_OPENAI_KEY = "sk-test-4f9abcXYZwxyz1234567890"
FAKE_GHP = "ghp_TESTFAKEabcdef1234567890"
FAKE_PAT = "github_pat_11TESTFAKE0000_ABCDEFGHIJ"
FAKE_AWS = "AKIATESTFAKE00000000"  # AKIA + 16 chars
FAKE_PEM_BLOCK = "-----BEGIN RSA PRIVATE KEY-----\nMIIEfakeTESTFAKEdataline\n-----END RSA PRIVATE KEY-----"
FAKE_PEM_HEADER = "-----BEGIN PRIVATE KEY-----"
FAKE_BEARER_VALUE = "fake-token-abc12345"
FAKE_SESSION_VALUE = "fakeSessionValue123"


@pytest.fixture(autouse=True)
def _isolate_memory_manager():
    """Reset the singleton + restore config around every test (existing style)."""
    orig = get_memory_config()
    reset_memory_manager()
    yield
    set_memory_config(orig)
    reset_memory_manager()


# ── Layer 1: pattern families ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected_kind", "expected_pattern", "secret_fragment"),
    [
        pytest.param(f"the key is {FAKE_OPENAI_KEY} ok", "openai_key", "openai_sk_key", FAKE_OPENAI_KEY, id="openai_sk_key"),
        pytest.param(f"use {FAKE_GHP} for deploy", "github_token", "github_token_prefix", FAKE_GHP, id="github_token_prefix"),
        pytest.param(f"fine pat {FAKE_PAT} here", "github_token", "github_fine_grained_pat", FAKE_PAT, id="github_fine_grained_pat"),
        pytest.param(f"aws id {FAKE_AWS} ok", "aws_access_key_id", "aws_access_key_id", FAKE_AWS, id="aws_access_key_id"),
        pytest.param(FAKE_PEM_BLOCK, "private_key", "pem_private_key_block", "MIIEfakeTESTFAKEdataline", id="pem_block"),
        pytest.param(FAKE_PEM_HEADER, "private_key", "pem_private_key_header", FAKE_PEM_HEADER, id="pem_header"),
        pytest.param(f"Authorization: Bearer {FAKE_BEARER_VALUE}", "authorization_header", "bearer_token", FAKE_BEARER_VALUE, id="bearer_token"),
        pytest.param(f"login session={FAKE_SESSION_VALUE} done", "session_cookie", "session_cookie_assignment", FAKE_SESSION_VALUE, id="session_cookie"),
    ],
)
def test_each_pattern_family_redacts(text: str, expected_kind: str, expected_pattern: str, secret_fragment: str) -> None:
    """Each covered family replaces its span and reports (kind, pattern_name)."""
    result = redact_for_memory(text)
    assert result.count >= 1
    assert (expected_kind, expected_pattern) in [(r.kind, r.pattern_name) for r in result.redactions]
    assert result.redacted_text != text
    assert secret_fragment not in result.redacted_text
    assert result.count == len(result.redactions)


def test_env_file_line_reports_env_assignment_and_keeps_key_label() -> None:
    """Line-start assignments report ``env_assignment``; the key label survives."""
    text = "X_API_KEY=fake-env-key-123\nOPENAI_API_KEY=" + FAKE_OPENAI_KEY + "\n"
    result = redact_for_memory(text)
    assert result.count == 2
    assert all(r.kind == "env_assignment" for r in result.redactions)
    assert "X_API_KEY=" in result.redacted_text
    assert "OPENAI_API_KEY=" in result.redacted_text
    assert "fake-env-key-123" not in result.redacted_text
    assert FAKE_OPENAI_KEY not in result.redacted_text


def test_mid_line_assignment_reports_secret_assignment() -> None:
    """Mid-line assignments (not line-anchored) report ``secret_assignment``."""
    text = 'use DB_PASSWORD=hunter2-fake for the db; the key api_key = "fake-value-123456" was sent'
    result = redact_for_memory(text)
    assert result.count == 2
    assert all(r.kind == "secret_assignment" for r in result.redactions)
    assert "DB_PASSWORD=[REDACTED:generic_key_assignment]" in result.redacted_text
    assert 'api_key = "[REDACTED:generic_key_assignment]"' in result.redacted_text
    assert "hunter2-fake" not in result.redacted_text
    assert "fake-value-123456" not in result.redacted_text


@pytest.mark.parametrize(
    "text",
    [
        "max_tokens=1024",
        "n_tokens = 500 of 8000",
        "SECRETARY=alice",
        "tokenizer = 'gpt-4o'",
        "api_docs_url=https://example.com/docs",
        "Authorization: Basic ZmFrZS1iYXNpYzY0dmFsdWU=",
    ],
)
def test_benign_config_text_passes_through_unredacted(text: str) -> None:
    """Documented non-matches (segment alignment, no Bearer) stay untouched."""
    result = redact_for_memory(text)
    assert result.count == 0
    assert result.redactions == []
    assert result.redacted_text == text


def test_overlapping_matches_merge_to_one_report() -> None:
    """``OPENAI_API_KEY=sk-...``: assignment + sk- overlap merge into ONE span,
    count is 1 (merged, not raw hits), and the key label is preserved."""
    text = f"rotated the OPENAI_API_KEY={FAKE_OPENAI_KEY} yesterday"
    result = redact_for_memory(text)
    assert result.count == 1
    assert result.redacted_text == "rotated the OPENAI_API_KEY=[REDACTED:generic_key_assignment] yesterday"
    assert FAKE_OPENAI_KEY not in result.redacted_text


def test_extra_patterns_str_and_named_pair_forms() -> None:
    """``extra_patterns`` accepts a bare regex (auto-named) and a named pair."""
    bare = redact_for_memory("internal ref RC-FAKE001234 in log", extra_patterns=[r"RC-[A-Z0-9]{6,}"])
    assert bare.count == 1
    assert bare.redactions[0].kind == "extra_pattern"
    assert bare.redactions[0].pattern_name == "extra_pattern_0"
    assert "RC-FAKE001234" not in bare.redacted_text

    named = redact_for_memory("val RC-FAKE001234 end", extra_patterns=[("internal_ref", r"RC-[A-Z0-9]{6,}")])
    assert named.count == 1
    assert named.redactions[0].pattern_name == "internal_ref"
    assert "RC-FAKE001234" not in named.redacted_text


def test_invalid_extra_pattern_regex_raises() -> None:
    """An invalid caller regex surfaces as ``re.error`` (documented)."""
    with pytest.raises(re.error):
        redact_for_memory("some text", extra_patterns=["[unclosed"])


def test_builtin_redaction_is_idempotent_on_its_own_output() -> None:
    """A second pass over already-redacted text reports count 0 (placeholders
    never re-match) -- so nested/double writes cannot inflate metadata."""
    text = f"key {FAKE_OPENAI_KEY}; DB_PASSWORD=hunter2-fake; session={FAKE_SESSION_VALUE}; X_API_KEY=fake-env-key-123"
    first = redact_for_memory(text)
    assert first.count == 4
    second = redact_for_memory(first.redacted_text)
    assert second.count == 0
    assert second.redacted_text == first.redacted_text


def test_redact_mapping_covers_secret_keys_values_and_lists() -> None:
    """Secret-named keys redact whole string values; value patterns scan plain
    strings and list elements (inheriting the enclosing key); ints untouched."""
    payload = {
        "api_key": "fake-value-123456",
        "note": f"rotated {FAKE_OPENAI_KEY} today",
        "db_password": "hunter2-fake",
        "max_tokens": 1024,
        "webhooks": [FAKE_GHP],
        "service_secret": ["queued-for-rotation-1"],
    }
    snapshot = json.dumps(payload, sort_keys=True)
    result = redact_mapping(payload)
    assert result.count == 5
    redacted = result.redacted_payload
    # keys preserved
    assert set(redacted) == set(payload)
    # secret-named keys -> whole value replaced, key kept
    assert redacted["api_key"] == "[REDACTED:secret_named_key]"
    assert redacted["db_password"] == "[REDACTED:secret_named_key]"
    # secret-shaped value inside a plain string value
    assert FAKE_OPENAI_KEY not in redacted["note"]
    # value pattern inside a list under a plain key
    assert redacted["webhooks"] == ["[REDACTED:github_token_prefix]"]
    # list elements inherit the enclosing secret-named key
    assert redacted["service_secret"] == ["[REDACTED:secret_named_key]"]
    # non-string value under a non-secret key untouched
    assert redacted["max_tokens"] == 1024
    # original payload not mutated
    assert json.dumps(payload, sort_keys=True) == snapshot
    assert payload["api_key"] == "fake-value-123456"
    assert MEMORY_REDACTION_METADATA_KEY not in payload


def test_redact_mapping_plural_secret_key_documented_limit() -> None:
    """Documented limit: plural ``secrets:`` keys do not match the singular
    keyword segments, so such a payload passes through unchanged."""
    payload = {"secrets": ["value123456"]}
    result = redact_mapping(payload)
    assert result.count == 0
    assert result.redactions == []
    assert result.redacted_payload == {"secrets": ["value123456"]}


def test_redact_mapping_is_idempotent_on_redacted_output() -> None:
    """Re-mapping an already-redacted payload reports count 0 (no phantom
    re-counts, no metadata inflation on repeated imports)."""
    payload = {"api_key": "fake-value-123456", "note": f"key {FAKE_OPENAI_KEY}"}
    first = redact_mapping(payload)
    assert first.count == 2
    second = redact_mapping(first.redacted_payload)
    assert second.count == 0
    assert second.redacted_payload == first.redacted_payload


def test_module_docstring_documents_every_kind_and_its_limits() -> None:
    """Honesty gate: the module docstring must name every reported kind AND
    state the coverage limits (heuristic / not exhaustive), so docstring claims
    can never drift away from what the tests actually verify."""
    doc = memory_redaction.__doc__ or ""
    for kind in (
        "openai_key",
        "github_token",
        "aws_access_key_id",
        "private_key",
        "authorization_header",
        "session_cookie",
        "secret_assignment",
        "env_assignment",
        "mapping_secret_key",
        "extra_pattern",
    ):
        assert kind in doc, f"docstring must document kind {kind!r}"
    assert "NOT exhaustive" in doc
    assert "HEURISTIC" in doc
    assert "Basic" in doc  # documents the Authorization: Basic gap
    assert "secrets:" in doc  # documents the plural-key gap


# ── Layer 2: the single MemoryManager write boundary ───────────────────────


class _RecordingBackend(MemoryManager):
    """Records every write payload it receives (AFTER the redaction boundary),
    overriding all six write methods (sync + async + dict + str paths)."""

    _writes: list = PrivateAttr(default_factory=list)

    def add(self, thread_id, messages, *, agent_name=None, user_id=None, trace_id=None) -> None:
        self._writes.append(("add", messages))

    def add_nowait(self, thread_id, messages, *, agent_name=None, user_id=None) -> None:
        self._writes.append(("add_nowait", messages))

    async def aadd(self, thread_id, messages, *, agent_name=None, user_id=None, trace_id=None) -> None:
        self._writes.append(("aadd", messages))

    def get_context(self, user_id, *, agent_name=None, thread_id=None) -> str:
        return ""

    def create_fact(self, content, category="context", confidence=0.5, *, agent_name=None, user_id=None):
        self._writes.append(("create_fact", content))
        return {"facts": []}, "f1"

    def update_fact(self, fact_id, content=None, category=None, confidence=None, *, agent_name=None, user_id=None):
        self._writes.append(("update_fact", content))
        return {"facts": []}

    def import_memory(self, memory_data, *, user_id=None, agent_name=None):
        self._writes.append(("import_memory", memory_data))
        return memory_data

    @classmethod
    def from_config(cls, backend_config, *, mode="middleware", **host_hooks):
        return cls(backend_config=backend_config or {}, mode=mode)


class _AddOnlyBackend(MemoryManager):
    """Implements only tier-1, so ``add_nowait`` / ``aadd`` are the INHERITED
    base implementations that delegate to the wrapped ``add``."""

    _adds: list = PrivateAttr(default_factory=list)

    def add(self, thread_id, messages, *, agent_name=None, user_id=None, trace_id=None) -> None:
        self._adds.append(messages)

    def get_context(self, user_id, *, agent_name=None, thread_id=None) -> str:
        return ""

    @classmethod
    def from_config(cls, backend_config, *, mode="middleware", **host_hooks):
        return cls(backend_config=backend_config or {}, mode=mode)


class _AsyncBackend(MemoryManager):
    """Overrides ``aadd`` as a real coroutine method, exercising the async
    wrapper branch of the boundary."""

    _aadds: list = PrivateAttr(default_factory=list)
    _adds: list = PrivateAttr(default_factory=list)

    def add(self, thread_id, messages, *, agent_name=None, user_id=None, trace_id=None) -> None:
        self._adds.append(messages)

    async def aadd(self, thread_id, messages, *, agent_name=None, user_id=None, trace_id=None) -> None:
        self._aadds.append(messages)

    def get_context(self, user_id, *, agent_name=None, thread_id=None) -> str:
        return ""

    @classmethod
    def from_config(cls, backend_config, *, mode="middleware", **host_hooks):
        return cls(backend_config=backend_config or {}, mode=mode)


def test_add_redacts_messages_and_attaches_honest_metadata() -> None:
    """``add`` copies ONLY the dirty message, redacts it, attaches honest
    per-message metadata, keeps existing kwargs, and never mutates the
    caller's original messages."""
    backend = _RecordingBackend(backend_config={})
    original = HumanMessage(content=f"the secret is {FAKE_OPENAI_KEY} ok", additional_kwargs={"trace": "keep"})
    clean = AIMessage(content="nothing sensitive here")
    backend.add("t1", [original, clean], user_id="u1")

    kind, stored = backend._writes[-1]
    assert kind == "add"
    # clean message passes through by reference; dirty one is a copy
    assert stored[1] is clean
    dirty = stored[0]
    assert dirty is not original
    assert dirty.content == "the secret is [REDACTED:openai_sk_key] ok"
    assert FAKE_OPENAI_KEY not in dirty.content
    assert dirty.additional_kwargs[MEMORY_REDACTION_METADATA_KEY] == {
        "redacted_count": 1,
        "redacted_kinds": ["openai_key"],
    }
    assert dirty.additional_kwargs["trace"] == "keep"
    # caller's original message untouched, no metadata injected into it
    assert original.content == f"the secret is {FAKE_OPENAI_KEY} ok"
    assert MEMORY_REDACTION_METADATA_KEY not in original.additional_kwargs


def test_add_nowait_override_is_redacted() -> None:
    """A backend-defined ``add_nowait`` override is wrapped too (it does not
    delegate to ``add``), so its own body never sees the secret."""
    backend = _RecordingBackend(backend_config={})
    backend.add_nowait("t1", [HumanMessage(content="pw DB_PASSWORD=hunter2-fake!")], user_id="u1")
    kind, stored = backend._writes[-1]
    assert kind == "add_nowait"
    assert "hunter2-fake" not in stored[0].content
    assert stored[0].additional_kwargs[MEMORY_REDACTION_METADATA_KEY] == {
        "redacted_count": 1,
        "redacted_kinds": ["secret_assignment"],
    }


def test_inherited_add_nowait_and_aadd_delegate_through_the_seam() -> None:
    """The base (inherited) ``add_nowait`` / ``aadd`` delegate to the wrapped
    ``add``, so writes are redacted exactly once via the seam."""
    backend = _AddOnlyBackend(backend_config={})
    backend.add_nowait("t1", [HumanMessage(content=f"key {FAKE_AWS} ok")], user_id="u1")
    asyncio.run(backend.aadd("t1", [HumanMessage(content=f"key {FAKE_AWS} again")], user_id="u1"))
    assert len(backend._adds) == 2
    for stored_list in backend._adds:
        msg = stored_list[0]
        assert FAKE_AWS not in msg.content
        assert msg.additional_kwargs[MEMORY_REDACTION_METADATA_KEY] == {
            "redacted_count": 1,
            "redacted_kinds": ["aws_access_key_id"],
        }


def test_async_aadd_override_is_redacted() -> None:
    """A coroutine ``aadd`` override is wrapped by the async boundary branch."""
    backend = _AsyncBackend(backend_config={})
    asyncio.run(backend.aadd("t1", [HumanMessage(content=f"token {FAKE_GHP} rotated")], user_id="u1"))
    assert backend._adds == []  # the override ran, not the base delegation
    msg = backend._aadds[0][0]
    assert FAKE_GHP not in msg.content
    assert msg.additional_kwargs[MEMORY_REDACTION_METADATA_KEY] == {
        "redacted_count": 1,
        "redacted_kinds": ["github_token"],
    }


def test_fact_writes_redact_and_log_counts_without_secret_text(caplog) -> None:
    """``create_fact`` / ``update_fact`` content (str, no metadata slot) is
    redacted before the backend sees it; counts/kinds are logged -- the secret
    text itself never appears in the stored value OR the log."""
    backend = _RecordingBackend(backend_config={})
    with caplog.at_level(logging.INFO, logger="alpha.agents.memory.manager"):
        backend.create_fact("connect with DB_PASSWORD=hunter2-fake now", user_id="u1")
        backend.update_fact("f9", content=f"token {FAKE_GHP} rotated", user_id="u1")
        # omitted content: no seam call, records None unchanged
        backend.update_fact("f10", user_id="u1")

    kind, content = backend._writes[-3]
    assert kind == "create_fact"
    assert content == "connect with DB_PASSWORD=[REDACTED:generic_key_assignment] now"
    assert "hunter2-fake" not in content

    kind, content = backend._writes[-2]
    assert kind == "update_fact"
    assert content == "token [REDACTED:github_token_prefix] rotated"
    assert FAKE_GHP not in content

    kind, content = backend._writes[-1]
    assert (kind, content) == ("update_fact", None)

    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "create_fact" in joined
    assert "update_fact" in joined
    assert "secret_assignment" in joined
    assert "github_token" in joined
    # honest log: counts and kinds only, never the secret itself
    assert "hunter2-fake" not in joined
    assert FAKE_GHP not in joined


def test_import_memory_redacts_and_attaches_top_level_metadata() -> None:
    """``import_memory`` payloads are redacted as mappings and get the honest
    top-level ``memory_redaction`` key; the caller's dict is not mutated."""
    backend = _RecordingBackend(backend_config={})
    payload = {
        "note": f"the OpenAI key was {FAKE_OPENAI_KEY}",
        "db_password": "hunter2-fake",
        "max_tokens": 1024,
    }
    result = backend.import_memory(payload, user_id="u1")

    kind, stored = backend._writes[-1]
    assert kind == "import_memory"
    dumped = json.dumps(stored)
    assert FAKE_OPENAI_KEY not in dumped
    assert "hunter2-fake" not in dumped
    assert stored["db_password"] == "[REDACTED:secret_named_key]"
    assert stored["max_tokens"] == 1024
    assert stored[MEMORY_REDACTION_METADATA_KEY] == {
        "redacted_count": 2,
        "redacted_kinds": ["mapping_secret_key", "openai_key"],
    }
    # backend echoes its input; the wrapper-injected payload is what was stored
    assert result is stored
    # caller's original payload untouched
    assert payload["db_password"] == "hunter2-fake"
    assert MEMORY_REDACTION_METADATA_KEY not in payload


def test_single_seam_stub_intercepts_writes_and_drives_metadata(monkeypatch) -> None:
    """TWO things pin the single-seam design: (a) stubbing the ONE module-level
    function ``alpha.agents.memory.manager.redact_for_memory_write`` intercepts
    every write path (``calls == ["anything"]`` after the first write), and
    (b) the metadata attached to a stored entry is derived from the redactions
    the stub returned -- not from any second hook."""
    calls: list = []

    def fake_redact(content):
        calls.append(content)
        redactions = [Redaction(kind="stubbed_kind", pattern_name="stub_pattern")]
        if isinstance(content, dict):
            return {"note": "clean"}, redactions
        return "anything", redactions

    monkeypatch.setattr("alpha.agents.memory.manager.redact_for_memory_write", fake_redact)

    backend = _RecordingBackend(backend_config={})
    backend.add("t1", "anything", user_id="u1")
    # the ONE stubbed function saw this write, and its return reached the backend
    assert calls == ["anything"]
    assert backend._writes[-1] == ("add", "anything")

    backend.create_fact("raw fact content", user_id="u1")
    assert backend._writes[-1] == ("create_fact", "anything")

    backend.import_memory({"raw": "payload"}, user_id="u1")
    kind, stored = backend._writes[-1]
    assert kind == "import_memory"
    # metadata derived from the STUB's returned redactions
    assert stored == {
        "note": "clean",
        MEMORY_REDACTION_METADATA_KEY: {"redacted_count": 1, "redacted_kinds": ["stubbed_kind"]},
    }
    assert calls == ["anything", "raw fact content", {"raw": "payload"}]


def test_multipart_message_redacts_text_and_preserves_other_parts() -> None:
    """Multi-part message content: the text part is redacted (with per-message
    metadata covering it), the image part passes through untouched."""
    backend = _RecordingBackend(backend_config={})
    image_part = {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}}
    original = HumanMessage(
        content=[
            {"type": "text", "text": f"deploy key {FAKE_AWS} leaked"},
            image_part,
        ]
    )
    backend.add("t1", [original], user_id="u1")

    stored = backend._writes[-1][1][0]
    assert stored.content[0]["text"] == "deploy key [REDACTED:aws_access_key_id] leaked"
    # the clean image part passes the boundary by reference (langchain copies
    # content parts at construction, so identity is vs. the original message)
    assert stored.content[1] is original.content[1]
    assert stored.content[1] == image_part
    assert stored.additional_kwargs[MEMORY_REDACTION_METADATA_KEY] == {
        "redacted_count": 1,
        "redacted_kinds": ["aws_access_key_id"],
    }
    # caller's original message untouched
    assert original.content[0]["text"] == f"deploy key {FAKE_AWS} leaked"
    assert MEMORY_REDACTION_METADATA_KEY not in original.additional_kwargs


def test_dict_shaped_message_gets_redacted_with_metadata() -> None:
    """Dict-shaped messages (role/content) inside the write payload are copied,
    redacted, and carry per-message metadata in their ``additional_kwargs``."""
    backend = _RecordingBackend(backend_config={})
    original = {"role": "user", "content": f"login with Bearer {FAKE_BEARER_VALUE} now"}
    backend.add("t1", [original], user_id="u1")

    stored = backend._writes[-1][1][0]
    assert stored["content"] == "login with [REDACTED:bearer_token] now"
    assert stored["additional_kwargs"][MEMORY_REDACTION_METADATA_KEY] == {
        "redacted_count": 1,
        "redacted_kinds": ["authorization_header"],
    }
    # original dict untouched
    assert original["content"] == f"login with Bearer {FAKE_BEARER_VALUE} now"
    assert "additional_kwargs" not in original


def test_deermem_fact_write_never_stores_secret_text(tmp_path, monkeypatch) -> None:
    """End-to-end through a real backend: a DeerMem ``create_fact`` write lands
    on disk redacted -- no file under the storage root contains the secret."""
    import alpha.config.runtime_paths as rp

    monkeypatch.setattr(rp, "runtime_home", lambda: tmp_path)
    set_memory_config(MemoryConfig(manager_class="deermem"))
    manager = get_memory_manager()
    manager.create_fact("connect with DB_PASSWORD=hunter2-fake now", user_id="u1", agent_name="test-agent")

    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert files, "expected DeerMem to persist memory files under tmp_path"
    blobs = [path.read_bytes() for path in files]
    assert all(b"hunter2-fake" not in blob for blob in blobs)
    # positive control: the redacted form IS what got stored
    assert any(b"[REDACTED:generic_key_assignment]" in blob for blob in blobs)
