"""First-pass score, 0 to 100, a verdict, flags, and one line on the biggest gap.

The dimensions follow Tim's resume scoring prompt (Coaching Technology
Group), adjusted for an IC agentic / full stack role so they total 100:

    core discipline fit (title)       25   Tim: core discipline / function fit 25
    stack and technical skills        20   Tim: framework and technical skills 20
    AI and agent depth                20   Tim's third dimension (program mgmt) swapped for AI depth
    seniority and credentials         15   Tim: certifications and credentials 10, plus years asked
    domain fit                        10   Tim: domain / industry fit 15
    ownership and scope               10   Tim: leadership and stakeholder scope 10

Location and pay are hard filters (filters.py), not points.

Verdicts use Tim's scale: 85+ Priority Submit, 75 to 84 Submit, 65 to 74
Conditional Submit, 50 to 64 Reach, under 50 Do Not Submit. A posting with
almost no text (email digests) gets "Read the posting" instead, because a
score from the title alone would be a guess.

Flags are things Tim says to raise before scoring: work authorization,
sponsorship, citizenship or clearance; hidden AI-detection phrases; a stated
policy against AI-written applications. They don't change the score.

This is a fast keyword pass so the daily run can rank hundreds of postings for
free. The run's agent then rereads the top of the list against
profile/rubric.md and adjusts.
"""

from __future__ import annotations

import re

from .filters import years_required
from .models import Posting

THIN_TEXT = 200  # characters; below this the posting is mostly a title

VERDICTS = [(85, "Priority Submit"), (75, "Submit"), (65, "Conditional Submit"), (50, "Reach"), (0, "Do Not Submit")]


def verdict(score: int | None, thin: bool = False) -> str:
    if thin:
        return "Read the posting"
    for floor, label in VERDICTS:
        if (score or 0) >= floor:
            return label
    return "Do Not Submit"


def _count(text: str, terms) -> list[str]:
    hits = []
    for term in terms:
        if re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", text):
            hits.append(term)
    return hits


def flags_for(p: Posting, cfg: dict) -> list[str]:
    """Tim's pre-scoring flags. Each is a short plain phrase."""
    text = f"{p.title}\n{p.description}".lower()
    rules = cfg.get("flags", {})
    out = []
    for label, phrases in (
        ("work authorization or sponsorship terms", rules.get("work_auth", [])),
        ("needs US citizenship or a clearance", rules.get("clearance", [])),
        ("says no AI-written applications", rules.get("no_ai_policy", [])),
        ("hidden AI-detection instruction in the posting", rules.get("ai_detection", [])),
        ("application has essay questions; write those yourself", rules.get("essays", [])),
    ):
        if any(ph.lower() in text for ph in phrases):
            out.append(label)
    return out


def score(p: Posting, cfg: dict, profile: dict) -> Posting:
    title = p.title.lower()
    text = f"{title}\n{p.description.lower()}\n{' '.join(p.tags).lower()}"
    thin = len(p.description) < THIN_TEXT and not p.tags
    reasons: list[tuple[int, str]] = []
    gaps: list[tuple[int, str]] = []

    # Core discipline fit (25)
    weights = profile["title_weights"]
    role = max((w for k, w in weights.items() if k in title), default=8)
    reasons.append((role, "title fits the agentic/AI path" if role >= 22 else "full stack role" if role >= 17 else ""))

    # Stack and technical skills (20)
    stack_hits = _count(text, profile["core_stack"])
    stack = min(len(stack_hits) * 3, 20)
    if stack >= 9:
        reasons.append((stack, f"stack match ({', '.join(stack_hits[:4])})"))
    missing = _count(text, profile["not_on_resume"])
    if missing:
        stack = max(stack - 2 * len(missing), 0)
        gaps.append((5 + len(missing), f"asks for {', '.join(missing[:3])}, not on your resume"))

    # AI and agent depth (20)
    ai_hits = _count(text, profile["ai_terms"])
    ai = min(len(ai_hits) * 4, 20)
    if ai >= 8:
        reasons.append((ai, f"AI work ({', '.join(ai_hits[:3])})"))
    elif ai == 0 and not thin:
        gaps.append((6, "no AI or agent work mentioned"))

    # Seniority and credentials (15)
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
    degree = _count(text, profile.get("credential_gaps", []))
    if degree:
        seniority = max(seniority - 4, 0)
        gaps.append((9, f"asks for {degree[0]}"))

    # Domain fit (10). Any software company gets partial credit.
    domain_hits = _count(text, profile["domain_bonus"])
    domain = 10 if domain_hits else 4
    if domain_hits:
        reasons.append((12, f"{domain_hits[0]} domain fits your background"))

    # Ownership and scope (10)
    scope_hits = _count(text, profile.get("scope_terms", []))
    scope = min(4 + 2 * len(scope_hits), 10)
    if len(scope_hits) >= 2:
        reasons.append((scope, "small team, end to end ownership"))

    if thin:
        gaps.append((15, "the alert had no job description, so this score is from the title only"))

    total = role + stack + ai + seniority + domain + scope
    p.score = max(0, min(100, total))
    p.flags = flags_for(p, cfg)
    p.verdict = verdict(p.score, thin)
    gaps.sort(key=lambda g: -g[0])
    p.gap = gaps[0][1] if gaps else "no obvious gap from the posting text"
    reasons = [r for r in sorted(reasons, key=lambda r: -r[0]) if r[1]]
    why = "; ".join(r[1] for r in reasons[:2])
    p.why = why[:1].upper() + why[1:]
    return p


def rank_key(p: Posting):
    """Highest score first. Ties go to the lower-risk posting (fewer flags), as Tim says."""
    return (-(p.score or 0), len(p.flags), p.company.lower())
