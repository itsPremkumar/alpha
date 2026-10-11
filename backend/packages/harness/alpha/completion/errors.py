"""Typed errors for the Alpha Completion Engine.

Every refusal in this package is one of these, so a caller can distinguish
"this transition is never legal" from "this contract is not dispatchable"
from "the store could not be read" without string-matching a message. The
Gateway router maps each class onto one HTTP status and one machine-readable
``code`` (see ``app/gateway/routers/completion.py``).

None of these carry secrets: a message names the subject id and the reason,
never the parameters or output of a check.
"""

from __future__ import annotations

from typing import Any


class CompletionError(Exception):
    """Base class for every refusal the Completion Engine raises."""

    #: HTTP/status hint for the router; the router owns the actual mapping.
    code = "completion_error"

    def __init__(self, message: str, *, subject: str = "") -> None:
        super().__init__(message)
        self.subject = subject


class ContractInvalid(CompletionError):
    """The acceptance contract has blocking findings, so work may not start.

    Raised at the two boundaries that matter — task dispatch and finalization —
    so a vague plan can never be executed or reported from.
    """

    code = "contract_invalid"

    def __init__(self, message: str, *, findings: list[Any] | None = None, subject: str = "") -> None:
        super().__init__(message, subject=subject)
        #: The findings that made the contract unusable (``ContractFinding``s).
        self.findings = findings or []


class IllegalTransition(CompletionError):
    """A state change outside the centrally validated graph.

    The state machine is the only writer of status strings: an adapter, an LLM
    or a subagent cannot hand ``verified`` to a task that was never checked.
    """

    code = "illegal_transition"


class NotFound(CompletionError):
    """The named scope, task, gate, dispatch or evidence does not exist."""

    code = "not_found"


class OwnershipConflict(CompletionError):
    """Two live claims cover the same path (or a path and its parent).

    Refused *before* launch, because a path lease is coordination rather than
    isolation — see ``ownership.py``.
    """

    code = "ownership_conflict"

    def __init__(self, message: str, *, conflicts: list[Any] | None = None, subject: str = "") -> None:
        super().__init__(message, subject=subject)
        self.conflicts = conflicts or []


class PathEscape(CompletionError):
    """A declared path resolves outside the workspace root."""

    code = "path_escape"


class BudgetExhausted(CompletionError):
    """A scope-level budget (deadline, gate runs, tasks) is spent.

    Exhaustion is reported with the measured figures; it never silently grants
    more headroom and it never converts into a pass.
    """

    code = "budget_exhausted"


class CheckUnavailable(CompletionError):
    """A gate's check cannot run in this deployment (policy, config, host).

    This is ``blocked``, never ``failed`` and never ``passed``: the difference
    between "the check says no" and "the check never ran" is the whole point
    of the engine.
    """

    code = "check_unavailable"


class ApprovalRequired(CompletionError):
    """Executing this gate needs an approval bound to its current definition."""

    code = "approval_required"


class ApprovalRefused(CompletionError):
    """An approval was requested for a check the policy never permits."""

    code = "approval_refused"


class StoreUnreadable(CompletionError):
    """The persisted ledger could not be read.

    The router answers 503 with this message: "could not look" must never
    render as "nothing is recorded".
    """

    code = "store_unreadable"


class StoreCorrupt(StoreUnreadable):
    """The store file exists but its content cannot be parsed as a document.

    A *missing* file is a fresh store; a *malformed* one is not, and is never
    silently replaced with an empty document (which would report every prior
    contract as absent).
    """

    code = "store_corrupt"


class StoreWriteFailed(StoreUnreadable):
    """A mutation could not be persisted; the in-memory change is discarded."""

    code = "store_write_failed"


class ScopeStateError(CompletionError):
    """The operation does not apply to the scope's current state."""

    code = "scope_state_error"
