"""``web_fetch`` backed by the Jina AI Reader.

Three properties this tool layer is responsible for:

* **The URL is screened before anything is sent.** ``community/url_safety.py``
  already provides the deployment-wide screen every *other* fetch provider
  calls (``browserless``, ``crawl4ai``, ``fastcrw``, ``browser_automation``);
  the Jina-backed ``web_fetch`` did not call it at all, so a loopback, private,
  IPv6-loopback or cloud-metadata URL handed to the tool was forwarded verbatim
  to the remote reader. The guard is reused here rather than reimplemented, and
  an operator who genuinely needs an internal target can still opt in with
  ``allow_private_addresses: true`` - the same escape hatch ``fastcrw``
  documents. Screening locally also means a refusal costs no third-party call
  and does not depend on the remote reader's own SSRF policy.
* **The call is time-bounded end to end**, and the configured timeout is clamped
  to a range that means something.
* **The output is capped and says so.** A silently clipped page reads to a
  model as a complete page; the truncation is marked so the model knows the
  tail is missing.
"""

import asyncio

from langchain.tools import tool

from alpha.community.jina_ai.jina_client import JinaClient, coerce_timeout_seconds
from alpha.community.url_safety import validate_public_http_url
from alpha.config import get_app_config
from alpha.utils.readability import ReadabilityExtractor

readability_extractor = ReadabilityExtractor()

#: Characters of extracted markdown handed to the model. Unchanged from the
#: historical value; the addition is the marker that says when it bit.
MAX_CONTENT_CHARS = 4096

#: Appended when the extracted page was cut at :data:`MAX_CONTENT_CHARS`, so the
#: model is never silently handed half a document.
TRUNCATION_MARKER = "\n\n---\n\n[web_fetch: page content truncated at {limit} characters; the rest of the page was not read]"


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_timeout(value: object, default: int) -> int:
    """Clamp the configured timeout, degrading a non-positive value to *default*.

    A config of ``0`` or ``-1`` used to be passed straight through to httpx,
    where it means "fail immediately" rather than "no limit" - so the setting
    that looks like it disables the timeout silently disabled the tool instead.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    if number <= 0:
        return default
    return coerce_timeout_seconds(number, default)


def _coerce_proxy(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    proxy = value.strip()
    return proxy or None


def _truncate_markdown(markdown: str, limit: int = MAX_CONTENT_CHARS) -> str:
    """Cap the markdown and mark the cut so the model knows the tail is missing."""
    if len(markdown) <= limit:
        return markdown
    return markdown[:limit] + TRUNCATION_MARKER.format(limit=limit)


@tool("web_fetch", parse_docstring=True)
async def web_fetch_tool(url: str) -> str:
    """Fetch the contents of a web page at a given URL.
    Only fetch EXACT URLs that have been provided directly by the user or have been returned in results from the web_search and web_fetch tools.
    This tool can NOT access content that requires authentication, such as private Google Docs or pages behind login walls.
    Do NOT add www. to URLs that do NOT have them.
    URLs must include the schema: https://example.com is a valid URL while example.com is an invalid URL.
    Loopback, private-network and cloud-metadata URLs are refused before any request is made. Long pages are truncated, and the output says so when that happens.

    Args:
        url: The URL to fetch the contents of.
    """
    jina_client = JinaClient()
    timeout = 10
    proxy = None
    trust_env = True
    allow_private_addresses = False
    config = get_app_config().get_tool_config("web_fetch")
    if config is not None:
        timeout = _coerce_timeout(config.model_extra.get("timeout"), timeout)
        proxy = _coerce_proxy(config.model_extra.get("proxy"))
        trust_env = _coerce_bool(config.model_extra.get("trust_env"), trust_env)
        allow_private_addresses = _coerce_bool(config.model_extra.get("allow_private_addresses"), False)

    # Reuse the shared screen so this provider cannot drift from the other fetch
    # tools: same blocked-address policy, same resolver, same message.
    url_error = validate_public_http_url(url, allow_private_addresses=allow_private_addresses)
    if url_error:
        return url_error

    html_content = await jina_client.crawl(url, return_format="html", timeout=timeout, proxy=proxy, trust_env=trust_env)
    if isinstance(html_content, str) and html_content.startswith("Error:"):
        return html_content
    article = await asyncio.to_thread(readability_extractor.extract_article, html_content, url=url)
    return _truncate_markdown(article.to_markdown())
