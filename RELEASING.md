# Releasing Alpha

How to cut a release of Alpha. The project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) and
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and its version string is
duplicated across four files that **must** stay identical.

> **The one rule that blocks publishing:** a `v*` git tag triggers CI, which runs
> [`scripts/verify_versions.sh`](scripts/verify_versions.sh). If any version source
> has drifted, **all publishing is blocked.** Verify before you tag, not after.

---

## Version sources (all four must match)

| File | Field | Current |
| :--- | :--- | :--- |
| `backend/pyproject.toml` | `version` | `2.1.0` |
| `backend/packages/harness/pyproject.toml` | `version` | `2.1.0` |
| `frontend/package.json` | `version` | `2.1.0` |
| `deploy/helm/agent-workspace/Chart.yaml` | `version` and `appVersion` | `2.1.0` / `"2.1.0"` |

---

## Release checklist

### 1. Prepare

- [ ] Confirm `main` is green: all required CI workflows pass.
- [ ] Confirm the working tree is clean.
- [ ] Confirm the `CHANGELOG.md` `[Unreleased]` section is drained into a new
      version heading with today's date, and that its release links resolve.

### 2. Bump the version

```bash
scripts/bump_version.sh 2.2.0
```

This updates all four sources in lockstep. It deliberately does **not** edit
`CHANGELOG.md` and does **not** create or push a tag — those stay manual so a human
owns the release narrative.

### 3. Verify the version

```bash
scripts/verify_versions.sh 2.2.0
```

Run this before committing. A failure here is a publishing failure later.

### 4. Re-run the documentation gates

Documentation drift is the most common release blocker in this repo:

```bash
# Capability manifest must match the live registries
python backend/scripts/generate_feature_manifest.py

# Docs index must match the classified document set
python scripts/generate_docs_index.py --check

# Tool schemas must still generate
python backend/scripts/check_tool_schemas.py
```

If any of these report drift, regenerate, commit the generated artifact, and update
the capability numbers in the user-facing content in the same commit — see
[`docs/DISCOVERABILITY.md`](docs/DISCOVERABILITY.md) for the full maintenance
checklist.

### 5. Run the test suites

```bash
cd backend && make test               # default suite
cd backend && make test-blocking-io   # strict blocking-IO gate
cd backend && make lint               # ruff check
cd backend && make format             # ruff format (CI enforces --check)
cd frontend && pnpm verify            # typecheck + unit tests
```

### 6. Commit and tag

```bash
git add -A
git commit -m "release: v2.2.0"
git tag v2.2.0
git push origin main
git push origin v2.2.0
```

Pushing the `v*` tag triggers the release workflows:
[`.github/workflows/container.yaml`](.github/workflows/container.yaml) for images
and [`.github/workflows/chart.yaml`](.github/workflows/chart.yaml) for the Helm
chart, plus
[`.github/workflows/windows-installer.yml`](.github/workflows/windows-installer.yml)
for the Electron installer.

### 7. Publish the GitHub Release

- [ ] Create the release from the pushed tag.
- [ ] Attach the Electron installer artifact (`Agent-Workspace-Setup-<ver>.exe`).
- [ ] Copy the `[Unreleased]`-derived notes from `CHANGELOG.md` into the release body.
- [ ] Update the `[![Release](…/badge/release/…)]` badge target if needed — it
      tracks the latest release automatically.

### 8. Announce

Announcements work best when the description, category words, and canonical URL are
identical to the repository's own. Reuse the README's first paragraph verbatim.

- [ ] GitHub Release notes
- [ ] `CHANGELOG.md` updated
- [ ] Any launch/listing profile descriptions reconciled with the README

---

## Versioning policy

| Change | Bump |
| :--- | :--- |
| Backwards-incompatible API, config key, or documented contract removal | **MAJOR** |
| New backwards-compatible capability (tools, routers, skills, deployment options) | **MINOR** |
| Bug fixes, docs, performance, internal refactors with no contract change | **PATCH** |

Anything listed under `### ⚠ Breaking changes` in `CHANGELOG.md` forces a MAJOR
bump.

---

## What a release does **not** include

- **Docker images, Helm releases, or Electron binaries are never mutated by the
  in-app source updater.** Those artifacts are orchestrator-owned. See
  [`docs/AUTO_UPDATE.md`](docs/AUTO_UPDATE.md).
- **The guarded source updater is check-only and disabled by default.** It is not a
  release mechanism.
- **Third-party extension packages are not re-vendored.** They are installed from
  their own sources via `alpha extensions install`.

---

## Rollback

If a release is broken:

1. **Do not delete or move the tag** — published images, charts, and installers
   already reference it.
2. Publish a new PATCH release with the fix, and mark the bad release as
   pre-release with a clear note.
3. If the tag was pushed but publishing failed on the version gate, fix the
   drifted version source, then re-run `scripts/verify_versions.sh <ver>` and re-run
   the workflow. Do not work around the gate.

---

## Related reading

- [`AGENTS.md`](AGENTS.md) — the version-lockstep and documentation-update conventions
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — branching and the PR checklist
- [`CHANGELOG.md`](CHANGELOG.md) — the release narrative
- [`docs/DISCOVERABILITY.md`](docs/DISCOVERABILITY.md) — documentation maintenance
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — Docker, Helm, and the Windows installer
- [`docs/AUTO_UPDATE.md`](docs/AUTO_UPDATE.md) — the guarded source updater
