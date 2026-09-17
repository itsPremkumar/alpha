"""PatchSynthesizer: Unified diff generation, applicability check, and patch hygiene validator."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PatchValidationResult:
    """Outcome of patch hygiene and applicability validation."""
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


class PatchSynthesizer:
    """Generates standardized unified patches, validates applicability, and scans for leaked secrets."""

    SECRET_PATTERNS = [
        (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key"),
        (re.compile(r"-----BEGIN (?:RSA|OPENSSH|EC|DSA) PRIVATE KEY-----"), "Private Key"),
        (re.compile(r"(?:api[_-]?key|secret[_-]?key|auth[_-]?token)\s*=\s*['\"][a-zA-Z0-9_\-]{16,}['\"]", re.IGNORECASE), "High-entropy API/Auth Secret"),
    ]

    DISALLOWED_EXTENSIONS = {".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib", ".DS_Store", ".tmp"}

    def generate_unified_patch(
        self,
        repo_dir: str,
        base_ref: str | None = None,
        include_untracked: bool = True,
    ) -> str:
        """Compile a standard git unified diff against HEAD or a base ref."""
        target_dir = os.path.abspath(repo_dir)

        # Stage untracked files with intent-to-add so they appear in diff
        if include_untracked:
            try:
                subprocess.run(
                    ["git", "add", "-N", "."],
                    cwd=target_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=10,
                )
            except Exception as e:
                logger.debug(f"git add -N error: {e}")

        cmd = ["git", "diff", "--unified=3"]
        if base_ref:
            cmd.append(base_ref)

        try:
            res = subprocess.run(
                cmd,
                cwd=target_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
            )
            return res.stdout
        except Exception as e:
            logger.error(f"Failed to generate git diff: {e}")
            return ""

    def validate_patch_hygiene(self, patch_content: str) -> PatchValidationResult:
        """Scan patch content for exposed secrets, unwanted binary extensions, and hygiene issues."""
        errors: list[str] = []
        warnings: list[str] = []
        stats = self.compute_diff_stats(patch_content)

        if not patch_content.strip():
            warnings.append("Patch content is empty.")
            return PatchValidationResult(is_valid=True, warnings=warnings, stats=stats)

        # 1. Secret scanning
        for pattern, label in self.SECRET_PATTERNS:
            if pattern.search(patch_content):
                errors.append(f"Security Alert: Potential {label} detected in patch content.")

        # 2. Check for disallowed file extensions
        for line in patch_content.splitlines():
            if line.startswith("+++ b/") or line.startswith("--- a/"):
                filepath = line[6:].strip()
                ext = Path(filepath).suffix
                if ext in self.DISALLOWED_EXTENSIONS:
                    errors.append(f"Hygiene Alert: Temporary/binary file '{filepath}' included in patch.")

        is_valid = len(errors) == 0
        return PatchValidationResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            stats=stats,
        )

    def check_patch_applicability(self, patch_content: str, target_dir: str) -> bool:
        """Verify whether git apply --check succeeds for this patch without modifying files."""
        if not patch_content.strip():
            return True

        with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False, encoding="utf-8") as tf:
            tf.write(patch_content)
            temp_patch_path = tf.name

        try:
            res = subprocess.run(
                ["git", "apply", "--check", temp_patch_path],
                cwd=os.path.abspath(target_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
            )
            return res.returncode == 0
        except Exception as e:
            logger.error(f"Error checking patch applicability: {e}")
            return False
        finally:
            try:
                os.remove(temp_patch_path)
            except OSError:
                pass

    def compute_diff_stats(self, patch_content: str) -> dict[str, Any]:
        """Parse unified diff to calculate modified files, additions, and deletions."""
        files_changed = set()
        insertions = 0
        deletions = 0

        for line in patch_content.splitlines():
            if line.startswith("+++ b/"):
                files_changed.add(line[6:].strip())
            elif line.startswith("+") and not line.startswith("+++"):
                insertions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

        return {
            "files_changed_count": len(files_changed),
            "files_changed": sorted(list(files_changed)),
            "insertions": insertions,
            "deletions": deletions,
            "total_lines_altered": insertions + deletions,
        }
