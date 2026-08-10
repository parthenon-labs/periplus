"""The unit of retrieved reading material, and how it becomes evidence.

A :class:`SourceDocument` is a cleaned page: the text a research agent actually reads.
:class:`~periplus.models.Evidence` is narrower — the exact passage a claim rests on.
Minting one from the other goes through :meth:`SourceDocument.evidence_for`, which
refuses a snippet that does not occur in the page.

That refusal is the point. A model asked to quote its source will occasionally produce a
quote that is *almost* there: a tidied number, a merged sentence, a plausible paraphrase
wearing quotation marks. Verification downstream would then be comparing a claim against
a snippet the model wrote, which checks nothing at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from periplus.models import Evidence, SourceKind

#: Domains whose kind is knowable without looking at the page.
_KNOWN_HOSTS: dict[str, SourceKind] = {
    "wikipedia.org": SourceKind.ENCYCLOPEDIA,
    "wikivoyage.org": SourceKind.ENCYCLOPEDIA,
    "wikidata.org": SourceKind.ENCYCLOPEDIA,
    "britannica.com": SourceKind.ENCYCLOPEDIA,
    "tripadvisor.com": SourceKind.REVIEW,
    "tripadvisor.co.uk": SourceKind.REVIEW,
    "yelp.com": SourceKind.REVIEW,
    "reddit.com": SourceKind.REVIEW,
    "quora.com": SourceKind.REVIEW,
    "trustpilot.com": SourceKind.REVIEW,
    "lonelyplanet.com": SourceKind.GUIDE,
    "timeout.com": SourceKind.GUIDE,
    "cntraveler.com": SourceKind.GUIDE,
    "atlasobscura.com": SourceKind.GUIDE,
    "roughguides.com": SourceKind.GUIDE,
    "fodors.com": SourceKind.GUIDE,
    "afar.com": SourceKind.GUIDE,
    "nomadicmatt.com": SourceKind.GUIDE,
    "openstreetmap.org": SourceKind.MAP,
    "google.com": SourceKind.MAP,
    "citymapper.com": SourceKind.MAP,
}

#: Host suffixes that identify an official public body in most jurisdictions.
_GOVERNMENT_SUFFIXES = (".gov", ".gov.uk", ".gov.au", ".gob.es", ".gouv.fr", ".go.jp", ".govt.nz")

_WHITESPACE = re.compile(r"\s+")


def _fold(text: str) -> str:
    """Whitespace-insensitive, case-insensitive form used for containment checks."""
    return _WHITESPACE.sub(" ", text).strip().casefold()


def _is_government(host: str) -> bool:
    """``gov.uk`` is itself a government host, not only ``x.gov.uk``."""
    return (
        host.endswith(_GOVERNMENT_SUFFIXES)
        or any(host == suffix.lstrip(".") for suffix in _GOVERNMENT_SUFFIXES)
        or ".gov." in host
    )


def _is_operator_domain(domain: str, subject: str) -> bool:
    """Whether ``domain`` looks like the venue's own site rather than a reseller's.

    The strict direction matters. ``museodelprado.es`` is the museum;
    ``museodelprado-tickets.org`` is a ticket agency, and treating its prices as the
    operator's own would put a markup into the itinerary wearing the museum's authority.
    So the domain stem may be *shorter* than the name — ``prado.es`` — but never a longer
    string that merely contains it.
    """
    name = re.sub(r"[^a-z0-9]", "", subject.casefold())
    stem = re.sub(r"[^a-z0-9]", "", domain.split(".")[0])
    if len(name) < 4 or len(stem) < 4:
        return False
    return stem == name or stem in name


def classify_source(url: str, *, subject: str | None = None) -> SourceKind:
    """Guess what kind of source a URL is.

    ``subject`` lets an operator's own site be recognised: when the domain is the venue's
    own name, this is the horse's mouth, and its opening hours outrank a guidebook's.
    """
    from periplus.retrieval.urls import host_of, registrable_domain

    host = host_of(url)
    if not host:
        return SourceKind.UNKNOWN

    if _is_government(host):
        return SourceKind.GOVERNMENT

    domain = registrable_domain(host)
    for known, kind in _KNOWN_HOSTS.items():
        if domain == known or host.endswith("." + known):
            return kind

    if subject and _is_operator_domain(domain, subject):
        return SourceKind.OFFICIAL

    return SourceKind.UNKNOWN


@dataclass(slots=True)
class SourceDocument:
    """One fetched, de-boilerplated page."""

    url: str
    text: str
    title: str | None = None
    published_at: date | None = None
    source_kind: SourceKind = SourceKind.UNKNOWN
    language: str | None = None
    query: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    from_cache: bool = False

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def approx_tokens(self) -> int:
        """Rough budget figure. Four characters per token is close enough to plan with."""
        return self.char_count // 4

    def contains(self, snippet: str) -> bool:
        return bool(snippet.strip()) and _fold(snippet) in _fold(self.text)

    def evidence_for(self, snippet: str, *, query: str | None = None) -> Evidence:
        """Bind a verbatim passage of this page to an :class:`Evidence` record.

        Raises :class:`SnippetNotFound` if the passage is not in the page, because an
        unverifiable quote is worse than no quote — it looks like provenance.
        """
        if not self.contains(snippet):
            raise SnippetNotFound(self.url, snippet)
        return Evidence(
            url=self.url,
            title=self.title,
            snippet=snippet.strip(),
            source_kind=self.source_kind,
            published_at=self.published_at,
            fetched_at=self.fetched_at,
            query=query or self.query,
        )

    def truncated(self, max_chars: int) -> SourceDocument:
        """A copy clipped to a character budget, cut at a paragraph break where possible."""
        if self.char_count <= max_chars:
            return self
        window = self.text[:max_chars]
        cut = window.rfind("\n")
        if cut < max_chars // 2:
            cut = max_chars
        clipped = window[:cut].rstrip()
        return SourceDocument(
            url=self.url,
            text=clipped,
            title=self.title,
            published_at=self.published_at,
            source_kind=self.source_kind,
            language=self.language,
            query=self.query,
            fetched_at=self.fetched_at,
            from_cache=self.from_cache,
        )


class SnippetNotFound(ValueError):
    """A quoted passage does not appear in the document it was attributed to."""

    def __init__(self, url: str, snippet: str) -> None:
        preview = snippet.strip()[:120]
        super().__init__(f"snippet not found in {url}: {preview!r}")
        self.url = url
        self.snippet = snippet
