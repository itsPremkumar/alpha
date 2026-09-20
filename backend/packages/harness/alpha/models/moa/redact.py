"""PII and secret redaction filter for multi-model privacy safety inspired by Hermes."""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?:\+?1[ .-]?)?(?:\(\d{3}\)[ .-]?|\d{3}[.-])\d{3}[.-]\d{4}")
_API_KEY_RE = re.compile(r"\b(?:sk-[a-zA-Z0-9_\-]{20,}|ghp_[a-zA-Z0-9]{20,}|AKIA[0-9A-Z]{16})\b")


def redact_pii_and_secrets(text: str) -> str:
    """Mask email addresses, phone numbers, and common API keys."""
    if not text:
        return text
    redacted = _EMAIL_RE.sub("[redacted email]", text)
    redacted = _PHONE_RE.sub("[redacted phone]", redacted)
    redacted = _API_KEY_RE.sub("[redacted secret key]", redacted)
    return redacted
