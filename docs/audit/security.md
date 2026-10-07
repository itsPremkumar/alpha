# Security audit — the `$`-anchored-regex newline bypass class

Scope: `backend/packages/harness/alpha/{sandbox,security,safety,tools}/`,
`backend/app/gateway/auth/`. Method: `search_files` only, then live probes with
`backend/.venv/Scripts/python.exe`. Every claim below is labelled.

## Inventory of `$`-anchored patterns in scope (MEASURED, exhaustive)

| file:line | pattern | validates? | trailing `\n` passes? | trailing `\r` passes? |
|---|---|---|---|---|
| `sandbox/worktree_strategy.py:484` | `^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$` | branch name (id) | **YES** | no |
| `sandbox/git_push_guard.py:104` | `^(?P<force>\+)?(?P<src>[^:]+):(?P<dst>.+)$` | git refspec | **YES** | no |
| `sandbox/tools.py:71` | `(?:^|[/\\=])\.\.(?:$|[/\\])` | `..` segment | **YES** | no |
| `sandbox/tools.py:1823` | `(?:\nExit Code: …)\s*$` | no (output scrape) | n/a | n/a |
| `sandbox/sandbox.py:14` | `^[A-Za-z_][A-Za-z0-9_]*$` | env key | no — uses `.fullmatch` | no |
| `safety/guard.py:40-43` | `(?:^|/)\.ssh/(id_rsa\|id_ecdsa\|id_ed25519)$` etc. | **filesystem path** | no | **YES** |
| `safety/guard.py:33-35` | destructive cmds | no `$` | n/a | n/a |
| `sandbox/computer_use.py:84` | `(?i)\.env(?:\.local)?$` | command (classification) | yes, but input is `.strip()`ped | no |
| `tools/builtins/bot_roster_tool.py:83` | `^\$?\s*(…)\s*(.*)$` | no (hint parser) | n/a | n/a |
| `tools/builtins/keyless_web_search_tool.py:125` | `^## \[(.+?)\]\((.+?)\)$` + `MULTILINE` | no (markdown parse) | n/a | n/a |
| `auth/pat.py:53-139` (~60 rules) | `^/api/…$` + `.match` | **route path** | **YES** | no |

## Findings

### 1. `safety/guard.py:40-43` — sensitive-path guard bypassed by a trailing `\r` (MEASURED)

`evaluate_file_access` (line 82) does `str(file_path).replace("\\","/")` with **no
`.strip()`**, then `pattern.search()` (line 84). Python's `$` matches before a
final `\n` but *not* a final `\r`, so `\r` is the live bypass. Measured:

```
'/etc/shadow'                              allowed=False  blocked
'/etc/shadow\n'                            allowed=False  blocked
'/etc/shadow\r'                            allowed=True   BYPASS
'/etc/shadow\r\n'                          allowed=True   BYPASS
'/home/u/.ssh/id_rsa\r'                    allowed=True   BYPASS
'C:/Windows/System32/config/SAM\r'         allowed=True   BYPASS
'/home/u/.aws/credentials\r'               allowed=True   BYPASS
'C:\Windows\System32\config\SAM\r'         allowed=True   BYPASS
```
Exploit: any file-access path arriving from a CRLF-normalised source (JSON
payload from a Windows agent, a CLI arg read with `newline` intact, an MCP
client) is judged "permitted" for `/etc/shadow`, `.ssh/id_ed25519`,
`System32/config/SAM` and `.aws/credentials`.

Reachability — **partially verified**: the only production caller found is the
self-probe at `alpha/commands/backend_handlers.py:806`, which always passes
literal `/etc/shadow`. `alpha/rsi/workspace.py:329` and
`harness/continuous/runner.py:23` obtain the guard but use
`evaluate_command`, not `evaluate_file_access`. So the bypass is **measured and
real, but I found no model-reachable sink today**. The defect is that a guard
whose docstring says "strictly blocked" (line 87) fails open on one byte.

### 2. `sandbox/worktree_strategy.py:484` — `_VALID_BRANCH.match` accepts `main\n` (MEASURED)

`is_safe_branch_name("main\n")` → **True**; `is_safe_branch_name("main\r")` →
False; `"../outside\n"` → False (caught by the independent `..`/segment rules at
line 496). Sibling `sandbox/worktrees.py:73,90` uses `re.fullmatch` and is not
affected — so the docstring's "mirror of `WorktreeManager`'s own check" is
**false for `\n`**: line 494's `.match` is laxer than line 73's `.fullmatch`.

Only caller: `sandbox/merge_simulation.py:160`, which then passes `head_ref` as
`["rev-parse","--verify","--end-of-options", f"{head_ref}^{{commit}}"]` — argv
form, no shell, no path join, so `main\n` resolves to a nonexistent ref and
returns `MergeOutcome.FAILED`. **Not exploitable today**; it is a live
divergence between the two validators and would become one the moment a caller
joins the name into a filesystem path (which is exactly the original shipped bug).

### 3. `auth/pat.py:53-139` — every route rule admits a trailing `\n` (MEASURED, not reachable)

Line 152 uses `pattern.match(normalized)` on `^…$` patterns. Measured:
`GET /api/threads/x\n` → True, `GET /api/projects\n` → True,
`DELETE /api/threads/x\n` → True. Critically, the **fail direction is intact**:
`GET /api/runs/stream\n` → False and `POST /api/threads/x/runs/y\n` → False.
An attacker cannot widen the PAT allow-list this way; at worst a denied route
reads as allowed. The input is `get_route_path(request.scope)` (Starlette,
`app/gateway/request_path.py:7`) taken from the ASGI scope, where a raw newline
cannot survive HTTP request-line parsing. **SPECULATIVE** as an exploit;
recommended hardening only (`fullmatch`).

## Not vulnerable / correctly hardened

- `sandbox/sandbox.py:14` — `_ENV_NAME_PATTERN` + `.fullmatch` (line 40). Correct.
- `sandbox/computer_use.py:84` — `classify` calls `.strip()` (line 90) before
  `re.search`; measured `.env`, `.env\n`, `.env\r` all classify `sensitive`. Correct.
- `sandbox/git_push_guard.py:104` — `_REFSPEC.match` on a shlex token; `\n` can
  only enter via an explicitly quoted token, and `_short_branch` (line 157)
  `.strip()`s. Not exploitable.

## Could not verify

- Whether any router/tool not in my scope feeds a raw string into
  `SafetyGuard.evaluate_file_access` (finding 1's severity depends on it). The
  gateway routers are out of my write-scope and I did not audit them.
- Whether `alpha.security/` contributes any `$`-anchored validator: it contains
  none — only a shell-AST parser (`security/shell_ast/parser.py`) matching bare
  `$` characters, which is unrelated.

## Fix priority

1. `safety/guard.py:82` — `str(file_path).replace("\\","/").strip()` (kills both
   `\r` and `\n`), and switch lines 40-43 to `fullmatch` or drop `$` for `\Z`.
2. `worktree_strategy.py:494` — `.match` → `.fullmatch`, restoring the stated
   mirror of `worktrees.py:73`.
3. `auth/pat.py:152` — `.match` → `.fullmatch` (defense-in-depth; no live exploit).
