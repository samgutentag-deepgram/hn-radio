"""Stage v2-2 - Source fetch. Read the actual thing a story links to, not just the headline.

For GitHub repos we pull the README via the API; for everything else we fetch the HTML and
extract readable text. Then an extractive summary (first meaningful sentences) gives a desk
something real to say. No LLM here: this is the offline stand-in that also feeds the LLM writer
later. Any failure falls back to title-only, recorded on the story so the writer can adapt.

The network entry point is `enrich_story`. The parsing/summarizing helpers are pure and tested.
"""

from __future__ import annotations

import html
import re
from typing import Optional, Tuple

from . import config
from ._http import get_text
from .models import Story

MAX_SOURCE_CHARS = 4000       # cap stored source text
SUMMARY_SENTENCES = 3

_GITHUB_RE = re.compile(r"https?://github\.com/([^/\s]+)/([^/\s#?]+)", re.IGNORECASE)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript|svg)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_P_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKLINES_RE = re.compile(r"\n\s*\n+")
_MD_RE = re.compile(r"[`*_>#]+")           # strip common markdown punctuation for speech
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def parse_github(url: str) -> Optional[Tuple[str, str]]:
    """Return (owner, repo) if the URL is a GitHub repo, else None."""
    m = _GITHUB_RE.match(url or "")
    if not m:
        return None
    repo = m.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    # skip non-repo paths like github.com/orgs/... or github.com/features
    if m.group(1).lower() in {"orgs", "features", "about", "sponsors", "marketplace"}:
        return None
    return m.group(1), repo


def extract_readable(html_text: str) -> str:
    """Turn an HTML (or markdown) document into plain, speakable text."""
    text = _SCRIPT_STYLE_RE.sub(" ", html_text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _MD_RE.sub(" ", text)
    # normalize whitespace but keep paragraph breaks
    text = _WS_RE.sub(" ", text)
    text = _BLANKLINES_RE.sub("\n", text)
    return text.strip()


def extract_paragraphs(html_text: str) -> str:
    """Article body text taken only from <p> tags, so nav/headers/bylines are dropped.

    This is what keeps a desk from reading page chrome. Returns "" if the page has no real
    paragraphs (e.g. a README, which is markdown), so the caller can fall back to extract_readable.
    """
    body = _SCRIPT_STYLE_RE.sub(" ", html_text)
    paras = []
    for chunk in _P_RE.findall(body):
        t = html.unescape(_TAG_RE.sub(" ", chunk))
        t = _WS_RE.sub(" ", t).strip()
        if len(t.split()) >= 8:  # a real sentence-length paragraph, not a caption or link
            paras.append(t)
    return "\n".join(paras)


def summarize_extractive(text: str, max_sentences: int = SUMMARY_SENTENCES) -> str:
    """A cheap, offline summary: the first few substantial sentences.

    Skips boilerplate-looking lines (nav, one-word links). Good enough for a desk to reference
    the source, and it is replaced by the LLM summary once the key lands.
    """
    if not text:
        return ""
    # prefer sentences from lines that read like prose (have a few words)
    candidates = []
    for line in text.split("\n"):
        line = line.strip()
        if len(line.split()) < 6:
            continue
        for sent in _SENT_SPLIT_RE.split(line):
            sent = sent.strip()
            if len(sent.split()) >= 6:
                candidates.append(sent)
        if len(candidates) >= max_sentences:
            break
    return " ".join(candidates[:max_sentences])


def _github_readme(owner: str, repo: str) -> Optional[str]:
    url = f"https://api.github.com/repos/{owner}/{repo}/readme"
    headers = {"Accept": "application/vnd.github.raw+json", "User-Agent": "hn-radio"}
    return get_text(url, headers=headers, timeout=config.HTTP_TIMEOUT_SECONDS,
                    retries=config.HTTP_RETRIES, backoff=config.HTTP_BACKOFF_SECONDS)


def _fetch_source(url: str) -> Tuple[Optional[str], str]:
    """Return (readable_text, kind). kind is 'repo' | 'article' | 'none'."""
    gh = parse_github(url)
    try:
        if gh:
            raw = _github_readme(*gh)
            return extract_readable(raw), "repo"
        raw = get_text(url, headers={"User-Agent": "hn-radio"},
                       timeout=config.HTTP_TIMEOUT_SECONDS, retries=config.HTTP_RETRIES,
                       backoff=config.HTTP_BACKOFF_SECONDS)
        # prefer paragraph text (drops nav/headers); fall back to a full strip
        paragraphs = extract_paragraphs(raw)
        return (paragraphs or extract_readable(raw)), "article"
    except Exception:
        return None, "none"


def enrich_story(story: Story) -> Story:
    """Fetch and summarize the story's source in place. Falls back to title-only on failure."""
    if not story.url:
        story.source_kind = "text"   # Ask HN / self posts carry no external source
        return story
    text, kind = _fetch_source(story.url)
    if text:
        story.source_text = text[:MAX_SOURCE_CHARS]
        story.source_kind = kind
        story.summary = summarize_extractive(story.source_text)
    else:
        story.source_kind = "none"
    return story
