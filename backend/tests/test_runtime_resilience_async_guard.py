"""The async guard on the synchronous resilience helpers.

Reproduced on arrival: `recover(async_fn)` returned `succeeded` with a coroutine
as its value, and `retry_call(async_fn, ...)` returned a `RetryResult` wrapping
a coroutine - both while the work never ran, with only a "coroutine was never
awaited" warning to hint at it. A resilience primitive that certifies work that
did not happen is worse than no primitive, so these tests pin the refusal.

Two shapes must be refused:
  * an `async def` passed directly, and
  * a SYNC callable that RETURNS a coroutine (a sync wrapper hiding async work),
    which is the sneakier one because the callable looks synchronous.
"""

from __future__ import annotations

import asyncio
import warnings
from typing import Any

import pytest

from alpha.runtime.resilience.errors import AsyncOperationRefused
from alpha.runtime.resilience.recovery import RecoveryStatus, recover
from alpha.runtime.resilience.retry import RetryPolicy, retry_call


async def _async_ok() -> str:
    return "async-ok"


def _sync_wrapper_returning_coroutine() -> Any:
    """Looks synchronous; returns a coroutine. The dangerous shape."""
    return _async_ok()


class _AsyncCallable:
    async def __call__(self) -> str:  # noqa: D401 - test double
        return "async-ok"


def test_recover_refuses_an_async_operation() -> None:
    with pytest.raises(AsyncOperationRefused) as excinfo:
        recover(_async_ok, policy=RetryPolicy(attempts=2))
    message = str(excinfo.value)
    assert "async" in message.lower()
    assert "recover" in message


def test_recover_refuses_an_async_callable_object() -> None:
    with pytest.raises(AsyncOperationRefused):
        recover(_AsyncCallable(), policy=RetryPolicy(attempts=2))


def test_retry_call_refuses_an_async_operation() -> None:
    with pytest.raises(AsyncOperationRefused) as excinfo:
        retry_call(_async_ok, policy=RetryPolicy(attempts=3))
    assert "retry_call" in str(excinfo.value)


def test_recover_refuses_a_sync_callable_returning_a_coroutine() -> None:
    """The sneaky shape: the callable is sync, the RESULT is a coroutine.

    `recover` has a closed status set and does not raise for expected failures,
    so the contract here is "never SUCCEEDED", not "always raises": it either
    refuses up front or reports a non-success status naming the reason.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            outcome = recover(_sync_wrapper_returning_coroutine, policy=RetryPolicy(attempts=2))
        except AsyncOperationRefused:
            return
    assert outcome.status != RecoveryStatus.SUCCEEDED, (
        f"recover reported {outcome.status!r} for an operation that never ran"
    )


def test_retry_call_refuses_a_sync_callable_returning_a_coroutine() -> None:
    with pytest.raises(AsyncOperationRefused):
        retry_call(_sync_wrapper_returning_coroutine, policy=RetryPolicy(attempts=2))


def test_refusal_closes_the_coroutine_so_no_warning_is_emitted() -> None:
    """The guard must not replace one silent failure with a noisy one.

    If the returned coroutine were left un-awaited, Python would emit a
    "coroutine was never awaited" RuntimeWarning - easy to lose in a busy log.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            recover(_sync_wrapper_returning_coroutine, policy=RetryPolicy(attempts=1))
        except AsyncOperationRefused:
            pass
    never_awaited = [
        w for w in caught if "never awaited" in str(w.message).lower()
    ]
    assert not never_awaited, f"refusal leaked a coroutine: {[str(w.message) for w in never_awaited]}"


def test_the_refusal_is_a_control_signal_and_never_retried() -> None:
    """A refusal must propagate immediately, not be treated as a transient error."""
    assert issubclass(AsyncOperationRefused, Exception)
    from alpha.runtime.resilience.errors import ControlSignal

    assert issubclass(AsyncOperationRefused, ControlSignal)


def test_a_real_sync_operation_still_works_after_the_guard() -> None:
    """The guard must not break the honest path."""

    def sync_ok() -> str:
        return "sync-ok"

    result = retry_call(sync_ok, policy=RetryPolicy(attempts=2))
    assert result.value == "sync-ok"

    outcome = recover(sync_ok, policy=RetryPolicy(attempts=2))
    assert outcome.status == RecoveryStatus.SUCCEEDED
    assert outcome.value == "sync-ok"


def test_disabled_pass_through_also_refuses_async() -> None:
    """`enabled=False` is a pass-through, and a pass-through must not lie either."""
    with pytest.raises(AsyncOperationRefused):
        recover(_async_ok, policy=RetryPolicy(attempts=1), enabled=False)


def test_an_awaited_coroutine_is_still_fine_elsewhere() -> None:
    """Guard the guard: normal async usage is untouched by this change."""
    async def driver() -> str:
        return await _async_ok()

    assert asyncio.run(driver()) == "async-ok"
