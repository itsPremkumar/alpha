"""Security helpers for sandbox capability gating."""

from alpha.config import get_app_config

_LOCAL_SANDBOX_PROVIDER_MARKERS = (
    "alpha.sandbox.local:LocalSandboxProvider",
    "alpha.sandbox.local.local_sandbox_provider:LocalSandboxProvider",
)

LOCAL_HOST_BASH_DISABLED_MESSAGE = (
    "Host bash execution is disabled for LocalSandboxProvider because it is not a secure "
    "sandbox boundary. Switch to AioSandboxProvider for isolated bash access, or set "
    "sandbox.allow_host_bash: true only in a fully trusted local environment."
)

LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE = (
    "Bash subagent is disabled for LocalSandboxProvider because host bash execution is not "
    "a secure sandbox boundary. Switch to AioSandboxProvider for isolated bash access, or "
    "set sandbox.allow_host_bash: true only in a fully trusted local environment."
)


LOCAL_IN_PROCESS_REPL_DISABLED_MESSAGE = (
    "In-process Python execution is disabled because it runs via exec() inside the Gateway "
    "process itself and therefore has no sandbox boundary: it cannot be confined by "
    "environment scrubbing, and os.environ is readable regardless. Set "
    "sandbox.allow_in_process_repl: true only in a fully trusted local environment."
)


def uses_local_sandbox_provider(config=None) -> bool:
    """Return True when the active sandbox provider is the host-local provider."""
    if config is None:
        config = get_app_config()

    sandbox_cfg = getattr(config, "sandbox", None)
    sandbox_use = getattr(sandbox_cfg, "use", "")
    if sandbox_use in _LOCAL_SANDBOX_PROVIDER_MARKERS:
        return True
    return sandbox_use.endswith(":LocalSandboxProvider") and "alpha.sandbox.local" in sandbox_use


def is_host_bash_allowed(config=None) -> bool:
    """Return whether host bash execution is explicitly allowed."""
    if config is None:
        config = get_app_config()

    sandbox_cfg = getattr(config, "sandbox", None)
    if sandbox_cfg is None:
        return False
    if not uses_local_sandbox_provider(config):
        return True
    return bool(getattr(sandbox_cfg, "allow_host_bash", False))


def is_in_process_repl_allowed(config=None) -> bool:
    """Return whether in-process Python execution (``python_repl``) is allowed.

    This is a **separate** switch from :func:`is_host_bash_allowed`, and it has to
    be. ``python_repl`` does not spawn a subprocess: it ``exec()``s the cell body
    inside the Gateway process itself, with a namespace that preloads ``os`` and
    ``sys``. It therefore cannot be confined by ``build_sandbox_env`` — no env
    scrubbing applies to a cell that reads ``os.environ`` directly — and it is not
    covered by ``allow_host_bash: false`` at all.

    That gap was the defect: in the shipped default the operator set
    ``allow_host_bash: false``, reasonably read that as "no host execution", and
    the model could still run arbitrary Python at full process privilege. A kill
    switch that leaves the door open is worse than no kill switch, so the REPL gets
    its own default-off key rather than being folded into the bash one.

    A non-local sandbox provider does not help here — the cell still runs in the
    Gateway process, not in the sandbox — so unlike
    :func:`is_host_bash_allowed` this returns the flag's value regardless of
    provider, with no ``True`` for a containerised deployment.
    """
    if config is None:
        config = get_app_config()

    sandbox_cfg = getattr(config, "sandbox", None)
    if sandbox_cfg is None:
        return False
    return bool(getattr(sandbox_cfg, "allow_in_process_repl", False))
