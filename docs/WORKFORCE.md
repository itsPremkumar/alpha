# Workforce Layer Documentation

## Overview

The Workforce layer adds multi-agent collaboration, project management, and continuous execution capabilities to Alpha. It transforms Alpha from a single-agent system into a collaborative AI workforce.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
                        Workforce Layer
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │   Bots      │  │  Projects   │  │   Teams     │             │
│  │  (Agents)   │  │  (Workspaces)│  │ (Group Chat)│             │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘             │
│         │                │                │                     │
│         └────────────────┼────────────────┘                     │
│                          ▼                                      │
│              ┌─────────────────────┐                            │
│              │  Workforce Engine   │                            │
│              │  (Harness Layer)    │                            │
│              └──────────┬──────────┘                            │
│                         │                                       │
│        ┌────────────────┼────────────────┐                      │
│        ▼               ▼               ▼                         │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                    │
│  │ Memory   │   │  Tools   │   │ Scheduler│                    │
│  │ Systems  │   │  Skills  │   │  Cron    │                    │
│  └──────────┘   └──────────┘   └──────────┘                    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Core Concepts

### Bots (Agents)
- **Definition**: Specialized AI agents with distinct roles, skills, and personalities
- **Identity**: Unique name, avatar, system prompt, skill set
- **Capabilities**: Can be messaged directly (DM), added to teams, assigned to projects
- **Persistence**: State stored in database, survives restarts

### Projects
- **Definition**: Collaborative workspaces with shared context, resources, and goals
- **Membership**: Bots and humans can be members
- **Resources**: Files, context, locks, constitution (rules)
- **Memory**: Project-scoped knowledge graph, decisions (ADRs), evidence

### Teams (Group Chat)
- **Definition**: Multi-bot conversations with a moderator
- **Execution**: Parallel bot execution, moderator synthesis
- **Use case**: Complex tasks requiring diverse expertise

## Bots System

### Bot Structure
```python
# Bot configuration (stored in database)
{
    "name": "researcher",
    "display_name": "Research Bot",
    "avatar": "🔬",
    "system_prompt": "You are a research specialist...",
    "description": "Deep research and analysis",
    "skills": ["web-search", "academic-search", "data-analysis"],
    "model": "primary",
    "config": {
        "temperature": 0.3,
        "max_tokens": 8192
    },
    "metadata": {
        "department": "research",
        "seniority": "senior"
    }
}
```

### Bot Roster
- **Global registry** of all available bots
- **Dynamic loading** from configuration
- **Health monitoring** - track last active, error rate
- **Presence** - online/offline/busy status

### Bot DMs (Direct Messages)
```
POST /api/bots/{bot_name}/dm
{
    "content": "Research quantum computing advances",
    "thread_id": "optional-existing-thread",
    "context": {"priority": "high"}
}
```
- **Fire-and-forget** - returns immediately with thread ID
- **Server-side attribution** - messages marked as from bot
- **Per-bot inbox** - `GET /api/bots/{bot_name}/inbox`
- **SOUL protocol** - Custom SOULs remain DM-capable

### Bot Profiles
- **Gallery view** - Browse available bots
- **Detail panel** - Skills, performance, recent work
- **Active picker** - Select bots for tasks/teams

## Projects System

### Project Structure
```python
{
    "id": "proj-uuid",
    "name": "Website Redesign",
    "description": "Complete redesign of company website",
    "constitution": "## Project Rules\n1. Mobile-first\n2. Accessibility WCAG 2.1\n3. Performance budget: <3s load",
    "owner_id": "user-uuid",
    "created_at": "2026-09-17T10:00:00Z",
    "updated_at": "2026-09-17T15:30:00Z",
    "status": "active",
    "metadata": {}
}
```

### Project Membership
```python
# Add member (bot or human)
POST /api/projects/{project_id}/members
{
    "member_type": "bot",  # or "user"
    "member_id": "researcher",  # bot name or user ID
    "role": "member"  # owner, admin, member, viewer
}

# Roles:
# - owner: Full control, can delete project
# - admin: Manage members, settings
# - member: Read/write access
# - viewer: Read-only
```

