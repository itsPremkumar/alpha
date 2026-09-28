"""A keyless run's tokens must be attributable to the model that served them.

Found by assigning a real task to the agent on the keyless `alpha-free` model
and reading the persisted run row afterwards. The run worked, the run row
correctly recorded `model = 'alpha-free'`, and the tokens were counted — but
attributed to a model literally named `unknown`:

    model                = 'alpha-free'
    total_input_tokens   = 150832
    token_usage_by_model = {"unknown": {"input_tokens": 150832, ...}}

`RunJournal` reads the per-model bucket key from `response_metadata`'s
`model_name` (or `model`), which is the key every attribution consumer already
uses. The free router's chat model set only its own `free_llm_provider` /
`free_llm_model` pair, so nothing on the keyless path ever supplied a name the
journal could read.

That is the same unattributable spend as the missing run-row model name, reached
through a different door — and it matters precisely *because* the keyless path
now works, since it is the only path available to a user with no API key. With
no attribution there is no per-model cost, and a keyless user has no other way
to see what a run consumed.

The fix sets the standard keys from the router rather than teaching each consumer
a second vocabulary. Both are kept: `free_llm_*` names the *gateway* that served
the call, which is a different fact from the model id (one gateway serves many
models, and the router picks a different one per call), so an operator debugging
"why was this slow" needs the gateway, while cost accounting needs the model.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from alpha.runtime.journal import RunJournal


class _Result:
    """Stands in for `FreeChatResult` without a live provider."""

    def __init__(self, provider="vireonix", model_id="auto", text="hello", usage=None, latency_ms=12.3):
        self.provider = provider
        self.model_id = model_id
        self.text = text
        self.tool_calls = []
        self.invalid = []
        self.usage = usage or {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        self.latency_ms = latency_ms


def _message(result) -> AIMessage:
    """Build the message the *shipped* code builds.

    This calls the real ``ChatFreeLLM._result_to_message`` rather than restating
    its logic. The first version of this test re-implemented the metadata dict
    inline, so it kept asserting against a body the product had already moved
    past - it failed to catch the bare-colon case the same commit fixed. A test
    that can pass against code it does not run is worse than no test.
    """
    from alpha.models.free_router.chat_model import ChatFreeLLM

    return ChatFreeLLM._result_to_message(None, result)  # type: ignore[arg-type]


class TestTheJournalCanReadTheKeylessModel:
    def test_the_journal_reads_a_name_off_the_keyless_metadata(self) -> None:
        """The contract the journal actually implements, pinned with its input."""
        metadata = _message(_Result()).response_metadata
        assert metadata["model_name"] == "vireonix:auto", "the journal has no other key to read"

    def test_the_bucket_name_carries_both_the_gateway_and_the_model(self) -> None:
        """`vireonix:auto` is more useful than either half alone.

        The gateway is what an operator needs to attribute a slow or failing
        call; the model id is what cost accounting needs. One gateway serves many
        models and the router picks a different one per call, so collapsing them
        would lose a fact.
        """
        metadata = _message(_Result()).response_metadata
        assert metadata["model_provider"] == "vireonix"
        assert metadata["free_llm_provider"] == "vireonix"
        assert metadata["free_llm_model"] == "auto"

    def test_the_legacy_vocabulary_is_preserved(self) -> None:
        metadata = _message(_Result()).response_metadata
        assert metadata["free_llm_provider"] == "vireonix"
        assert metadata["free_llm_model"] == "auto"

    def test_a_missing_model_id_does_not_produce_a_bare_colon(self) -> None:
        metadata = _message(_Result(model_id="")).response_metadata
        assert metadata["model_name"] == "vireonix", "an empty model id must not read as 'vireonix:'"

    def test_a_missing_provider_does_not_produce_a_leading_colon(self) -> None:
        metadata = _message(_Result(provider="")).response_metadata
        assert metadata["model_name"] == "auto"
        assert metadata["model_provider"] == ""


class TestTokensReachTheRightBucket:
    def test_a_keyless_call_is_bucketed_under_its_model_not_unknown(self) -> None:
        """End of the chain: the value the journal persists is the model's name."""
        journal = RunJournal(run_id="r1", thread_id="t1", event_store=_store())
        message = _message(_Result())

        response_metadata = message.response_metadata
        per_call_model = response_metadata.get("model_name") or response_metadata.get("model")
        journal._record_model_usage(per_call_model, 10, 5, 15, 0)

        by_model = journal._tokens_by_model
        assert set(by_model) == {"vireonix:auto"}, f"expected one real bucket, got {sorted(by_model)}"
        assert by_model["vireonix:auto"]["total_tokens"] == 15

    def test_no_bucket_is_ever_named_unknown_for_a_named_call(self) -> None:
        journal = RunJournal(run_id="r2", thread_id="t2", event_store=_store())
        journal._record_model_usage("vireonix:auto", 1, 1, 2, 0)
        assert "unknown" not in journal._tokens_by_model


def _store():
    from alpha.runtime.events.store.memory import MemoryRunEventStore

    return MemoryRunEventStore()


@pytest.mark.parametrize(
    ("provider", "model_id", "expected"),
    [
        ("vireonix", "auto", "vireonix:auto"),
        ("pollinations", "openai-fast", "pollinations:openai-fast"),
        ("llm7", "gpt-oss", "llm7:gpt-oss"),
    ],
)
def test_bucket_names_for_the_gateways_that_actually_answered(provider: str, model_id: str, expected: str) -> None:
    """The three providers verified live as reachable, keyless and no-signup."""
    assert _message(_Result(provider=provider, model_id=model_id)).response_metadata["model_name"] == expected
