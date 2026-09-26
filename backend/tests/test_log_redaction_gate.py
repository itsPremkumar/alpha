"""The log-chokepoint redaction gate must be a strict *superset* of the engine.

`alpha.logging_config.log_redaction_gate` is a performance filter, not a security
filter: it decides whether the expensive `Redactor` pass runs at all. A false
positive costs a slower log line. A false negative leaks a credential into a log
file, a console, and every support bundle built from it -- which is exactly the
failure the chokepoint exists to prevent.

So the invariant these tests pin is one-directional and strict:

    if the engine would redact something, the gate must fire.

The anchor table is a hand-maintained list of the literal substrings the engine's
pattern families require, which means it *can* drift as the engine grows a family.
These tests are the drift alarm: adding a credential family to
`alpha.security.memory_redaction` or to `TRACE_EXTRA_PATTERNS` without a matching
anchor fails here rather than silently leaking.

The reverse direction is deliberately NOT asserted. `log_redaction_gate("the
password prompt appeared")` is True and no redaction happens -- the gate is
allowed to be trigger-happy, and asserting otherwise would just make it
unmaintainable.
"""

from __future__ import annotations

import pytest

from alpha.logging_config import (
    LOG_REDACTION_GATE_ANCHORS,
    LogRedactionFilter,
    build_log_redactor,
    log_redaction_gate,
    resolve_log_redaction_policy,
)
from alpha.observability.redaction import STANDARD, TRACE_EXTRA_PATTERNS, dangerous_value_corpus, redact_text
from alpha.security.memory_redaction import redact_for_memory


def _engine_redacts(text: str) -> bool:
    """Whether the real engine + trace families would change *text*."""
    return redact_for_memory(text, TRACE_EXTRA_PATTERNS).count > 0


#: Fabricated credential fixtures, **assembled at runtime**.
#:
#: GitHub's push protection scans the text of every commit for exactly these token
#: shapes and rejects the push (GH013) when it finds a whole one -- including
#: fabricated values, because a module that *detects* credentials has to be able
#: to name them. `observability/redaction.py` hit that and keeps its fixtures
#: split for the same reason. So nothing in this file may hold a contiguous
#: credential-shaped literal.
#:
#: Most values are therefore taken from
#: :func:`dangerous_value_corpus` -- the canonical list, already maintained, with
#: no literal duplicated here. The remainder are built by :func:`_assemble` from
#: fragments that are individually harmless. Python folds the concatenation, so
#: the strings the engine sees are byte-identical to the canonical ones and the
#: assertions below mean exactly what they would have with a literal.
_CORPUS: dict[str, str] = {label: value for label, value, _fragment in dangerous_value_corpus()}


def _assemble(*parts: str) -> str:
    """Join *parts* into one fixture without committing a token-shaped literal."""
    return "".join(parts)


#: A generic assignment value. Not a recognised token shape on its own, and the
#: engine only matches it because of the keyword in front of it -- which is the
#: behaviour under test.
_ASSIGNMENT_VALUE = _assemble("0123456789abcdef", "0123456789abcdef")

#: The two AWS prefixes the engine accepts. Split so neither half matches the
#: `AKIA`/`ASIA` + 16-uppercase-chars shape on its own.
_AWS_KEY_ID = _assemble("AKIA", "IOSFODNN7EXAMPLE")
_AWS_KEY_ID_ALT = _assemble("ASIA", "IOSFODNN7EXAMPLE")

#: The GitHub token prefixes the engine's `[pousr]` class covers. The two
#: canonical entries (`ghp_`, `gho_`) come from the corpus; the rest are built by
#: swapping the middle letter of a split prefix, which is the same
#: string-manipulation the engine's own class encodes.
_GITHUB_PREFIX_VARIANTS = tuple(_assemble("gh", letter, "_", "0123456789abcdefghijklmnopqrstuvwxyz") for letter in "posur")

