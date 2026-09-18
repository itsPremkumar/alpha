# Roo Code, Cline, and Goose: Open-Source IDE Extensions & Terminal Developer Harnesses

> **Classification:** Developer Tooling & Harness Engineering Reference  
> **Status:** Production Architecture Breakdown & Integration Blueprint  
> **Target System:** Alpha Desktop, Electron, and CLI Orchestration Harness  
> **Systems Analyzed:** Cline (formerly Claude Dev), Roo Code (fork of Cline), Goose (Block)  

---

## 1. Executive Summary

Autonomous coding agents have evolved along two primary deployment form factors:
1. **IDE-Integrated Extension Agents (Cline, Roo Code):** Living directly inside the developer's workspace (VS Code / Cursor), leveraging the editor's Language Server Protocol (LSP), active editor tabs, diff visualizers, and webviews.
2. **Terminal-Native Autonomous Harnesses (Goose by Block, Aider, Claude Code):** Running in the CLI with direct shell execution privileges, local process supervision, and extensible plugin architectures via protocols like the **Model Context Protocol (MCP)**.

This reference examines the architecture, execution loops, checkpoint/rollback mechanisms, and permission models of Cline, Roo Code, and Goose, extracting essential engineering patterns for the Alpha platform.

```
+-------------------------------------------------------------------------+
|                  Cline / Roo Code Extension Architecture                |
+-------------------------------------------------------------------------+
|                                                                         |
|  [VS Code Webview Panel]                                                |
|      | Chat UI, Diff Approvals, Mode Toggles (Architect, Code, Ask)    |
|      v                                                                  |
|  [Extension Host Controller]                                            |
|      |                                                                  |
|      +---> [LLM Gateway] (OpenAI, Anthropic, Ollama, OpenRouter)        |
|      +---> [Workspace State Manager] (Active file, selection, git status)|
|      +---> [Tool Execution Engine]                                      |
|               |-- read_file / write_to_file / apply_diff                |
|               |-- execute_command (Terminal integration)                |
|               |-- browser_action (Puppeteer / Playwright integration)   |
|               \-- mcp_hub (Model Context Protocol client)              |
|                                                                         |
+-------------------------------------------------------------------------+
```

---

## 2. Cline & Roo Code Architecture Deconstruction

Cline (originally Claude Dev) and its advanced community fork **Roo Code** represent the current state-of-the-art in in-editor autonomous agents.

### 2.1 The XML/Markdown Tool-Calling Harness
Unlike OpenAI function calling, which relies on structured JSON schema payloads that can be brittle or constrained across diverse open-source model providers, Cline and Roo Code employ a robust **XML Block Tagging System**:

```xml
<write_to_file>
<path>src/core/router.ts</path>
<content>
export class Router {
  // implementation
}
</content>
</write_to_file>
```

**Architectural Advantages:**
- **Streaming Execution:** The parser can begin validating tool calls as soon as `<write_to_file>` is encountered in the token stream, rather than waiting for the entire JSON payload to complete.
- **Provider Agnosticism:** Works consistently across Anthropic Claude, OpenAI GPT-4o, DeepSeek-Coder, and local Ollama/vLLM instances without custom function calling formatting.

### 2.2 Shadow Git Checkpoints & Rollback Engine
A key innovation in Roo Code is the **Shadow Git Checkpoint System**:
1. At the inception of an agent task, the harness initializes or updates an isolated shadow git branch (`refs/roo-checkpoints/<task-id>`) tracking workspace state.
2. Before and after every file mutation or terminal command, a micro-commit is recorded.
3. If an agent executes an erroneous refactor or corrupts a configuration, the user can click **"Restore Checkpoint"** in the webview.
4. The system executes a non-destructive hard restore of the modified files while preserving the conversational trajectory.

### 2.3 Custom Modes & Role-Based Prompt Profiles
Roo Code introduces dynamic **Modes**, configuring distinct system prompts, temperature settings, and tool permissions:
- **Architect Mode:** Read-only access to files; restricted from running destructive terminal commands; focused on system design and task decomposition.
- **Code Mode:** Full read/write access and test runner execution privileges.
- **Ask Mode:** Read-only exploration agent for explaining codebase architecture without modifying files.
- **Custom Modes:** User-defined personas (e.g., Security Auditor, SQL Migration Specialist) with tailored tool access.

