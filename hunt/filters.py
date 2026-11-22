"""Hard skip rules. Each check returns a short reason, or "" to keep the posting.

These mirror Rob's skip criteria: staffing agencies with no named client,
Java/JVM, mobile native or Go-only roles, PERM postings, pay ceilings under
the floor, fully onsite, plus location (NYC or remote US) and seniority.
"""

from __future__ import annotations

import re

from .models import Posting, norm

_YEARS = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:\+\s*)?years?(?:\s+of)?(?:\s+\w+){0,4}?\s+(?:experience|exp\b|building|working|in software|of software|professional)",
    re.I,
)


def _has(text: str, phrases) -> str:
    t = f" {text.lower()} "
    for p in phrases:
        if p.lower() in t:
            return p
    return ""


def title_check(p: Posting, cfg: dict) -> str:
    t = f" {p.title.lower()} "
    hit = _has(t, cfg["titles"]["exclude"])
    if hit:
        return f"title: {hit.strip()}"
    if not _has(t, cfg["titles"]["include"]):
        return "title: not a target role"
    return ""


def location_check(p: Posting, cfg: dict) -> str:
    loc = p.location.lower()
    lc = cfg["location"]
    if not loc.strip():
        return "" if lc.get("keep_unknown", True) else "location: unknown"
    in_nyc = bool(_has(loc, lc["nyc"]))
    remote = bool(_has(loc, lc["remote"]))
    if in_nyc:
        return ""
    if remote:
        # Whole words only: "Brussels" and "Belarus" contain "us".
        us_hint = bool(re.search(r"(?<![a-z])(us|usa|u\.s\.?|united states|america|north america)(?![a-z])", loc))
        if _has(loc, lc["non_us"]) and not us_hint:
            return f"location: remote outside the US ({p.location})"
        return ""
    return f"location: {p.location}"


def pay_check(p: Posting, cfg: dict) -> str:
    pc = cfg["pay"]
    if p.pay_currency and p.pay_currency != "USD" and pc.get("skip_non_usd", True):
        return f"pay: in {p.pay_currency}"
    if p.pay_max is not None and p.pay_max < pc["floor_usd"]:
        return f"pay: tops out at ${p.pay_max // 1000}K"
    return ""


def years_required(text: str) -> int | None:
    """Smallest 'N+ years of experience' figure in the text, if any."""
    found = [int(m.group(1)) for m in _YEARS.finditer(text or "")]
    found = [n for n in found if 1 <= n <= 20]
    return min(found) if found else None


def seniority_check(p: Posting, cfg: dict) -> str:
    yrs = years_required(p.description)
    if yrs is not None and yrs >= cfg["seniority"]["skip_if_years_at_least"]:
        return f"seniority: asks for {yrs}+ years"
    return ""


def text_check(p: Posting, cfg: dict) -> str:
    st = cfg["skip_text"]
    body = f"{p.description}\n{' '.join(p.tags)}"
    if hit := _has(body, st["perm"]):
        return "PERM / labor certification posting"
    if hit := _has(body, st["onsite"]):
        return f"fully onsite ({hit})"
    return ""


def staffing_check(p: Posting, cfg: dict) -> str:
    company = f" {norm(p.company)} "
    if any(f" {norm(a)} " in company for a in cfg["staffing"]["agencies"]):
        return f"staffing agency: {p.company}"
    if _has(p.description, cfg["skip_text"]["staffing"]):
        return "staffing agency, no named client"
    if not p.company.strip():
        return "no company named"
    return ""


def stack_check(p: Posting, cfg: dict, profile: dict) -> str:
    """Skip roles whose description is dominated by a stack Rob doesn't use."""
    text = f" {p.description.lower()} {' '.join(p.tags).lower()} "
    if len(text) < 80 and not p.tags:
        return ""  # too little to judge (email digests)
    ours = sum(text.count(f" {k.lower()}") + text.count(f"{k.lower()},") for k in profile["core_stack"])
    for lang in cfg["stack_mismatch"]["primary"]:
        theirs = len(re.findall(rf"(?<![a-z]){re.escape(lang)}(?![a-z])", text))
        if lang == "golang":
            # "Go" as a language shows up as "Go," "Go /" "(Go)" in stack lists.
            theirs += len(re.findall(r"(?<![a-z])go(?=\s*[,/)])", text))
        if theirs >= 3 and theirs > 2 * max(ours, 1):
            return f"stack: mostly {lang}"
    return ""


def scam_check(p: Posting, cfg: dict) -> str:
    """Tim's scam markers: asks for banking or personal details, fees, or pay that can't be real."""
    sc = cfg.get("scam", {})
    body = f"{p.title}\n{p.description}".lower()
    for ph in sc.get("phrases", []):
        if re.search(rf"(?<![a-z0-9]){re.escape(ph.lower())}(?![a-z0-9])", body):
            return f"scam marker: {ph}"
    ceiling = sc.get("unrealistic_pay_usd")
    if ceiling and p.pay_max and (p.pay_currency or "USD") == "USD" and p.pay_max > ceiling:
        return f"scam marker: pay up to ${p.pay_max:,}"
    return ""


def first_skip_reason(p: Posting, cfg: dict, profile: dict) -> str:
    for check in (title_check, location_check, pay_check, staffing_check, scam_check, text_check, seniority_check):
        reason = check(p, cfg)
        if reason:
            return reason
    return stack_check(p, cfg, profile)
