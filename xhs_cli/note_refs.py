"""Helpers for resolving and persisting note references across commands."""

from __future__ import annotations

import re

import click
import httpx

from .cookies import get_note_by_index, save_note_index
from .formatter import parse_note_reference


def resolve_note_reference(id_or_url: str, *, xsec_token: str = "") -> tuple[str, str, str]:
    """Resolve a note reference from URL/ID or the last listing index."""
    if id_or_url.isdigit():
        entry = get_note_by_index(int(id_or_url))
        if entry is None:
            raise click.UsageError(
                f"Index {id_or_url} not found — run a listing command first "
                "(search / feed / hot / user-posts / favorites / my-notes)"
            )
        return (
            entry["note_id"],
            xsec_token or entry.get("xsec_token", ""),
            entry.get("xsec_source", ""),
        )

    if "://" in id_or_url:
        # Full URLs, xhslink short links, or app share text containing one.
        id_or_url = expand_share_link(id_or_url)
    note_id, url_token, url_source = parse_note_reference(id_or_url)
    return note_id, xsec_token or url_token, url_source


def save_index_from_items(data: dict, *, xsec_source: str) -> None:
    """Persist ordered note references from list-style responses."""
    entries = []
    for item in data.get("items", []):
        note_card = item.get("note_card", {})
        note_id = item.get("id", note_card.get("note_id", ""))
        token = item.get("xsec_token", note_card.get("xsec_token", ""))
        if note_id:
            entries.append({
                "note_id": note_id,
                "xsec_token": token,
                "xsec_source": xsec_source if token else "",
            })
    save_note_index(entries)


def save_index_from_notes(notes: list[dict]) -> None:
    """Persist ordered note references from paged note payloads."""
    save_note_index([
        {
            "note_id": str(note.get("note_id", note.get("id", ""))).strip(),
            "xsec_token": str(note.get("xsec_token", "")).strip(),
            "xsec_source": "",
        }
        for note in notes
        if str(note.get("note_id", note.get("id", ""))).strip()
    ])


_URL_RE = re.compile(r"https?://[^\s\u3000，。！]+")
_SHORT_LINK_DOMAINS = ("xhslink.com", "xhslink.cn")


def _is_short_link(url: str) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in ("http", "https") and any(
        host == domain or host.endswith("." + domain) for domain in _SHORT_LINK_DOMAINS
    )


def _redirect_client() -> httpx.Client:
    from .constants import USER_AGENT

    return httpx.Client(timeout=15, follow_redirects=False, headers={"user-agent": USER_AGENT})


def expand_share_link(text: str, *, max_hops: int = 5) -> str:
    """Return the full note URL from a URL or app share text, following xhslink.com redirects."""
    match = _URL_RE.search(text)
    if not match:
        return text.strip()
    url = match.group(0)
    if not _is_short_link(url):
        return url
    with _redirect_client() as http:
        for _ in range(max_hops):
            resp = http.get(url)
            location = resp.headers.get("location")
            if resp.status_code not in (301, 302, 303, 307, 308) or not location:
                break
            url = location
            if not _is_short_link(url):
                break
    return url
