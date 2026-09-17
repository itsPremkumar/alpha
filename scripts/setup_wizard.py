#!/usr/bin/env python3
"""DeerFlow Setup Wizard (interactive + unattended).

Usage:
    uv run python scripts/setup_wizard.py                    # interactive (TTY)
    uv run python scripts/setup_wizard.py --non-interactive  # env-driven, no TTY needed

The unattended mode reads scripts/wizard/noninteractive.py environment
variables (AGENT_WORKSPACE_SETUP_PROVIDER, AGENT_WORKSPACE_SETUP_API_KEY, ...) so
Docker, Electron automation, and CI can provision DeerFlow with zero prompts.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

# Make the scripts/ directory importable so wizard.* works
sys.path.insert(0, str(Path(__file__).resolve().parent))

_PLACEHOLDER_MARKERS = ("your-", "changeme", "example", "placeholder", "replace-me")


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ensure_auth_secret(env_path: Path) -> bool:
    """Persist a real BETTER_AUTH_SECRET into .env when missing/placeholder.

    Returns True when a fresh secret was generated. Without this, logins break
    on every production host that copied the template verbatim.
    """
    from wizard.writer import read_env_file, write_env_file

    current = read_env_file(env_path).get("BETTER_AUTH_SECRET", "")
    if current and not any(
        marker in current.lower() for marker in _PLACEHOLDER_MARKERS
    ):
        return False
    write_env_file(env_path, {"BETTER_AUTH_SECRET": secrets.token_hex(32)})
    return True


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DeerFlow setup wizard")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="resolve every step from AGENT_WORKSPACE_SETUP_* env vars (no prompts)",
    )
    parser.add_argument(
        "--reconfigure",
        action="store_true",
        help="overwrite an existing config.yaml (non-interactive mode only)",
    )
    return parser.parse_args(argv)


def _persist_configuration(
    project_root, config_path, env_path, llm, search, execution, channels
) -> None:
    """Shared write phase: config.yaml, .env keys, auth secret, summary."""
    from wizard.ui import cyan, green, print_header, print_success
    from wizard.writer import write_config_yaml, write_env_file

    search_provider = search.search_provider
    search_api_key = search.search_api_key
    fetch_provider = search.fetch_provider
    fetch_api_key = search.fetch_api_key

    print_header("Writing configuration")

    write_config_yaml(
        config_path,
        provider_use=llm.provider.use,
        model_name=llm.model_name,
        display_name=f"{llm.provider.display_name} / {llm.model_name}",
        api_key_field=llm.provider.api_key_field,
        env_var=llm.provider.env_var,
        extra_model_config=llm.provider.extra_config_for(llm.model_name) or None,
        base_url=llm.base_url,
        search_use=search_provider.use if search_provider else None,
        search_tool_name=search_provider.tool_name if search_provider else "web_search",
        search_extra_config=search_provider.extra_config if search_provider else None,
        web_fetch_use=fetch_provider.use if fetch_provider else None,
        web_fetch_tool_name=fetch_provider.tool_name if fetch_provider else "web_fetch",
        web_fetch_extra_config=fetch_provider.extra_config if fetch_provider else None,
        sandbox_use=execution.sandbox_use,
        allow_host_bash=execution.allow_host_bash,
        include_bash_tool=execution.include_bash_tool,
        include_write_tools=execution.include_write_tools,
        channel_connection_providers=channels.enabled_providers,
    )
    print_success(f"Config written to: {config_path.relative_to(project_root)}")

    if not env_path.exists():
        env_example = project_root / ".env.example"
        if env_example.exists():
            import shutil

            shutil.copyfile(env_example, env_path)

    env_pairs: dict[str, str] = {}
    if llm.api_key and llm.provider.env_var:
        env_pairs[llm.provider.env_var] = llm.api_key
    if search_api_key and search_provider and search_provider.env_var:
        env_pairs[search_provider.env_var] = search_api_key
    if fetch_api_key and fetch_provider and fetch_provider.env_var:
        env_pairs[fetch_provider.env_var] = fetch_api_key

    if env_pairs:
        write_env_file(env_path, env_pairs)
        print_success(f"API keys written to: {env_path.relative_to(project_root)}")

    if _ensure_auth_secret(env_path):
        print_success("Generated BETTER_AUTH_SECRET")

    frontend_env = project_root / "frontend" / ".env"
    frontend_env_example = project_root / "frontend" / ".env.example"
    if not frontend_env.exists() and frontend_env_example.exists():
        import shutil

        shutil.copyfile(frontend_env_example, frontend_env)
        print_success("frontend/.env created from example")

    print_header("Setup complete!")
    print(f"  {green('✓')} LLM:        {llm.provider.display_name} / {llm.model_name}")
    if search_provider:
        print(f"  {green('✓')} Web search: {search_provider.display_name}")
    else:
        print(f"  {'—':>3} Web search: not configured")
    if fetch_provider:
        print(f"  {green('✓')} Web fetch:  {fetch_provider.display_name}")
    else:
        print(f"  {'—':>3} Web fetch:  not configured")
    sandbox_label = (
        "Local sandbox"
        if execution.sandbox_use.endswith("LocalSandboxProvider")
        else "Container sandbox"
    )
    print(f"  {green('✓')} Execution:  {sandbox_label}")
    if execution.include_bash_tool:
        bash_label = "enabled"
        if execution.allow_host_bash:
            bash_label += " (host bash)"
        print(f"  {green('✓')} Bash:       {bash_label}")
    else:
        print(f"  {'—':>3} Bash:       disabled")
    if execution.include_write_tools:
        print(f"  {green('✓')} File write: enabled")
    else:
        print(f"  {'—':>3} File write: disabled")
    if channels.enabled_providers:
        print(f"  {green('✓')} IM channels: {', '.join(channels.enabled_providers)}")
    else:
        print(f"  {'—':>3} IM channels: disabled")
    print()
    print("Next steps:")
    print(f"  {cyan('make install')}    # Install dependencies (first time only)")
    print(f"  {cyan('make dev')}        # Start DeerFlow")
    print()
    print(f"Run {cyan('make doctor')} to verify your setup at any time.")
    print()


def _run_interactive() -> int:
    from wizard.ui import (
        ask_yes_no,
        bold,
        print_header,
        print_info,
        yellow,
    )

    project_root = Path(__file__).resolve().parents[1]
    config_path = project_root / "config.yaml"
    env_path = project_root / ".env"

    print()
    print(bold("Welcome to DeerFlow Setup!"))
    print("This wizard will help you configure DeerFlow in a few minutes.")
    print()

    if config_path.exists():
        print(yellow("Existing configuration detected."))
        print()
        should_reconfigure = ask_yes_no("Do you want to reconfigure?", default=False)
        if not should_reconfigure:
            print()
            print_info(
                "Keeping existing config. Run 'make doctor' to verify your setup."
            )
            return 0
        print()

    total_steps = 5

    from wizard.steps.llm import run_llm_step

    llm = run_llm_step(f"Step 1/{total_steps}")

    from wizard.steps.search import run_search_step

    search = run_search_step(f"Step 2/{total_steps}")

    from wizard.steps.execution import run_execution_step

    execution = run_execution_step(f"Step 3/{total_steps}")

    from wizard.steps.channels import run_channels_step

    channels = run_channels_step(f"Step 4/{total_steps}")

    print_header(f"Step {total_steps}/{total_steps} · Writing configuration")
    _persist_configuration(
        project_root, config_path, env_path, llm, search, execution, channels
    )
    return 0


def _run_noninteractive(allow_reconfigure: bool) -> int:
    import os

    # Unattended runs often land on non-UTF-8 consoles (Windows cp1252, CI
    # pipes) where the wizard's box-drawing headers crash. Reconfigure stdout
    # instead of degrading the shared UI helpers used by interactive mode.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    from wizard.noninteractive import SetupError, resolve_noninteractive_setup

    project_root = Path(__file__).resolve().parents[1]
    config_path = project_root / "config.yaml"
    env_path = project_root / ".env"

    if config_path.exists() and not (
        allow_reconfigure or os.environ.get("AGENT_WORKSPACE_SETUP_RECONFIGURE") == "1"
    ):
        print(
            "config.yaml already exists. Set AGENT_WORKSPACE_SETUP_RECONFIGURE=1 "
            "or pass --reconfigure to overwrite it."
        )
        return 1

    try:
        llm, search, execution, channels = resolve_noninteractive_setup()
    except SetupError as exc:
        print(f"Setup failed: {exc}")
        return 1

    print(f"Provider: {llm.provider.display_name} / {llm.model_name}")
    _persist_configuration(
        project_root, config_path, env_path, llm, search, execution, channels
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.non_interactive:
            return _run_noninteractive(allow_reconfigure=args.reconfigure)
        if not _is_interactive():
            print(
                "Non-interactive environment detected.\n"
                "Re-run with --non-interactive plus AGENT_WORKSPACE_SETUP_* env vars, "
                "or run 'make setup' in a terminal."
            )
            return 1
        return _run_interactive()
    except KeyboardInterrupt:
        print("\n\nSetup cancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
