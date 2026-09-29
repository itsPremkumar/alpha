"""One code, one *cause* -- not one code, one vague family of causes.

``alpha.errors.registry`` is the vocabulary every surface reports through: the
REST error frame, the SSE payload, the persisted ``run.error`` metadata, the
recovery ledger and the log record all read the same :class:`ErrorDefinition`.
A code that asserts a cause its classifier cannot know is therefore wrong in all
five at once, and the damage lands on the *remediation*, not the label: an
operator reading "A run or token budget for this thread is exhausted" goes to
quota and token settings when the run actually died because the graph was too
deep.

The defect class this file pins has three shapes, and every test below is one of
them:

1. **Structural conflation.** ``RUN_QUOTA_EXCEEDED`` claimed
   ``exception_types=("BudgetExceeded", "TokenBudgetExceeded", "RecursionLimit")``
   and the ``"recursion limit"`` message hint. A LangGraph
   ``GraphRecursionError`` is the graph being too deep: no budget was consumed,
   none is exhausted, and the fix is ``recursion_limit`` or a shorter agent
   chain -- never more tokens. Fixed by splitting, not by rewording, because a
   combined "a budget or the depth was exhausted" message is still not
   actionable.
2. **The same exception type reachable through two codes with contradictory
   remediation.** Nine names were claimed twice. Classification ranks by MRO
   position and then prefers the higher severity, so the *losing* code was
   unreachable for its own type -- which meant a corrupt goal store inherited
   ``retryable=True``/``recovery=restart`` from the checkpoint code, and a 429
   throttle inherited ``retryable=False``/``recovery=investigate`` from the
   billing code.
3. **A ``retryable``/``severity`` flag taken from the code rather than the
   cause.** ``RUN_OWNERSHIP_LOST`` matched the bare substring ``"lease"``, which
   is a substring of ``please``, ``release`` and ``unleased``, so *every*
   message containing "please" was published as a lost run lease with a retry
   policy. This is the same failure mode as
   ``alpha.models.fallback.is_credit_exhausted_error``, which correctly
   suppresses rate-limit phrasing so a throttle is not mislabelled a billing
   outage; that function is the pattern these tests hold the registry to.

A fourth guard is structural rather than behavioural: no name may be claimed by
two codes at all, so the next duplicate fails the build instead of producing a
code that silently can never fire.
"""

from __future__ import annotations

import errno
import sqlite3
from collections import defaultdict

import pytest
from langgraph.errors import GraphRecursionError

from alpha.errors.registry import (
    ERROR_CODES,
    ErrorSeverity,
    RecoveryAction,
    classify,
    get_definition,
    require_definition,
)

# The real message LangGraph raises, reproduced verbatim so the message-hint
# path (not just the type path) is what these tests exercise. Taken from
# ``GraphRecursionError``'s observed output:
# "Recursion limit of 25 reached without getting a final response."
RECURSION_MESSAGE = "Recursion limit of 25 reached without getting a final response."


