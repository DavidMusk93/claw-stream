"""scrapers/v2/filters.py — Collection-stage visibility filter

Decide at sync time whether a work belongs in the library at all. Hidden
categories:
- VR works (【VR】/[VR] in the title, or a VR resolution tag like [8KVR])
- Multi-star (共演/omnibus) works: ijavtorrent card actress-link count > 1,
  共演/オムニバス keywords, cast-list titles, or a title mentioning another
  rostered star

Filtering here (instead of at display time) keeps the DB, /api/stars
ordering, and every downstream consumer consistent: there is exactly one
notion of "the star's titles". The frontend previously re-implemented this
with matching regexes, which let hidden titles drive the star ordering
(miru sorted by hidden omnibus MIRD-282).

Recall is intentionally conservative — a false positive drops a legit solo
work permanently, so the rules only fire on strong signals. The same module
backs the offline cleanup script (scripts/drop_hidden_titles.py).
"""

from __future__ import annotations

import re

from scrapers.v2.schemas import VideoItem

VR_TITLE_RE = re.compile(r"(【VR】|\[VR\])", re.IGNORECASE)
MULTI_KEYWORD_RE = re.compile(r"共演|オムニバス")

# Latin cast list: two or more comma-separated "First Last" names.
_LATIN_NAME = r"[A-Z][a-z]+ [A-Z][a-z]+"
COLON_CAST_RE = re.compile(rf"[:：]\s*(?:{_LATIN_NAME},\s*)+{_LATIN_NAME}")
WHOLE_CAST_RE = re.compile(rf"^(?:{_LATIN_NAME},\s*){{2,}}{_LATIN_NAME}\.?\s*$")
# Japanese cast run: 3+ kanji names separated by 、or space.
JP_CAST_RE = re.compile(r"(?:[一-龥]{2,4}[、\s]){2,}[一-龥]{2,4}")

MIN_NAME_LEN = 3  # shorter aliases are noise-prone as substring matches


def hidden_reason(
    item: VideoItem,
    own_names: list[str] | None = None,
    roster_names: list[str] | None = None,
) -> str | None:
    """Return why the item must not enter the library, or None to keep it.

    ``own_names`` (the synced star's name/jp) are excluded from the roster
    mention check — sukebei uploaders routinely tag torrents with the star's
    own name.
    """
    if item.star_count > 1:
        return "star_count"

    text = item.title.strip()
    if text and VR_TITLE_RE.search(text):
        return "vr"
    if any("vr" in m.resolution.lower() for m in item.magnets):
        return "vr"
    if not text:
        return None

    if MULTI_KEYWORD_RE.search(text):
        return "keyword"

    lower = text.lower()
    own = {n.strip().lower() for n in (own_names or [])}
    for name in roster_names or []:
        nl = name.strip().lower()
        if len(nl) >= MIN_NAME_LEN and nl not in own and nl in lower:
            return "roster"

    if COLON_CAST_RE.search(text) or WHOLE_CAST_RE.search(text) or JP_CAST_RE.search(text):
        return "cast"
    return None


def drop_hidden(
    items: list[VideoItem],
    own_names: list[str] | None = None,
    roster_names: list[str] | None = None,
) -> tuple[list[VideoItem], list[tuple[str, str]]]:
    """Split items into (kept, [(code, reason)])."""
    kept: list[VideoItem] = []
    dropped: list[tuple[str, str]] = []
    for it in items:
        reason = hidden_reason(it, own_names, roster_names)
        if reason:
            dropped.append((it.code, reason))
        else:
            kept.append(it)
    return kept, dropped