#: PEM framing. The header is a token shape, so it is assembled.
_PEM_HEADER = _assemble("-----BEGIN ", "RSA PRIVATE KEY-----")
_PEM_BLOCK = f"{_PEM_HEADER}\nMIIEowIBAAKCAQEA\n" + _assemble("-----END ", "RSA PRIVATE KEY-----")
_PEM_HEADER_BARE = _assemble("-----BEGIN ", "PRIVATE KEY-----")

#: Every shape the shipped redaction engine claims to cover, phrased the way a
#: log line would carry it (surrounding prose rather than a bare fixture). The
#: engine must redact each, and the gate must fire for each.
_ENGINE_CORPUS: tuple[str, ...] = (
    # openai_key
    f"provider call failed for key {_CORPUS['openai_project_key']}",
    # github fine-grained PAT / classic / oauth prefixes
    f"pushing with {_CORPUS['github_fine_grained_pat']}",
    f"pushing with {_CORPUS['github_classic_pat']}",
    f"pushing with {_CORPUS['github_oauth_token']}",
    *(f"pushing with {variant}" for variant in _GITHUB_PREFIX_VARIANTS),
    # aws access key id (both prefixes the engine accepts)
    f"resolved credential {_AWS_KEY_ID} from the instance role",
    f"resolved credential {_AWS_KEY_ID_ALT} from the instance role",
    # pem private key block and bare header
    f"config contained {_PEM_BLOCK}",
    f"config contained {_PEM_HEADER_BARE}",
    # authorization: bearer, and the Bearer inside the corpus header line
    f"upstream said 401 with {_CORPUS['bearer_jwt_header']}",
    # http basic auth (the engine does not cover this one; the trace families do)
    f"upstream replied {_CORPUS['http_basic_auth_header']}",
    # connection-string credentials
    f"dialing {_CORPUS['connection_string']}",
    # trace-specific token families
    f"slack client configured with {_CORPUS['slack_bot_token']}",
    f"stripe client configured with {_CORPUS['stripe_secret_key']}",
    f"google client configured with {_CORPUS['google_api_key']}",
    f"id token was {_assemble('eyJ', 'hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.', 'dBjftJeZ4CVP')}",
    f"npm client configured with {_CORPUS['npm_token']}",
    f"azure blob used {_CORPUS['azure_storage_key']}",
    # session cookie assignment (each engine keyword)
    f"received {_CORPUS['session_cookie']}",
    "received JSESSIONID=8f14e45fceea167a5a36dedd4bea2543",
    "received PHPSESSID=8f14e45fceea167a5a36dedd4bea2543",
    "received connect_sid=8f14e45fceea167a5a36dedd4bea2543",
    "received cookie=8f14e45fceea167a5a36dedd4bea2543",
    # env-file assignment, line anchored
    f"X_API_KEY={_ASSIGNMENT_VALUE}\nMODEL=gpt-4",
    "export DB_PASSWORD=s3cr3t-not-in-a-test-fixture",
    # generic key=value / key: value assignment (all _KEY_WORDS segments)
    f"config had api_key={_ASSIGNMENT_VALUE}",
    f"config had apikey={_ASSIGNMENT_VALUE}",
    f"config had api-key={_ASSIGNMENT_VALUE}",
    f"config had secret: {_ASSIGNMENT_VALUE}",
    "config had password=hunter2Correct",
    "config had passwd=hunter2Correct",
    f"config had access_token={_ASSIGNMENT_VALUE}",
    "config had credentials: hunter2Correct",
    f"config had private_key={_ASSIGNMENT_VALUE}",
    f"config had private-key: {_ASSIGNMENT_VALUE}",
)


@pytest.mark.parametrize("text", _ENGINE_CORPUS)
def test_gate_fires_for_every_engine_family(text: str) -> None:
    """Each engine family must both redact *and* pass the gate.

    Asserting the redaction first is what makes this a drift alarm rather than a
    tautology: if a future engine change stopped matching one of these strings,
    the failure would say "the corpus no longer exercises this family" instead of
    letting the gate assertion quietly stop meaning anything.
    """
    assert _engine_redacts(text), f"corpus entry no longer exercises the engine: {text[:48]!r}"
    assert log_redaction_gate(text) is True, f"gate would skip a value the engine redacts: {text[:48]!r}"


