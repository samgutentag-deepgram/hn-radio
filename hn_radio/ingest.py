"""Stage 1 - Fetch. Pull the HN front page and top comments via the public Firebase API.

No auth, no scraping. Two calls matter: `topstories.json` for the ranked id list, and
`item/{id}.json` for each story or comment. Everything else is shaping that into our models.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import List, Optional

from . import config
from ._http import get_json
from .models import Comment, Story


def _item(item_id: int) -> Optional[dict]:
    """Fetch a single HN item (story, comment, job, ...). None on failure or if empty."""
    url = f"{config.HN_API_BASE}/item/{item_id}.json"
    try:
        return get_json(
            url,
            timeout=config.HTTP_TIMEOUT_SECONDS,
            retries=config.HTTP_RETRIES,
            backoff=config.HTTP_BACKOFF_SECONDS,
        )
    except Exception:
        return None


def _top_story_ids() -> List[int]:
    url = f"{config.HN_API_BASE}/topstories.json"
    return get_json(
        url,
        timeout=config.HTTP_TIMEOUT_SECONDS,
        retries=config.HTTP_RETRIES,
        backoff=config.HTTP_BACKOFF_SECONDS,
    )


def _to_story(item: dict, rank: int) -> Story:
    return Story(
        id=item["id"],
        title=item.get("title", "(untitled)"),
        url=item.get("url"),  # None for text/self posts; that's fine
        points=item.get("score", 0),
        author=item.get("by", "unknown"),
        num_comments=item.get("descendants", 0),
        rank=rank,
        kids=item.get("kids", []) or [],
    )


def fetch_front_page(n: int = config.N_STORIES) -> List[Story]:
    """Return the top `n` front-page *stories* (skipping jobs/polls), in rank order.

    Walks the ranked id list and keeps fetching until it has `n` real stories, so a job
    posting near the top does not cost us a slot.
    """
    ids = _top_story_ids()
    stories: List[Story] = []
    rank = 0
    for item_id in ids:
        if len(stories) >= n:
            break
        item = _item(item_id)
        if not item or item.get("type") != "story" or item.get("dead") or item.get("deleted"):
            continue
        rank += 1
        stories.append(_to_story(item, rank))
    if not stories:
        raise RuntimeError("HN front page fetch returned no usable stories.")
    return stories


ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search"


def _algolia_search(start_ts: int, end_ts: int, tag: str) -> List[dict]:
    url = (f"{ALGOLIA_SEARCH}?tags={tag}"
           f"&numericFilters=created_at_i>={start_ts},created_at_i<{end_ts}&hitsPerPage=100")
    try:
        data = get_json(url, timeout=config.HTTP_TIMEOUT_SECONDS,
                        retries=config.HTTP_RETRIES, backoff=config.HTTP_BACKOFF_SECONDS)
        return data.get("hits", []) or []
    except Exception:
        return []


def fetch_front_page_for_date(day: date, pool: int = config.N_STORIES) -> List[Story]:
    """Approximate a past day's front page via the Algolia HN Search API (top `pool` stories).

    HN has no historical front-page archive, so this is an approximation: stories that carried the
    `front_page` tag within that day, ranked by points (falling back to that day's top stories).
    For today (or the future), it defers to the live front page. Kids/comments are populated lazily
    by the caller for the few selected stories.
    """
    if day >= datetime.now(config.PACIFIC).date():
        return fetch_front_page(pool)

    # Window to exactly the Pacific calendar day [midnight, next midnight), so each day's episode
    # covers only posts submitted that Pacific day (no day-to-day overlap).
    start = int(datetime.combine(day, time.min, tzinfo=config.PACIFIC).timestamp())
    end = int(datetime.combine(day + timedelta(days=1), time.min, tzinfo=config.PACIFIC).timestamp())
    # top stories of that day by points (a solid front-page proxy; the front_page tag is too sparse)
    hits = _algolia_search(start, end, "story")

    seen, ranked = set(), []
    for h in hits:
        oid = h.get("objectID")
        if not oid or oid in seen:
            continue
        seen.add(oid)
        ranked.append(h)
    ranked.sort(key=lambda h: h.get("points") or 0, reverse=True)

    stories: List[Story] = []
    for h in ranked[:pool]:
        try:
            sid = int(h["objectID"])
        except (KeyError, ValueError, TypeError):
            continue
        stories.append(Story(
            id=sid,
            title=h.get("title") or "(untitled)",
            url=h.get("url"),
            points=h.get("points") or 0,
            author=h.get("author") or "unknown",
            num_comments=h.get("num_comments") or 0,
            rank=len(stories) + 1,
            kids=[],  # filled lazily by the caller (only the selected top thread needs them)
        ))
    if not stories:
        raise RuntimeError(f"No stories found for {day.isoformat()} via Algolia.")
    return stories


def populate_kids(stories: List[Story]) -> None:
    """Fill in top-level comment ids for stories that lack them (Algolia hits don't include kids)."""
    for s in stories:
        if not s.kids:
            item = _item(s.id) or {}
            s.kids = item.get("kids", []) or []


def pick_top_thread(stories: List[Story]) -> Optional[Story]:
    """The most comment-heavy story that actually has comments. None if none qualify."""
    with_comments = [s for s in stories if s.kids]
    if not with_comments:
        return None
    return max(with_comments, key=lambda s: s.num_comments)


def fetch_top_comments(story: Story, k: int = config.N_COMMENTS) -> List[Comment]:
    """Top `k` top-level comments for a story, in HN's ranked order, skipping dead/deleted.

    Raw comment HTML is preserved here; cleaning for speech happens in script_assembly so
    ingest stays a pure data-fetch stage.
    """
    comments: List[Comment] = []
    for kid in story.kids:
        if len(comments) >= k:
            break
        item = _item(kid)
        if not item or item.get("type") != "comment":
            continue
        if item.get("dead") or item.get("deleted") or not item.get("text"):
            continue
        comments.append(
            Comment(
                id=item["id"],
                author=item.get("by", "unknown"),
                text=item["text"],
            )
        )
    return comments
