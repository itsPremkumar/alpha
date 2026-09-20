"""ORM model registration entry point.

Importing this module ensures all ORM models are registered with
``Base.metadata`` so Alembic autogenerate detects every table.

The actual ORM classes have moved to entity-specific subpackages:
- ``alpha.persistence.thread_meta``
- ``alpha.persistence.run``
- ``alpha.persistence.feedback``
- ``alpha.persistence.user``

``RunEventRow`` remains in ``alpha.persistence.models.run_event`` because
its storage implementation lives in ``alpha.runtime.events.store.db`` and
there is no matching entity directory.
"""

from alpha.persistence.agents.model import AgentRow
from alpha.persistence.channel_connections.model import (
    ChannelConnectionRow,
    ChannelConversationRow,
    ChannelCredentialRow,
    ChannelOAuthStateRow,
)
from alpha.persistence.feedback.model import FeedbackRow
from alpha.persistence.managed_subagents.model import ManagedSubagentRow
from alpha.persistence.mcp_tasks.model import McpTaskRow
from alpha.persistence.models.run_event import RunEventRow
from alpha.persistence.personal_access_tokens.model import PersonalAccessTokenRow
from alpha.persistence.projects.model import ProjectRow
from alpha.persistence.run.model import RunRow
from alpha.persistence.scheduled_task_runs.model import ScheduledTaskRunRow
from alpha.persistence.scheduled_tasks.model import ScheduledTaskRow
from alpha.persistence.subagent_batches.model import SubagentBatchItemRow, SubagentBatchRow
from alpha.persistence.thread_meta.model import ThreadMetaRow
from alpha.persistence.user.model import UserRow
from alpha.persistence.webhook_delivery.model import WebhookDeliveryRow

__all__ = [
    "AgentRow",
    "ChannelConnectionRow",
    "ChannelConversationRow",
    "ChannelCredentialRow",
    "ChannelOAuthStateRow",
    "FeedbackRow",
    "McpTaskRow",
    "ManagedSubagentRow",
    "PersonalAccessTokenRow",
    "ProjectRow",
    "RunEventRow",
    "RunRow",
    "ScheduledTaskRow",
    "ScheduledTaskRunRow",
    "SubagentBatchRow",
    "SubagentBatchItemRow",
    "ThreadMetaRow",
    "UserRow",
    "WebhookDeliveryRow",
]
