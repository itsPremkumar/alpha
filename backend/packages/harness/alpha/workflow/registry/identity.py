"""Runtime-identity registry — "which product, from which public repository, am I?"

Source of truth, in two parts:

* ``config/project-manifest.json`` — the **repository of record**: provider,
  owner, name, url, default branch, release channel. Validated by
  ``alpha.evolution.manifest.load_project_manifest_at``, which refuses a
  ``version`` field so a fifth version source cannot drift from the four gated by
  ``scripts/verify_versions.sh``.
* ``alpha.evolution.identity.get_runtime_identity()`` — the runtime identity:
  agent id, alpha version, git commit, platform, update state.

Why it is a registry and not just an endpoint
---------------------------------------------
``GET /api/evolution/identity`` already returns this payload — but only to an
operator with Gateway auth, and only as a field on a wider state document. The
question "which public repository is this, and who maintains it" is one the
*agent itself* needs to answer honestly: when it explains a feature, names an
upstream project, or is asked "where do you come from", the answer must come from
the shipped manifest rather than from a plausible-sounding guess. This registry
puts that fact into the same ``list`` / ``describe`` / ``health`` shape as every
other selection-plane source, so it can never be one endpoint that drifts away
from the rest.

Honesty contract
----------------
* Every field is **read**, never composed. A repository the manifest does not
  declare produces an ``unavailable`` descriptor with the reason, not a guessed
  GitHub URL — the failure mode here is a confidently wrong provenance claim,
  which is worse than no claim.
* ``gitCommit`` keeps the ``identity`` module's own honesty shape intact: it is
  ``"unknown"`` with source ``"unavailable"`` and the real reason as the note
  whenever ``git rev-parse`` fails, times out, or finds no repository. That is
  passed through verbatim rather than smoothed into a null.
* ``health`` stays ``unverified``: identity is a declaration plus one git probe,
  not a statement that anything is running.
* ``version`` is the **declared** ``alphaVersion`` for the identity row only —
  this is the one registry where the source genuinely does declare a version. Every
  other row keeps ``None``.
* ``evidence_kind`` is ``"measured"`` for rows read off disk/config and
  ``"unverified"`` for a row whose fact came back ``unknown``.

``get_runtime_identity()`` runs ``git`` as a subprocess and reads identity state
from disk, so both are blocking: Gateway callers must use ``asyncio.to_thread``.
"""

from __future__ import annotations

from typing import Any

from alpha.workflow.registry.base import (
    CapabilityDescriptor,
    RegistryHealth,
    RegistryUnavailable,
)

_SOURCE = "config/project-manifest.json"
_IDENTITY_SOURCE = "alpha.evolution.identity:get_runtime_identity"


def _row(
    entry_id: str,
    kind: str,
    source: str,
    *,
    authority: str,
    available: bool,
    reason: str | None = None,
    version: str | None = None,
    evidence: str = "measured",
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=entry_id,
        kind=kind,
        availability="available" if available else "unavailable",
        source=source,
        version=version,
        health="unverified",
        authority=authority,
        evidence_kind=evidence,  # type: ignore[arg-type]
        reason=reason,
    )