class TestRecursionLimitIsNotABudget:
    """The confirmed defect, pinned from both directions."""

    def test_a_recursion_limit_does_not_resolve_to_the_budget_code(self):
        """The headline assertion: the false claim is gone.

        Before the split this resolved to ``RUN_QUOTA_EXCEEDED`` and rendered
        "A run or token budget for this thread is exhausted" for a run that had
        consumed no budget at all.
        """
        assert classify(GraphRecursionError(RECURSION_MESSAGE)).code == "RUN_RECURSION_LIMIT"
        assert classify(GraphRecursionError(RECURSION_MESSAGE)).code != "RUN_QUOTA_EXCEEDED"

    def test_the_type_claim_fires_not_just_the_message_hint(self):
        """Classification must survive a message LangGraph might reword.

        The old entry claimed the class name ``RecursionLimit``, which exists in
        no dependency at all, so the *type* path never fired and only the
        ``"recursion limit"`` substring caught it. Claiming the class LangGraph
        actually raises pins the behaviour to the type rather than to prose.
        """
        neutral = GraphRecursionError("a message with none of the registry's keywords")
        assert classify(neutral).code == "RUN_RECURSION_LIMIT", "the type claim alone must resolve it"

    def test_the_new_code_remediates_graph_depth_not_tokens(self):
        """The whole point of the split: the text must be actionable and true."""
        definition = require_definition("RUN_RECURSION_LIMIT")
        lowered = definition.message.lower()

        assert "recursion" in lowered, "the message must name the graph-depth condition"
        assert "graph" in lowered
        # The remediation the operator actually performs.
        assert "recursion_limit" in lowered or "recursion limit" in lowered
        assert "chain" in lowered, "shortening the agent chain is half the fix"
        # And it must not assert a budget that was never consumed.
        assert "token budget" not in lowered
        assert "quota" not in lowered
        assert "exhausted" not in lowered

    def test_the_budget_code_does_not_claim_a_graph_condition_any_more(self):
        """Neither half of the split may reach back into the other.

        ``exception_types`` and ``message_hints`` are the two independent paths
        into a code, so leaving the old hint behind would keep re-conflating
        them the next time an unrelated message contains the phrase.
        """
        budget = require_definition("RUN_QUOTA_EXCEEDED")
        assert "RecursionLimit" not in budget.exception_types
        assert "GraphRecursionError" not in budget.exception_types
        assert "recursion limit" not in budget.message_hints
        assert "recursion" not in budget.message.lower()

    def test_the_split_did_not_mutate_the_persisted_budget_contract(self):
        """These codes are written into run records, so the old one must not drift.

        A stored ``RUN_QUOTA_EXCEEDED`` has to keep reading the same way after
        the split: a historical record is not migrated, it is left alone.
        """
        budget = require_definition("RUN_QUOTA_EXCEEDED")
        assert budget.severity is ErrorSeverity.WARNING
        assert budget.retryable is False
        assert budget.recovery is RecoveryAction.NONE
        assert budget.http_status == 429
        assert budget.message == "A run or token budget for this thread is exhausted."

    def test_the_new_code_is_not_graded_as_a_transient_rate_limit(self):
        """A 429 invites a client to back off; nothing here is rate-limited.

        The old code answered 429 because it was shared with the budget claim.
        The graph ceiling is a server-side configuration fact, so it is a 500,
        and it is not retryable: the same graph against the same limit hits the
        same wall.
        """
        definition = require_definition("RUN_RECURSION_LIMIT")
        assert definition.http_status == 500
        assert definition.retryable is False, "an identical retry hits the identical wall"
        assert definition.recovery is RecoveryAction.INVESTIGATE, "an operator must shorten the chain"
        assert definition.severity is ErrorSeverity.ERROR, "the run produced no answer at all"

    def test_a_real_token_budget_still_reaches_the_budget_code(self):
        """The split must not empty the code it was carved out of.

        ``TokenBudgetExceeded`` was removed from the type claim because no such
        class exists -- the token budget is enforced by
        ``TokenBudgetMiddleware``, which sets a stop reason rather than raising.
        The explicit path is what remains, so it is what is tested.
        """
        from alpha.errors import CodedError

        assert classify(CodedError("RUN_QUOTA_EXCEEDED")).code == "RUN_QUOTA_EXCEEDED"
        assert classify(Exception("token budget exceeded")).code == "RUN_QUOTA_EXCEEDED"


