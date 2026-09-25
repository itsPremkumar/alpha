# Alpha Auto-Update

Alpha can keep a **local source checkout** synchronized with GitHub without
blindly replacing files in a running process. The updater is an explicit,
recoverable transaction:

```text
check → discover published target → validate policy/worktree
       → fetch/verify → backup → quiesce/stop local services
       → fast-forward → config/dependency hooks → restart
       → Gateway/frontend health checks
       → HEALTHY or automatic rollback
```

The default policy is intentionally safe:

```json
{
  "enabled": false,
  "auto_apply": false,
  "channel": "stable"
}
```

`enabled` is the hard kill switch. `auto_apply` is a second explicit opt-in
that allows a validated candidate to be applied without an operator clicking
**Apply update**. A manual `make update-check` is always read-only;
`make update-apply` is an explicit operator action, but it still cannot bypass
`enabled: false`. The API/UI confirmation flag only permits an attended apply
when `auto_apply` is off; it never bypasses the persisted `canApply` verdict,
clean-worktree checks, fast-forward ancestry, or health verification.

## What is trusted

The default `stable` channel consumes the published GitHub Release for the
repository in [`config/project-manifest.json`](../config/project-manifest.json).
It does **not** treat an arbitrary `main` commit as a production release.
Developers who want branch tracking can opt into `main` explicitly:

```json
{
  "enabled": true,
  "auto_apply": true,
  "channel": "main",
  "verify_signed_commit": true
}
```

Before applying, the engine requires:

- a local-source deployment (Docker, Helm, Electron, and multi-worker Gateway
  report `canSelfUpdate=false` and are never mutated in place);

- a clean tracked **and untracked** worktree;
- the configured remote to match the manifest's GitHub owner/repository;
- a branch in `allowed_branches` (default `main`);
- a fast-forward-only history relationship;
- enough free disk space;
- the target ref to still resolve to the commit that was checked (no moving
  target race);
- optional Git commit signature verification;
- release tags whose backend version manifest agrees with the tag (when
  `verify_release_version` is enabled, the default);
- successful post-update config/dependency hooks and health checks.

All Git and hook commands use an argv list. No HTTP field can provide a URL,
branch, commit, shell command, or archive path. An update API request can only
apply the candidate already verified and persisted by the server.

## Configure

The committed, credential-free policy is
[`config/update-policy.json`](../config/update-policy.json). It is discovered
from the repository root. Treat it as a reviewed template: for a real
unattended deployment, copy it to a path under the deployment's runtime home
(for example `.agent-workspace/update-policy.json`) and point
`ALPHA_UPDATE_POLICY_PATH` at that copy.
Keeping the mutable policy outside the checkout prevents an operator policy
edit from making the clean-worktree gate permanently fail. The repository file
itself remains disabled by default.

To enable unattended updates on a local checkout:

1. Set `enabled: true` and `auto_apply: true` in the policy.
2. Keep `autonomy.enabled: true` and restart Alpha once so the startup-scoped
   loop configuration is captured. The Gateway registers `self_update`
   automatically when the policy is enabled; an explicit
   `autonomy.loops.self_update` block may be used to override its interval or
   disable it without changing the policy.
3. Register Windows autostart if applicable:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/register_autostart.ps1 -Force
```

When the policy is enabled, registration creates the optional
`Alpha_Update` Task Scheduler task. It runs every 15 minutes and calls the same
transaction engine; the watchdog remains responsible for service recovery,
not source updates.

## Manual operations

From the repository root:

```bash
make update-status     # persisted state + policy + bounded audit history
make update-check      # network/release discovery, no source mutation
make update-apply      # explicit confirmed transaction
make update-recover    # recover an interrupted transaction
make update-skip VERSION=2.1.1  # skip one verified version
```

The equivalent direct commands are:

```bash
python scripts/auto_update.py status --json
python scripts/auto_update.py check --force --json
python scripts/auto_update.py apply --yes --force --json
python scripts/auto_update.py recover --json
```

Use the project environment when dependencies are not globally installed:

```bash
cd backend
uv run --no-sync python ../scripts/auto_update.py check --force --json
```

## API

The authenticated Gateway exposes:

- `POST /api/evolution/update-check` — read-only discovery; when the policy
  is enabled it persists the same immutable candidate consumed by Apply;
- `GET /api/evolution/update-state` — persisted state without network access;
- `POST /api/evolution/update-apply` — admin-only detached apply request;
- `POST /api/evolution/update-recover` — admin-only recovery request.

The apply endpoint returns a transaction id and does not perform Git or service
restart work inside the request handler. The UI exposes the same operation in
**Settings → General & Identity**. Non-admin callers receive `403`; PATs never
inherit admin capability.

## State and rollback

State is stored under the deployment's runtime home. Treat the runtime home
as sensitive: rollback snapshots can contain the existing local configuration
(including values resolved from environment variables), while state/history and
logs are credential-redacted.

```text
.agent-workspace/update_state.json       # current durable state
.agent-workspace/update_history.jsonl    # bounded append-only audit trail
.agent-workspace/update.lock/            # cross-process transaction lock
.agent-workspace/updates/daemon.log      # rotated detached helper output
.agent-workspace/update_maintenance.json  # cross-process run-admission barrier
```

The state machine includes the honest intermediate values
`DOWNLOADING`, `VERIFYING`, `STAGING`, `BACKUP_CREATED`, `STOPPING`,
`READY_TO_SWITCH`, `INSTALLING`, `RESTARTING`, `HEALTH_CHECK`, and
`RECOVERY_REQUIRED`. A successful update is
`HEALTHY` only after checks pass. A failed update restores the previous Git
ref and `config.yaml`/`extensions_config.json` snapshots, restarts the old
services, and records `FAILED_UPDATE_RECORDED` with the reason. If the old
services still fail health after restoration, it records `RECOVERY_REQUIRED`
and requires an explicit recovery attempt.

Git backup refs are retained under `refs/alpha-update/backups/` according to
`keep_backups`. If the helper is interrupted, run `make update-recover`; it
will refuse to act without a valid backup ref, and the policy must be re-enabled
for this explicit recovery operation.

## Deployment boundary

This feature is for a local Git checkout launched by `start.ps1`,
`start.sh`, or `scripts/serve.sh`. It deliberately does **not** update a
running Docker image or Helm release in place. Use the image/chart release
pipeline for those deployments. The Electron installer likewise remains an
installer-owned update surface.

The updater never uploads runtime data or reads secrets for release
discovery. An optional `GITHUB_TOKEN`/`GH_TOKEN` may be used by the existing
GitHub release client, but it is never written to state, history, logs, or API
responses.
