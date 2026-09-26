"""Bounded public-web fetching used by the AgentEye research adapter.

AgentEye's standalone fetch helpers do not enforce Alpha's server-side SSRF
policy.  This module keeps that trust boundary in Alpha: every initial URL and
every redirect is screened before an HTTP request is made, responses are
size-bounded, and redirects are capped.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from alpha.community.url_safety import validate_public_http_url

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_TEXT_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/rss+xml",
    "application/atom+xml",
)
_DEFAULT_USER_AGENT = "Alpha-AgentEye/1.0 (+https://github.com/itsPremkumar/AgentEye)"


@dataclass(frozen=True, slots=True)
class FetchedText:
    """A bounded textual response after public-URL validation."""

    requested_url: str
    final_url: str
    content: str
    content_type: str
    status_code: int


async def _validate_public_url(url: str, *, action: str) -> None:
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise ValueError("URL could not be parsed") from exc
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Refusing to fetch URLs containing embedded credentials")
    error = await asyncio.to_thread(
        validate_public_http_url,
        url,
        allow_private_addresses=False,
        action=action,
    )
    if error:
        raise ValueError(error.removeprefix("Error: "))


def _is_textual(content_type: str) -> bool:
    normalized = content_type.split(";", 1)[0].strip().lower()
    return not normalized or normalized.startswith(_TEXT_CONTENT_TYPES)


def _decode(body: bytes, encoding: str | None) -> str:
    for candidate in (encoding, "utf-8"):
        if not candidate:
            continue
        try:
            return body.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


async def fetch_public_text(
    url: str,
    *,
    timeout_seconds: float = 20.0,
    max_bytes: int = 2_000_000,
    max_redirects: int = 5,
    user_agent: str = _DEFAULT_USER_AGENT,
) -> FetchedText:
    """Fetch a public textual URL without following unchecked redirects.

    DNS resolution is screened before the initial request and again after every
    redirect.  The byte cap is applied while streaming, before the complete
    response is materialized in memory.
    """

    requested_url = url.strip()
    if not requested_url:
        raise ValueError("URL must not be empty")
    timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))
    max_bytes = max(1_024, min(int(max_bytes), 10_000_000))
    max_redirects = max(0, min(int(max_redirects), 10))

    current_url = requested_url
    await _validate_public_url(current_url, action="fetch")
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(timeout_seconds),
        headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.5"},
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
    ) as client:
        for redirect_count in range(max_redirects + 1):
            if redirect_count > 0:
                await _validate_public_url(current_url, action="fetch")
            async with client.stream("GET", current_url) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError(f"Redirect from {current_url} did not include a Location header")
                    if redirect_count >= max_redirects:
                        raise ValueError(f"Too many redirects while fetching {requested_url}")
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if not _is_textual(content_type):
                    raise ValueError(f"Unsupported non-text content type: {content_type or 'unknown'}")

                declared_length = response.headers.get("content-length")
                if declared_length:
                    try:
                        declared_bytes = int(declared_length)
                    except ValueError:
                        declared_bytes = None
                    if declared_bytes is not None and declared_bytes > max_bytes:
                        raise ValueError(f"Response from {current_url} exceeds the {max_bytes}-byte limit")

                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > max_bytes:
                        raise ValueError(f"Response from {current_url} exceeds the {max_bytes}-byte limit")
                    body.extend(chunk)

                return FetchedText(
                    requested_url=requested_url,
                    final_url=current_url,
                    content=_decode(bytes(body), response.encoding),
                    content_type=content_type,
                    status_code=response.status_code,
                )

    raise ValueError(f"Unable to fetch {requested_url}")