class IdentityRegistry:
    """list/describe/health over the shipped repository identity."""

    name = "identity"

    def list(self) -> list[CapabilityDescriptor]:
        manifest = self._load_manifest()
        repository = dict(manifest.get("repository") or {})
        release = dict(manifest.get("release") or {})
        runtime = self._runtime_identity()

        owner = str(repository.get("owner") or "")
        name = str(repository.get("name") or "")
        url = str(repository.get("url") or "")
        provider = str(repository.get("provider") or "")
        branch = str(repository.get("defaultBranch") or "")

        descriptors = [
            _row(
                "repository",
                "repository",
                f"{_SOURCE}#repository -> provider={provider or 'unset'} owner={owner or 'unset'} name={name or 'unset'} defaultBranch={branch or 'unset'}",
                authority="operator config (project manifest; a rename is an operator change)",
                available=bool(owner and name and url),
                reason=None if (owner and name and url) else f"project manifest repository is incomplete: provider={provider!r} owner={owner!r} name={name!r} url={url!r}",
            ),
            _row(
                "repository_url",
                "repository_link",
                f"{_SOURCE}#repository.url -> {url or '(unset)'}",
                authority="operator config (project manifest)",
                available=bool(url),
                reason=None if url else "project manifest declares no repository url; no public repository can be claimed",
            ),
            _row(
                "release_channel",
                "release",
                f"{_SOURCE}#release.channel -> {release.get('channel') or '(unset)'}",
                authority="operator config (project manifest)",
                available=bool(release.get("channel")),
                reason=None if release.get("channel") else "project manifest declares no release channel",
            ),
        ]
        descriptors.append(self._runtime_row(runtime))
        return descriptors

    def describe(self, entry_id: str) -> CapabilityDescriptor | None:
        wanted = _identity_aliases().get(entry_id, entry_id)
        for descriptor in self.list():
            if descriptor.id == wanted:
                return descriptor
        return None

    def health(self) -> RegistryHealth:
        try:
            descriptors = self.list()
        except Exception as exc:
            return RegistryHealth(
                registry=self.name,
                status="unavailable",
                count=None,
                error=f"{type(exc).__name__}: {exc}",
                evidence_kind="measured",
            )
        incomplete = [descriptor.id for descriptor in descriptors if descriptor.availability == "unavailable"]
        return RegistryHealth(
            registry=self.name,
            status="ok",
            count=len(descriptors),
            error=f"incomplete identity field(s): {', '.join(incomplete)}" if incomplete else None,
            evidence_kind="measured",
        )

    # -- sources ------------------------------------------------------------

    @staticmethod
    def _load_manifest() -> dict[str, Any]:
        try:
            from alpha.evolution.manifest import load_project_manifest
        except Exception as exc:  # pragma: no cover - production-wired import
            raise RegistryUnavailable(f"{type(exc).__name__}: {exc}") from exc
        try:
            return dict(load_project_manifest())
        except Exception as exc:
            raise RegistryUnavailable(f"project manifest could not be loaded: {type(exc).__name__}: {exc}") from exc

    @staticmethod
    def _runtime_identity() -> dict[str, Any]:
        """Runtime identity, or an honest empty mapping when it cannot be built.

        The project manifest is the authoritative half of this registry and is
        enough to answer "which repository is this", so a runtime-identity failure
        must degrade one row rather than fail the whole registry. Returning ``{}``
        makes the runtime row honestly ``unavailable`` with its reason attached,
        instead of losing the repository answer as collateral damage.
        """
        try:
            from alpha.evolution.identity import get_runtime_identity

            return dict(get_runtime_identity())
        except Exception:
            return {}

    # -- projections -------------------------------------------------------

    @staticmethod
    def _runtime_row(runtime: dict[str, Any]) -> CapabilityDescriptor:
        if not runtime:
            return _row(
                "runtime",
                "runtime_identity",
                _IDENTITY_SOURCE,
                authority="runtime (alpha.evolution.identity)",
                available=False,
                reason="get_runtime_identity() could not run; see GET /api/evolution/identity for the live error",
                evidence="unverified",
            )
        commit = str(runtime.get("gitCommit") or "unknown")
        commit_source = str(runtime.get("gitCommitSource") or "unavailable")
        commit_note = str(runtime.get("gitCommitNote") or "")
        version = str(runtime.get("alphaVersion") or "")
        repository = dict(runtime.get("repository") or {})
        # identity.py already refuses to fabricate a commit. Carry its real note
        # through instead of dropping it: "unknown" alone would read as a fact
        # about this checkout rather than as a git probe that failed.
        unresolved = None if commit_source == "git" else f"git commit unresolved (source={commit_source}): {commit_note or 'no reason reported'}"
        return _row(
            "runtime",
            "runtime_identity",
            f"{_IDENTITY_SOURCE} -> agentId={runtime.get('agentId') or 'unset'} os={runtime.get('os') or 'unset'} "
            f"arch={runtime.get('architecture') or 'unset'} gitCommit={commit} (source={commit_source}) repo={repository.get('url') or 'unset'}",
            authority="runtime (alpha.evolution.identity)",
            available=True,
            reason=unresolved,
            # The one row allowed a declared version: identity resolves it from the
            # installed distribution, and identity.py is the module that owns it.
            version=version or None,
            evidence="measured" if commit_source == "git" else "unverified",
        )


def _identity_aliases() -> dict[str, str]:
    """Accepted alternate spellings for ``describe`` ids.

    A caller asking for ``repo`` or ``commit`` should land on the row it means
    rather than on an honest-but-useless ``None``. The table is static — it maps a
    word to a declared row id and never to a value — so an alias cannot invent a
    match for something the manifest does not declare.
    """
    return {
        "repo": "repository",
        "repository_url": "repository_url",
        "url": "repository_url",
        "channel": "release_channel",
        "release": "release_channel",
        "version": "runtime",
        "commit": "runtime",
    }


__all__ = ["IdentityRegistry"]
