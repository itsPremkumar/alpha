"""Tests for readability extraction fallback behavior."""

import subprocess

import pytest

from alpha.utils.readability import Article, ReadabilityExtractor


def test_extract_article_falls_back_when_readability_js_fails(monkeypatch):
    """When Node-based readability fails, extraction should fall back to Python mode."""

    calls: list[bool] = []

    def _fake_simple_json_from_html_string(html: str, use_readability: bool = False):
        calls.append(use_readability)
        if use_readability:
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=["node", "ExtractArticle.js"],
                stderr="boom",
            )
        return {"title": "Fallback Title", "content": "<p>Fallback Content</p>"}

    monkeypatch.setattr(
        "alpha.utils.readability.simple_json_from_html_string",
        _fake_simple_json_from_html_string,
    )

    article = ReadabilityExtractor().extract_article("<html><body>test</body></html>")

    assert calls == [True, False]
    assert article.title == "Fallback Title"
    assert article.html_content == "<p>Fallback Content</p>"


def test_extract_article_re_raises_unexpected_exception(monkeypatch):
    """Unexpected errors should be surfaced instead of silently falling back."""

    calls: list[bool] = []

    def _fake_simple_json_from_html_string(html: str, use_readability: bool = False):
        calls.append(use_readability)
        if use_readability:
            raise RuntimeError("unexpected parser failure")
        return {"title": "Should Not Reach Fallback", "content": "<p>Fallback</p>"}

    monkeypatch.setattr(
        "alpha.utils.readability.simple_json_from_html_string",
        _fake_simple_json_from_html_string,
    )

    with pytest.raises(RuntimeError, match="unexpected parser failure"):
        ReadabilityExtractor().extract_article("<html><body>test</body></html>")
    assert calls == [True]


# ---------------------------------------------------------------------------
# Article.url — to_message must not crash on an article containing an image
# ---------------------------------------------------------------------------


def test_article_stores_the_source_url() -> None:
    """``extract_article`` must carry the source URL onto the Article.

    ``Article`` declared ``url: str`` at class level, but a bare
    annotation creates no attribute and ``__init__`` never assigned it,
    so ``to_message`` raised ``AttributeError`` on any article whose
    markdown contained an image. The URL is the base ``urljoin``
    resolves image paths against, so losing it is a real defect, not a
    cosmetic one.
    """
    article = ReadabilityExtractor().extract_article("<html><body>test</body></html>", url="https://example.com/page")
    assert article.url == "https://example.com/page"


def test_article_to_message_resolves_image_against_source_url() -> None:
    """An image in the article must resolve against the source URL.

    Regression: ``to_message`` accessed ``self.url`` before any code
    path assigned it, so an article with an image raised
    ``AttributeError``. The image URL must be joined with the source
    page, not returned bare.
    """
    article = Article(
        title="T",
        html_content="![alt](pic.png)",
        url="https://example.com/page",
    )
    messages = article.to_message()
    image_blocks = [m for m in messages if m["type"] == "image_url"]
    assert image_blocks, "the image block must survive extraction"
    assert image_blocks[0]["image_url"]["url"] == "https://example.com/pic.png"


def test_article_to_message_without_url_leaves_relative_image_bare() -> None:
    """A missing source URL must not fabricate a base.

    With no source URL the honest fallback is the unresolved relative
    path — inventing a base would point images at the wrong host.
    """
    article = Article(title="T", html_content="![alt](pic.png)")
    messages = article.to_message()
    image_blocks = [m for m in messages if m["type"] == "image_url"]
    assert image_blocks
    assert image_blocks[0]["image_url"]["url"] == "pic.png"


def test_article_to_message_without_images_never_touched_url() -> None:
    """A text-only article must not depend on ``url`` at all.

    The crash was image-gated: text-only articles worked before the fix
    and must keep working, which pins the fix to the image path only.
    """
    article = Article(title="T", html_content="plain text only")
    messages = article.to_message()
    assert messages == [{"type": "text", "text": "# T\n\nplain text only"}]
