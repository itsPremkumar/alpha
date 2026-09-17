"""ORM model registration entry point.

Importing this module ensures all ORM models are registered with
``Base.metadata`` so Alembic autogenerate detects every table.

The actual ORM classes have moved to entity-specific subpackages:
- ``deerflow.persistence.thread_meta``
- ``deerflow.persistence.run``
- ``deerflow.persistence.feedback``
- ``deerflow.persistence.user``

``RunEventRow`` remains in ``deerflow.persistence.models.run_event`` because
its storage implementation lives in ``deerflow.runtime.events.store.db`` and
there is no matching entity directory.
"""

from agent_workspace.persistence.agents.model import AgentRow
from agent_workspace.persistence.channel_connections.model import (
    ChannelConnectionRow,
    ChannelConversationRow,
    ChannelCredentialRow,
    ChannelOAuthStateRow,
)
from agent_workspace.persistence.feedback.model import FeedbackRow
from agent_workspace.persistence.managed_subagents.model import ManagedSubagentRow
from agent_workspace.persistence.mcp_tasks.model import McpTaskRow
from agent_workspace.persistence.models.run_event import RunEventRow
from agent_workspace.persistence.personal_access_tokens.model import PersonalAccessTokenRow
from agent_workspace.persistence.projects.model import ProjectRow
from agent_workspace.persistence.run.model import RunRow
from agent_workspace.persistence.scheduled_task_runs.model import ScheduledTaskRunRow
from agent_workspace.persistence.scheduled_tasks.model import ScheduledTaskRow
from agent_workspace.persistence.subagent_batches.model import SubagentBatchItemRow, SubagentBatchRow
from agent_workspace.persistence.thread_meta.model import ThreadMetaRow
from agent_workspace.persistence.user.model import UserRow
from agent_workspace.persistence.webhook_delivery.model import WebhookDeliveryRow

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
