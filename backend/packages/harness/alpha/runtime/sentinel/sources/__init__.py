"""Signal sources for the Sentinel OBSERVE stage."""

from alpha.runtime.sentinel.sources.logs import (
    classify_kind,
    classify_severity,
    from_records,
    scan_directory,
    scan_file,
    scan_text,
)

__all__ = [
    "classify_kind",
    "classify_severity",
    "from_records",
    "scan_directory",
    "scan_file",
    "scan_text",
]