---

## 3. Goose Architecture (Block / Square)

**Goose** is an open-source, terminal-native AI developer agent developed by Block (Square) designed for deep machine autonomy and enterprise tooling integration.

```
+------------------------------------------------------------------------+
|                          Goose Architecture                            |
+------------------------------------------------------------------------+
|                                                                        |
|  [User Terminal / CLI / Desktop GUI]                                   |
|        |                                                               |
|        v                                                               |
|  [Goose Core Engine (Rust / Python)]                                   |
|        |                                                               |
|        +---> [Context Manager] (Session persistence, sqlite history)   |
|        +---> [LLM Orchestrator] (Provider routing & fallback)          |
|        +---> [MCP Subsystem] (Model Context Protocol Host)              |
|                 |                                                      |
|                 +-- Developer Tools (git, shell, filesystem)           |
|                 +-- Enterprise Tools (Jira, GitHub, Slack, Datadog)    |
|                 \-- Custom Company Extensions                         |
|                                                                        |
+------------------------------------------------------------------------+
```

### 3.1 First-Class Model Context Protocol (MCP) Integration
Goose was built from the ground up to utilize Anthropic's **Model Context Protocol**:
- Instead of hardcoding tools into the agent runtime, Goose treats every capability as an external MCP server running over `stdio` or `sse`.
- Adding capabilities (e.g., database inspection, AWS deployment, Jira ticket resolution) requires zero modifications to the agent core—simply appending an MCP server entry to `~/.config/goose/config.yaml`.

### 3.2 Granular Security & Confirmation Gates
Goose implements an enterprise-grade permission model:
- **Autonomous Mode:** The agent runs shell commands and edits files without interruption.
- **Confirm-Execute Mode:** Read commands are auto-approved; mutating commands (`rm`, `git push`, `npm publish`) pause execution and prompt the user for interactive terminal authorization.
- **Secure Secret Scrubbing:** Environment variables containing credentials (`*_KEY`, `*_SECRET`, `*_TOKEN`) are automatically sanitized before logs or context are routed to external model providers.

---

## 4. Comprehensive Comparison Matrix

| Architectural Feature | Cline | Roo Code | Goose (Block) | Alpha Engineering Platform |
| :--- | :--- | :--- | :--- | :--- |
| **Interface** | VS Code Webview | VS Code Webview | Terminal CLI & Desktop GUI | Electron Desktop + Web + CLI |
| **Tool Protocol** | Custom XML Blocks | Custom XML Blocks | Model Context Protocol (MCP) | Hybrid MCP + Native Fast Tools |
| **State Rollback** | Basic Undo | Git Shadow Checkpoints | Local session state | Git Shadow Ref Trees + Snapshots |
| **Context Windowing** | Truncation buffer | Sliding Window Compaction | SQLite Session Cache | AST-Filtered Hierarchical Memory |
| **Multi-Agent Teams** | Single Agent | Single Agent (Modes) | Single Agent + Extensions | Full Multi-Agent Swarm (Subagents) |
| **Execution Sandbox** | Host Extension Host | Host Extension Host | Host Local Process | Ephemeral Docker / MicroVM Option |

---

## 5. Alpha Implementation Blueprint: Adopting SOTA Harness Patterns

Alpha synthesizes the finest engineering principles from Cline, Roo Code, and Goose into its unified architecture:

### 5.1 Local Git Shadow Checkpoint Integration
Alpha maintains an internal ref space (`.git/refs/alpha-checkpoints/<session-id>`) allowing developers to roll back multi-file modifications with a single click in the Electron UI.

### 5.2 Dynamic Persona Modes
Alpha implements dynamic operational modes:
- `Planner`: Zero write permissions; outputs approved `implementation_plan.md`.
- `Coder`: Scoped file mutation permissions with automatic syntax linter validation.
- `Reviewer`: AST diff validation, code review, and security audit engine.
- `Executor`: Supervised terminal execution with timeout enforcement and buffer truncation.

### 5.3 Universal MCP Hub Integration
Alpha integrates a universal Model Context Protocol hub, allowing seamless connectivity to any enterprise tool, database, or API via standard MCP configuration files.

---
*Reference Document authored for Alpha Autonomous Agent Architecture.*