class TestPerCycleBudgetIsNotAThreadTokenBudget:
    """``alpha.rsi.budgets.BudgetExceeded`` is a different system entirely."""

    def test_a_per_cycle_resource_budget_is_not_reported_as_a_token_budget(self):
        from alpha.rsi.budgets import BudgetExceeded

        # The real message is f"{kind} {actual} exceeds {limit}" for one of
        # wall_time_s / candidate / changed_file / diff_line / workspace_mb.
        # None of those is a token count and none is scoped to a thread.
        for kind, actual, limit in (("wall_time_s", 30.1, 30.0), ("changed_file", 12.0, 10.0), ("workspace_mb", 512.0, 500.0)):
            definition = classify(BudgetExceeded(kind, actual, limit))
            assert definition.code == "RSI_BUDGET_EXCEEDED", f"{kind} was misfiled as {definition.code}"
            assert "token" not in definition.message.lower()
            assert "thread" not in definition.message.lower()

    def test_the_budget_codes_no_longer_overlap(self):
        assert "BudgetExceeded" not in require_definition("RUN_QUOTA_EXCEEDED").exception_types
        assert "BudgetExceeded" in require_definition("RSI_BUDGET_EXCEEDED").exception_types

    def test_the_per_cycle_code_gets_its_own_correlation_family(self):
        """It is a different subsystem, so it must not hide under alpha.errors.run."""
        assert require_definition("RSI_BUDGET_EXCEEDED").correlation_id == "alpha.errors.rsi"


