"""Global Emergency Stop (ESTOP) System inspired by Hermes Agent."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SENTINEL_NAME = "ESTOP"


def _get_runtime_home(root_dir: Path | str | None = None) -> Path:
    if root_dir is not None:
        return Path(root_dir)
    return Path.cwd() / ".alpha"


class EmergencyStopManager:
    """Manages the global pause/resume sentinel state for the agent runtime fleet."""

    def __init__(self, root_dir: Path | str | None = None):
        self.home_dir = _get_runtime_home(root_dir)
        self.sentinel_file = self.home_dir / SENTINEL_NAME

    def is_engaged(self) -> bool:
        """True if the ESTOP sentinel file exists; failsafe against runtime anomalies."""
        try:
            return self.sentinel_file.exists()
        except OSError:
            return True

    def engage(self, reason: str | None = None) -> Path:
        """Engage the emergency stop, writing the ESTOP sentinel."""
        self.home_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "engaged_at": datetime.now(UTC).isoformat(),
            "reason": reason or "Emergency stop triggered by operator or safety policy.",
        }
        try:
            self.sentinel_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            self.sentinel_file.touch(exist_ok=True)
        logger.warning(f"ESTOP engaged: {payload['reason']}")
        return self.sentinel_file

    def disengage(self) -> bool:
        """Disengage the emergency stop, removing the sentinel."""
        if self.sentinel_file.exists():
            try:
                self.sentinel_file.unlink(missing_ok=True)
                logger.info("ESTOP disengaged: normal agent operations resumed.")
                return True
            except OSError as e:
                logger.error(f"Failed to disengage ESTOP: {e}")
                return False
        return True

    def get_status(self) -> dict[str, Any]:
        """Inspect current emergency stop status."""
        engaged = self.is_engaged()
        details = {}
        if engaged and self.sentinel_file.exists():
            try:
                details = json.loads(self.sentinel_file.read_text(encoding="utf-8"))
            except Exception:
                details = {"reason": "Corrupted or empty sentinel file (fail-safe engaged)."}
        return {
            "is_engaged": engaged,
            "reason": details.get("reason", "None" if not engaged else "Failsafe active"),
            "engaged_at": details.get("engaged_at"),
            "sentinel_path": str(self.sentinel_file),
        }


_global_estop = EmergencyStopManager()


def get_estop_manager(root_dir: Path | str | None = None) -> EmergencyStopManager:
    global _global_estop
    if root_dir is not None:
        return EmergencyStopManager(root_dir)
    return _global_estop
