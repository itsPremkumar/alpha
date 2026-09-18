"""Pre-commit AST Syntax & Linter Guardrail (Princeton SWE-agent / ACI style).

Validates source code syntax and formatting before modifications are committed to disk.
If syntax errors, indentation errors, or unparseable JSON/YAML are detected, the write is
blocked and detailed error diagnostics (with line and column offsets) are returned to the agent.
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


class SyntaxValidationError(Exception):
    """Raised when source code fails pre-commit static syntax verification."""

    def __init__(self, message: str, file_path: str, line_number: int | None = None, column: int | None = None):
        super().__init__(message)
        self.file_path = file_path
        self.line_number = line_number
        self.column = column


def validate_syntax_precommit(
    file_path: str,
    content: str,
    bypass: bool = False,
) -> Tuple[bool, str | None]:
    """Verify syntactic correctness of file content before writing to disk.

    Args:
        file_path: Path to the target file (extension determines parser).
        content: Proposed file content to validate.
        bypass: If True, skips syntax checks (for intentional test fixtures).

    Returns:
        (True, None) if syntax is valid or unsupported file type.
        (False, formatted_error_message) if syntax error is detected.
    """
    if bypass:
        return True, None

    ext = Path(file_path).suffix.lower()

    # 1. Python Syntax & Indentation Check
    if ext == ".py":
        return _validate_python_syntax(file_path, content)

    # 2. JSON Syntax Check
    elif ext == ".json":
        return _validate_json_syntax(file_path, content)

    # 3. YAML Syntax Check
    elif ext in {".yaml", ".yml"}:
        return _validate_yaml_syntax(file_path, content)

    # Unsupported / plain-text extensions pass through cleanly
    return True, None


def _validate_python_syntax(file_path: str, content: str) -> Tuple[bool, str | None]:
    """Parse Python code using Python's native AST parser."""
    try:
        ast.parse(content, filename=file_path)
        return True, None
    except IndentationError as e:
        line_num = e.lineno or 0
        col = e.offset or 0
        msg = (
            f"Pre-commit AST check failed: IndentationError in '{file_path}' at line {line_num}, column {col}: {e.msg}\n"
            f"Faulty line: {e.text.strip() if e.text else 'N/A'}\n"
            f"The file was NOT modified to prevent breaking the build. Please fix your indentation."
        )
        logger.warning(f"Rejected syntactically invalid Python write: {msg}")
        return False, msg
    except SyntaxError as e:
        line_num = e.lineno or 0
        col = e.offset or 0
        msg = (
            f"Pre-commit AST check failed: SyntaxError in '{file_path}' at line {line_num}, column {col}: {e.msg}\n"
            f"Faulty line: {e.text.strip() if e.text else 'N/A'}\n"
            f"The file was NOT modified to prevent breaking the build. Please correct the syntax."
        )
        logger.warning(f"Rejected syntactically invalid Python write: {msg}")
        return False, msg
    except Exception as e:
        msg = f"Pre-commit AST check failed: Unexpected error parsing '{file_path}': {e}"
        logger.warning(msg)
        return False, msg


def _validate_json_syntax(file_path: str, content: str) -> Tuple[bool, str | None]:
    """Parse JSON content using Python's native json module."""
    if not content.strip():
        return True, None
    try:
        json.loads(content)
        return True, None
    except json.JSONDecodeError as e:
        msg = (
            f"Pre-commit check failed: JSONDecodeError in '{file_path}' at line {e.lineno}, column {e.colno}: {e.msg}\n"
            f"The file was NOT modified. Please ensure valid JSON formatting (check commas, quotes, and brackets)."
        )
        logger.warning(f"Rejected invalid JSON write: {msg}")
        return False, msg


def _validate_yaml_syntax(file_path: str, content: str) -> Tuple[bool, str | None]:
    """Parse YAML content using PyYAML if available."""
    if not content.strip():
        return True, None
    try:
        import yaml
        yaml.safe_load(content)
        return True, None
    except ImportError:
        return True, None
    except Exception as e:
        msg = (
            f"Pre-commit check failed: YAML parse error in '{file_path}': {e}\n"
            f"The file was NOT modified. Please ensure valid YAML indentation and syntax."
        )
        logger.warning(f"Rejected invalid YAML write: {msg}")
        return False, msg
