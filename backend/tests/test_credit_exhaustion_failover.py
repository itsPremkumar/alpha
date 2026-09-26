"""Credit exhaustion drives failover, and cost accounting is actually wired.

Two behaviours that used to be dead paths:

* **Credit exhaustion** had no error type at the provider boundary, so an
  out-of-credit provider surfaced as a generic failure instead of failing over
  to a provider that still has money. It is now a typed, retryable condition
  (:class:`alpha.models.fallback.CreditExhaustedError`) that moves the chain
  forward and is reported structurally.
* **Cost accounting** was a library nobody called. The real request path -
  :class:`alpha.models.fallback.FallbackChatModel` serving a call - now charges
  the provider-reported token usage to
  :meth:`alpha.models.cost_governor.CostGovernor.record_usage`, attributed to
  the member that actually served the call.

Offline: scripted chat models stand in for provider clients and the errors are
synthetic objects shaped like the provider SDKs' (duck-typed ``status_code``,
OpenAI-style ``body.error.code``), so nothing here needs a network, a key, or a
real budget.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import pytest
from langchain.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from alpha.models import cost_governor as cost_governor_module
from alpha.models.cost_governor import (
    CostGovernor,
    get_cost_governor,
    record_token_usage,
    reset_cost_governor,
    usage_attribution,
)
from alpha.models.fallback import (
    CREDIT_EXHAUSTED_STATUS,
    REASON_BUDGET_EXHAUSTED,
    REASON_CREDIT_EXHAUSTED,
    REASON_EMPTY_STREAM,
    REASON_RATE_LIMITED,
    CreditExhaustedError,
    FallbackChatModel,
    ModelFallbackExhaustedError,
    is_credit_exhausted_error,
    is_retryable_llm_error,
    normalize_provider_error,
)

# ---------------------------------------------------------------------------
# Provider error shapes (no SDK import needed)
# ---------------------------------------------------------------------------


class ProviderStatusError(Exception):
    """Duck-typed provider HTTP error carrying a status and a message."""

    def __init__(self, status: int, message: str = "upstream failure") -> None:
        super().__init__(message)
        self.status_code = status


class ProviderQuotaError(Exception):
    """OpenAI-shaped error: an error code and a nested body."""

    def __init__(self, status: int, code: str, message: str = "provider rejected the request") -> None:
        super().__init__(message)
        self.status_code = status
        self.body = {"error": {"code": code, "type": code, "message": message}}


# ---------------------------------------------------------------------------
# Scripted chat models
# ---------------------------------------------------------------------------


class ScriptedModel(BaseChatModel):
    """Replays a class-level script of exceptions / answers with token usage."""

    _SCRIPT: ClassVar[list[Any]] = []
    calls: int = 0
    usage: tuple[int, int] | None = (11, 7)

    @property
    def _llm_type(self) -> str:
        return "scripted-usage-model"

    def _answer(self, text: str) -> ChatResult:
        message = AIMessage(content=text, usage_metadata=(None if self.usage is None else {"input_tokens": self.usage[0], "output_tokens": self.usage[1], "total_tokens": sum(self.usage)}))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _next(self) -> Any:
        script = type(self)._SCRIPT
        action = script[min(self.calls, len(script) - 1)]
        self.calls += 1
        return action

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        action = self._next()
        if isinstance(action, BaseException):
            raise action
        return self._answer(str(action))

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        action = self._next()
        if isinstance(action, BaseException):
            raise action
        usage = None if self.usage is None else {"input_tokens": self.usage[0], "output_tokens": self.usage[1], "total_tokens": sum(self.usage)}
        yield ChatGenerationChunk(message=AIMessageChunk(content=str(action)))
        # Providers send usage on the trailing chunk when asked for it.
        yield ChatGenerationChunk(message=AIMessageChunk(content="", usage_metadata=usage))


def _member(script: list[Any], **fields: Any) -> ScriptedModel:
    return type("ScriptedMember", (ScriptedModel,), {"_SCRIPT": list(script), "__module__": __name__})(**fields)


def _messages() -> list[BaseMessage]:
    return [HumanMessage(content="hello")]


@pytest.fixture(autouse=True)
def _isolated_governor(tmp_path, monkeypatch):
    """Point the cost governor at a private ledger and a private runtime home."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path / "runtime-home"))
    monkeypatch.delenv(cost_governor_module.PROJECT_ID_ENV, raising=False)
    reset_cost_governor()
    yield
    reset_cost_governor()


