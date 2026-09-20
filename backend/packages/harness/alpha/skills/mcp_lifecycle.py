"""Skill-Embedded On-Demand MCP Lifecycle Manager.

Inspired by oh-my-openagent (OmO) skill-embedded MCP architecture:
Starting 10+ MCP servers at boot consumes valuable prompt tokens with tool schemas.
Instead:
1. Skills declare their embedded MCP server requirements in SKILL.md YAML frontmatter.
2. Servers spin up on-demand strictly when that skill is invoked.
3. When the skill execution finishes, servers are torn down to preserve context window.
"""

from __future__ import annotations

from dataclasses import dataclass, field

try:
    import yaml
except ImportError:
    yaml = None


@dataclass
class SkillMcpSpec:
    server_name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


class SkillMcpLifecycleManager:
    """Manages lazy on-demand lifecycle of skill-embedded MCP servers."""

    def __init__(self):
        self._active_servers: dict[str, SkillMcpSpec] = {}
        self._skill_server_mapping: dict[str, set[str]] = {}

    def parse_skill_mcp_specs(self, skill_markdown: str) -> list[SkillMcpSpec]:
        """Extract MCP server declarations from SKILL.md frontmatter."""
        specs: list[SkillMcpSpec] = []
        if not skill_markdown.startswith("---"):
            return specs

        parts = skill_markdown.split("---", 2)
        if len(parts) < 3:
            return specs

        frontmatter_text = parts[1]
        if yaml:
            data = yaml.safe_load(frontmatter_text) or {}
        else:
            # Fallback naive YAML parser for mcp-servers block
            data = {}
            if "mcp-servers:" in frontmatter_text:
                mcp_part = frontmatter_text.split("mcp-servers:")[1]
                data["mcp-servers"] = {}
                for line in mcp_part.splitlines():
                    line = line.strip()
                    if line and ":" in line:
                        k, v = line.split(":", 1)
                        data["mcp-servers"][k.strip()] = {"command": v.strip()}

        mcp_servers = data.get("mcp-servers", {}) or data.get("mcp_servers", {})
        if isinstance(mcp_servers, dict):
            for sname, sconfig in mcp_servers.items():
                if isinstance(sconfig, dict):
                    specs.append(SkillMcpSpec(
                        server_name=sname,
                        command=sconfig.get("command", "node"),
                        args=sconfig.get("args", []),
                        env=sconfig.get("env", {}),
                    ))
        return specs

    def acquire_for_skill(self, skill_name: str, skill_markdown: str) -> list[str]:
        """Spin up declared MCP servers for an active skill."""
        specs = self.parse_skill_mcp_specs(skill_markdown)
        server_names = []
        for s in specs:
            self._active_servers[s.server_name] = s
            server_names.append(s.server_name)
            if skill_name not in self._skill_server_mapping:
                self._skill_server_mapping[skill_name] = set()
            self._skill_server_mapping[skill_name].add(s.server_name)

        return server_names

    def release_for_skill(self, skill_name: str) -> list[str]:
        """Tear down and unload MCP servers when skill execution finishes."""
        released = []
        if skill_name in self._skill_server_mapping:
            for sname in self._skill_server_mapping[skill_name]:
                if sname in self._active_servers:
                    del self._active_servers[sname]
                    released.append(sname)
            del self._skill_server_mapping[skill_name]
        return released

    def get_active_servers(self) -> list[str]:
        return list(self._active_servers.keys())