### Resource Locks
```python
# Acquire lock
POST /api/projects/{project_id}/locks
{
    "resource": "design-system.figma",
    "lock_type": "exclusive",  # or "shared"
    "ttl_seconds": 3600,
    "reason": "Updating color palette"
}

# Lock types:
# - exclusive: Only holder can access
# - shared: Multiple readers, exclusive writer
# - Automatic expiry via TTL
# - Manual release or auto on TTL expiry
```

### Project Constitution
- **Markdown document** defining project rules
- **Injected into context** for all project members
- **Version controlled** - changes tracked as events
- **Enforced by convention** - bots reference in decisions

### Context Files
```python
# Add context file
POST /api/projects/{project_id}/context
{
    "name": "brand-guidelines.pdf",
    "content": "base64-encoded-content",
    "mime_type": "application/pdf",
    "tags": ["brand", "design"]
}

# Context automatically included in bot prompts when working in project
```

### Decisions (ADRs - Architecture Decision Records)
```python
# Record decision
POST /api/projects/{project_id}/decisions
{
    "title": "Use React for frontend",
    "status": "accepted",  # proposed, accepted, rejected, superseded
    "context": "Need modern frontend framework",
    "decision": "React 18 with TypeScript",
    "consequences": "Team needs React training",
    "tags": ["frontend", "architecture"]
}
```

### Evidence System
```python
# Submit evidence for task completion
POST /api/projects/{project_id}/evidence
{
    "task_id": "task-uuid",
    "type": "file",  # file, url, test_result, screenshot
    "content": "base64-or-url",
    "description": "Accessibility audit results",
    "verdict": "pass"  # pass, fail, partial
}

# Evidence-gated completion: tasks require evidence before marking done
```

### Project Events
```python
# Event stream for real-time updates
GET /api/projects/{project_id}/events?since=timestamp

# Event types:
# - member_added, member_removed
# - lock_acquired, lock_released
# - context_added, context_removed
# - decision_created, decision_updated
# - evidence_submitted
# - task_created, task_updated, task_completed
```

### Handoffs
```python
# Create handoff between members
POST /api/projects/{project_id}/handoffs
{
    "from_member": "researcher",
    "to_member": "designer",
    "summary": "Research complete, ready for design phase",
    "context": {"key_findings": [...], "assets": [...]},
    "due_date": "2026-09-20T17:00:00Z"
}
```

## Teams (Group Chat)

### Team Structure
```python
{
    "name": "product-team",
    "display_name": "Product Team",
    "members": ["product-manager", "designer", "engineer", "qa"],
    "moderator": "product-manager",
    "description": "Cross-functional product team",
    "created_at": "2026-09-17T10:00:00Z"
}
```

### Team Execution
```python
# Run team on objective
POST /api/groups/{group_name}/runs
{
    "objective": "Design and specify user dashboard",
    "context": {"project_id": "proj-uuid", "deadline": "2026-09-30"},
    "config": {
        "max_rounds": 5,
        "parallel_execution": true
    }
}
```

### Team Run Lifecycle
1. **Moderator analyzes** objective, creates plan
2. **Parallel execution** - each member works on their slice
3. **Moderator reviews** outputs, requests revisions if needed
4. **Synthesis** - moderator merges into final deliverable
5. **Completion** - final output posted to thread

### Team Run Streaming
```http
GET /api/groups/{group_name}/runs/{run_id}/stream

Events:
- round_started: {round: 1, assignments: {...}}
- member_message: {member: "designer", content: "..."}
- tool_call: {member: "engineer", tool: "write_file", ...}
- round_completed: {round: 1, outputs: {...}}
- revision_requested: {member: "qa", feedback: "..."}
- run_completed: {output: "Final specification..."}
```

