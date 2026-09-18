"""Swarm Local Bridge: Discovers, parses, and connects local agent profiles to the Company OS."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Default location for agent installation on Windows/Posix
DEFAULT_SWARM_DIR = Path(os.environ.get("AGENT_WORKSPACE_SWARM_HOME", os.environ.get("HERMES_HOME", Path.home() / ".hermes")))


class SwarmBotMetadata(BaseModel):
    name: str
    role: str = ""
    symbol: str = "🤖"
    style: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    personality: list[str] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)
    direct_reports: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    work_protocol: str = ""
    primary_model: str = "poolside/laguna-s-2.1:free"
    fallback_models: list[str] = Field(default_factory=list)
    has_local_profile: bool = True


class SwarmLocalBridge:
    """Discovers and imports local agent bot profiles into Agent Workspace Company OS."""

    def __init__(self, swarm_dir: Path | str | None = None):
        if swarm_dir:
            self._swarm_dir = Path(swarm_dir)
        else:
            self._swarm_dir = DEFAULT_SWARM_DIR
        self._bots_dir = self._swarm_dir / "bots"
        self._cached_profiles: dict[str, SwarmBotMetadata] = {}

    @property
    def is_swarm_installed(self) -> bool:
        return self._bots_dir.exists() and self._bots_dir.is_dir()

    # Backwards compatibility property
    @property
    def is_hermes_installed(self) -> bool:
        return self.is_swarm_installed

    def discover_local_bots(self) -> list[str]:
        """Returns sorted list of all available agent bot profile directory names."""
        if not self.is_swarm_installed:
            return []
        try:
            return sorted([d.name for d in self._bots_dir.iterdir() if d.is_dir()])
        except Exception as exc:
            logger.warning(f"Error scanning swarm bots dir: {exc}")
            return []

    def get_bot_metadata(self, bot_name: str) -> SwarmBotMetadata:
        """Parses SOUL.md and config.yaml for a specific local agent bot profile."""
        if bot_name in self._cached_profiles:
            return self._cached_profiles[bot_name]

        bot_dir = self._bots_dir / bot_name
        if not bot_dir.exists():
            return SwarmBotMetadata(name=bot_name, has_local_profile=False)

        role = ""
        style = ""
        responsibilities: list[str] = []
        skills: list[str] = []
        primary_model = "poolside/laguna-s-2.1:free"

        soul_file = bot_dir / "SOUL.md"
        if soul_file.exists():
            try:
                lines = soul_file.read_text(encoding="utf-8").splitlines()
                current_section = ""
                for line in lines:
                    line_str = line.strip()
                    if line_str.startswith("# "):
                        role = line_str.lstrip("# ").strip()
                    elif line_str.startswith("## "):
                        current_section = line_str.lstrip("## ").strip().lower()
                    elif line_str.startswith("- ") and "responsibilities" in current_section:
                        responsibilities.append(line_str.lstrip("- ").strip())
                    elif line_str.startswith("- ") and ("skill" in current_section or "tool" in current_section):
                        skills.append(line_str.lstrip("- ").strip())
                    elif "style" in current_section or "tone" in current_section:
                        if line_str and not line_str.startswith("#"):
                            style += f" {line_str}"
            except Exception as exc:
                logger.warning(f"Error reading SOUL.md for {bot_name}: {exc}")

        config_file = bot_dir / "config.yaml"
        if config_file.exists():
            try:
                with open(config_file, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                if "model" in cfg:
                    primary_model = cfg["model"]
                elif "default_model" in cfg:
                    primary_model = cfg["default_model"]
            except Exception as exc:
                logger.warning(f"Error reading config.yaml for {bot_name}: {exc}")

        metadata = SwarmBotMetadata(
            name=bot_name,
            role=role or bot_name.replace("_", " ").title(),
            style=style.strip(),
            responsibilities=responsibilities,
            skills=skills,
            primary_model=primary_model,
            has_local_profile=True,
        )
        self._cached_profiles[bot_name] = metadata
        return metadata

    def get_all_local_profiles(self) -> dict[str, SwarmBotMetadata]:
        """Loads and returns metadata for all discovered local bot profiles."""
        bots = self.discover_local_bots()
        return {bot: self.get_bot_metadata(bot) for bot in bots}


# Transparent aliases for backwards compatibility
HermesBotMetadata = SwarmBotMetadata
HermesLocalBridge = SwarmLocalBridge
DEFAULT_HERMES_DIR = DEFAULT_SWARM_DIR
