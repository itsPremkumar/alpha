# Advanced Group & Messaging Platform — Full Feature Plan

> **Status**: Implemented in part — see
> [Implementation Status](#0-implementation-status) for the per-feature map.
> Every row there points at a real route, module or component; anything not
> listed as shipped is **not** shipped.
> **Date**: 2026-10-03
> **Scope**: Group profiles, messaging, notifications, subgroups, bot visibility, UI/UX

---

## 0. Implementation Status

This document was written before the work. It is kept as the design record; the
table below is the claim about reality, and it is the one to trust. Branch
`feature/advanced-group-messaging`.

### Shipped — 32 new routes, verified by tests

| Area | Backend | Frontend | Tests |
| --- | --- | --- | --- |
| Group profile (avatar/banner, description, purpose, category, tags) | `GET\|PATCH /{name}/profile`, `service.update_group_profile` | `GroupProfilePanel` (completion bar names the missing fields) | `test_group_advanced_messaging.py` |
| Group links | `GET\|POST\|DELETE /{name}/links`, `PATCH /{name}/links/reorder` | `GroupProfilePanel` → Links; `safeHref` renders non-http(s) as inert text | `test_group_advanced_messaging.py`, `groups-profile.test.mjs` |
| Goals | `GET\|POST /{name}/goals`, `PATCH /{name}/goals/{goal_id}` | `GroupProfilePanel` → Goals | `test_group_advanced_messaging.py` |
| Project binding | `GET\|POST\|DELETE /{name}/project-link` | `GroupProfilePanel` → Linked project | `test_group_advanced_messaging.py` |
| Clone | `POST /{name}/clone` | `GroupProfilePanel` → Clone this group | `test_group_advanced_messaging.py` |
| Pinning | `POST\|DELETE /{name}/messages/{id}/pin`, `GET /{name}/pinned` | `MessagesSection` | `test_group_advanced_messaging.py` |
| Threading | `GET /{name}/messages/{id}/thread`, `GET /{name}/threads` | `MessagesSection` | `test_group_advanced_messaging.py` |
| Search | `GET /{name}/search` | `MessagesSection` | `test_group_advanced_messaging.py` |
| Read receipts / unread | `POST /{name}/messages/{id}/read`, `GET /{name}/unread`, `GET /{name}/messages/{id}/readers` | `groups-profile-model` (`readersSummary`, `unreadCount`) | `test_group_advanced_messaging.py`, `groups-profile.test.mjs` |
| Typing indicators | `POST\|GET /{name}/typing` | `typingHeadline` | `test_group_advanced_messaging.py` |
| Real-time | `GET /{name}/events` (SSE via `app/gateway/group_events.py`) | `lib/groups-profile.ts` SSE client | `test_group_advanced_messaging.py` |
| Notifications | `alpha/notifications/` (store, triggers, preferences) + `routers/notifications.py` (7 routes), raised from `service._raise_notifications` | `NotificationsBell` (history, unread badge, sound, desktop popup, toasts) | `test_group_notifications.py` (82 backend cases), `notifications.test.mjs` (51) |

Notifications are raised **inside `GroupChatService.post_message`** — the single
write path — so a message that reaches the room always raises its notification,
and a message that never did can never claim to have been announced.

### Not shipped

Deliberately out of scope for this change, still design-only: rich-text/markdown
composer rendering, file & image attachments, message scheduling, message
export, drag-and-drop tree reordering, bot performance metrics, analytics /
health score / activity heatmap, group & subgroup templates, settings
inheritance, the outbound webhook and email channels, and the plugin system.
Real-time uses **SSE, not WebSocket** (section 10.1); WebSocket remains design.

### Standing caveats that did not change

- Notification history is a **single-process, per-operator JSON store**
  (bounded at 500 records). It is not cross-process exactly-once, and the router
  hard-codes the single operator id rather than inventing multi-user isolation.
- Every new room field defaults so an older room round-trips unchanged; the
  persistence envelope is versioned `2 → 3`.

---

## Table of Contents

0. [Implementation Status](#0-implementation-status)
1. [Executive Summary](#1-executive-summary)
2. [Current State Assessment](#2-current-state-assessment)
3. [Feature Categories](#3-feature-categories)
4. [Phase 1: Group Identity & Profiles](#4-phase-1-group-identity--profiles)
5. [Phase 2: Advanced Messaging](#5-phase-2-advanced-messaging)
6. [Phase 3: Notification System](#6-phase-3-notification-system)
7. [Phase 4: Subgroups & Hierarchy](#7-phase-4-subgroups--hierarchy)
8. [Phase 5: Bot Visibility & Management](#8-phase-5-bot-visibility--management)
9. [Phase 6: Group Purpose & Project Linking](#9-phase-6-group-purpose--project-linking)
10. [Phase 7: Real-Time Infrastructure](#10-phase-7-real-time-infrastructure)
11. [Phase 8: Advanced UI/UX](#11-phase-8-advanced-uiux)
12. [Phase 9: Analytics & Insights](#12-phase-9-analytics--insights)
13. [Phase 10: Integrations & Extensibility](#13-phase-10-integrations--extensibility)
14. [Implementation Roadmap](#14-implementation-roadmap)
15. [Technical Architecture](#15-technical-architecture)
16. [Risk Assessment](#16-risk-assessment)

---

## 1. Executive Summary

Alpha's group and messaging system already has a solid foundation: nested groups with multi-parent visibility, 5 orchestration modes, rule-based membership, work claims, activity tracking, and 10 IM bridges. This plan adds the missing layer: **group identity, rich messaging, real-time notifications, advanced subgroup management, bot visibility, and a modern UI** — transforming it from a coordination backend into a full communication platform.

### Key Principles

- **Honesty-first**: Every count carries evidence, every absence is disclosed
- **Bounded everything**: Hard limits, refused not clamped
- **Projection over storage**: Inherited state computed, never stored
- **Single-process contract**: No cross-process exactly-once
- **Fail-closed**: Unreadable state is never defaulted

---

## 2. Current State Assessment

### What Exists

| Area | Status | Key Files |
|------|--------|-----------|
| Group models | Solid | `groups/room.py` — `GroupRoom`, `GroupMessage`, `MessageIntent` |
| Group service | Solid | `groups/service.py` — 932 lines, full CRUD + relay |
| Nested groups | Solid | `groups/scope.py` — multi-parent, MAX_DEPTH=4, relay policies |
| Membership | Solid | `groups/roster.py` — direct/rule/inherited/excluded/expired |
| Orchestration | Solid | `groups/orchestration.py` — 5 modes |
| Activity tracking | Solid | `groups/activity.py` — 8 states with evidence |
| Work claims | Solid | `groups/claims.py` — soft conflicts, crash orphaning |
| War room | Solid | `groups/war_room.py` — quorum, consensus, taint |
| Group runs | Solid | `groups/runner.py` — autonomous team execution |
| IM bridges | Solid | 10 platforms, streaming, commands |
| Bot registry | Solid | `bots/registry.py` — full lifecycle |
| Frontend tree | Basic | `GroupTreeSidebar.tsx` — expand/collapse, subgroup form |
| Frontend coordination | Basic | `GroupCoordinationPanel.tsx` — activity + claims |

### What's Missing

| Gap | Impact |
|-----|--------|
| No group profile (avatar, description, links) | Groups are anonymous rooms |
| No message composer UI | Can't post messages from frontend |
| No message viewer UI | Can't read group transcripts |
| No real-time push | Polling only, no live updates |
| No notification system | Humans don't know when agents act |
| No group settings UI | Can't configure policy/relay from frontend |
| No member management UI | Can't add/remove bots from frontend |
| No message search | Can't find past discussions |
| No rich media | No images/files in group messages |
| No read receipts | Can't track what's been seen |
| No group analytics | No participation metrics |
| No group templates | No predefined configurations |
| No group purpose/project link | Groups disconnected from projects |

---

## 3. Feature Categories

```
Advanced Group & Messaging Platform
├── A. Group Identity & Profiles
│   ├── Group avatar & banner
│   ├── Group description & purpose
│   ├── Group links (project, docs, repo, external)
│   ├── Group tags & categories
│   └── Group templates
├── B. Advanced Messaging
│   ├── Rich text composer
│   ├── Message threading
│   ├── File/image attachments
│   ├── Message pinning
│   ├── Message search
│   ├── Read receipts
│   ├── Typing indicators
│   ├── Message scheduling
│   └── Message export
├── C. Notification System
│   ├── Real-time push (WebSocket/SSE)
│   ├── Desktop notifications
│   ├── Sound alerts
│   ├── @mention notifications
│   ├── Notification preferences
│   ├── Notification history
│   └── Digest mode
├── D. Subgroups & Hierarchy
│   ├── Visual tree editor
│   ├── Drag-and-drop reordering
│   ├── Group cloning
│   ├── Cross-tree operations
│   ├── Settings inheritance
│   └── Subgroup templates
├── E. Bot Visibility & Management
│   ├── Member list with profiles
│   ├── Bot activity dashboard
│   ├── Bot skill/capability view
│   ├── Bot invite flow
│   └── Bot performance metrics
├── F. Group Purpose & Projects
│   ├── Project linking
│   ├── Purpose declaration
│   ├── Goal tracking
│   ├── Milestone integration
│   └── Progress reporting
├── G. Real-Time Infrastructure
│   ├── WebSocket gateway
│   ├── SSE streaming
│   ├── Presence system
│   └── Connection management
├── H. Advanced UI/UX
│   ├── Modern chat interface
│   ├── Group settings panel
│   ├── Member management panel
│   ├── Search interface
│   ├── Analytics dashboard
│   └── Mobile-responsive design
├── I. Analytics & Insights
│   ├── Participation metrics
│   ├── Response time tracking
│   ├── Agent performance
│   ├── Group health scores
│   └── Activity heatmaps
└── J. Integrations & Extensibility
    ├── Webhook outbound
    ├── Email notifications
    ├── Slack/Teams integration
    ├── API extensions
    └── Plugin system
```

---

## 4. Phase 1: Group Identity & Profiles

### 4.1 Group Avatar & Banner

**Backend Changes**:

```python
# backend/packages/harness/alpha/groups/room.py

@dataclass
class GroupRoom:
    # ... existing fields ...
    avatar_url: str | None = None          # Group avatar image URL
    banner_url: str | None = None          # Group banner image URL
    avatar_color: str | None = None        # Fallback color (hex) when no avatar
```

**API Changes**:

```python
# backend/app/gateway/routers/groups.py

@router.patch("/api/groups/{name}/profile")
async def update_group_profile(
    name: str,
    profile: GroupProfileUpdate,  # avatar_url, banner_url, avatar_color
) -> GroupRoom:
    """Update group visual identity."""

@router.post("/api/groups/{name}/avatar")
async def upload_group_avatar(
    name: str,
    file: UploadFile,
) -> GroupRoom:
    """Upload group avatar image."""
```

**Frontend Changes**:

```tsx
// frontend/src/components/sections/GroupProfileEditor.tsx

interface GroupProfileEditorProps {
  room: GroupRoom;
  onSave: (profile: GroupProfileUpdate) => Promise<void>;
}

// Avatar upload with drag-and-drop
// Banner upload with preview
// Color picker for fallback avatar
```

### 4.2 Group Description & Purpose

**Backend Changes**:

```python
# backend/packages/harness/alpha/groups/room.py

@dataclass
class GroupRoom:
    # ... existing fields ...
    description: str | None = None         # Rich text description
    purpose: str | None = None             # Short purpose statement
    goals: list[str] = field(default_factory=list)  # Group goals
    created_by: str | None = None          # Creator identifier
    created_at: str | None = None          # Creation timestamp
```

**API Changes**:

```python
class GroupDescriptionUpdate(BaseModel):
    description: str | None = None
    purpose: str | None = None
    goals: list[str] | None = None

@router.patch("/api/groups/{name}/description")
async def update_group_description(
    name: str,
    update: GroupDescriptionUpdate,
) -> GroupRoom:
    """Update group description, purpose, and goals."""
```

### 4.3 Group Links

**Backend Changes**:

```python
# backend/packages/harness/alpha/groups/links.py

@dataclass
class GroupLink:
    link_id: str
    room_id: str
    label: str                           # "Project Repo", "Documentation", etc.
    url: str
    link_type: str                       # "project", "docs", "repo", "external", "custom"
    icon: str | None = None              # Icon identifier
    created_by: str | None = None
    created_at: str | None = None
    position: int = 0                    # Display order

class GroupLinkStore:
    def add_link(self, room_id: str, link: GroupLink) -> GroupLink: ...
    def remove_link(self, room_id: str, link_id: str) -> None: ...
    def list_links(self, room_id: str) -> list[GroupLink]: ...
    def reorder_links(self, room_id: str, link_ids: list[str]) -> None: ...
```

**API Changes**:

```python
@router.post("/api/groups/{name}/links")
async def add_group_link(name: str, link: GroupLinkCreate) -> GroupLink: ...

@router.delete("/api/groups/{name}/links/{link_id}")
async def remove_group_link(name: str, link_id: str) -> None: ...

@router.get("/api/groups/{name}/links")
async def list_group_links(name: str) -> list[GroupLink]: ...

@router.patch("/api/groups/{name}/links/reorder")
async def reorder_group_links(name: str, link_ids: list[str]) -> None: ...
```

### 4.4 Group Tags & Categories

**Backend Changes**:

```python
@dataclass
class GroupRoom:
    # ... existing fields ...
    tags: list[str] = field(default_factory=list)     # Free-form tags
    category: str | None = None                       # Predefined category
```

**Predefined Categories**:
- `engineering` — Development teams
- `research` — Research and analysis
- `operations` — Ops and infrastructure
- `support` — Customer support
- `management` — Leadership and planning
- `custom` — User-defined

### 4.5 Group Templates

**Backend Changes**:

```python
# backend/packages/harness/alpha/groups/templates.py

@dataclass
class GroupTemplate:
    template_id: str
    name: str                            # "Engineering Team", "Research Squad"
    description: str
    category: str
    default_members: list[str]           # Bot names to auto-add
    default_rules: list[dict]            # Membership rules
    default_mode: str                    # Orchestration mode
    default_policy: dict                 # Relay policy
    default_links: list[dict]            # Default links
    icon: str | None = None
    color: str | None = None

class GroupTemplateStore:
    def list_templates(self) -> list[GroupTemplate]: ...
    def get_template(self, template_id: str) -> GroupTemplate | None: ...
    def create_from_template(self, template_id: str, name: str, **overrides) -> GroupRoom: ...
```

**Built-in Templates**:

| Template | Members | Mode | Description |
|----------|---------|------|-------------|
| Engineering Team | architect, coder, reviewer, tester | moderated | Standard dev team |
| Research Squad | researcher, analyst, writer | parallel | Research and documentation |
| Support Team | support, analyst, coder | mention | Customer support |
| War Room | architect, coder, reviewer, tester, researcher | quorum | Crisis response |
| Data Team | data-analyst, researcher, writer | round_robin | Data analysis |

---

## 5. Phase 2: Advanced Messaging

### 5.1 Rich Text Composer

**Backend Changes**:

```python
# backend/packages/harness/alpha/groups/room.py

@dataclass
class GroupMessage:
    # ... existing fields ...
    content_type: str = "text"           # "text", "markdown", "html"
    attachments: list[MessageAttachment] = field(default_factory=list)
    pinned: bool = False
    pinned_at: str | None = None
    pinned_by: str | None = None

@dataclass
class MessageAttachment:
    attachment_id: str
    message_id: str
    file_name: str
    file_type: str                       # MIME type
    file_size: int
    url: str                             # Storage URL
    thumbnail_url: str | None = None
    created_at: str | None = None
```

**API Changes**:

```python
class MessageCreate(BaseModel):
    content: str
    content_type: str = "text"           # "text" or "markdown"
    intent: str | None = None
    mentions: list[str] = []
    reply_to: str | None = None
    attachments: list[AttachmentCreate] = []

@router.post("/api/groups/{name}/messages")
async def post_message(name: str, message: MessageCreate) -> GroupMessage:
    """Post a message with rich content and attachments."""
```

### 5.2 Message Threading

**Backend Changes**:

```python
@dataclass
class GroupMessage:
    # ... existing fields ...
    thread_id: str | None = None         # Thread root message id
    thread_position: int = 0             # Position within thread
    reply_count: int = 0                 # Number of replies
    last_reply_at: str | None = None     # Last reply timestamp

class MessageThreadStore:
    def get_thread(self, thread_id: str) -> list[GroupMessage]: ...
    def get_thread_root(self, message_id: str) -> str | None: ...
    def get_replies(self, message_id: str) -> list[GroupMessage]: ...
```

**API Changes**:

```python
@router.get("/api/groups/{name}/messages/{message_id}/thread")
async def get_message_thread(name: str, message_id: str) -> list[GroupMessage]:
    """Get all messages in a thread."""

@router.get("/api/groups/{name}/threads")
async def list_threads(name: str) -> list[ThreadSummary]:
    """List all threads in a group."""
```

### 5.3 File/Image Attachments

**Backend Changes**:

```python
# backend/packages/harness/alpha/groups/attachments.py

class AttachmentStore:
    def store(self, room_id: str, message_id: str, file: UploadFile) -> MessageAttachment: ...
    def get(self, attachment_id: str) -> MessageAttachment | None: ...
    def delete(self, attachment_id: str) -> None: ...
    def list_for_message(self, message_id: str) -> list[MessageAttachment]: ...
    def list_for_room(self, room_id: str) -> list[MessageAttachment]: ...

# Storage: ALPHA_HOME/groups/{room_id}/attachments/{attachment_id}/
# Max file size: 25MB per file, 100MB per room
# Allowed types: images, documents, archives
```

**API Changes**:

```python
@router.post("/api/groups/{name}/messages/{message_id}/attachments")
async def upload_attachment(
    name: str,
    message_id: str,
    file: UploadFile,
) -> MessageAttachment:
    """Upload a file attachment to a message."""

@router.get("/api/groups/{name}/attachments/{attachment_id}")
async def download_attachment(name: str, attachment_id: str):
    """Download an attachment."""

@router.delete("/api/groups/{name}/attachments/{attachment_id}")
async def delete_attachment(name: str, attachment_id: str) -> None:
    """Delete an attachment."""
```

### 5.4 Message Pinning

**Backend Changes**:

```python
@router.post("/api/groups/{name}/messages/{message_id}/pin")
async def pin_message(name: str, message_id: str) -> GroupMessage:
    """Pin a message to the group."""

@router.delete("/api/groups/{name}/messages/{message_id}/pin")
async def unpin_message(name: str, message_id: str) -> GroupMessage:
    """Unpin a message."""

@router.get("/api/groups/{name}/pinned")
async def list_pinned_messages(name: str) -> list[GroupMessage]:
    """List all pinned messages in a group."""
```

### 5.5 Message Search

**Backend Changes**:

```python
# backend/packages/harness/alpha/groups/search.py

class MessageSearchEngine:
    def search(
        self,
        query: str,
        room_id: str | None = None,
        sender: str | None = None,
        intent: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
    ) -> list[SearchResult]: ...

    def index_message(self, message: GroupMessage) -> None: ...
    def remove_from_index(self, message_id: str) -> None: ...

@dataclass
class SearchResult:
    message: GroupMessage
    score: float
    highlights: list[str]                # Matching snippets
```

**API Changes**:

```python
@router.get("/api/groups/{name}/search")
async def search_messages(
    name: str,
    q: str,
    sender: str | None = None,
    intent: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
) -> list[SearchResult]:
    """Search messages within a group."""

@router.get("/api/groups/search")
async def search_all_groups(
    q: str,
    limit: int = 50,
) -> list[SearchResult]:
    """Search messages across all groups."""
```

### 5.6 Read Receipts

**Backend Changes**:

```python
# backend/packages/harness/alpha/groups/receipts.py

@dataclass
class ReadReceipt:
    room_id: str
    message_id: str
    reader: str                          # Bot or user name
    read_at: str

class ReadReceiptStore:
    def mark_read(self, room_id: str, message_id: str, reader: str) -> None: ...
    def get_readers(self, room_id: str, message_id: str) -> list[str]: ...
    def get_unread(self, room_id: str, reader: str) -> list[GroupMessage]: ...
    def get_last_read(self, room_id: str, reader: str) -> str | None: ...
```

**API Changes**:

```python
@router.post("/api/groups/{name}/messages/{message_id}/read")
async def mark_message_read(name: str, message_id: str, reader: str) -> None:
    """Mark a message as read."""

@router.get("/api/groups/{name}/unread")
async def get_unread_messages(name: str, reader: str) -> list[GroupMessage]:
    """Get unread messages for a reader."""

@router.get("/api/groups/{name}/messages/{message_id}/readers")
async def get_message_readers(name: str, message_id: str) -> list[str]:
    """Get list of readers for a message."""
```

### 5.7 Typing Indicators

**Backend Changes**:

```python
# backend/packages/harness/alpha/groups/presence.py

@dataclass
class TypingIndicator:
    room_id: str
    bot_name: str
    is_typing: bool
    timestamp: str

class TypingStore:
    def set_typing(self, room_id: str, bot_name: str, is_typing: bool) -> None: ...
    def get_typing(self, room_id: str) -> list[TypingIndicator]: ...
    def clear_typing(self, room_id: str, bot_name: str) -> None: ...
```

**API Changes**:

```python
@router.post("/api/groups/{name}/typing")
async def set_typing_indicator(name: str, bot_name: str, is_typing: bool) -> None:
    """Set typing indicator for a bot in a group."""

@router.get("/api/groups/{name}/typing")
async def get_typing_indicators(name: str) -> list[TypingIndicator]:
    """Get current typing indicators for a group."""
```

### 5.8 Message Scheduling

**Backend Changes**:

```python
# backend/packages/harness/alpha/groups/scheduler.py

@dataclass
class ScheduledMessage:
    schedule_id: str
    room_id: str
    content: str
    sender: str
    scheduled_for: str                   # ISO timestamp
    status: str                          # "pending", "sent", "cancelled"
    created_at: str

class MessageScheduler:
    def schedule(self, room_id: str, content: str, sender: str, scheduled_for: str) -> ScheduledMessage: ...
    def cancel(self, schedule_id: str) -> None: ...
    def list_pending(self, room_id: str) -> list[ScheduledMessage]: ...
    def process_due(self) -> list[ScheduledMessage]: ...  # Called by background loop
```

**API Changes**:

```python
@router.post("/api/groups/{name}/scheduled")
async def schedule_message(name: str, message: ScheduledMessageCreate) -> ScheduledMessage:
    """Schedule a message for later delivery."""

@router.get("/api/groups/{name}/scheduled")
async def list_scheduled_messages(name: str) -> list[ScheduledMessage]:
    """List pending scheduled messages."""

@router.delete("/api/groups/{name}/scheduled/{schedule_id}")
async def cancel_scheduled_message(name: str, schedule_id: str) -> None:
    """Cancel a scheduled message."""
```

### 5.9 Message Export

**API Changes**:

```python
@router.get("/api/groups/{name}/export")
async def export_messages(
    name: str,
    format: str = "json",                # "json", "markdown", "text", "html"
    date_from: str | None = None,
    date_to: str | None = None,
):
    """Export group messages in various formats."""
```

---

## 6. Phase 3: Notification System

### 6.1 Real-Time Push Infrastructure

**Backend Changes**:

```python
# backend/app/gateway/websocket.py

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        self.room_subscriptions: dict[str, set[str]] = {}  # room_id -> connection_ids

    async def connect(self, websocket: WebSocket, client_id: str) -> None: ...
    async def disconnect(self, client_id: str) -> None: ...
    async def subscribe_to_room(self, client_id: str, room_id: str) -> None: ...
    async def unsubscribe_from_room(self, client_id: str, room_id: str) -> None: ...
    async def broadcast_to_room(self, room_id: str, message: dict) -> None: ...
    async def send_to_client(self, client_id: str, message: dict) -> None: ...

manager = ConnectionManager()

@router.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_id = str(uuid.uuid4())
    await manager.connect(websocket, client_id)
    try:
        while True:
            data = await websocket.receive_json()
            await handle_ws_message(client_id, data)
    except WebSocketDisconnect:
        await manager.disconnect(client_id)
```

### 6.2 Notification Model

**Backend Changes**:

```python
# backend/packages/harness/alpha/notifications/models.py

@dataclass
class Notification:
    notification_id: str
    type: str                            # "message", "mention", "activity", "claim", "run", "system"
    title: str
    body: str
    room_id: str | None = None
    message_id: str | None = None
    sender: str | None = None
    priority: str = "normal"             # "low", "normal", "high", "urgent"
    created_at: str | None = None
    read: bool = False
    read_at: str | None = None
    action_url: str | None = None        # Deep link to relevant content
    icon: str | None = None              # Notification icon
    sound: bool = True                   # Whether to play sound

class NotificationStore:
    def create(self, notification: Notification) -> Notification: ...
    def list(
        self,
        room_id: str | None = None,
        types: list[str] | None = None,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[Notification]: ...
    def mark_read(self, notification_id: str) -> None: ...
    def mark_all_read(self, room_id: str | None = None) -> None: ...
    def get_unread_count(self, room_id: str | None = None) -> int: ...
    def delete_old(self, days: int = 30) -> int: ...
```

### 6.3 Notification Triggers

**Backend Changes**:

```python
# backend/packages/harness/alpha/notifications/triggers.py

class NotificationTriggers:
    def __init__(self, store: NotificationStore, manager: ConnectionManager):
        self.store = store
        self.manager = manager

    async def on_message_posted(self, room_id: str, message: GroupMessage) -> None:
        """Notify on new message."""
        # Notify room members (except sender)
        # Notify mentioned users
        # Notify on high-priority intents (escalation, blocker, approval_request)

    async def on_mention(self, room_id: str, message: GroupMessage, mentioned: list[str]) -> None:
        """Notify on @mention."""
        for bot_name in mentioned:
            await self._create_notification(
                type="mention",
                title=f"You were mentioned in {room_id}",
                body=message.content[:200],
                room_id=room_id,
                message_id=message.id,
                sender=message.sender,
                priority="high",
            )

    async def on_activity_change(self, room_id: str, bot_name: str, old_state: str, new_state: str) -> None:
        """Notify on significant activity changes."""
        if new_state in ("blocked", "crashed", "unresponsive"):
            await self._create_notification(
                type="activity",
                title=f"{bot_name} is {new_state}",
                room_id=room_id,
                sender=bot_name,
                priority="high" if new_state == "crashed" else "normal",
            )

    async def on_claim_created(self, room_id: str, claim: WorkClaim) -> None:
        """Notify on work claim."""
        await self._create_notification(
            type="claim",
            title=f"New work claim in {room_id}",
            body=f"{claim.claimant} claimed: {claim.description}",
            room_id=room_id,
            sender=claim.claimant,
        )

    async def on_run_started(self, room_id: str, run: GroupRun) -> None:
        """Notify on run start."""
        await self._create_notification(
            type="run",
            title=f"Run started in {room_id}",
            body=run.description,
            room_id=room_id,
            priority="high",
        )

    async def on_run_completed(self, room_id: str, run: GroupRun) -> None:
        """Notify on run completion."""
        await self._create_notification(
            type="run",
            title=f"Run completed in {room_id}",
            body=f"Status: {run.status}",
            room_id=room_id,
        )
```

### 6.4 Notification Preferences

**Backend Changes**:

```python
# backend/packages/harness/alpha/notifications/preferences.py

@dataclass
class NotificationPreference:
    user_id: str
    enabled: bool = True
    sound_enabled: bool = True
    desktop_enabled: bool = True
    types: dict[str, bool] = field(default_factory=lambda: {
        "message": True,
        "mention": True,
        "activity": True,
        "claim": True,
        "run": True,
        "system": True,
    })
    priorities: dict[str, bool] = field(default_factory=lambda: {
        "low": False,
        "normal": True,
        "high": True,
        "urgent": True,
    })
    quiet_hours_start: str | None = None   # "22:00"
    quiet_hours_end: str | None = None     # "08:00"
    digest_mode: bool = False              # Batch notifications
    digest_interval_minutes: int = 30

class PreferenceStore:
    def get(self, user_id: str) -> NotificationPreference: ...
    def update(self, user_id: str, pref: NotificationPreference) -> None: ...
```

### 6.5 Desktop Notifications & Sound

**Frontend Changes**:

```tsx
// frontend/src/lib/notifications.ts

class NotificationManager {
  private permission: NotificationPermission = "default";
  private soundEnabled: boolean = true;
  private audioContext: AudioContext | null = null;

  async requestPermission(): Promise<void> {
    if ("Notification" in window) {
      this.permission = await Notification.requestPermission();
    }
  }

  async notify(notification: Notification): Promise<void> {
    // Desktop notification
    if (this.permission === "granted" && notification.desktop_enabled) {
      new Notification(notification.title, {
        body: notification.body,
        icon: "/favicon-32x32.png",
        tag: notification.notification_id,
        requireInteraction: notification.priority === "urgent",
      });
    }

    // Sound alert
    if (this.soundEnabled && notification.sound) {
      this.playSound(notification.priority);
    }

    // In-app toast
    this.showToast(notification);
  }

  private playSound(priority: string): void {
    const frequencies: Record<string, number> = {
      low: 440,
      normal: 523,
      high: 659,
      urgent: 880,
    };
    // Play tone via Web Audio API
  }

  private showToast(notification: Notification): void {
    // Show in-app toast notification
  }
}
```

### 6.6 Notification History UI

**Frontend Changes**:

```tsx
// frontend/src/components/sections/NotificationPanel.tsx

interface NotificationPanelProps {
  notifications: Notification[];
  onMarkRead: (id: string) => void;
  onMarkAllRead: () => void;
  onNotificationClick: (notification: Notification) => void;
}

// Notification list with filters (all, unread, mentions, activity)
// Grouped by date
// Mark as read on click
// Clear all button
// Notification settings link
```

---

## 7. Phase 4: Subgroups & Hierarchy

### 7.1 Visual Tree Editor

**Frontend Changes**:

```tsx
// frontend/src/components/sections/GroupTreeEditor.tsx

interface GroupTreeEditorProps {
  rooms: GroupTreeNode[];
  onMove: (roomId: string, newParentId: string) => Promise<void>;
  onReorder: (parentId: string, orderedIds: string[]) => Promise<void>;
}

// Drag-and-drop tree editor
// Visual indentation with connecting lines
// Expand/collapse all
// Search/filter nodes
// Context menu (move, merge, promote, archive)
// Undo/redo support
```

### 7.2 Drag-and-Drop Reordering

**Backend Changes**:

```python
@router.patch("/api/groups/{name}/order")
async def reorder_children(name: str, ordered_ids: list[str]) -> None:
    """Set display order of child groups."""

@router.get("/api/groups/{name}/order")
async def get_child_order(name: str) -> list[str]:
    """Get display order of child groups."""
```

### 7.3 Group Cloning

**Backend Changes**:

```python
@router.post("/api/groups/{name}/clone")
async def clone_group(
    name: str,
    new_name: str,
    include_members: bool = True,
    include_rules: bool = True,
    include_messages: bool = False,
) -> GroupRoom:
    """Clone a group with optional members, rules, and messages."""
```

### 7.4 Settings Inheritance

**Backend Changes**:

```python
@dataclass
class GroupRoom:
    # ... existing fields ...
    inherit_settings: bool = True         # Inherit policy from authority parent
    inherited_from: str | None = None     # Which parent we inherit from

class GroupSettingsResolver:
    def resolve(self, room_id: str) -> ResolvedSettings:
        """Resolve effective settings (own or inherited)."""
        # Walk up authority parent chain
        # Merge settings with child overrides
        # Return resolved settings with provenance
```

### 7.5 Subgroup Templates

**Backend Changes**:

```python
@dataclass
class SubgroupTemplate:
    template_id: str
    name: str
    description: str
    default_mode: str
    default_members: list[str]
    default_rules: list[dict]
    max_depth: int = 2                    # How deep this template can nest
```

---

## 8. Phase 5: Bot Visibility & Management

### 8.1 Member List with Profiles

**Frontend Changes**:

```tsx
// frontend/src/components/sections/GroupMemberList.tsx

interface GroupMemberListProps {
  roster: ResolvedRoster;
  activity: Record<string, AgentActivity>;
  onInvite: () => void;
  onRemove: (botName: string) => void;
  onExclude: (botName: string) => void;
}

// Member cards with avatar, role, status
// Activity indicator (working/idle/blocked/offline)
// Skills and capabilities tags
// Last active timestamp
// Remove/exclude buttons
// Invite button
```

### 8.2 Bot Activity Dashboard

**Frontend Changes**:

```tsx
// frontend/src/components/sections/GroupBotDashboard.tsx

interface GroupBotDashboardProps {
  roomId: string;
  activity: Record<string, AgentActivity>;
  claims: WorkClaim[];
}

// Per-bot activity cards
// Current task/claim
// Activity timeline
// Performance metrics (tasks completed, avg response time)
// Health status
// Kill switch / pause button
```

### 8.3 Bot Invite Flow

**Backend Changes**:

```python
@router.post("/api/groups/{name}/invite")
async def invite_bot_to_group(
    name: str,
    bot_name: str,
    role: str | None = None,              # Optional role override
    message: str | None = None,           # Invitation message
) -> GroupRoom:
    """Invite a bot to join a group."""
    # Check if bot exists
    # Check if bot is already a member
    # Add to direct members
    # Create notification for bot
    # Return updated room
```

### 8.4 Bot Performance Metrics

**Backend Changes**:

```python
# backend/packages/harness/alpha/groups/metrics.py

@dataclass
class BotMetrics:
    bot_name: str
    room_id: str
    messages_sent: int = 0
    messages_received: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    avg_response_time_seconds: float = 0.0
    last_active: str | None = None
    uptime_seconds: int = 0
    claims_created: int = 0
    claims_completed: int = 0

class MetricsCollector:
    def record_message(self, room_id: str, sender: str) -> None: ...
    def record_task_complete(self, room_id: str, bot_name: str, duration: float) -> None: ...
    def record_task_fail(self, room_id: str, bot_name: str) -> None: ...
    def get_metrics(self, room_id: str, bot_name: str | None = None) -> list[BotMetrics]: ...
```

---

## 9. Phase 6: Group Purpose & Project Linking

### 9.1 Project Linking

**Backend Changes**:

```python
# backend/packages/harness/alpha/groups/projects.py

@dataclass
class ProjectLink:
    room_id: str
    project_id: str
    project_name: str
    project_type: str                     # "kanban", "external", "custom"
    linked_at: str
    linked_by: str | None = None

class ProjectLinkStore:
    def link(self, room_id: str, project_id: str, project_name: str, project_type: str) -> ProjectLink: ...
    def unlink(self, room_id: str, project_id: str) -> None: ...
    def get_link(self, room_id: str) -> ProjectLink | None: ...
    def list_by_project(self, project_id: str) -> list[ProjectLink]: ...
```

**API Changes**:

```python
@router.post("/api/groups/{name}/project-link")
async def link_project(
    name: str,
    project_id: str,
    project_name: str,
    project_type: str = "kanban",
) -> ProjectLink:
    """Link a group to a project."""

@router.delete("/api/groups/{name}/project-link/{project_id}")
async def unlink_project(name: str, project_id: str) -> None:
    """Remove project link from a group."""

@router.get("/api/groups/{name}/project-link")
async def get_project_link(name: str) -> ProjectLink | None:
    """Get project link for a group."""
```

### 9.2 Purpose Declaration

**Backend Changes**:

```python
@dataclass
class GroupRoom:
    # ... existing fields ...
    purpose: str | None = None             # Short purpose statement
    goals: list[str] = field(default_factory=list)  # Group goals
    success_criteria: list[str] = field(default_factory=list)  # How to measure success
    target_date: str | None = None         # Target completion date
```

### 9.3 Goal Tracking

**Backend Changes**:

```python
# backend/packages/harness/alpha/groups/goals.py

@dataclass
class GroupGoal:
    goal_id: str
    room_id: str
    title: str
    description: str
    status: str                          # "pending", "in_progress", "completed", "blocked"
    progress: int = 0                    # 0-100
    created_at: str | None = None
    completed_at: str | None = None
    created_by: str | None = None

class GoalStore:
    def create(self, room_id: str, title: str, description: str) -> GroupGoal: ...
    def update_status(self, goal_id: str, status: str) -> GroupGoal: ...
    def update_progress(self, goal_id: str, progress: int) -> GroupGoal: ...
    def list_goals(self, room_id: str) -> list[GroupGoal]: ...
    def delete_goal(self, goal_id: str) -> None: ...
```

**API Changes**:

```python
@router.post("/api/groups/{name}/goals")
async def create_goal(name: str, goal: GoalCreate) -> GroupGoal: ...

@router.patch("/api/groups/{name}/goals/{goal_id}")
async def update_goal(name: str, goal_id: str, update: GoalUpdate) -> GroupGoal: ...

@router.get("/api/groups/{name}/goals")
async def list_goals(name: str) -> list[GroupGoal]: ...

@router.delete("/api/groups/{name}/goals/{goal_id}")
async def delete_goal(name: str, goal_id: str) -> None: ...
```

### 9.4 Progress Reporting

**Backend Changes**:

```python
@router.get("/api/groups/{name}/progress")
async def get_group_progress(name: str) -> GroupProgress:
    """Get overall group progress report."""
    # Aggregate goal progress
    # Count completed vs total tasks
    # Calculate activity metrics
    # Generate summary
```

---

## 10. Phase 7: Real-Time Infrastructure

### 10.1 WebSocket Gateway

**Backend Changes**:

```python
# backend/app/gateway/websocket.py

class ConnectionManager:
    """Manages WebSocket connections for real-time updates."""

    async def connect(self, websocket: WebSocket, client_id: str) -> None:
        await websocket.accept()
        self.active_connections[client_id] = websocket

    async def disconnect(self, client_id: str) -> None:
        # Remove from all room subscriptions
        for room_id in list(self.room_subscriptions.keys()):
            self.room_subscriptions[room_id].discard(client_id)
        del self.active_connections[client_id]

    async def subscribe_to_room(self, client_id: str, room_id: str) -> None:
        if room_id not in self.room_subscriptions:
            self.room_subscriptions[room_id] = set()
        self.room_subscriptions[room_id].add(client_id)

    async def broadcast_to_room(self, room_id: str, event: str, data: dict) -> None:
        if room_id not in self.room_subscriptions:
            return
        message = {"event": event, "data": data, "timestamp": datetime.now().isoformat()}
        for client_id in self.room_subscriptions[room_id]:
            if client_id in self.active_connections:
                await self.active_connections[client_id].send_json(message)

    async def broadcast_event(self, event: str, data: dict) -> None:
        """Broadcast to all connected clients."""
        message = {"event": event, "data": data, "timestamp": datetime.now().isoformat()}
        for client_id, ws in self.active_connections.items():
            await ws.send_json(message)
```

### 10.2 SSE Streaming for Groups

**Backend Changes**:

```python
# backend/app/gateway/routers/groups.py

@router.get("/api/groups/{name}/events")
async def group_events(name: str):
    """SSE endpoint for real-time group events."""
    async def event_generator():
        queue = asyncio.Queue()
        await event_bus.subscribe(name, queue)
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            await event_bus.unsubscribe(name, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

### 10.3 Presence System

**Backend Changes**:

```python
# backend/packages/harness/alpha/groups/presence.py

@dataclass
class MemberPresence:
    room_id: str
    bot_name: str
    status: str                          # "online", "away", "busy", "offline"
    current_task: str | None = None
    last_seen: str | None = None
    connected_at: str | None = None

class PresenceStore:
    def __init__(self):
        self._presence: dict[str, dict[str, MemberPresence]] = {}  # room_id -> {bot_name -> presence}

    def update(self, room_id: str, bot_name: str, status: str, **kwargs) -> None: ...
    def get(self, room_id: str, bot_name: str) -> MemberPresence | None: ...
    def get_room_presence(self, room_id: str) -> list[MemberPresence]: ...
    def remove(self, room_id: str, bot_name: str) -> None: ...
    def heartbeat(self, room_id: str, bot_name: str) -> None: ...
```

### 10.4 Frontend Real-Time Client

**Frontend Changes**:

```tsx
// frontend/src/lib/group-events.ts

class GroupEventClient {
  private eventSource: EventSource | null = null;
  private handlers: Map<string, Set<Function>> = new Map();

  connect(roomId: string): void {
    this.eventSource = new EventSource(`/api/groups/${roomId}/events`);
    this.eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);
      this.dispatch(data);
    };
  }

  on(event: string, handler: Function): void {
    if (!this.handlers.has(event)) {
      this.handlers.set(event, new Set());
    }
    this.handlers.get(event)!.add(handler);
  }

  off(event: string, handler: Function): void {
    this.handlers.get(event)?.delete(handler);
  }

  private dispatch(data: any): void {
    const handlers = this.handlers.get(data.event);
    if (handlers) {
      handlers.forEach((h) => h(data.data));
    }
  }

  disconnect(): void {
    this.eventSource?.close();
  }
}

// Usage in React
useEffect(() => {
  const client = new GroupEventClient();
  client.connect(roomId);
  client.on("message", (msg) => setMessages((prev) => [...prev, msg]));
  client.on("activity", (act) => setActivity(act));
  client.on("typing", (t) => setTyping(t));
  return () => client.disconnect();
}, [roomId]);
```

---

## 11. Phase 8: Advanced UI/UX

### 11.1 Modern Chat Interface

**Frontend Changes**:

```tsx
// frontend/src/components/sections/GroupChatView.tsx

interface GroupChatViewProps {
  room: GroupRoom;
  messages: GroupMessage[];
  roster: ResolvedRoster;
  activity: Record<string, AgentActivity>;
  typing: TypingIndicator[];
  onSendMessage: (content: string, options?: MessageOptions) => Promise<void>;
  onEditMessage: (messageId: string, content: string) => Promise<void>;
  onDeleteMessage: (messageId: string) => Promise<void>;
  onReact: (messageId: string, emoji: string) => Promise<void>;
  onPin: (messageId: string) => Promise<void>;
}

// Message list with:
// - Avatar per sender
// - Timestamp with relative time
// - Intent badges (proposal, vote, action, etc.)
// - Reply threading indicator
// - Reaction display
// - Attachment previews
// - Edited/deleted indicators
// - Pinned indicator
// - Read receipts

// Composer with:
// - Rich text toolbar (bold, italic, code, link)
// - @mention autocomplete
// - File attachment button
// - Intent selector
// - Reply preview
// - Send button with keyboard shortcut (Ctrl+Enter)
```

### 11.2 Group Settings Panel

**Frontend Changes**:

```tsx
// frontend/src/components/sections/GroupSettingsPanel.tsx

interface GroupSettingsPanelProps {
  room: GroupRoom;
  onUpdate: (settings: GroupSettings) => Promise<void>;
}

// Tabs:
// - General (name, description, purpose, category, tags)
// - Identity (avatar, banner, color)
// - Links (add/remove/reorder links)
// - Members (invite, remove, exclude, rules)
// - Orchestration (mode, moderator, quorum settings)
// - Policy (relay inbound/outbound, max_hop, authority parent)
// - Notifications (preferences, quiet hours)
// - Danger Zone (archive, dissolve, delete)
```

### 11.3 Member Management Panel

**Frontend Changes**:

```tsx
// frontend/src/components/sections/GroupMemberPanel.tsx

interface GroupMemberPanelProps {
  roster: ResolvedRoster;
  onInvite: (botName: string) => Promise<void>;
  onRemove: (botName: string) => Promise<void>;
  onExclude: (botName: string) => Promise<void>;
  onAddRule: (rule: MembershipRule) => Promise<void>;
  onRemoveRule: (ruleId: string) => Promise<void>;
}

// Sections:
// - Direct Members (with remove button)
// - Rule-Matched Members (with rule reference)
// - Inherited Members (with source parent)
// - Excluded Members (with re-include button)
// - Invite Bot (search + select)
// - Membership Rules (list + create + delete)
```

### 11.4 Search Interface

**Frontend Changes**:

```tsx
// frontend/src/components/sections/GroupSearch.tsx

interface GroupSearchProps {
  onSearch: (query: string, filters: SearchFilters) => Promise<SearchResult[]>;
}

// Search bar with:
// - Full-text query input
// - Filters (sender, intent, date range, has attachments)
// - Search scope (this group, all groups)
// - Results with highlights
// - Click to navigate to message
```

### 11.5 Analytics Dashboard

**Frontend Changes**:

```tsx
// frontend/src/components/sections/GroupAnalytics.tsx

interface GroupAnalyticsProps {
  roomId: string;
  metrics: GroupMetrics;
}

// Visualizations:
// - Message volume over time (line chart)
// - Participation by member (bar chart)
// - Intent distribution (pie chart)
// - Response time distribution (histogram)
// - Activity heatmap (calendar view)
// - Goal progress (progress bars)
// - Member leaderboard
```

### 11.6 Mobile-Responsive Design

**Frontend Changes**:

```tsx
// Responsive layout with:
// - Collapsible sidebar on mobile
// - Bottom navigation for groups
// - Swipe gestures for message actions
// - Touch-friendly composer
// - Optimized attachment viewer
// - Pull-to-refresh for messages
```

---

## 12. Phase 9: Analytics & Insights

### 12.1 Participation Metrics

**Backend Changes**:

```python
# backend/packages/harness/alpha/groups/analytics.py

@dataclass
class GroupAnalytics:
    room_id: str
    period_start: str
    period_end: str
    total_messages: int = 0
    unique_senders: int = 0
    messages_per_sender: dict[str, int] = field(default_factory=dict)
    intent_distribution: dict[str, int] = field(default_factory=dict)
    avg_response_time_seconds: float = 0.0
    peak_activity_hour: int | None = None
    most_active_member: str | None = None
    least_active_member: str | None = None
    total_reactions: int = 0
    total_replies: int = 0
    total_attachments: int = 0

class AnalyticsEngine:
    def compute(self, room_id: str, period_start: str, period_end: str) -> GroupAnalytics: ...
    def get_trend(self, room_id: str, days: int = 30) -> list[GroupAnalytics]: ...
    def compare_rooms(self, room_ids: list[str], period: str) -> list[GroupAnalytics]: ...
```

### 12.2 Group Health Score

**Backend Changes**:

```python
@dataclass
class GroupHealthScore:
    room_id: str
    overall: float                       # 0-100
    activity_score: float                # Based on message volume
    participation_score: float           # Based on unique senders
    response_score: float                # Based on response times
    completion_score: float              # Based on goal completion
    collaboration_score: float           # Based on replies, reactions, mentions
    computed_at: str | None = None

class HealthScoreEngine:
    def compute(self, room_id: str) -> GroupHealthScore: ...
    def get_trend(self, room_id: str, days: int = 30) -> list[GroupHealthScore]: ...
```

### 12.3 Activity Heatmap

**Backend Changes**:

```python
@router.get("/api/groups/{name}/heatmap")
async def get_activity_heatmap(
    name: str,
    days: int = 30,
) -> list[HeatmapDay]:
    """Get activity heatmap data."""
    # Returns daily message counts for calendar visualization
```

---

## 13. Phase 10: Integrations & Extensibility

### 13.1 Webhook Outbound

**Backend Changes**:

```python
# backend/packages/harness/alpha/integrations/webhooks.py

@dataclass
class Webhook:
    webhook_id: str
    url: str
    events: list[str]                    # ["message", "mention", "activity", "run"]
    secret: str | None = None            # HMAC secret
    active: bool = True
    created_at: str | None = None

class WebhookStore:
    def create(self, url: str, events: list[str], secret: str | None = None) -> Webhook: ...
    def list(self) -> list[Webhook]: ...
    def delete(self, webhook_id: str) -> None: ...
    def trigger(self, event: str, data: dict) -> None: ...
```

### 13.2 Email Notifications

**Backend Changes**:

```python
# backend/packages/harness/alpha/integrations/email.py

class EmailNotifier:
    def __init__(self, smtp_host: str, smtp_port: int, username: str, password: str):
        ...

    async def send_notification(
        self,
        to: str,
        subject: str,
        body: str,
        html: str | None = None,
    ) -> None: ...

    async def send_digest(
        self,
        to: str,
        notifications: list[Notification],
    ) -> None: ...
```

### 13.3 Plugin System

**Backend Changes**:

```python
# backend/packages/harness/alpha/integrations/plugins.py

class PluginHook:
    """Hook points for plugins."""
    ON_MESSAGE_POSTED = "on_message_posted"
    ON_MENTION = "on_mention"
    ON_ACTIVITY_CHANGE = "on_activity_change"
    ON_RUN_STARTED = "on_run_started"
    ON_RUN_COMPLETED = "on_run_completed"
    ON_MEMBER_JOINED = "on_member_joined"
    ON_MEMBER_LEFT = "on_member_left"

class PluginManager:
    def register(self, plugin: Plugin) -> None: ...
    def unregister(self, plugin_id: str) -> None: ...
    async def trigger(self, hook: str, context: dict) -> None: ...
```

---

## 14. Implementation Roadmap

### Phase 1: Foundation (Weeks 1-2)
- [ ] Group profile models (avatar, description, links, tags)
- [ ] Group templates
- [ ] Basic group settings API
- [ ] Frontend group profile editor

### Phase 2: Messaging (Weeks 3-4)
- [ ] Rich text composer
- [ ] Message threading
- [ ] File attachments
- [ ] Message pinning
- [ ] Message search
- [ ] Read receipts
- [ ] Typing indicators

### Phase 3: Notifications (Weeks 5-6)
- [ ] WebSocket gateway
- [ ] Notification model and store
- [ ] Notification triggers
- [ ] Desktop notifications
- [ ] Sound alerts
- [ ] Notification preferences
- [ ] Notification history UI

### Phase 4: Subgroups (Weeks 7-8)
- [ ] Visual tree editor
- [ ] Drag-and-drop reordering
- [ ] Group cloning
- [ ] Settings inheritance
- [ ] Subgroup templates

### Phase 5: Bot Management (Weeks 9-10)
- [ ] Member list with profiles
- [ ] Bot activity dashboard
- [ ] Bot invite flow
- [ ] Bot performance metrics

### Phase 6: Projects & Goals (Weeks 11-12)
- [ ] Project linking
- [ ] Purpose declaration
- [ ] Goal tracking
- [ ] Progress reporting

### Phase 7: Real-Time (Weeks 13-14)
- [ ] WebSocket gateway (full)
- [ ] SSE streaming
- [ ] Presence system
- [ ] Frontend real-time client

### Phase 8: UI/UX (Weeks 15-16)
- [ ] Modern chat interface
- [ ] Group settings panel
- [ ] Member management panel
- [ ] Search interface
- [ ] Analytics dashboard
- [ ] Mobile-responsive design

### Phase 9: Analytics (Weeks 17-18)
- [ ] Participation metrics
- [ ] Group health score
- [ ] Activity heatmap
- [ ] Trend analysis

### Phase 10: Integrations (Weeks 19-20)
- [ ] Webhook outbound
- [ ] Email notifications
- [ ] Plugin system
- [ ] API extensions

---

## 15. Technical Architecture

### 15.1 Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend (Next.js)                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │ Chat UI  │  │ Tree UI  │  │ Settings │  │ Notifications│   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬───────┘   │
│       │              │              │               │           │
│       └──────────────┴──────────────┴───────────────┘           │
│                              │                                   │
│                    ┌─────────┴─────────┐                        │
│                    │  Real-Time Client  │                        │
│                    │  (WebSocket/SSE)   │                        │
│                    └─────────┬─────────┘                        │
└──────────────────────────────┼──────────────────────────────────┘
                               │
┌──────────────────────────────┼──────────────────────────────────┐
│                    Gateway (FastAPI)                             │
│  ┌──────────┐  ┌──────────┐  │  ┌──────────┐  ┌────────────┐  │
│  │ Groups   │  │ WebSocket│  │  │ Notifs   │  │ Analytics  │  │
│  │ Router   │  │ Router   │  │  │ Router   │  │ Router     │  │
│  └────┬─────┘  └────┬─────┘  │  └────┬─────┘  └─────┬──────┘  │
│       │              │        │       │              │          │
│       └──────────────┴────────┴───────┴──────────────┘          │
│                              │                                   │
│                    ┌─────────┴─────────┐                        │
│                    │   Event Bus        │                        │
│                    └─────────┬─────────┘                        │
└──────────────────────────────┼──────────────────────────────────┘
                               │
┌──────────────────────────────┼──────────────────────────────────┐
│                    Harness (Python)                              │
│  ┌──────────┐  ┌──────────┐  │  ┌──────────┐  ┌────────────┐  │
│  │ Groups   │  │ Notifs   │  │  │ Presence │  │ Analytics  │  │
│  │ Service  │  │ Service  │  │  │ Service  │  │ Engine     │  │
│  └────┬─────┘  └────┬─────┘  │  └────┬─────┘  └─────┬──────┘  │
│       │              │        │       │              │          │
│       └──────────────┴────────┴───────┴──────────────┘          │
│                              │                                   │
│                    ┌─────────┴─────────┐                        │
│                    │   Storage Layer    │                        │
│                    │  (JSON + Files)    │                        │
│                    └───────────────────┘                        │
└─────────────────────────────────────────────────────────────────┘
```

### 15.2 Storage Layout

```
ALPHA_HOME/
├── groups/
│   ├── {room_id}/
│   │   ├── room.json              # Group metadata
│   │   ├── messages.jsonl         # Message log
│   │   ├── roster.json            # Membership rules
│   │   ├── links.json             # Group links
│   │   ├── goals.json             # Group goals
│   │   ├── claims.json            # Work claims
│   │   ├── activity.json          # Activity ledger
│   │   ├── receipts.json          # Read receipts
│   │   ├── scheduled.json         # Scheduled messages
│   │   ├── attachments/           # File attachments
│   │   │   └── {attachment_id}/
│   │   │       ├── file
│   │   │       └── meta.json
│   │   └── exports/               # Exported transcripts
│   │       └── {timestamp}.{format}
│   └── ...
├── notifications/
│   ├── {user_id}/
│   │   ├── notifications.json     # Notification history
│   │   └── preferences.json       # User preferences
│   └── ...
├── analytics/
│   ├── {room_id}/
│   │   ├── metrics.json           # Computed metrics
│   │   └── health.json            # Health scores
│   └── ...
└── webhooks/
    └── webhooks.json              # Webhook configurations
```

### 15.3 API Endpoint Summary

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/groups` | List all groups |
| POST | `/api/groups` | Create group |
| GET | `/api/groups/tree` | Get group forest |
| GET | `/api/groups/{name}` | Get group details |
| PATCH | `/api/groups/{name}/profile` | Update group profile |
| PATCH | `/api/groups/{name}/description` | Update description |
| POST | `/api/groups/{name}/avatar` | Upload avatar |
| POST | `/api/groups/{name}/links` | Add link |
| DELETE | `/api/groups/{name}/links/{id}` | Remove link |
| POST | `/api/groups/{name}/subgroups` | Create subgroup |
| GET | `/api/groups/{name}/children` | List children |
| GET | `/api/groups/{name}/roster` | Get roster |
| POST | `/api/groups/{name}/members` | Add member |
| DELETE | `/api/groups/{name}/members/{bot}` | Remove member |
| POST | `/api/groups/{name}/invite` | Invite bot |
| GET | `/api/groups/{name}/messages` | List messages |
| POST | `/api/groups/{name}/messages` | Post message |
| PATCH | `/api/groups/{name}/messages/{id}` | Edit message |
| DELETE | `/api/groups/{name}/messages/{id}` | Delete message |
| POST | `/api/groups/{name}/messages/{id}/pin` | Pin message |
| GET | `/api/groups/{name}/messages/{id}/thread` | Get thread |
| GET | `/api/groups/{name}/search` | Search messages |
| GET | `/api/groups/{name}/pinned` | List pinned |
| POST | `/api/groups/{name}/messages/{id}/read` | Mark read |
| GET | `/api/groups/{name}/unread` | Get unread |
| POST | `/api/groups/{name}/typing` | Set typing |
| GET | `/api/groups/{name}/typing` | Get typing |
| POST | `/api/groups/{name}/scheduled` | Schedule message |
| GET | `/api/groups/{name}/scheduled` | List scheduled |
| GET | `/api/groups/{name}/export` | Export messages |
| GET | `/api/groups/{name}/activity` | Get activity |
| GET | `/api/groups/{name}/heatmap` | Get heatmap |
| GET | `/api/groups/{name}/progress` | Get progress |
| POST | `/api/groups/{name}/goals` | Create goal |
| GET | `/api/groups/{name}/goals` | List goals |
| POST | `/api/groups/{name}/project-link` | Link project |
| GET | `/api/groups/{name}/project-link` | Get project link |
| GET | `/api/groups/{name}/events` | SSE stream |
| WS | `/api/ws` | WebSocket |
| GET | `/api/notifications` | List notifications |
| POST | `/api/notifications/{id}/read` | Mark read |
| GET | `/api/notifications/preferences` | Get preferences |
| PATCH | `/api/notifications/preferences` | Update preferences |
| GET | `/api/analytics/{name}` | Get analytics |
| GET | `/api/analytics/{name}/health` | Get health score |

---

## 16. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| WebSocket scaling | Medium | High | Start with SSE, add WS later; use connection pooling |
| File storage growth | Medium | Medium | Implement retention policy; compress old attachments |
| Message search performance | Low | Medium | Use SQLite FTS5 for indexing; limit search scope |
| Notification fatigue | High | Medium | Implement quiet hours, digest mode, granular preferences |
| Group tree complexity | Medium | Medium | MAX_DEPTH=4 already enforced; visual editor helps |
| Real-time sync conflicts | Low | High | Single-process contract; last-write-wins with timestamps |
| Mobile performance | Medium | Medium | Lazy load messages; virtualize long lists |
| Plugin security | Medium | High | Sandboxed execution; permission model; audit logging |

---

## Appendix A: Database Schema (JSON)

### GroupRoom (Extended)

```json
{
  "room_id": "engineering-team",
  "name": "Engineering Team",
  "description": "Core engineering team for product development",
  "purpose": "Build and maintain the core product",
  "goals": ["Ship v2.0", "Reduce bug count by 50%"],
  "success_criteria": ["All tests passing", "Zero critical bugs"],
  "target_date": "2026-12-31",
  "avatar_url": "/groups/engineering-team/avatar.png",
  "banner_url": "/groups/engineering-team/banner.png",
  "avatar_color": "#4A90D9",
  "tags": ["engineering", "core", "product"],
  "category": "engineering",
  "members": ["architect", "coder", "reviewer", "tester"],
  "mode": "moderated",
  "moderator": "architect",
  "kanban_board_id": null,
  "project_id": "proj-123",
  "log": true,
  "parent_ids": [],
  "summary": "Engineering team for core product",
  "lifecycle": "active",
  "created_by": "user-1",
  "created_at": "2026-10-01T10:00:00Z",
  "inherit_settings": true,
  "inherited_from": null
}
```

### GroupMessage (Extended)

```json
{
  "id": "msg-123",
  "sender": "architect",
  "content": "Let's discuss the new architecture proposal",
  "content_type": "markdown",
  "intent": "proposal",
  "mentions": ["coder", "reviewer"],
  "metadata": {},
  "created_at": "2026-10-03T14:30:00Z",
  "edited_at": null,
  "deleted": false,
  "reactions": {"👍": ["coder"], "🤔": ["reviewer"]},
  "reply_to": null,
  "forwarded_from": null,
  "thread_id": null,
  "thread_position": 0,
  "reply_count": 0,
  "last_reply_at": null,
  "pinned": false,
  "pinned_at": null,
  "pinned_by": null,
  "attachments": []
}
```

### Notification

```json
{
  "notification_id": "notif-456",
  "type": "mention",
  "title": "You were mentioned in Engineering Team",
  "body": "Let's discuss the new architecture proposal",
  "room_id": "engineering-team",
  "message_id": "msg-123",
  "sender": "architect",
  "priority": "high",
  "created_at": "2026-10-03T14:30:00Z",
  "read": false,
  "read_at": null,
  "action_url": "/groups/engineering-team/messages/msg-123",
  "icon": "mention",
  "sound": true
}
```

---

## Appendix B: Frontend Component Tree

```
GroupChatPage
├── GroupHeader
│   ├── GroupAvatar
│   ├── GroupName
│   ├── GroupStatus (member count, activity)
│   └── GroupActions (settings, export, search)
├── GroupSidebar
│   ├── GroupTreeEditor
│   │   ├── TreeNode (recursive)
│   │   └── DragDropContext
│   ├── GroupMemberList
│   │   ├── MemberCard
│   │   └── InviteButton
│   └── GroupLinks
│       └── LinkCard
├── GroupMainContent
│   ├── MessageList
│   │   ├── MessageItem
│   │   │   ├── MessageAvatar
│   │   │   ├── MessageContent
│   │   │   ├── MessageActions (reply, react, pin, edit, delete)
│   │   │   ├── MessageReactions
│   │   │   ├── MessageAttachments
│   │   │   └── ThreadReplies
│   │   └── MessageDivider
│   ├── MessageComposer
│   │   ├── RichTextEditor
│   │   ├── MentionAutocomplete
│   │   ├── AttachmentUpload
│   │   ├── IntentSelector
│   │   └── SendButton
│   └── PinnedMessages
├── GroupRightPanel (collapsible)
│   ├── GroupActivity
│   ├── GroupClaims
│   ├── GroupGoals
│   └── GroupAnalytics
└── NotificationPanel
    ├── NotificationItem
    └── NotificationSettings
```

---

*This plan is a living document. Update as implementation progresses.*
