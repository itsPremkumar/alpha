#!/usr/bin/env python3
"""Read the System One decision log and report how well calibrated it is.

System One's whole value proposition is that a stated 0.9 means "right about
90% of the time". That claim is worth real money — it is what lets a call site
skip the LLM — and it is also site-specific. A model that is calibrated in
general can still be overconfident on Alpha's particular traces. The only way
to know is to measure, so this script reads the JSONL log written when
``system_one.record_decisions`` (or ``shadow_mode``) is on.

Usage
-----
Report (human readable)::

    python backend/scripts/system_one_calibration.py

Machine readable::

    python backend/scripts/system_one_calibration.py --json

Only one site, and the last 20 decisions::

    python backend/scripts/system_one_calibration.py --site guardrail --tail 20

The log path comes from ``system_one.calibration_log_path`` in config.yaml
(relative paths resolve under the Alpha state directory). Override with
``--path``.

How to read the output
----------------------
* **stated** — the bucket of probabilities the model reported.
* **observed** — how often those decisions were actually right.
* **gap** — stated minus observed. Positive means overconfident, which is the
  dangerous direction: the model says 0.9 and is wrong half the time.
* **Brier score** — mean squared error of the probabilities. Lower is better;
  0.25 is what you get by always answering 0.5.
* **calibration error** — weighted mean |stated - observed|. 0 is perfect.

If there are no outcomes yet you will only see coverage and latency. Most sites
have no automatic ground truth; call :func:`record_outcome` from those that
eventually learn it (a test passed, a human accepted, a file exists).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from alpha.evaluation.system_one_calibration import (  # noqa: E402
    DEFAULT_LOG_NAME,
    calibration_report,
    load_records,
)


def default_log_path() -> Path:
    """Where the config says the log is."""
    try:
        from alpha.config import get_app_config

        cfg = get_app_config().system_one
        raw = cfg.calibration_log_path or DEFAULT_LOG_NAME
    except Exception:
        return Path(DEFAULT_LOG_NAME)
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    try:
        from alpha.config.paths import STATE_DIR

        return Path(STATE_DIR) / path
    except Exception:
        return Path.cwd() / path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report System One calibration from the decision log.")
    parser.add_argument("--path", default=None, help="JSONL log to read (default: from config).")
    parser.add_argument("--site", default=None, help="Restrict the report to one call-site label.")
    parser.add_argument("--tail", type=int, default=0, help="Only the last N decisions.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    args = parser.parse_args(argv)

    path = Path(args.path) if args.path else default_log_path()
    if not path.exists():
        print(f"No decision log at {path}")
        print("Turn on system_one.record_decisions (or shadow_mode) in config.yaml and run Alpha for a while.")
        return 1

    records = load_records(path)
    if not records:
        print(f"Decision log at {path} is empty.")
        return 1

    if args.site:
        records = [r for r in records if r.site == args.site]
        if not records:
            print(f"No decisions recorded for site {args.site!r}.")
            return 1
    if args.tail:
        records = records[-args.tail :]

    report = calibration_report(records)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"log: {path}")
        if args.site:
            print(f"site: {args.site}")
        print(report.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
