"""First-pass score, 0 to 100, plus one line on the biggest gap.

This is a fast keyword pass so the daily run can rank hundreds of postings for
free. The run's agent then rereads the top of the list against
profile/rubric.md (the condensed version of Tim's rubric) and adjusts.

Points:
    role fit (title)           25
    AI / agentic work          20
    stack overlap              20
    seniority fit              15
    location                   10
    pay posted and above floor  5
    domain (edtech, identity)   5
"""

from __future__ import annotations

import re

from .filters import years_required
from .models import Posting


def _count(text: str, terms) -> list[str]:
    hits = []
    for term in terms:
        if re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", text):
            hits.append(term)
    return hits


def score(p: Posting, cfg: dict, profile: dict) -> Posting:
    title = p.title.lower()
    text = f"{title}\n{p.description.lower()}\n{' '.join(p.tags).lower()}"
    reasons: list[tuple[int, str]] = []
    gaps: list[tuple[int, str]] = []

    # Role fit
    weights = profile["title_weights"]
    role = max((w for k, w in weights.items() if k in title), default=8)
    reasons.append((role, "title fits the agentic/AI path" if role >= 22 else "full stack role" if role >= 17 else ""))

    # AI work
    ai_hits = _count(text, profile["ai_terms"])
    ai = min(len(ai_hits) * 4, 20)
    if ai >= 8:
        reasons.append((ai, f"AI work ({', '.join(ai_hits[:3])})"))
    elif ai == 0:
        gaps.append((6, "no AI or agent work mentioned"))

    # Stack overlap
    stack_hits = _count(text, profile["core_stack"])
    stack = min(len(stack_hits) * 3, 20)
    if stack >= 9:
        reasons.append((stack, f"stack match ({', '.join(stack_hits[:4])})"))
    missing = _count(text, profile["not_on_resume"])
    if missing:
        stack = max(stack - 2 * len(missing), 0)
        gaps.append((5 + len(missing), f"asks for {', '.join(missing[:3])}, not on your resume"))

    # Seniority
    yrs = years_required(p.description)
    seniority = 15
    if yrs is not None:
        if yrs >= cfg["seniority"]["penalize_if_years_at_least"]:
            seniority = 3
            gaps.append((20, f"asks for {yrs}+ years; you have about {profile['years_experience']:g}"))
        elif yrs == 4:
            seniority = 10
            gaps.append((8, "asks for 4+ years; a stretch but doable"))
    if re.search(r"\bsenior\b|\bsr\.?\b|\blead\b", title):
        seniority = min(seniority, 8)
        if yrs is None:
            gaps.append((10, "senior title; check the years they want"))
    if re.search(r"early career|new grad|junior|\bjr\b|engineer i\b|\bswe i\b", title):
        seniority = min(seniority, 10)
        gaps.append((4, "pitched as early career; may pay under your level"))

    # Location
    loc = p.location.lower()
    if any(x in loc for x in cfg["location"]["nyc"]):
        location = 10
    elif any(x in loc for x in cfg["location"]["remote"]):
        location = 9
    else:
        location = 5
        gaps.append((3, "location not listed; check before applying"))

    # Pay
    if p.pay_max is not None and p.pay_max >= cfg["pay"]["floor_usd"]:
        pay = 5
    elif p.pay_max is None:
        pay = 3
    else:
        pay = 0

    # Domain
    domain_hits = _count(text, profile["domain_bonus"])
    domain = 5 if domain_hits else 0
    if domain:
        reasons.append((domain + 5, f"{domain_hits[0]} domain fits your background"))

    total = role + ai + stack + seniority + location + pay + domain
    p.score = max(0, min(100, total))
    gaps.sort(key=lambda g: -g[0])
    p.gap = gaps[0][1] if gaps else "no obvious gap from the posting text"
    reasons = [r for r in sorted(reasons, key=lambda r: -r[0]) if r[1]]
    why = "; ".join(r[1] for r in reasons[:2])
    p.why = why[:1].upper() + why[1:]
    return p