@pytest.fixture()
def usage_spy(monkeypatch) -> list[dict[str, Any]]:
    """Spy on the real ``CostGovernor.record_usage`` and still charge for real."""
    calls: list[dict[str, Any]] = []
    original = CostGovernor.record_usage

    def spy(self, project_id, bot_name, model_name, input_tokens, output_tokens, **kwargs):
        calls.append(
            {
                "project_id": project_id,
                "bot_name": bot_name,
                "model_name": model_name,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "kwargs": kwargs,
            }
        )
        return original(self, project_id, bot_name, model_name, input_tokens, output_tokens, **kwargs)

    monkeypatch.setattr(CostGovernor, "record_usage", spy)
    return calls


# ---------------------------------------------------------------------------
# Credit-exhaustion classification
# ---------------------------------------------------------------------------


def test_credit_exhausted_error_is_typed_at_the_provider_boundary():
    raw = ProviderQuotaError(429, "insufficient_quota", "You exceeded your current quota, please check your plan and billing details")
    typed = normalize_provider_error(raw)
    assert isinstance(typed, CreditExhaustedError)
    assert typed.original is raw
    assert typed.status == 429
    assert typed.model_name == "ProviderQuotaError"
    # The typed error never carries the provider's message (it can echo a key).
    assert "exceeded your current quota" not in str(typed)
    assert normalize_provider_error(typed) is typed  # already typed: unchanged


@pytest.mark.parametrize(
    "error",
    [
        ProviderStatusError(402, "Payment Required"),
        ProviderQuotaError(429, "insufficient_quota"),
        ProviderQuotaError(400, "billing_hard_limit_reached"),
        ProviderStatusError(400, "Your credit balance is too low to access the API"),
        ProviderStatusError(403, "out of credits"),
        ProviderStatusError(400, "余额不足，请充值"),
    ],
)
def test_credit_exhaustion_is_detected_and_retryable(error):
    assert is_credit_exhausted_error(error) is True
    assert is_retryable_llm_error(error) is True


@pytest.mark.parametrize(
    "error",
    [
        # A throttle is not a billing outage: same HTTP status, different problem.
        ProviderStatusError(429, "Rate limit reached for quota metric: RequestsPerMinute"),
        ProviderStatusError(429, "Too Many Requests: requests per minute limit exceeded"),
        ProviderStatusError(401, "Invalid API key provided"),
        ProviderStatusError(404, "model not found"),
        ValueError("bad request shape"),
    ],
)
def test_throttles_and_deterministic_failures_are_not_credit_exhaustion(error):
    assert is_credit_exhausted_error(error) is False


def test_throttle_still_fails_over_just_not_as_credit_exhaustion():
    wrapper = FallbackChatModel(
        instances=[_member([ProviderStatusError(429, "Rate limit reached")]), _member(["backup-answer"])],
        model_names=["primary", "backup"],
    )
    assert wrapper.invoke(_messages()).content == "backup-answer"
    events = wrapper.get_last_failover_events()
    assert [(e.from_model, e.reason) for e in events] == [("primary", REASON_RATE_LIMITED)]
    assert wrapper.get_last_credit_exhausted_models() == []


# ---------------------------------------------------------------------------
# Credit exhaustion drives the fallback chain
# ---------------------------------------------------------------------------


