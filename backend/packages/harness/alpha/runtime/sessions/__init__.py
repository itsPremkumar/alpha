"""Durable session lifecycle: an explicit state machine over a disposable process.

Why this exists
---------------
Alpha's *run* already has a real, durable, DB-enforced lifecycle
(:class:`alpha.runtime.runs.schemas.RunStatus`), and Alpha's *thread* has a
durable metadata row. What neither has is the thing the durable-runtime
contract actually promises: **a lifecycle that can represent "this task is
alive but parked because the machine has no internet"**.

``threads_meta.status`` is a free-text ``String(20)`` and only ever says
``idle``/``running``/``error``. A task waiting for connectivity is, today,
indistinguishable from a task that failed -- which is exactly the failure mode
the contract forbids ("an internet outage must not become a task failure").

This package adds that missing vocabulary. It is deliberately **not** a second
lifecycle owner: :mod:`alpha.runtime.sessions.states` is a pure derivation over
signals the existing owners already hold (``RunStatus``, the network monitor, a
pending permission, an in-flight compaction, a recovery disposition). Nothing
here writes a run, cancels a run, or admits work. The state machine exists to
make the existing durable facts *readable* as a lifecycle, and to make an
illegal lifecycle transition a loud, testable error instead of a silently
mis-typed string.

Deliberate non-goals
--------------------
* No new table. The session is a thread; adding a second durable session table
  would fork ownership of the very state this is meant to describe.
* No new worker. ``RunManager`` stays the sole lifecycle owner.
* ``COMPLETED`` never implies verified. See
  :mod:`alpha.runtime.runs.verification`; acceptance stays an overlay.
"""

from alpha.runtime.sessions.states import (
    ACTIVE_SESSION_STATES,
    SESSION_STATE_CLASSES,
    SESSION_STATE_TRANSITIONS,
    TERMINAL_SESSION_STATES,
    WAITING_SESSION_STATES,
    IllegalSessionTransition,
    SessionState,
    SessionStateClass,
    SessionStateReport,
    SessionStateSignal,
    can_transition,
    derive_session_state,
    is_active,
    is_resumable,
    is_terminal,
    session_state_class,
    transition_table,
    validate_transition,
)

__all__ = [
    "ACTIVE_SESSION_STATES",
    "SESSION_STATE_CLASSES",
    "SESSION_STATE_TRANSITIONS",
    "TERMINAL_SESSION_STATES",
    "WAITING_SESSION_STATES",
    "IllegalSessionTransition",
    "SessionState",
    "SessionStateClass",
    "SessionStateReport",
    "SessionStateSignal",
    "can_transition",
    "derive_session_state",
    "is_active",
    "is_resumable",
    "is_terminal",
    "session_state_class",
    "transition_table",
    "validate_transition",
]
