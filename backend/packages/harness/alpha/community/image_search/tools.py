"""
Image Search Tool - Search images using DuckDuckGo for reference in image generation.

Failure honesty
---------------
The rule this module enforces: **a failed image search is never reported as
"no images found"**. The previous implementation caught every exception and
returned ``[]``, which the tool then rendered as
``{"error": "No images found"}`` - a claim that the web had no matching images
when in fact the engine was unreachable. It also let a ``DDGS()`` construction
failure escape the tool boundary as a raw exception.

Failures are reported with the same typed ``error`` vocabulary
(:mod:`alpha.community.search_federation.errors`) the text-search providers
use, so the model reads one set of failure words.

Result bound
------------
``max_results`` is model-controlled and is clamped to
:data:`MAX_RESULTS_HARD_CAP`, so one tool call cannot inject an unbounded wall
of third-party URLs and titles into the context window.
"""

import json
import logging

from langchain.tools import tool

from alpha.community.ddg_search.tools import (
    PROVIDER,
    _clamp_max_results,
    _classify_engine_error,
)
from alpha.community.search_federation.errors import (
    ProviderNotConfiguredError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from alpha.config import get_app_config

logger = logging.getLogger(__name__)

#: Returned (not raised) when the engine answered and genuinely had no images.
EMPTY_RESULT_NOTE = (
    "The DuckDuckGo image engine answered this query and returned zero images. This is an "
    "empty result set, not a search failure: nothing matched, so rephrase the query or use a "
    "different image_search provider rather than retrying the same query."
)


class ImageSearchError(ProviderUnavailableError):
    """An image search could not be performed.

    Default ``kind`` is ``unavailable`` (transport failure); the two subclasses
    narrow it to the cases worth telling apart.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(PROVIDER, message, status_code=status_code)


class ImageSearchRateLimitedError(ProviderRateLimitedError, ImageSearchError):
    """The engine answered, but refused the query as too frequent."""


class ImageSearchNotInstalledError(ProviderNotConfiguredError, ImageSearchError):
    """The optional ``ddgs`` package is not installed."""


def _search_images(
    query: str,
    max_results: int = 5,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    size: str | None = None,
    color: str | None = None,
    type_image: str | None = None,
    layout: str | None = None,
    license_image: str | None = None,
) -> list[dict]:
    """
    Execute image search using DuckDuckGo.

    Returns an empty list **only** when the engine answered with no images.

    Args:
        query: Search keywords
        max_results: Maximum number of results
        region: Search region
        safesearch: Safe search level
        size: Image size (Small/Medium/Large/Wallpaper)
        color: Color filter
        type_image: Image type (photo/clipart/gif/transparent/line)
        layout: Layout (Square/Tall/Wide)
        license_image: License filter

    Returns:
        List of search results; empty **only** when the engine answered with none.

    Raises:
        ImageSearchNotInstalledError: The optional ``ddgs`` package is missing.
        ImageSearchError: The engine could not be reached, or refused the query.
    """
    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise ImageSearchNotInstalledError("ddgs library not installed. Run: pip install ddgs") from exc

    try:
        ddgs = DDGS(timeout=30)
    except Exception as exc:
        # Session construction is part of the network call, so its failures
        # belong to the same typed channel as everything else. Letting it
        # escape the tool boundary is what this arm exists to prevent.
        raise ImageSearchError(f"{type(exc).__name__}: could not open a DuckDuckGo session: {str(exc)[:160]}") from exc

    try:
        kwargs = {
            "region": region,
            "safesearch": safesearch,
            "max_results": _clamp_max_results(max_results),
        }

        if size:
            kwargs["size"] = size
        if color:
            kwargs["color"] = color
        if type_image:
            kwargs["type_image"] = type_image
        if layout:
            kwargs["layout"] = layout
        if license_image:
            kwargs["license_image"] = license_image

        results = ddgs.images(query, **kwargs)
        return list(results) if results else []

    except Exception as exc:
        raise _as_image_error(exc) from exc


def _as_image_error(exc: Exception) -> ImageSearchError:
    """Reuse the text-search classifier so both bundled tools report one vocabulary."""
    if isinstance(exc, ImageSearchError):
        return exc
    classified = _classify_engine_error(exc)
    if isinstance(classified, ProviderRateLimitedError):
        return ImageSearchRateLimitedError(classified.message)
    return ImageSearchError(classified.message)


def _hint_for(exc: ImageSearchError) -> str:
    """One actionable sentence per failure kind."""
    if isinstance(exc, ImageSearchNotInstalledError):
        return "install the optional package with `pip install ddgs`, or configure a different image_search provider"
    if isinstance(exc, ImageSearchRateLimitedError):
        return "the engine is rate-limiting this client; wait before retrying, or configure a different image_search provider"
    return "the image engine could not be reached; this is not evidence that the web has no matching images"


@tool("image_search", parse_docstring=True)
def image_search_tool(
    query: str,
    max_results: int = 5,
    size: str | None = None,
    type_image: str | None = None,
    layout: str | None = None,
) -> str:
    """Search for images online. Use this tool BEFORE image generation to find reference images for characters, portraits, objects, scenes, or any content requiring visual accuracy.

    **When to use:**
    - Before generating character/portrait images: search for similar poses, expressions, styles
    - Before generating specific objects/products: search for accurate visual references
    - Before generating scenes/locations: search for architectural or environmental references
    - Before generating fashion/clothing: search for style and detail references

    The returned image URLs can be used as reference images in image generation to significantly improve quality.

    An empty `results` list means the image engine answered and had nothing for this
    query. When the search did not run at all the response instead carries an `error`
    object whose `kind` is one of `unavailable` (the engine could not be reached),
    `rate_limited` (retry later) or `not_configured` (the optional `ddgs` package is
    not installed). Never report an `error` as "no such images exist".

    Args:
        query: Search keywords describing the images you want to find. Be specific for better results (e.g., "Japanese woman street photography 1990s" instead of just "woman").
        max_results: Maximum number of images to return (1-20). Default is 5.
        size: Image size filter. Options: "Small", "Medium", "Large", "Wallpaper". Use "Large" for reference images.
        type_image: Image type filter. Options: "photo", "clipart", "gif", "transparent", "line". Use "photo" for realistic references.
        layout: Layout filter. Options: "Square", "Tall", "Wide". Choose based on your generation needs.
    """
    config = get_app_config().get_tool_config("image_search")

    # Override max_results from config if set
    if config is not None and "max_results" in config.model_extra:
        max_results = config.model_extra.get("max_results", max_results)

    max_results = _clamp_max_results(max_results)

    try:
        results = _search_images(
            query=query,
            max_results=max_results,
            size=size,
            type_image=type_image,
            layout=layout,
        )
    except ImageSearchError as exc:
        logger.error("DuckDuckGo image search failed: %s", exc)
        payload: dict[str, object] = {"error": exc.to_dict(), "query": query}
        hint = _hint_for(exc)
        if hint:
            payload["hint"] = hint
        return json.dumps(payload, indent=2, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 - a tool must return, not explode
        logger.exception("DuckDuckGo image search failed unexpectedly")
        return json.dumps(
            {
                "error": {
                    "kind": "unavailable",
                    "provider": PROVIDER,
                    "message": f"{type(exc).__name__}: {str(exc)[:200]}",
                    "status_code": None,
                    "retry_after_seconds": None,
                },
                "query": query,
                "hint": "the image search did not run; this is not evidence that the web has no matching images",
            },
            indent=2,
            ensure_ascii=False,
        )

    if not results:
        return json.dumps(
            {"query": query, "total_results": 0, "results": [], "note": EMPTY_RESULT_NOTE},
            indent=2,
            ensure_ascii=False,
        )

    normalized_results = [
        {
            "title": r.get("title", ""),
            "image_url": r.get("image", ""),
            "thumbnail_url": r.get("thumbnail", ""),
        }
        for r in results
    ]

    output = {
        "query": query,
        "total_results": len(normalized_results),
        "results": normalized_results,
        "usage_hint": "Use the 'image_url' values as reference images in image generation. Download them first if needed.",
    }

    return json.dumps(output, indent=2, ensure_ascii=False)