class TestOneExceptionTypeOneCode:
    """Defect class 2: a name claimed twice makes the loser unreachable.

    ``classify`` ranks candidates by MRO position and then prefers the higher
    severity, so a duplicate is not a harmless redundancy -- it silently decides
    the outcome, and the code an operator would want is the one that can never
    fire.
    """

    def test_no_exception_type_name_is_claimed_by_two_codes(self):
        owners: dict[str, list[str]] = defaultdict(list)
        for code, definition in ERROR_CODES.items():
            for name in definition.exception_types:
                owners[name].append(code)

        duplicates = {name: codes for name, codes in owners.items() if len(codes) > 1}
        assert not duplicates, f"these exception types are claimed by more than one code, so the loser can never fire: {duplicates}"

    def test_no_provider_status_is_claimed_by_two_codes(self):
        """Same failure through the status path: first definition wins outright."""
        owners: dict[int, list[str]] = defaultdict(list)
        for code, definition in ERROR_CODES.items():
            for status in definition.status_codes:
                owners[status].append(code)

        duplicates = {status: codes for status, codes in owners.items() if len(codes) > 1}
        assert not duplicates, f"these provider statuses are claimed by more than one code; the first one silently wins: {duplicates}"

    def test_a_corrupt_store_is_not_reported_as_a_retryable_checkpoint_write(self):
        """The most damaging instance: a permanent fault given a restart loop.

        ``alpha.goals.store`` raises this for "Invalid goal store snapshot".
        ``CHECKPOINT_WRITE_FAILED`` grades CRITICAL and ``PERSISTENCE_CORRUPT``
        also grades CRITICAL, so the earlier definition won the tie -- and the
        corruption code was unreachable for its own type. A supervisor was told
        to bounce the process over data that would still be corrupt afterwards.
        """
        from alpha.goals.store import StoreCorruptionError

        definition = classify(StoreCorruptionError("Invalid goal store snapshot"))
        assert definition.code == "PERSISTENCE_CORRUPT"
        assert definition.retryable is False, "corruption does not fix itself on a retry"
        assert definition.recovery is RecoveryAction.INVESTIGATE
        assert "corrupt" in definition.message.lower()

    def test_a_throttle_is_not_reported_as_a_billing_outage(self):
        """A 429 is the transient/transient inversion, in the other direction.

        ``MODEL_PROVIDER_QUOTA`` and ``MODEL_PROVIDER_RATE_LIMITED`` both claimed
        429, and the billing code is defined first, so every unrecognised 429 was
        published as an exhausted account with ``retryable=False`` -- sending a
        supervisor to billing for a throttle that clears in seconds.
        """

        class Throttled(Exception):
            status_code = 429

        definition = classify(Throttled())
        assert definition.code == "MODEL_PROVIDER_RATE_LIMITED"
        assert definition.retryable is True
        assert definition.recovery is RecoveryAction.RETRY

    def test_payment_required_is_still_a_billing_failure(self):
        """402 is unambiguous; the split must not have cost us the real signal."""

        class PaymentRequired(Exception):
            status_code = 402

        definition = classify(PaymentRequired())
        assert definition.code == "MODEL_PROVIDER_QUOTA"
        assert definition.retryable is False, "a retry cannot refill an account"

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("You exceeded your current quota, please check your plan and billing details.", "MODEL_PROVIDER_QUOTA"),
            ("Your account balance is not enough to complete the request.", "MODEL_PROVIDER_QUOTA"),
            ("Rate limit reached for RequestsPerMinute, please slow down.", "MODEL_PROVIDER_RATE_LIMITED"),
            ("You exceeded your quota metric for requests per minute.", "MODEL_PROVIDER_RATE_LIMITED"),
        ],
    )
    def test_billing_and_throttle_phrasing_are_told_apart(self, message: str, expected: str) -> None:
        """The ``models/fallback.py`` pattern, applied to the registry.

        ``is_credit_exhausted_error`` already suppresses rate-limit phrasing so a
        throttle is never mislabelled a billing outage. The bare words "quota"
        and "exhausted" previously did the opposite here, claiming "Disk quota
        exceeded on /var" and "connection pool exhausted" as provider billing
        failures.
        """
        assert classify(Exception(message)).code == expected

    def test_a_local_resource_exhaustion_is_not_a_provider_billing_failure(self):
        assert classify(Exception("Disk quota exceeded on /var")).code != "MODEL_PROVIDER_QUOTA"
        assert classify(Exception("connection pool exhausted")).code != "MODEL_PROVIDER_QUOTA"

    def test_a_rate_limit_error_reaches_the_rate_limit_code(self):
        """``RateLimitError`` was also claimed by the outage code, which won on severity.

        The effect was a 429 published as 503 "could not be reached" and a
        rate-limit code that could never fire for its own exception type.
        """
        openai = pytest.importorskip("openai")
        exc = openai.RateLimitError("slow down", response=_response(429), body=None)
        assert classify(exc).code == "MODEL_PROVIDER_RATE_LIMITED"

    def test_a_provider_timeout_is_still_a_provider_outage(self):
        """The other half of that pair, pinned so the dedup did not move it.

        ``APITimeoutError`` was claimed by both ``TIMEOUT`` and
        ``MODEL_PROVIDER_UNAVAILABLE``; the provider code grades higher severity
        and so always won. The dead claim was dropped from ``TIMEOUT`` rather
        than from the provider, because a provider that did not answer in time
        genuinely was not reached.
        """
        openai = pytest.importorskip("openai")
        exc = openai.APITimeoutError(request=_request())
        assert classify(exc).code == "MODEL_PROVIDER_UNAVAILABLE"

    def test_a_provider_credential_failure_is_not_a_local_sign_in_prompt(self):
        """``AuthenticationError``/``PermissionDeniedError`` were claimed twice.

        ``MODEL_PROVIDER_AUTH`` grades CRITICAL with ``recovery=RESTART``, so it
        always won, and a local sign-in failure would have been published as
        "The model provider rejected the configured credentials" with an
        instruction to restart the process.
        """
        openai = pytest.importorskip("openai")
        assert classify(openai.AuthenticationError("bad key", response=_response(401), body=None)).code == "MODEL_PROVIDER_AUTH"
        assert classify(openai.PermissionDeniedError("no", response=_response(403), body=None)).code == "MODEL_PROVIDER_AUTH"

    def test_a_socket_write_failure_is_not_a_storage_outage(self):
        """``httpx.WriteError`` is a transport failure, not a disk failure.

        ``PERSISTENCE_WRITE_FAILED`` grades CRITICAL/CRITICAL-with-restart, so it
        won over ``STORAGE_WRITE_FAILED`` and told an operator to restart the
        process over a network blip.
        """
        httpx = pytest.importorskip("httpx")
        assert classify(httpx.WriteError("socket gone")).code == "STORAGE_WRITE_FAILED"