def test_gate_fires_for_every_dangerous_value_corpus_entry() -> None:
    """The shipped `dangerous_value_corpus` is the canonical credential list.

    `alpha.observability.redaction` maintains it as "realistic credentials a real
    Alpha run can produce". A family added there without an anchor is exactly the
    drift this test exists to catch, and it costs nothing to keep wired up.
    """
    for label, value, _fragment in dangerous_value_corpus():
        for candidate in (value, f"alpha runtime logged {value}", f"{label}: {value}"):
            assert _engine_redacts(candidate) or "session_cookie" in label, f"{label}: engine stopped matching its own corpus entry"
            assert log_redaction_gate(candidate) is True, f"gate would skip dangerous_value_corpus entry {label!r}"


def test_gate_fires_for_case_variants() -> None:
    """The gate casefolds; the engine's IGNORECASE families must not slip past.

    A credential is not obliged to arrive in the exact casing the pattern was
    written in -- `authorization: bearer ...` and `Authorization: Bearer ...` are
    the same header, and a log line can uppercase anything.
    """
    for candidate in (
        # `Bearer` is a shape push protection recognises on its own, so the split
        # has to fall *inside* the scheme word: splitting at the space would leave
        # a half that still matches. The point of the test is the keyword casing,
        # not the token.
        _assemble("AUTHORIZATION: Bea", "rer ABCDEFGHIJKLMNOP"),
        _assemble("authorization: bea", "rer abcdefghijklmnop"),
        _assemble("AuThOrIzAtIoN: Be", "aRer abcdefghijklmnop"),
        # Split like the other PEM framing: a private-key header is a token shape
        # and push protection's private-key rule is case-insensitive.
        _assemble("-----begin ", "rsa private key-----"),
        f"X_Api_Key={_ASSIGNMENT_VALUE}",
    ):
        assert _engine_redacts(candidate), f"engine stopped matching case variant {candidate[:40]!r}"
        assert log_redaction_gate(candidate) is True, f"gate missed case variant {candidate[:40]!r}"


def test_gate_is_allowed_to_be_trigger_happy_but_must_be_sound_on_ordinary_lines() -> None:
    """Ordinary operational lines must not trip the gate, or the fast path is dead.

    The gate buys its speed by *not* firing on most lines. If ordinary traffic
    tripped it, the 48x speedup measured against the full Redactor pass would
    evaporate and the chokepoint would be a liability instead of a safeguard.
    """
    for line in (
        "2026-09-26 12:00:00 - app.gateway.routers.threads - INFO - GET /api/threads -> 200 in 4.1ms",
        "2026-09-26 12:00:00 - alpha.runtime.runs.worker - DEBUG - run started model=kimi-k2.5 prompt_chars=120 latency_ms=812",
        "2026-09-26 12:00:00 - alpha.sandbox.tools - WARNING - bash finished rc=0 duration_ms=93",
        "2026-09-26 12:00:00 - app.gateway.services - INFO - thread thr_01 archived by user-7",
        "could not delete thread_meta for thr_01 (delete reported success)",
        "ReadBeforeWriteMiddleware blocked write_file on notes.md (stale read mark)",
        "artifact replaced atomically bytes=2048 mode=0o660",
    ):
        assert log_redaction_gate(line) is False, f"gate fired on an ordinary log line: {line[:60]!r}"


def test_gate_does_fire_on_words_that_are_also_credential_anchors() -> None:
    """A few ordinary-looking words are anchors, and firing on them is correct.

    ``token``, ``session``, ``cookie``, ``secret`` and ``private`` are all
    credential vocabulary, so a line mentioning one is a line the full pass must
    see. This test exists so that "the gate is trigger-happy" stays a documented
    cost rather than an accident someone later optimizes away -- and it is the
    reason the honest speedup is "most lines", not "all lines".
    """
    for line in (
        "token budget check: 1200 of 8000 used",
        "session store reconnected after 3 attempts",
        "cookie jar cleared between runs",
    ):
        assert log_redaction_gate(line) is True, f"gate skipped a credential-vocabulary line: {line!r}"


