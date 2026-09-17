from __future__ import annotations

import logging
import os
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent_workspace.security.enclave.boundary")


class BoundaryViolationError(PermissionError):
    """Raised when an action or path crosses a task boundary constraint."""
    pass


@dataclass
class TaskBoundaryPolicy:
    """
    Task-Boundary Policy Engine inspired by OpenAI GPT-6 Astra.
    Enforces strict confinement:
      - Path enclaves (blocks directory traversal outside designated root)
      - Outbound network whitelists (blocks cloud metadata endpoints like 169.254.169.254)
      - Execution budget caps (subagent recursion depth, mutation counters)
    """
    allowed_root_paths: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    blocked_hosts: list[str] = field(
        default_factory=lambda: [
            "169.254.169.254",       # AWS/GCP/Azure instance metadata
            "metadata.google.internal",
            "169.254.170.2",         # AWS ECS task metadata
        ]
    )
    max_subagent_depth: int = 3
    max_mutations: int = 100
    mutation_count: int = 0

    def __post_init__(self) -> None:
        if not self.allowed_root_paths:
            # Default to current working directory as safe root
            self.allowed_root_paths = [os.path.abspath(".")]
        else:
            self.allowed_root_paths = [os.path.abspath(p) for p in self.allowed_root_paths]

        if not self.allowed_domains:
            self.allowed_domains = ["github.com", "pypi.org", "arxiv.org", "openai.com", "huggingface.co"]

    def validate_path(self, target_path: str) -> str:
        """
        Validates that a file/directory path is strictly within the allowed roots.
        Prevents path traversal attacks (e.g. '../../etc/passwd').
        """
        clean_path = os.path.abspath(target_path)
        is_safe = False

        for root in self.allowed_root_paths:
            # Must either equal root or be a child of root
            if clean_path == root or clean_path.startswith(root + os.sep):
                is_safe = True
                break

        if not is_safe:
            msg = (
                f"TASK_BOUNDARY_VIOLATION: Access to '{target_path}' (resolved: '{clean_path}') "
                f"is outside the permitted enclave roots: {self.allowed_root_paths}"
            )
            logger.error(msg)
            raise BoundaryViolationError(msg)

        return clean_path

    def validate_network_target(self, url_or_host: str) -> bool:
        """
        Validates outbound network target. Rejects internal metadata endpoints and unlisted domains.
        """
        host = url_or_host
        if "://" in url_or_host:
            parsed = urllib.parse.urlparse(url_or_host)
            host = parsed.hostname or url_or_host

        host_lower = host.lower()

        # Check blocked infrastructure hosts
        if host_lower in self.blocked_hosts:
            msg = f"TASK_BOUNDARY_VIOLATION: Blocked cloud metadata destination: '{host}'"
            logger.error(msg)
            raise BoundaryViolationError(msg)

        # Domain whitelist check
        is_allowed = any(host_lower == d or host_lower.endswith("." + d) for d in self.allowed_domains)
        if not is_allowed:
            msg = (
                f"TASK_BOUNDARY_VIOLATION: Outbound destination '{host}' "
                f"is not in the permitted domain whitelist: {self.allowed_domains}"
            )
            logger.warning(msg)
            return False

        return True

    def record_mutation(self) -> int:
        """Increments mutation counter; enforces resource budget."""
        self.mutation_count += 1
        if self.mutation_count > self.max_mutations:
            msg = f"TASK_BOUNDARY_VIOLATION: Mutation budget exceeded ({self.mutation_count}/{self.max_mutations})."
            logger.error(msg)
            raise BoundaryViolationError(msg)
        return self.mutation_count

    def validate_subagent_depth(self, current_depth: int) -> bool:
        """Ensures subagent spawning doesn't trigger recursive fork bombs."""
        if current_depth >= self.max_subagent_depth:
            msg = (
                f"TASK_BOUNDARY_VIOLATION: Subagent depth {current_depth} "
                f"exceeds boundary maximum {self.max_subagent_depth}."
            )
            logger.error(msg)
            raise BoundaryViolationError(msg)
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_root_paths": self.allowed_root_paths,
            "allowed_domains": self.allowed_domains,
            "max_subagent_depth": self.max_subagent_depth,
            "max_mutations": self.max_mutations,
            "mutation_count": self.mutation_count,
        }