class TestFlagsComeFromTheCause:
    """Defect class 3: a policy flag read off the code instead of the cause."""

    @pytest.mark.parametrize("message", ["please retry", "please try again", "release the file lock", "unleased port", "PLEASE contact support"])
    def test_a_message_containing_lease_as_a_substring_is_not_a_lost_lease(self, message: str) -> None:
        """The registry's worst hint, and the reason this file exists twice over.

        ``RUN_OWNERSHIP_LOST`` matched the bare substring ``"lease"``, which is
        contained in ``please``, ``release`` and ``unleased``. Every one of those
        messages was published as "This run is no longer owned by this worker"
        with ``retryable=True`` and ``recovery=retry`` -- a deterministic or
        entirely unrelated failure handed a transient flag and a lease
        remediation.
        """
        assert classify(Exception(message)).code != "RUN_OWNERSHIP_LOST"

    def test_a_genuine_lease_loss_still_reaches_the_lease_code(self):
        """Narrowing the hint must not lose the case it was written for."""
        assert classify(Exception("reservation lease was lost for run r-1")).code == "RUN_OWNERSHIP_LOST"

    def test_a_message_containing_sso_as_a_substring_is_not_a_sign_in_failure(self):
        """The same hijack a second time, found by the guard above.

        ``AUTH_SSO_FAILED`` matched the bare token ``sso``, which is a substring
        of "assorted" and "asserts", so "assorted results returned" was
        published as "Single sign-on could not be completed" with a retry.
        """
        for message in ("assorted results returned", "the assorted tools were used", "it asserts the schema is valid"):
            assert classify(Exception(message)).code != "AUTH_SSO_FAILED", f"{message!r} was misfiled as a sign-on failure"

    def test_a_genuine_sso_failure_still_reaches_the_sso_code(self):
        assert classify(Exception("sso callback failed: state mismatch")).code == "AUTH_SSO_FAILED"
        assert classify(Exception("oidc discovery document is unreachable")).code == "AUTH_SSO_FAILED"

    def test_a_lease_loss_raised_as_its_own_class_still_reaches_the_lease_code(self):
        class LeaseLost(Exception):
            pass

        assert classify(LeaseLost("gone")).code == "RUN_OWNERSHIP_LOST"

    def test_no_single_word_hint_is_a_substring_of_an_unrelated_english_word(self):
        """The structural guard behind the fix above.

        Hints are matched as raw substrings of ``str(exc)``, so a one-word hint
        that happens to sit inside a *different* word silently claims every
        message containing that word. ``lease`` inside ``please`` is the case
        that shipped; this asserts the property over the whole table so the next
        one fails the build.

        Inflections of the hint itself are excluded, because they carry the same
        meaning and claiming them is correct: ``timeout`` inside ``timeouts`` is
        a timeout, not a hijack. The list is therefore words whose *meaning* is
        unrelated to the hint, which is exactly the hijack condition.
        """
        # Real English words only. An invented neologism would make the guard
        # pass vacuously, so every entry here is a word a message could contain
        # for reasons that have nothing to do with the code claiming it.
        unrelated = {
            # "lease" hides in all of these.
            "please",
            "release",
            "unleased",
            "prelude",
            "pleased",
            "releasing",
            # "sso" hides in these.
            "assorted",
            "asserts",
            "assessor",
        }
        for code, definition in ERROR_CODES.items():
            for hint in definition.message_hints:
                if " " in hint:
                    continue  # a phrase cannot hide inside a single word
                for word in unrelated:
                    if hint == word or hint in {word.rstrip("s"), word.removesuffix("ing")}:
                        continue  # an inflection of the hint: same meaning, not a hijack
                    assert hint not in word, f"{code} hint {hint!r} is a substring of the unrelated word {word!r}, so it hijacks every message containing it"

    def test_a_permission_denied_read_is_not_offered_a_retry(self):
        """``PermissionError`` was filed under a retryable read-failure code.

        Neither the process nor a retry changes the mode bits, so the old entry
        handed a supervisor a hot loop over a permanent condition. The builtin
        is no longer claimed by ``STORAGE_READ_FAILED``; a genuinely transient
        transport read still is.
        """
        definition = classify(PermissionError(errno.EACCES, "permission denied"))
        assert definition.code != "STORAGE_READ_FAILED"
        assert definition.retryable is not True or definition.recovery is not RecoveryAction.RETRY

    def test_a_transient_read_failure_keeps_its_retry_policy(self):
        httpx = pytest.importorskip("httpx")
        definition = classify(httpx.ReadError("connection reset"))
        assert definition.code == "STORAGE_READ_FAILED"
        assert definition.retryable is True

    def test_a_directory_read_is_not_offered_a_retry(self):
        assert classify(IsADirectoryError(errno.EISDIR, "Is a directory")).code != "STORAGE_READ_FAILED"

    def test_every_retryable_code_still_offers_a_recovery_action(self):
        """The registry's own invariant, re-asserted against the edited table.

        A retryable code that tells nobody what to do is a hot loop, so this
        holds for the two new codes as well as the surviving ones.
        """
        for code, definition in ERROR_CODES.items():
            if definition.retryable:
                assert definition.recovery is not RecoveryAction.NONE, f"{code} is retryable but offers no recovery"


