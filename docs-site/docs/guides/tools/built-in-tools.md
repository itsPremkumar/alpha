---
sidebar_position: 1
title: Built-in Tools
description: Complete reference of Alpha's 456+ built-in tools
slug: /guides/tools/built-in-tools
---

# Built-in Tools

Alpha ships with 456+ built-in tools organized by category.

## Core Tools

### File Operations

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents |
| `write_file` | Write file contents |
| `str_replace` | Replace string in file |
| `list_files` | List directory contents |
| `glob` | Find files by pattern |
| `delete_file` | Delete file |

### Shell Operations

| Tool | Description |
|------|-------------|
| `bash` | Execute shell command |
| `bash_background` | Run command in background |

### Web Operations

| Tool | Description |
|------|-------------|
| `web_search` | Search the web |
| `web_fetch` | Fetch URL content |
| `browser_navigate` | Navigate browser |
| `browser_snapshot` | Capture page state |

### Code Operations

| Tool | Description |
|------|-------------|
| `code_execution` | Execute code in sandbox |
| `python_repl` | Python REPL |
| `str_replace_editor` | Edit code files |

### Memory Operations

| Tool | Description |
|------|-------------|
| `memory_search` | Search persistent memory |
| `memory_add` | Add to memory |
| `memory_forget` | Remove from memory |

### Agent Operations

| Tool | Description |
|------|-------------|
| `task` | Delegate to subagent |
| `subagent_spawn` | Spawn subagent |
| `subagent_list` | List active subagents |

## Tool Governance

Each tool has governance metadata:

```yaml
# Example tool governance
governance:
  risk_class: "read"  # read, write, execute, external, destructive
  permissions: ["filesystem:read"]
  side_effects: []
  reversibility: true
  timeout_seconds: 30
  verification_method: "output_inspection"
```

## Tool Categories

### File System (24 tools)
- `read_file`, `write_file`, `str_replace`, `list_files`, `glob`, `delete_file`, `file_exists`, `file_size`, `file_hash`, `file_info`, `read_lines`, `write_lines`, `append_file`, `copy_file`, `move_file`, `create_dir`, `remove_dir`, `list_dir`, `file_stats`, `file_permissions`, `chmod`, `chown`, `readlink`, `symlink`

### Shell (8 tools)
- `bash`, `bash_background`, `bash_stream`, `bash_kill`, `bash_wait`, `bash_pid`, `bash_status`, `bash_output`

### Web (16 tools)
- `web_search`, `web_fetch`, `web_fetch_raw`, `browser_navigate`, `browser_click`, `browser_type`, `browser_screenshot`, `browser_get_text`, `browser_get_html`, `browser_evaluate`, `browser_wait`, `browser_back`, `browser_forward`, `browser_refresh`, `browser_tabs`, `browser_close`

### Code (28 tools)
- `code_execution`, `python_repl`, `str_replace_editor`, `code_search`, `code_replace`, `code_lint`, `code_format`, `code_test`, `code_debug`, `code_analyze`, `git_status`, `git_diff`, `git_log`, `git_commit`, `git_push`, `git_pull`, `git_branch`, `git_checkout`, `git_merge`, `git_rebase`, `git_stash`, `git_diff_cached`, `git_show`, `git_blame`, `git_grep`

### Web Search (12 tools)
- `web_search`, `web_search_news`, `web_search_academic`, `web_search_images`, `web_search_videos`, `web_fetch`, `web_fetch_raw`, `web_fetch_markdown`, `web_extract_links`, `web_extract_text`, `web_extract_tables`, `web_screenshot`

### Browser Automation (20 tools)
- `browser_navigate`, `browser_click`, `browser_type`, `browser_screenshot`, `browser_get_text`, `browser_get_html`, `browser_evaluate`, `browser_wait`, `browser_back`, `browser_forward`, `browser_refresh`, `browser_tabs`, `browser_close`, `browser_scroll`, `browser_hover`, `browser_select`, `browser_upload`, `browser_download`, `browser_pdf`, `browser_print`

### Data Analysis (15 tools)
- `data_analysis`, `data_visualization`, `statistical_analysis`, `ml_training`, `ml_inference`, `data_cleaning`, `data_transformation`, `data_validation`, `sql_query`, `csv_process`, `json_process`, `xml_process`, `yaml_process`, `csv_to_json`, `json_to_csv`

### Memory (12 tools)
- `memory_search`, `memory_add`, `memory_forget`, `memory_inspect`, `memory_summarize`, `memory_consolidate`, `memory_promote`, `memory_demote`, `memory_project`, `memory_user`, `memory_experience`, `memory_failure`

### Subagents (6 tools)
- `task`, `subagent_spawn`, `subagent_list`, `subagent_status`, `subagent_stop`, `subagent_logs`

### Skills (8 tools)
- `skill_create`, `skill_install`, `skill_list`, `skill_test`, `skill_update`, `skill_uninstall`, `skill_inspect`, `skill_benchmark`

### MCP (12 tools)
- `mcp_list`, `mcp_connect`, `mcp_disconnect`, `mcp_call`, `mcp_list_tools`, `mcp_list_resources`, `mcp_read_resource`, `mcp_list_prompts`, `mcp_get_prompt`, `mcp_install_server`, `mcp_remove_server`, `mcp_reload`

### External APIs (24 tools)
- `github_api`, `github_search`, `github_create_issue`, `github_create_pr`, `github_get_pr`, `github_list_prs`, `github_create_repo`, `github_fork_repo`, `github_clone_repo`, `github_push`, `github_pull`, `github_merge`, `slack_post`, `slack_search`, `slack_channels`, `jira_create`, `jira_search`, `jira_update`, `jira_transition`, `notion_create`, `notion_search`, `notion_update`

### Verification (12 tools)
- `verify_tests`, `verify_security`, `verify_evidence`, `verify_artifact`, `verify_runtime`, `verify_independent`, `verify_adversarial`, `verify_completeness`, `verify_final`, `verify_regression`, `verify_visual`, `verify_completeness`

### Git (16 tools)
- `git_status`, `git_diff`, `git_log`, `git_commit`, `git_push`, `git_pull`, `git_branch`, `git_checkout`, `git_merge`, `git_rebase`, `git_stash`, `git_diff_cached`, `git_show`, `git_blame`, `git_grep`, `git_status_short`

### System (8 tools)
- `system_info`, `system_monitor`, `process_list`, `disk_usage`, `network_info`, `environment_vars`, `user_info`, `date_time`

## Tool Governance

Each tool has governance metadata:

```yaml
governance:
  risk_class: "read"  # read, write, execute, external, destructive
  permissions: ["filesystem:read"]
  side_effects: []
  reversibility: true
  timeout_seconds: 30
  verification_method: "output_inspection"
```

### Risk Classes

| Class | Description | Approval |
|-------|-------------|----------|
| `read` | Read-only operations | Auto |
| `write` | Modifies state | Auto |
| `execute` | Executes code | Ask |
| `external` | External calls | Ask |
| `destructive` | Irreversible | Block |

## Custom Tools

Create custom tools via skills:

```markdown
# SKILL.md
name: "custom-tool"
description: "Custom tool for specific task"
tools:
  - name: "my_custom_tool"
    description: "Does something custom"
    parameters:
      type: object
      properties:
        input:
          type: string
    execute: |
      # Tool implementation
      return {"result": "success"}
```