def test_credit_exhausted_response_fails_over_instead_of_surfacing():
    backup = _member(["served-by-paid-provider"])
    wrapper = FallbackChatModel(
        instances=[_member([ProviderQuotaError(429, "insufficient_quota", "quota exhausted")]), backup],
        model_names=["out-of-credit", "paid-provider"],
    )

    answer = wrapper.invoke(_messages())

    assert answer.content == "served-by-paid-provider"
    assert wrapper.get_last_effective_model() == "paid-provider"
    assert backup.calls == 1
    assert wrapper.get_last_credit_exhausted_models() == ["out-of-credit"]
    events = wrapper.get_last_failover_events()
    assert len(events) == 1
    assert events[0].reason == REASON_CREDIT_EXHAUSTED
    assert events[0].credit_exhausted is True
    assert events[0].from_model == "out-of-credit"
    assert events[0].to_model == "paid-provider"
    # Structural, not just a log line: the error label carries no provider prose.
    assert "exhausted" not in events[0].error_label.lower()


def test_payment_required_402_fails_over():
    wrapper = FallbackChatModel(
        instances=[_member([ProviderStatusError(402, "Payment Required")]), _member(["backup"])],
        model_names=["card-declined", "backup"],
    )
    assert wrapper.invoke(_messages()).content == "backup"
    assert wrapper.get_last_credit_exhausted_models() == ["card-declined"]


def test_credit_exhaustion_fails_over_while_streaming():
    wrapper = FallbackChatModel(
        instances=[_member([ProviderQuotaError(400, "insufficient_quota")]), _member(["streamed-backup"])],
        model_names=["out-of-credit", "backup"],
    )
    chunks = list(wrapper.stream(_messages()))
    assert "".join(chunk.content for chunk in chunks) == "streamed-backup"
    assert wrapper.get_last_credit_exhausted_models() == ["out-of-credit"]


def test_chain_out_of_credit_reports_the_budget_status():
    wrapper = FallbackChatModel(
        instances=[
            _member([ProviderQuotaError(429, "insufficient_quota")]),
            _member([ProviderStatusError(402, "Payment Required")]),
        ],
        model_names=["primary-exhausted", "backup-exhausted"],
    )
    with pytest.raises(ModelFallbackExhaustedError) as exc_info:
        wrapper.invoke(_messages())
    error = exc_info.value
    assert error.credit_exhausted is True
    assert error.credit_exhausted_models == ["primary-exhausted", "backup-exhausted"]
    assert error.budget_status == CREDIT_EXHAUSTED_STATUS
    assert CREDIT_EXHAUSTED_STATUS in str(error)


def test_chain_exhausted_for_other_reasons_has_no_budget_status():
    wrapper = FallbackChatModel(
        instances=[_member([ProviderStatusError(503)]), _member([ProviderStatusError(500)])],
        model_names=["primary", "backup"],
    )
    with pytest.raises(ModelFallbackExhaustedError) as exc_info:
        wrapper.invoke(_messages())
    assert exc_info.value.credit_exhausted is False
    assert exc_info.value.budget_status is None


# ---------------------------------------------------------------------------
# Provider switches are observable, not only logged
# ---------------------------------------------------------------------------


def test_failover_callback_receives_events_and_never_breaks_a_call():
    seen: list[Any] = []
    wrapper = FallbackChatModel(
        instances=[_member([ProviderStatusError(500)]), _member(["answered"])],
        model_names=["primary", "backup"],
        on_failover=seen.append,
    )
    assert wrapper.invoke(_messages()).content == "answered"
    assert [(e.from_model, e.to_model) for e in seen] == [("primary", "backup")]

    def _explode(_event: Any) -> None:
        raise RuntimeError("observability backend is down")

    brittle = FallbackChatModel(
        instances=[_member([ProviderStatusError(500)]), _member(["still-answered"])],
        model_names=["primary", "backup"],
        on_failover=_explode,
    )
    assert brittle.invoke(_messages()).content == "still-answered"


class SilentStreamModel(BaseChatModel):
    """A member whose stream yields nothing (a provider that answers with silence)."""

    @property
    def _llm_type(self) -> str:
        return "silent-stream-model"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="never-reached"))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        return
        yield  # pragma: no cover - makes this an (empty) generator


