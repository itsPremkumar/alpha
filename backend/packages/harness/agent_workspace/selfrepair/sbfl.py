"""Spectrum-Based Fault Localization (SBFL) using the Ochiai metric.

Calculates statement/function suspiciousness scores from test execution spectra
and failure tracebacks to pinpoint the exact location of bugs.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class SuspiciousLocation:
    file_path: str
    line_number: int
    score: float
    function_name: str = ""
    error_type: str = ""
    error_message: str = ""
    context_lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "line_number": self.line_number,
            "score": round(self.score, 4),
            "function_name": self.function_name,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "context_lines": self.context_lines,
        }


class OchiaiFaultLocalizer:
    """Computes Ochiai SBFL rankings over execution spectra and traceback logs.

    Formula:
        S_Ochiai(e) = N_CF(e) / sqrt(N_F * (N_CF(e) + N_CS(e)))

    Where:
        N_CF(e): Failing tests that executed element e
        N_CS(e): Passing tests that executed element e
        N_F: Total number of failing tests
    """

    def __init__(self, workspace_root: Optional[str] = None) -> None:
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    @staticmethod
    def calculate_ochiai_score(
        failing_hits: int,
        passing_hits: int,
        total_failing: int,
    ) -> float:
        """Calculates raw Ochiai score for an element."""
        if total_failing <= 0 or (failing_hits + passing_hits) <= 0 or failing_hits <= 0:
            return 0.0
        denominator = math.sqrt(total_failing * (failing_hits + passing_hits))
        if denominator == 0.0:
            return 0.0
        return failing_hits / denominator

    def localize_from_spectrum(
        self,
        spectrum: dict[str, dict[str, int]],  # element_id -> {"failing_hits": X, "passing_hits": Y}
        total_failing: int,
    ) -> list[SuspiciousLocation]:
        """Rank elements from a structured coverage spectrum dictionary."""
        results: list[SuspiciousLocation] = []
        for element_id, counts in spectrum.items():
            n_cf = counts.get("failing_hits", 0)
            n_cs = counts.get("passing_hits", 0)
            score = self.calculate_ochiai_score(n_cf, n_cs, total_failing)
            if score > 0.0:
                parts = element_id.split(":")
                file_p = parts[0]
                line_no = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
                func = parts[2] if len(parts) > 2 else ""
                results.append(
                    SuspiciousLocation(
                        file_path=file_p,
                        line_number=line_no,
                        score=score,
                        function_name=func,
                    )
                )

        results.sort(key=lambda s: s.score, reverse=True)
        return results

    def parse_traceback_output(
        self,
        test_output: str,
        total_passing_tests: int = 1,
    ) -> list[SuspiciousLocation]:
        """Parses Python or Node.js failure traceback logs and computes SBFL suspiciousness."""
        # Python traceback regex: File "path/to/file.py", line 123, in func_name
        py_frame_pattern = re.compile(
            r'File\s+"([^"]+)",\s+line\s+(\d+)(?:,\s+in\s+([\w<>]+))?'
        )
        # Error message regex: AssertionError: msg or ValueError: msg
        error_pattern = re.compile(r"^([A-Z]\w*(?:Error|Exception|Failure)):\s*(.*)$", re.MULTILINE)

        error_match = error_pattern.search(test_output)
        error_type = error_match.group(1) if error_match else "TestFailure"
        error_msg = error_match.group(2) if error_match else ""

        hits: dict[str, int] = {}
        frame_details: dict[str, tuple[str, int, str]] = {}

        for match in py_frame_pattern.finditer(test_output):
            raw_path = match.group(1)
            line_no = int(match.group(2))
            func = match.group(3) or ""

            # Filter out standard library and virtualenv third-party frames
            is_in_workspace = False
            if self.workspace_root:
                try:
                    is_in_workspace = Path(raw_path).resolve().is_relative_to(self.workspace_root)
                except Exception:
                    pass

            if not is_in_workspace:
                if "site-packages" in raw_path or "lib/python" in raw_path or "AppData" in raw_path:
                    continue

            norm_path = raw_path
            if self.workspace_root:
                try:
                    norm_path = str(Path(raw_path).resolve().relative_to(self.workspace_root)).replace("\\", "/")
                except Exception:
                    norm_path = raw_path.replace("\\", "/")
            else:
                norm_path = raw_path.replace("\\", "/")

            element_key = f"{norm_path}:{line_no}"
            hits[element_key] = hits.get(element_key, 0) + 1
            frame_details[element_key] = (norm_path, line_no, func)

        if not hits:
            return []

        total_failing = 1
        results: list[SuspiciousLocation] = []

        # Elements deeper in the stack trace have higher localization weight
        keys_in_order = list(hits.keys())
        total_frames = len(keys_in_order)

        for depth_idx, element_key in enumerate(keys_in_order):
            norm_path, line_no, func = frame_details[element_key]
            # Deeper frames (closer to point of failure) get boosted failing weight
            depth_weight = (depth_idx + 1) / total_frames
            n_cf = hits[element_key] * (1.0 + depth_weight)
            n_cs = total_passing_tests
            
            # Ochiai calculation
            score = n_cf / math.sqrt(total_failing * (n_cf + n_cs))

            # Retrieve context lines if file is accessible
            context: list[str] = []
            if self.workspace_root:
                target_file = self.workspace_root / norm_path
                if target_file.is_file():
                    try:
                        all_lines = target_file.read_text(encoding="utf-8", errors="replace").splitlines()
                        start = max(0, line_no - 3)
                        end = min(len(all_lines), line_no + 2)
                        context = [f"{i+1}: {all_lines[i]}" for i in range(start, end)]
                    except Exception:
                        pass

            results.append(
                SuspiciousLocation(
                    file_path=norm_path,
                    line_number=line_no,
                    score=min(1.0, score),
                    function_name=func,
                    error_type=error_type,
                    error_message=error_msg,
                    context_lines=context,
                )
            )

        results.sort(key=lambda s: s.score, reverse=True)
        return results
