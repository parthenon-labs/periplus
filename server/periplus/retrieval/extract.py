"""Turning HTML into the text a model should actually read.

This is the cheapest lever in the whole pipeline. A travel page is typically 80–90%
navigation, cookie banners, newsletter prompts, related-article rails and footers. Feeding
that to the research stage costs real money per run and, worse, buries the three sentences
that matter among a hundred that do not.

The approach is structural, not statistical: drop the elements that are boilerplate by
tag or by name, prefer the page's own declared main region, then keep block-level text.
No readability scoring, because a wrong guess here silently deletes content, and a page
that survives with some clutter is a far better failure than a page missing its hours.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime

from selectolax.parser import HTMLParser, Node

from periplus.models import SourceKind
from periplus.retrieval.document import SourceDocument, classify_source

#: Tags that never carry article text.
_DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "iframe",
    "form",
    "button",
    "select",
    "nav",
    "header",
    "footer",
    "aside",
)

#: Substrings in ``class``/``id``/``role`` that mark a container as furniture.
_DROP_HINTS = (
    "nav",
    "menu",
    "breadcrumb",
    "sidebar",
    "side-bar",
    "footer",
    "header",
    "masthead",
    "cookie",
    "consent",
    "gdpr",
    "banner",
    "popup",
    "modal",
    "newsletter",
    "subscribe",
    "signup",
    "social",
    "share",
    "comment",
    "disqus",
    "related",
    "recommend",
    "promo",
    "advert",
    "sponsor",
    "widget",
    "skip-link",
    "pagination",
    "toolbar",
    "search-form",
)

#: Names that look like furniture but routinely wrap the content on travel sites.
_KEEP_HINTS = ("content", "article", "main", "post", "entry", "body")

#: Where the real text lives, most trustworthy first.
_MAIN_SELECTORS = (
    "article",
    "main",
    "[role=main]",
    "#content",
    ".content",
    "#main",
    ".main",
    ".post",
    ".entry-content",
)

#: Block elements whose text becomes its own line.
_BLOCK_TAGS = (
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "dt",
    "dd",
    "td",
    "th",
    "blockquote",
    "pre",
    "figcaption",
    "address",
    "time",
    "summary",
)

_DATE_META = (
    'meta[property="article:published_time"]',
    'meta[property="article:modified_time"]',
    'meta[name="article:published_time"]',
    'meta[itemprop="datePublished"]',
    'meta[name="datePublished"]',
    'meta[name="date"]',
    'meta[name="pubdate"]',
    'meta[name="dc.date"]',
    'meta[name="last-modified"]',
    'meta[property="og:updated_time"]',
)

_WHITESPACE = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_MIN_LINE_CHARS = 2


def extract(
    html: str,
    url: str,
    *,
    subject: str | None = None,
    query: str | None = None,
    fallback_title: str | None = None,
) -> SourceDocument:
    """Parse a page into a :class:`SourceDocument`."""
    tree = HTMLParser(html)
    title = _title(tree) or fallback_title
    published = _published_date(tree)
    language = _language(tree)

    _strip(tree)
    root = _main_region(tree)
    text = _text_of(root) if root is not None else ""

    return SourceDocument(
        url=url,
        text=text,
        title=title,
        published_at=published,
        source_kind=classify_source(url, subject=subject),
        language=language,
        query=query,
    )


def _strip(tree: HTMLParser) -> None:
    for tag in _DROP_TAGS:
        for node in tree.css(tag):
            node.decompose()
    for node in tree.css("[class],[id],[role]"):
        if _is_furniture(node):
            node.decompose()


def _is_furniture(node: Node) -> bool:
    attrs = node.attributes
    names = " ".join(
        value or "" for key, value in attrs.items() if key in {"class", "id", "role"}
    ).lower()
    if not names:
        return False
    if any(hint in names for hint in _KEEP_HINTS):
        return False
    return any(hint in names for hint in _DROP_HINTS)


def _main_region(tree: HTMLParser) -> Node | None:
    """The largest node matching a main-content selector, or the body."""
    best: Node | None = None
    best_size = 0
    for selector in _MAIN_SELECTORS:
        for node in tree.css(selector):
            size = len(node.text(deep=True, separator=" ", strip=True) or "")
            if size > best_size:
                best, best_size = node, size

    body = tree.body or tree.root
    if best is None:
        return body

    # A "main" region smaller than a fifth of the page usually means the selector matched a
    # teaser rail rather than the article.
    body_size = len(body.text(deep=True, separator=" ", strip=True) or "") if body else 0
    if body_size and best_size * 5 < body_size:
        return body
    return best


def _text_of(root: Node) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for node in root.css(",".join(_BLOCK_TAGS)):
        raw = node.text(deep=True, separator=" ", strip=True)
        if not raw:
            continue
        line = _WHITESPACE.sub(" ", raw).strip()
        if len(line) < _MIN_LINE_CHARS:
            continue
        # Nested blocks (a <p> inside an <li>) would otherwise duplicate their text, and a
        # repeated line is a repeated line in the prompt.
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)

    if not lines:
        fallback = root.text(deep=True, separator="\n", strip=True) or ""
        lines = [_WHITESPACE.sub(" ", part).strip() for part in fallback.splitlines()]
        lines = [line for line in lines if len(line) >= _MIN_LINE_CHARS]

    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _title(tree: HTMLParser) -> str | None:
    for selector, attribute in (
        ('meta[property="og:title"]', "content"),
        ('meta[name="twitter:title"]', "content"),
    ):
        node = tree.css_first(selector)
        if node is not None:
            value = (node.attributes.get(attribute) or "").strip()
            if value:
                return value

    for selector in ("title", "h1"):
        node = tree.css_first(selector)
        if node is not None:
            value = (node.text(strip=True) or "").strip()
            if value:
                return value
    return None


def _language(tree: HTMLParser) -> str | None:
    node = tree.css_first("html")
    if node is None:
        return None
    value = (node.attributes.get("lang") or "").strip()
    return value.split("-")[0].lower() or None if value else None


def _published_date(tree: HTMLParser) -> date | None:
    """The page's own date, if it states one.

    Only a date the publisher declared counts. Guessing one from the text would hand the
    freshness rules a number nobody stands behind, and a wrong date there means stale
    prices are scheduled as verified.
    """
    for selector in _DATE_META:
        node = tree.css_first(selector)
        if node is None:
            continue
        parsed = parse_date(node.attributes.get("content"))
        if parsed is not None:
            return parsed

    for node in tree.css("time[datetime]"):
        parsed = parse_date(node.attributes.get("datetime"))
        if parsed is not None:
            return parsed

    return _json_ld_date(tree)


def _json_ld_date(tree: HTMLParser) -> date | None:
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            payload = json.loads(node.text(deep=True) or "")
        except (ValueError, TypeError):
            continue
        for entry in payload if isinstance(payload, list) else [payload]:
            if not isinstance(entry, dict):
                continue
            for key in ("datePublished", "dateModified", "uploadDate"):
                parsed = parse_date(entry.get(key))
                if parsed is not None:
                    return parsed
    return None


def parse_date(value: str | None) -> date | None:
    """Parse a declared date string; ``None`` when it is absent or unrecognised."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d %B %Y", "%B %d, %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:32], fmt).date()
        except ValueError:
            continue
    return None


__all__ = ["SourceDocument", "SourceKind", "extract", "parse_date"]