## Collaborative Real-Time Kanban Board
*Tool: `kanban_board_tool`*

The shared Kanban board enables multi-agent teams and human operators to coordinate asynchronously across complex deliverables:
- **Columns**: `Backlog` → `To Do` → `In Progress` → `Review / Audit` → `Done`.
- **Card Lifecycle**:
  - `action="create_card"`: Generates new tasks with priority, description, and required evidence artifacts.
  - `action="assign_card"`: Assigns specific bots from the roster.
  - `action="move_card"`: Transitions cards across columns while updating execution logs.
  - `action="complete_card"`: Finalizes cards gated by empirical evidence submission.
- **Audit Trails**: Every transition records timestamp, responsible bot name, and artifact links.

## Agent-to-Agent (A2A) Messaging Protocol
*Tools: `a2a_tool`, `agent_message_tool`, `agent_observe_tool`*

Structured inter-agent protocol enabling distributed micro-teams:
- **Peer Delegation**: Agents dispatch bounded subtasks directly to peers without involving the lead orchestrator for every minor step.
- **Peer Observation**: Agents subscribe to and observe execution progress and intermediate thought vectors of collaborating agents.
- **Direct Mailboxes**: Dedicated inbox queues ensure no communication loss during high-concurrency turns.

## Autonomous Swarms & Dynamic Topologies
*Tool: `swarm_tool`*

For emergent, self-organizing problem solving:
- **Leader Election**: Evaluates bot agency and domain competence to elect an optimal swarm leader.
- **Dynamic Work Partitioning**: Breaks massive jobs (e.g. multi-repo migrations, vulnerability scanning) across worker swarms.
- **Barrier Synchronization**: Enforces phase gates where all swarm workers must complete their slice before merging into the final deliverable.

## Subagent Delegation with Intent Category Presets
*Tool: `task(category="...")`*

Delegation is guided by intent categories that configure model chains, turn budgets, and tool whitelists:
- **`general`**: Default identity preset with standard turn budgets.
- **`research`**: High-rigor multi-source investigation (`max_turns=100`).
- **`quick`**: Low-latency, terse execution for small tasks (`max_turns=30`).
- **`deep-research`**: Autonomous 5-pass research, recursive gap filling, contradiction detection, and publication-ready citations (`max_turns=150`).

## Workforce Frontend (WorkforceSection)

### Tabs
| Tab | Purpose |
|-----|---------|
| **Inbox** | Bot DMs, notifications, mentions |
| **Presence** | Bot status, availability, current tasks |
| **Curator** | Skill lifecycle, trust tiers, usage analytics |
| **Automation** | Scheduled tasks, cron jobs, workflows |
| **Oversight** | Quality councils, review queues, audits |
| **Insights** | Productivity metrics, token usage, costs |

### Inbox
- **Unified view** of all bot communications
- **Filters**: Unread, mentions, DMs, team messages
- **Actions**: Reply, delegate, archive, create task

### Presence
- **Real-time status** via WebSocket
- **Bot cards** showing current task, load, health
- **Availability scheduling** - set office hours

### Curator
- **Skill tiers**: Quarantine → Reviewed → Trusted → Core
- **Usage analytics**: Calls, success rate, latency, feedback
- **Recommendations**: Promote, archive, investigate
- **Lifecycle**: Auto-archive after 90 days inactive

### Automation
- **Scheduled tasks**: Cron-based background runs
- **Blueprints**: Reusable task templates
- **Incidents**: Failed runs, auto-pause, alerting
- **Wake gate**: Preflight checks before execution

### Oversight
- **Quality councils**: Multi-deliberator review
- **Review queues**: Pending reviews, SLAs
- **Evidence tracking**: Task completion verification
- **Audit logs**: Compliance reporting

### Insights
- **Bot productivity**: Tasks completed, token efficiency
- **Project health**: Velocity, blockers, cycle time
- **Cost analysis**: Per-bot, per-project, per-model
- **Skill ROI**: Usage vs. maintenance cost

