"""Jina AI Reader client: fetch a page as rendered HTML.

Two properties this module is responsible for:

* **A real total-time budget.** ``httpx``'s ``timeout`` argument is a
  *per-operation* timeout: it bounds one connect and one inter-chunk read, not
  the whole call. A slow-drip upstream (a chunk every few seconds, for
  minutes) satisfies every per-operation deadline, so passing ``timeout=10`` did
  **not** bound a fetch to 10 seconds - measured live at 48s and 130s against
  the very same ``timeout: 10`` config. :func:`_post_with_total_budget` adds the
  missing wall-clock bound around the whole request.

* **A bounded upstream error body.** The raw response body is third-party
  content, and the error body is the part of a remote service most likely to be
  large and least likely to be worth reading. It is collapsed and truncated to
  :data:`MAX_ERROR_BODY_CHARS` before it can reach a log line or, via the tool,
  the model context.
"""

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_api_key_warned = False

#: Reader endpoint. Fixed by the Jina Reader API; not operator-configurable, so
#: the ``JINA_API_KEY`` bearer token below can only ever travel to this host.
READER_URL = "https://r.jina.ai/"

#: Hard bounds on the per-request timeout, in seconds.
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 120
DEFAULT_TIMEOUT_SECONDS = 10

#: Grace added on top of the per-request timeout to form the total budget. The
#: per-request timeout covers one read; the budget covers the *whole* call, so
#: it must be at least as large or it would be the only thing bounding the
#: request. It is deliberately small so the configured number still governs.
TOTAL_BUDGET_SLACK = 5

#: Cap on any upstream body echoed back to a caller (error text, log line).
MAX_ERROR_BODY_CHARS = 500


def coerce_timeout_seconds(value: object, default: int = DEFAULT_TIMEOUT_SECONDS) -> int:
    """Clamp a requested timeout into ``[1, 120]`` whole seconds.

    The previous coercion returned whatever the config held, so ``timeout: 0``
    (which reads like "no timeout", and is documented as ``-1`` disables for
    the sibling providers) reached httpx and failed every single fetch with
    ``ConnectTimeout``. A junk value now degrades to *default* instead of
    breaking the tool, and a wild value is bounded.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, number))


def _truncate_body(text: object, limit: int = MAX_ERROR_BODY_CHARS) -> str:
    """Bound an upstream body to *limit* characters and mark that it was cut."""
    collapsed = " ".join(str(text).split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit] + f"... [truncated, {len(collapsed)} chars total]"


async def _post_with_total_budget(client: httpx.AsyncClient, url: str, *, headers: dict, data: dict, budget: float) -> httpx.Response:
    """POST with a hard wall-clock ceiling, independent of chunk-level timeouts.

    ``asyncio.timeout`` is the only construct here that bounds *elapsed time* -
    ``httpx``'s own timeout cannot. It cancels the in-flight request, so the
    connection pool is torn down rather than left holding a half-read socket.
    """
    async with asyncio.timeout(budget):
        return await client.post(url, headers=headers, json=data)


class JinaClient:
    async def crawl(self, url: str, return_format: str = "html", timeout: int = 10, proxy: str | None = None, trust_env: bool = True) -> str:
        """Fetch *url* through the Jina Reader and return the body as text.

        Args:
            url: Absolute http(s) URL for the reader to fetch. The caller is
                responsible for screening it (see
                :func:`alpha.community.url_safety.validate_public_http_url`).
            return_format: Jina ``X-Return-Format`` value (``html``/``markdown``/``text``).
            timeout: Per-request timeout in seconds, clamped to
                ``[MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS]``. The total
                wall-clock budget for the call is this plus
                :data:`TOTAL_BUDGET_SLACK`.
            proxy: Optional explicit proxy URL.
            trust_env: Whether httpx may read proxy settings from the environment.

        Returns:
            The response body on success, or a string starting with ``"Error: "``
            on failure.
        """
        global _api_key_warned
        seconds = coerce_timeout_seconds(timeout)
        headers = {
            "Content-Type": "application/json",
            "X-Return-Format": return_format,
            "X-Timeout": str(seconds),
        }
        if os.getenv("JINA_API_KEY"):
            headers["Authorization"] = f"Bearer {os.getenv('JINA_API_KEY')}"
        elif not _api_key_warned:
            _api_key_warned = True
            logger.warning("Jina API key is not set. Provide your own key to access a higher rate limit. See https://jina.ai/reader for more information.")
        data = {"url": url}
        budget = seconds + TOTAL_BUDGET_SLACK
        try:
            client_kwargs: dict[str, object] = {"trust_env": trust_env}
            if proxy:
                client_kwargs["proxy"] = proxy
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await _post_with_total_budget(client, READER_URL, headers=headers, data=data, budget=budget)

            if response.status_code != 200:
                error_message = f"Jina API returned status {response.status_code}: {_truncate_body(response.text)}"
                logger.error(error_message)
                return f"Error: {error_message}"

            if not response.text or not response.text.strip():
                error_message = "Jina API returned empty response"
                logger.error(error_message)
                return f"Error: {error_message}"

            return response.text
        except TimeoutError:
            # asyncio.timeout raises the builtin TimeoutError; httpx raises its
            # own TimeoutException subclasses, which are not builtin ones and so
            # still take the generic arm below. Both are bounded and honest.
            error_message = f"Request to Jina API exceeded its {budget}s total time budget"
            logger.warning(error_message)
            return f"Error: {error_message}"
        except Exception as e:
            error_message = f"Request to Jina API failed: {type(e).__name__}: {_truncate_body(e)}"
            logger.warning(error_message)
            return f"Error: {error_message}"
