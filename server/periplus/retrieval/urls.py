"""URL normalisation.

Two links to the same page arrive in a dozen shapes: with and without ``www``, with a
tracking parameter, with a fragment, with the query in a different order. Every one of
those is a separate fetch and a separate copy of the same text in a prompt unless they
collapse to one key first. This module is what makes the page cache and the evidence
deduplicator actually hit.
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: Parameters that identify a campaign, not a document.
_TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "mc_", "hsa_")
_TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "igshid",
        "twclid",
        "yclid",
        "ref",
        "ref_src",
        "referrer",
        "source",
        "spm",
        "cmpid",
        "campaign_id",
        "_ga",
        "_gl",
        "s_kwcid",
        "vero_id",
    }
)

_DEFAULT_PORTS = {"http": "80", "https": "443"}

#: Suffixes where the meaningful name sits one label further left (``bbc.co.uk``).
_COMPOUND_SUFFIXES = frozenset(
    {"co.uk", "org.uk", "gov.uk", "ac.uk", "com.au", "gov.au", "org.au", "edu.au", "co.jp", "co.nz"}
)


def normalise_url(url: str) -> str:
    """Collapse a URL to the canonical form used as a cache and dedup key.

    Drops the fragment and tracking parameters, lowercases scheme and host, strips a
    default port and a leading ``www.``, and sorts the surviving query so parameter order
    stops producing distinct keys. Path case is preserved: plenty of servers care.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"

    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host
    if parts.port is not None and str(parts.port) != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{parts.port}"

    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"

    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking(key)
    ]
    query = urlencode(sorted(kept))

    return urlunsplit((scheme, netloc, path, query, ""))


def _is_tracking(key: str) -> bool:
    lowered = key.lower()
    return lowered in _TRACKING_PARAMS or lowered.startswith(_TRACKING_PREFIXES)


def canonical_key(url: str) -> str:
    """Identity of the *document*, ignoring how it is addressed.

    Same as :func:`normalise_url` minus the scheme, because ``http`` and ``https`` on the
    same host and path serve the same page — and a run that reads both has paid twice for
    one source and then presented it as two.
    """
    return normalise_url(url).split("//", 1)[-1]


def url_key(url: str) -> str:
    """Stable filesystem-safe digest of :func:`canonical_key`."""
    return hashlib.sha256(canonical_key(url).encode()).hexdigest()[:32]


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def registrable_domain(url_or_host: str) -> str:
    """Best-effort ``example.co.uk`` from a URL or host.

    Deliberately not a public-suffix implementation: this feeds source classification and
    a same-site check, both of which degrade gracefully when the guess is imperfect.
    """
    host = host_of(url_or_host) if "//" in url_or_host or "/" in url_or_host else url_or_host
    host = host.lower().removeprefix("www.")
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if ".".join(labels[-2:]) in _COMPOUND_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def same_site(left: str, right: str) -> bool:
    return registrable_domain(left) == registrable_domain(right)