## API Reference

### Bots
```
GET    /api/bots                    # List all bots
GET    /api/bots/{name}             # Get bot details
POST   /api/bots/{name}/dm          # Send DM to bot
GET    /api/bots/{name}/inbox       # Get bot inbox
POST   /api/bots/{name}/chat        # Interactive chat
GET    /api/bots/{name}/presence    # Get presence status
```

### Projects
```
GET    /api/projects                # List projects
POST   /api/projects                # Create project
GET    /api/projects/{id}           # Get project
PATCH  /api/projects/{id}           # Update project
DELETE /api/projects/{id}           # Delete project

GET    /api/projects/{id}/members   # List members
POST   /api/projects/{id}/members   # Add member
DELETE /api/projects/{id}/members/{member_id}  # Remove member

GET    /api/projects/{id}/locks     # List locks
POST   /api/projects/{id}/locks     # Acquire lock
DELETE /api/projects/{id}/locks/{lock_id}      # Release lock

GET    /api/projects/{id}/context   # List context files
POST   /api/projects/{id}/context   # Add context
DELETE /api/projects/{id}/context/{file_id}    # Remove context

GET    /api/projects/{id}/decisions # List decisions
POST   /api/projects/{id}/decisions # Create decision

GET    /api/projects/{id}/evidence  # List evidence
POST   /api/projects/{id}/evidence  # Submit evidence

GET    /api/projects/{id}/events    # Event stream
GET    /api/projects/{id}/handoffs  # List handoffs
POST   /api/projects/{id}/handoffs  # Create handoff
```

### Teams (Groups)
```
GET    /api/groups                  # List teams
POST   /api/groups                  # Create team
GET    /api/groups/{name}           # Get team
POST   /api/groups/{name}/runs      # Run team
GET    /api/groups/{name}/runs/{run_id}  # Get run status
GET    /api/groups/{name}/runs/{run_id}/stream  # Stream run
```

## Configuration

### Bot Configuration (config.yaml)
```yaml
bots:
  - name: "researcher"
    display_name: "Research Bot"
    avatar: "🔬"
    system_prompt: "You are a research specialist..."
    skills: ["web-search", "academic-search"]
    model: "primary"
    config:
      temperature: 0.3
  - name: "engineer"
    display_name: "Engineering Bot"
    avatar: "💻"
    system_prompt: "You are a senior software engineer..."
    skills: ["code-execution", "git-operations", "github-api"]
    model: "primary"
```

### Project Defaults
```yaml
projects:
  default_constitution: |
    ## Default Project Rules
    1. Document decisions as ADRs
    2. Require evidence for task completion
    3. Weekly sync on Fridays
  evidence_required: true
  lock_ttl_default: 3600
```

### Scheduler (for Automation tab)
```yaml
scheduler:
  enabled: true
  max_concurrent_runs: 10
  queue_timeout_seconds: 300
  wake_gate:
    enabled: true
    preflight_checks:
      - database_connectivity
      - model_availability
      - disk_space
```

## Frontend Integration

### Workforce Client (lib/workforce.ts)
```typescript
// Bot operations
export async function sendBotDM(botName: string, content: string): Promise<Thread>
export async function getBotInbox(botName: string): Promise<Message[]>
export async function getBotPresence(botName: string): Promise<BotPresence>

// Project operations
export async function createProject(data: CreateProjectRequest): Promise<Project>
export async function getProjectMembers(projectId: string): Promise<Member[]>
export async function acquireLock(projectId: string, resource: string): Promise<Lock>

// Team operations
export async function runTeam(groupName: string, objective: string): Promise<Run>
export async function streamTeamRun(groupName: string, runId: string): AsyncIterable<TeamEvent>
```