class TestNewCodesAreFirstClass:
    """A new code has to work through every surface, not just ``classify``."""

    @pytest.mark.parametrize("code", ["RUN_RECURSION_LIMIT", "RSI_BUDGET_EXCEEDED"])
    def test_the_new_code_is_registered_and_complete(self, code: str) -> None:
        definition = get_definition(code)
        assert definition is not None, f"{code} is not registered"
        assert definition.message.strip()
        assert definition.correlation_id.startswith("alpha.errors.")
        assert 100 <= definition.http_status < 600
        assert definition.to_metadata()["error_code"] == code

    @pytest.mark.parametrize("code", ["RUN_RECURSION_LIMIT", "RSI_BUDGET_EXCEEDED"])
    def test_the_new_code_survives_the_trace_alias_resolution(self, code: str) -> None:
        """``resolve_error_code`` raises on anything unregistered.

        A persisted ``error_code`` read back through the trace contract goes
        through it, so an unregistered new code would make historical rows
        unreadable rather than merely mislabelled.
        """
        from alpha.observability.trace.codes import resolve_error_code

        assert resolve_error_code(code) == code

    def test_the_registry_still_contains_no_orphaned_legacy_value(self):
        """The closure check the trace contract relies on."""
        from alpha.observability.trace.codes import unresolved_error_codes

        assert unresolved_error_codes() == ()

    def test_the_observability_taxonomy_still_sees_the_whole_registry(self):
        """The trace layer caches the code set; a split must not leave it stale."""
        from alpha.observability.taxonomy import registered_error_codes

        codes = registered_error_codes()
        assert codes is not None, "the taxonomy could not load the registry"
        assert set(codes) == set(ERROR_CODES)

    def test_a_classification_still_resolves_for_every_registry_code(self):
        """``classify`` is total by contract; the split must not open a hole."""
        for code in ERROR_CODES:
            assert get_definition(classify(Exception("unmatched")).code) is not None
            assert require_definition(code).code == code

    def test_the_new_codes_do_not_leak_severity_from_the_code_they_split_out_of(self):
        """A split must not launder a grade.

        The recursion code was carved out of a WARNING because that is all the
        shared code could honestly be, but the run it describes produced no
        answer at all, so WARNING would have understated a run that failed
        outright. It is ERROR; the per-cycle refusal is WARNING, matching the
        deliberate abort it describes.
        """
        assert require_definition("RUN_RECURSION_LIMIT").severity is ErrorSeverity.ERROR
        assert require_definition("RSI_BUDGET_EXCEEDED").severity is ErrorSeverity.WARNING

    def test_classification_is_still_total_for_unrelated_exceptions(self):
        """A split must not make the generic fallback unreachable."""
        assert classify(sqlite3.OperationalError("database is locked")).code == "PERSISTENCE_UNAVAILABLE"
        assert classify(ValueError("x")).code == "INVALID_INPUT"
        assert classify(Exception("completely unrelated text")).code == "INTERNAL_ERROR"


def _request():
    import httpx

    return httpx.Request("POST", "http://provider.invalid/v1/chat/completions")


def _response(status: int):
    import httpx

    return httpx.Response(status, request=_request())