def test_empty_stream_is_recorded_with_its_own_reason():
    """A member that answers with nothing must move the chain forward."""
    wrapper = FallbackChatModel(instances=[SilentStreamModel(), _member(["after-empty"])], model_names=["silent", "backup"])
    chunks = list(wrapper.stream(_messages()))
    assert "".join(chunk.content for chunk in chunks) == "after-empty"
    assert [e.reason for e in wrapper.get_last_failover_events()] == [REASON_EMPTY_STREAM]
    assert wrapper.get_last_credit_exhausted_models() == []


def test_failover_history_is_bounded():
    wrapper = FallbackChatModel(instances=[_member([ProviderStatusError(500)]), _member(["ok"])], model_names=["primary", "backup"])
    for _ in range(wrapper._EVENT_HISTORY + 7):
        wrapper.invoke(_messages())
    assert len(wrapper.get_last_failover_events()) == wrapper._EVENT_HISTORY


# ---------------------------------------------------------------------------
# record_usage is called on a real request path
# ---------------------------------------------------------------------------


def test_record_usage_is_called_when_a_chain_member_serves(usage_spy, tmp_path):
    wrapper = FallbackChatModel(
        instances=[_member(["primary-answer"]), _member(["backup-answer"])],
        model_names=["primary-model", "backup-model"],
    )

    answer = wrapper.invoke(_messages())

    assert answer.content == "primary-answer"
    assert len(usage_spy) == 1
    call = usage_spy[0]
    assert call["model_name"] == "primary-model"
    assert (call["input_tokens"], call["output_tokens"]) == (11, 7)
    # Real effect, not just a call: the charge landed in the durable ledger.
    ledger = json.loads((tmp_path / "runtime-home" / "governance" / "costs.json").read_text(encoding="utf-8"))
    assert ledger["records"][-1]["model_name"] == "primary-model"
    assert ledger["records"][-1]["input_tokens"] == 11
    assert ledger["records"][-1]["cost_usd"] > 0


def test_spend_is_attributed_to_the_member_that_actually_served(usage_spy):
    """Per-effective-model accounting: a failover must not bill the primary."""
    wrapper = FallbackChatModel(
        instances=[_member([ProviderStatusError(500)]), _member(["backup-answer"])],
        model_names=["expensive-primary", "cheap-backup"],
    )

    with usage_attribution("proj-7", "bot-9"):
        assert wrapper.invoke(_messages()).content == "backup-answer"

    assert [c["model_name"] for c in usage_spy] == ["cheap-backup"]
    assert usage_spy[0]["project_id"] == "proj-7"
    assert usage_spy[0]["bot_name"] == "bot-9"


def test_record_usage_is_called_for_a_streamed_call(usage_spy):
    wrapper = FallbackChatModel(instances=[_member(["streamed"])], model_names=["streamer"])

    chunks = list(wrapper.stream(_messages()))

    assert "".join(c.content for c in chunks) == "streamed"
    assert len(usage_spy) == 1
    assert (usage_spy[0]["input_tokens"], usage_spy[0]["output_tokens"]) == (11, 7)


def test_failed_members_are_not_charged(usage_spy):
    wrapper = FallbackChatModel(
        instances=[_member([ProviderStatusError(500)]), _member(["backup"])],
        model_names=["failing", "serving"],
    )
    assert wrapper.invoke(_messages()).content == "backup"
    assert [c["model_name"] for c in usage_spy] == ["serving"]


def test_no_charge_when_the_provider_reports_no_usage(usage_spy):
    member = _member(["no-usage-answer"], usage=None)
    wrapper = FallbackChatModel(instances=[member], model_names=["no-usage"])
    assert wrapper.invoke(_messages()).content == "no-usage-answer"
    assert usage_spy == []


def test_accounting_failure_never_breaks_a_served_answer(monkeypatch):
    def _boom(*_args: Any, **_kwargs: Any):
        raise RuntimeError("ledger is unavailable")

    monkeypatch.setattr(cost_governor_module, "record_token_usage", _boom)
    wrapper = FallbackChatModel(instances=[_member(["answer-survives"])], model_names=["primary"])
    assert wrapper.invoke(_messages()).content == "answer-survives"


