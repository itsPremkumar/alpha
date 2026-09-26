# Reversible File Quarantine

Autonomous cleanup must not silently destroy user data. The built-in
`reversible_delete` tool provides a local, human-approved quarantine workflow
without Recycle Bin APIs, cloud trash providers, or paid services.

A model cannot authorize its own deletion or choose a host root. The tool's
hidden runtime resolves the authenticated user's current thread workspace and
compares it with server-created `thread_data`; a model-supplied `root` is not in
the schema. Execution accepts only an approval request resolved through Alpha's
authenticated project approval API.

## Workflow

### 1. Plan

```json
{
  "action": "plan",
  "paths_json": "[\"old-report.txt\", \"old-cache/\"]"
}
```

Planning returns target paths, types, sizes, fingerprints, a plan ID, and a
canonical plan digest. It does not move or delete target files; it writes only
internal plan metadata under `.alpha/quarantine/`.

### 2. Request human approval

```json
{
  "action": "request_approval",
  "plan_id": "plan_…",
  "project_id": "my-project"
}
```

This creates a high-risk request in the existing project approval queue. The
request stores a SHA-256 digest over the exact plan fields. It is not authorized
by a model-supplied `approved: true` flag. The digest binds:

- the canonical root;
- the plan ID and plan digest;
- every target path and fingerprint.

An authorized project member resolves it through:

```text
POST /api/projects/{project_id}/approvals/{request_id}/resolve
```

Pending, rejected, expired, missing, or mismatched requests never mutate files.

### 3. Execute

```json
{
  "action": "execute",
  "plan_id": "plan_…",
  "project_id": "my-project",
  "approval_request_id": "APPR-…"
}
```

Execution:

- re-verifies the server-resolved approval;
- re-checks every target fingerprint before and after movement;
- refuses changed, missing, or symlinked targets;
- enforces a maximum batch of ten targets;
- moves targets into `.alpha/quarantine/<batch>/`;
- records a receipt containing hashes and restore paths;
- rolls back completed moves if any target or receipt persistence fails;
- never calls hard-delete.

### 4. Request restoration and restore

```json
{
  "action": "request_restore_approval",
  "receipt_id": "receipt_…",
  "project_id": "my-project"
}
```

After that request is resolved through the authenticated API:

```json
{
  "action": "restore",
  "receipt_id": "receipt_…",
  "project_id": "my-project",
  "approval_request_id": "APPR-…"
}
```

Restore re-hashes quarantined content, refuses to overwrite a recreated path,
and rolls back partial restores if any target or receipt persistence fails.

## Safety boundaries

- Paths are relative to the server-resolved per-user, per-thread workspace;
  the workspace root itself is not a valid target.
- Project identifiers are storage-safe and length-bounded before any
  file-backed approval queue path is constructed.
- Traversal, absolute paths, symlinks, protected roots, and quarantine targets
  fail closed.
- Directory trees are bounded to 10,000 files and 512 MiB.
- Persisted plan and receipt JSON is treated as untrusted; path, ID, hash, size,
  and vault scope are revalidated after every process reload.
- Quarantine and restore use atomic metadata replacement and process-local
  locking. The underlying approval queue remains the human authority.
- The service is platform-independent and uses only Python's standard library.

This tool is intentionally separate from arbitrary shell execution. Existing
sandbox, authorization, and approval middleware remain the authority for
whether the agent may propose or execute a cleanup at all.

## Tests

```bash
cd backend
uv run pytest tests/test_reversible_delete.py -q
```
