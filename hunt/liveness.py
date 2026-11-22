"""Is the posting still open? Checked before scoring, so closed roles never
reach the tracker. (Idea from career-ops, which checks every link before it
spends time on a posting.)

Company board APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workable)
only list open jobs, so those are trusted as live. Alert emails and
cross-company feeds can be days old, so their links get one fetch each.

The check never opens a page on a site whose terms bar automated access
(LinkedIn, Indeed, Dice, Built In, Wellfound, Glassdoor, ZipRecruiter):
those postings stay "not checked" and the run's review step looks at the
employer's own page instead. A page that won't load is "unknown" and the
posting is kept: only a clear sign of closure drops it.
"""

from __future__ import annotations

import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .models import Posting
from .sources.http import FetchError, get_page

OPEN, CLOSED, UNKNOWN, NOT_CHECKED = "open", "closed", "unknown", "not checked"

# Sources whose postings come straight from a live board API.
LIVE_BY_API = ("Greenhouse", "Lever", "Ashby", "SmartRecruiters", "Workable")

# Never fetched (no scraping), and HN comments aren't job pages.
NO_FETCH_HOSTS = (
    "linkedin.com", "indeed.com", "dice.com", "builtin.com", "wellfound.com", "angel.co",
    "glassdoor.com", "ziprecruiter.com", "news.ycombinator.com",
)

CLOSED_PHRASES = (
    "no longer accepting applications",
    "no longer accepting applicants",
    "this job is no longer available",
    "this position is no longer available",
    "this job is no longer open",
    "position has been filled",
    "this position has been filled",
    "this job has been filled",
    "this job is closed",
    "this position is closed",
    "job posting has expired",
    "this job has expired",
    "this posting has expired",
    "the job you are looking for is no longer",
    "the position you are looking for is no longer",
    "job not found",
    "posting not found",
)

_SCRIPT = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def needs_check(p: Posting) -> bool:
    if p.source.split(":")[0].strip() in LIVE_BY_API:
        return False
    host = (urllib.parse.urlsplit(p.url).hostname or "").lower()
    if not host:
        return False
    return not any(host == h or host.endswith("." + h) for h in NO_FETCH_HOSTS)


def verdict_from_page(url: str, status: int, final_url: str, html: str) -> tuple[str, str]:
    """(state, reason) from one fetch. Pure function so it's easy to test."""
    if status in (404, 410):
        return CLOSED, f"the job page returns {status}"
    if status >= 400:
        return UNKNOWN, f"the job page returned {status}"
    final = (final_url or url).lower()
    # Greenhouse sends a closed job back to the board with ?error=true.
    if "error=true" in final and "error=true" not in url.lower():
        return CLOSED, "the job page redirects to the careers page"
    # Lever and Ashby send a closed job to the company's job list.
    start, end = urllib.parse.urlsplit(url.lower()), urllib.parse.urlsplit(final)
    if re.search(r"log-?in|sign-?in|signup|auth|sso|account", end.path + end.query):
        return UNKNOWN, "the job page asks for a sign-in"
    if start.hostname == end.hostname and len(end.path.rstrip("/")) < len(start.path.rstrip("/")) and end.path.rstrip("/").count("/") <= 1 and start.path.rstrip("/").count("/") >= 2:
        return CLOSED, "the job page redirects to the company's job list"
    text = _TAG.sub(" ", _SCRIPT.sub(" ", html or "")).lower()
    text = re.sub(r"\s+", " ", text)
    for phrase in CLOSED_PHRASES:
        if phrase in text:
            return CLOSED, f'the page says "{phrase}"'
    return OPEN, ""


def check(p: Posting, page_fetcher=get_page) -> tuple[str, str]:
    try:
        status, final_url, html = page_fetcher(p.url)
    except FetchError as e:
        return UNKNOWN, str(e)
    return verdict_from_page(p.url, status, final_url, html)


def check_all(postings: list[Posting], page_fetcher=get_page, limit: int = 60, workers: int = 6) -> dict:
    """Sets p.live on each posting. Returns counts by state."""
    todo = [p for p in postings if needs_check(p)][:limit]
    todo_ids = {id(p) for p in todo}
    counts = {OPEN: 0, CLOSED: 0, UNKNOWN: 0, NOT_CHECKED: 0}
    for p in postings:
        if id(p) not in todo_ids:
            p.live = OPEN if p.source.split(":")[0].strip() in LIVE_BY_API else NOT_CHECKED
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for p, (state, reason) in zip(todo, pool.map(lambda x: check(x, page_fetcher), todo)):
            p.live = state
            if state == CLOSED:
                p.skip_reason = f"closed: {reason}"
            elif state == UNKNOWN:
                p.live_note = reason
    for p in postings:
        counts[p.live] = counts.get(p.live, 0) + 1
    return counts