### WebSocket Events
```typescript
// Connect to workforce updates
const ws = new WebSocket('/api/ws/workforce');

ws.onmessage = (event) => {
  const update = JSON.parse(event.data);
  switch (update.type) {
    case 'bot_presence':
      // Bot status changed
      break;
    case 'project_event':
      // Project event (lock, member, decision)
      break;
    case 'team_run_update':
      // Team run progress
      break;
    case 'skill_recommendation':
      // Curator recommendation
      break;
  }
};
```

## Use Cases

### 1. Research Project
```
Project: "Q4 Market Analysis"
Members: researcher, analyst, writer
Flow:
1. Researcher gathers data (parallel web searches)
2. Analyst processes data (creates charts, finds trends)
3. Writer creates report (uses evidence from both)
4. Evidence submitted for each deliverable
5. Project constitution ensures quality standards
```

### 2. Feature Development
```
Project: "User Authentication Refactor"
Members: engineer, qa, designer, product-manager
Flow:
1. Product manager creates ADR for auth approach
2. Engineer implements (acquires lock on auth module)
3. Designer reviews UX flows (context: design system)
4. QA writes tests, submits evidence
5. Handoff to product manager for acceptance
```

### 3. Incident Response Team
```
Team: "incident-response"
Members: oncall-engineer, communications-lead, manager
Objective: "Investigate and resolve production outage"
Flow:
1. Moderator (manager) coordinates
2. Engineer investigates (parallel: logs, metrics, traces)
3. Communications drafts status updates
4. Synthesis: incident report + action items
```

### 4. Content Production Pipeline
```
Scheduled Task: "Daily Blog Post" (cron: 0 6 * * *)
Bot: writer
Flow:
1. Writer receives prompt with topic
2. Researches (web-search tool)
3. Drafts post
4. Submits for review (quality council)
5. Published on approval
```

## Best Practices

### Bot Design
1. **Single responsibility** - Each bot has clear domain
2. **Explicit skills** - Declare required skills in config
3. **Consistent personality** - System prompt defines behavior
4. **Observability** - Log decisions, tool usage, errors

### Project Management
1. **Living constitution** - Update as project evolves
2. **Evidence culture** - Require proof of completion
3. **Regular syncs** - Scheduled handoffs between phases
4. **Context hygiene** - Archive outdated context files

### Team Execution
1. **Clear objectives** - Specific, measurable, time-bound
2. **Right-sized teams** - 3-5 members optimal
3. **Moderator authority** - Final say on synthesis
4. **Parallel by default** - Maximize concurrency

### Automation
1. **Idempotent tasks** - Safe to retry
2. **Explicit preflight** - Wake gate checks
3. **Incident tracking** - Auto-pause on repeated failures
4. **Observability** - Logs, metrics, alerts

## Troubleshooting

### Bot Not Responding
```bash
# Check bot presence
curl /api/bots/researcher/presence

# Check bot inbox for stuck messages
curl /api/bots/researcher/inbox

# Restart bot (reload config)
docker compose restart gateway
```

### Project Lock Stuck
```bash
# List locks
curl /api/projects/proj-id/locks

# Force release (admin)
curl -X DELETE /api/projects/proj-id/locks/lock-id \
  -H "Authorization: Bearer <admin-token>"
```

### Team Run Hanging
```bash
# Check run status
curl /api/groups/team-name/runs/run-id

# Check for stuck member
# Look at stream events for last activity

# Cancel run
curl -X POST /api/groups/team-name/runs/run-id/cancel
```

### Performance Issues
- **Bot overload**: Reduce concurrent runs, add bot replicas
- **Context bloat**: Archive old context, use summaries
- **Lock contention**: Reduce lock scope, use shared locks
- **Token costs**: Set per-bot budgets, use cheaper models

## Migration Guide

### Enabling Workforce
1. Ensure `config.yaml` has bots configured
2. Run database migrations: `cd backend && make migrate-upgrade`
3. Restart gateway
4. Access Workforce tab in frontend

### From Single-Agent
- Existing threads work unchanged
- Add bots to projects for collaboration
- Use `/team` command for group chat
- Migrate ad-hoc coordination to projects