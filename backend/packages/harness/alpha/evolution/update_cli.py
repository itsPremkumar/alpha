"""Command-line entry point for the guarded Alpha source updater."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from alpha.evolution.update_engine import UpdateEngine
from alpha.evolution.update_policy import load_update_policy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alpha-auto-update",
        description="Safely check and transactionally update an Alpha source checkout from GitHub.",
    )
    parser.add_argument("command", choices=("status", "check", "apply", "recover", "skip"), nargs="?", default="status")
    parser.add_argument("--policy", type=Path, help="Path to update-policy.json (otherwise auto-discovered).")
    parser.add_argument("--force", action="store_true", help="Confirm an attended apply when auto_apply is off; it never bypasses server safety checks.")
    parser.add_argument("--yes", action="store_true", help="Confirm a source mutation for the apply command.")
    parser.add_argument("--auto", action="store_true", help="For check: apply only when policy.auto_apply is enabled.")
    parser.add_argument("--transaction-id", help="Internal transaction id used by the detached Gateway helper.")
    parser.add_argument("--version", help="Version to skip (with the skip command).")
    parser.add_argument("--json", action="store_true", help="Emit one machine-readable JSON object.")
    return parser


def _emit(payload: object, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            print(f"{key}: {value}")
    else:
        print(payload)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # The root wrapper sets PYTHONPATH for source checkouts.  Keeping this
    # import here (rather than importing at module import time) also gives a
    # useful error to operators who invoke the module from the wrong cwd.
    if args.policy is not None:
        os.environ["ALPHA_UPDATE_POLICY_PATH"] = str(args.policy.expanduser().resolve())
    policy = load_update_policy(args.policy)
    engine = UpdateEngine(policy=policy)

    if args.command == "status":
        _emit(engine.status(), as_json=args.json)
        return 0
    if args.command == "check":
        result = engine.check_and_maybe_apply() if args.auto else engine.check(force=args.force)
        _emit(result, as_json=args.json)
        return 0 if result.get("state") not in {"CHECK_FAILED", "FAILED_UPDATE_RECORDED"} else 1
    if args.command == "skip":
        if not args.version:
            _emit({"ok": False, "state": "CHECK_FAILED", "reason": "skip requires --version"}, as_json=args.json)
            return 2
        result = engine.skip_version(args.version)
        _emit(result, as_json=args.json)
        return 0 if result.get("ok") else 1
    if args.command == "recover":
        result = engine.recover_incomplete().public_dict()
        _emit(result, as_json=args.json)
        return 0 if result.get("ok") else 1

    if not args.yes and not args.force:
        _emit(
            {
                "ok": False,
                "state": "CONFIRMATION_REQUIRED",
                "reason": "apply changes the checkout and restarts services; pass --yes (or --force) explicitly",
            },
            as_json=args.json,
        )
        return 2
    result = engine.apply_now(transaction_id=args.transaction_id, force=args.force)
    _emit(result.public_dict(), as_json=args.json)
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover - exercised through CLI tests/subprocess
    raise SystemExit(main())


__all__ = ["main"]