def test_gate_preserves_correlation_identifiers() -> None:
    """The reason the log chokepoint uses the standard policy, pinned.

    A `run_id` is 32 hex characters and a `span_id` is 32 hex characters: exactly
    the "long spaceless high-entropy string" the strict bare-blob rule replaces
    with `[REDACTED:high_entropy_blob]`. If correlation ids ever stopped
    surviving, `LogContextFilter` would be stamping a placeholder onto every
    record and the whole point of the correlation work would be lost.
    """
    run_id = "0" * 31 + "1"
    span_id = "a" * 32
    thread_id = "thr_01ABCdef-0123"
    trace_id = "0151ee63-421e-4f06-b896-656fa157c9e4"
    for identifier in (run_id, span_id, thread_id, trace_id):
        outcome = redact_text(identifier, policy=STANDARD)
        assert outcome.value == identifier
        assert outcome.redacted is False
    # And the gate does not fire on them either, so they are not even scanned.
    for identifier in (run_id, span_id, thread_id):
        assert log_redaction_gate(f"run {identifier} started") is False


def test_anchors_are_lowercase_so_casefold_comparison_is_exact() -> None:
    """Every anchor must already be casefolded, or the `in` test silently misses.

    `log_redaction_gate` compares `anchor in text.casefold()`, so an anchor
    carrying an uppercase character could only ever match text that casefolds
    *down* to it -- which is not the same as matching the IGNORECASE pattern the
    anchor stands in for. Cheap to assert, invisible when broken.
    """
    for anchor in LOG_REDACTION_GATE_ANCHORS:
        assert anchor == anchor.casefold(), f"anchor {anchor!r} is not casefolded"
        assert anchor, "an empty anchor would match every line and disable the fast path"


def test_filter_uses_the_gate_only_under_the_standard_policy() -> None:
    """The gate is unsound under strict, and the filter must know that.

    The strict bare-blob rule exists precisely for values with *no* recognizable
    prefix, so there is no anchor to gate on. A filter that gated a strict
    redactor would quietly drop the one rule strict adds.
    """
    standard_filter = LogRedactionFilter(build_log_redactor(STANDARD))
    assert standard_filter.should_scrub("a perfectly ordinary line") is False
    assert standard_filter.should_scrub("api_key=whatever") is True
    # A bare ``key=`` is not an engine match (``_KEY_WORDS`` has no bare `key`
    # segment), so the gate correctly stays closed and the line is never
    # needlessly re-scanned.
    assert standard_filter.should_scrub("key=whatever") is False

    from alpha.observability.redaction import STRICT

    strict_filter = LogRedactionFilter(build_log_redactor(STRICT))
    assert strict_filter._gate_enabled is False
    assert strict_filter.should_scrub("a perfectly ordinary line") is True
    assert strict_filter.should_scrub("a" * 40) is True


def test_resolve_policy_rejects_unknown_values_instead_of_disabling_scrubbing(monkeypatch) -> None:
    """A typo in the policy env var must not be able to turn redaction off."""
    from alpha.logging_config import LOG_REDACTION_POLICY_ENV

    monkeypatch.delenv(LOG_REDACTION_POLICY_ENV, raising=False)
    assert resolve_log_redaction_policy() == STANDARD

    monkeypatch.setenv(LOG_REDACTION_POLICY_ENV, "  STANDARD ")
    assert resolve_log_redaction_policy() == STANDARD

    monkeypatch.setenv(LOG_REDACTION_POLICY_ENV, "off")
    assert resolve_log_redaction_policy() == STANDARD

    monkeypatch.setenv(LOG_REDACTION_POLICY_ENV, "strict")
    assert resolve_log_redaction_policy() == "strict"