def test_tripped_budget_breaker_is_recorded_and_the_answer_still_ships(usage_spy, tmp_path):
    """A tripped breaker stops *further* work; it never erases or swallows spend.

    The breaker hierarchy is scope-aware by design (a scope-less charge is
    deliberately not blocked — see ``test_cost_governor_project_breaker_blocks_
    scopes_but_not_legacy_path``), so the test binds a scope whose limit the
    first charge crosses.
    """
    from alpha.config.token_budget_config import budget_scope

    governor = get_cost_governor()
    # The first charge (11 in / 7 out on the default rate table) must cross the
    # limit so the breaker trips and the *second* charge is the blocked one.
    governor.set_scope_budget("workflow:refactor", limit_usd=0.00001)
    wrapper = FallbackChatModel(instances=[_member(["first", "second"])], model_names=["metered"])

    with usage_attribution("proj-budget", "bot-x"):
        with budget_scope("workflow:refactor"):
            first = wrapper.invoke(_messages())  # charge recorded, breaker now tripped
            events_after_first = list(wrapper.get_last_failover_events())
            second = wrapper.invoke(_messages())  # charge recorded, breaker blocks further work

    assert first.content == "first"
    assert second.content == "second"  # the answer is never swallowed by the budget
    assert events_after_first == []
    events = wrapper.get_last_failover_events()
    assert len(events) == 1
    assert events[0].reason == REASON_BUDGET_EXHAUSTED
    assert events[0].budget_breaker == "workflow:refactor"
    assert len(usage_spy) == 2  # both real charges are on the ledger
    ledger = json.loads((tmp_path / "runtime-home" / "governance" / "costs.json").read_text(encoding="utf-8"))
    assert len(ledger["records"]) == 2
    assert ledger["scopes"]["workflow:refactor"]["tripped"] is True


def test_governor_scope_budget_is_charged_on_the_request_path(usage_spy, tmp_path):
    from alpha.config.token_budget_config import budget_scope

    governor = get_cost_governor()
    governor.set_scope_budget("workflow:refactor", limit_usd=10.0)
    wrapper = FallbackChatModel(instances=[_member(["scoped"])], model_names=["scoped-model"])

    with budget_scope("workflow:refactor"):
        assert wrapper.invoke(_messages()).content == "scoped"

    # record_usage resolves the ambient scope itself, so the charge reaches the
    # scope hierarchy (and its ancestors) without the caller passing scope_id.
    state = governor.get_scope_state("workflow:refactor")
    assert state is not None and state.spent_usd > 0
    assert state.tripped is False
    ledger = json.loads((tmp_path / "runtime-home" / "governance" / "costs.json").read_text(encoding="utf-8"))
    assert ledger["scopes"]["workflow:refactor"]["spent_usd"] > 0
    assert usage_spy[0]["model_name"] == "scoped-model"


# ---------------------------------------------------------------------------
# record_token_usage contract
# ---------------------------------------------------------------------------


def test_record_token_usage_reports_missing_usage_instead_of_inventing_it():
    result = record_token_usage("some-model", 0, 0)
    assert result.recorded is False
    assert "no token usage" in result.reason


def test_record_token_usage_defaults_project_from_the_environment(monkeypatch):
    monkeypatch.setenv(cost_governor_module.PROJECT_ID_ENV, "proj-from-env")
    result = record_token_usage("gpt-4o", 1000, 1000)
    assert result.recorded is True
    assert result.project_id == "proj-from-env"
    assert result.cost_usd > 0


def test_record_token_usage_survives_a_broken_governor():
    class Exploding(CostGovernor):
        def record_usage(self, *args, **kwargs):
            raise RuntimeError("ledger is on fire")

    result = record_token_usage("gpt-4o", 10, 10, governor=Exploding(storage_path=None))
    assert result.recorded is False
    assert "accounting unavailable" in result.reason


def test_unknown_model_uses_the_default_rate_table():
    result = record_token_usage("some-unpriced-model", 10_000, 10_000)
    assert result.recorded is True
    # default: 0.001/1k in, 0.003/1k out
    assert result.cost_usd == pytest.approx(0.04)
