"""Public job board APIs: Greenhouse, Lever, Ashby, SmartRecruiters and Workable.

All of them publish a company's open roles as unauthenticated JSON, so no
logins or API keys are needed. Each function returns a list of Posting.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..health import expect
from ..models import Posting
from ..pay import parse_pay
from .http import get_json, html_to_text

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
LEVER = "https://api.lever.co/v0/postings/{slug}?mode=json"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
SMARTRECRUITERS = "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"
WORKABLE = "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"


def fetch(platform: str, slug: str, company: str, fetcher=get_json) -> list[Posting]:
    platform = platform.lower()
    if platform == "greenhouse":
        return parse_greenhouse(fetcher(GREENHOUSE.format(slug=slug)), company)
    if platform == "lever":
        return parse_lever(fetcher(LEVER.format(slug=slug)), company)
    if platform == "ashby":
        return parse_ashby(fetcher(ASHBY.format(slug=slug)), company)
    if platform == "smartrecruiters":
        return parse_smartrecruiters(fetcher(SMARTRECRUITERS.format(slug=slug)), company, slug)
    if platform == "workable":
        return parse_workable(fetcher(WORKABLE.format(slug=slug)), company)
    raise ValueError(f"unknown platform {platform!r}")


def parse_greenhouse(data: dict, company: str) -> list[Posting]:
    out = []
    for job in expect(data, dict, "jobs", "Greenhouse"):
        desc = html_to_text(job.get("content"))
        p = Posting(
            source="Greenhouse",
            company=job.get("company_name") or company,
            title=(job.get("title") or "").strip(),
            url=job.get("absolute_url") or "",
            location=((job.get("location") or {}).get("name") or "").strip(),
            description=desc,
            posted_at=_iso_date(job.get("first_published") or job.get("updated_at")),
        )
        p.pay_min, p.pay_max, p.pay_currency = parse_pay(desc)
        out.append(p)
    return out


def parse_lever(data: list, company: str) -> list[Posting]:
    out = []
    for job in expect(data, list, None, "Lever"):
        cats = job.get("categories") or {}
        locs = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
        location = " / ".join(locs)
        if (job.get("workplaceType") or "").lower() == "remote" and "remote" not in location.lower():
            location = f"{location} (Remote)".strip()
        parts = [job.get("descriptionPlain") or ""]
        for block in job.get("lists") or []:
            parts.append(block.get("text") or "")
            parts.append(html_to_text(block.get("content")))
        parts.append(job.get("additionalPlain") or "")
        desc = "\n".join(x for x in parts if x).strip()
        p = Posting(
            source="Lever",
            company=company,
            title=(job.get("text") or "").strip(),
            url=job.get("hostedUrl") or "",
            location=location,
            description=desc,
            posted_at=_ms_date(job.get("createdAt")),
        )
        sal = job.get("salaryRange") or {}
        if sal.get("min") or sal.get("max"):
            mult = _interval_multiplier(sal.get("interval"))
            p.pay_min = int(sal["min"] * mult) if sal.get("min") else None
            p.pay_max = int(sal["max"] * mult) if sal.get("max") else None
            p.pay_currency = sal.get("currency")
        else:
            p.pay_min, p.pay_max, p.pay_currency = parse_pay(desc)
        out.append(p)
    return out


def parse_ashby(data: dict, company: str) -> list[Posting]:
    out = []
    for job in expect(data, dict, "jobs", "Ashby"):
        if job.get("isListed") is False:
            continue
        locs = [job.get("location") or ""]
        locs += [(s.get("location") or "") for s in job.get("secondaryLocations") or []]
        location = " / ".join(x for x in locs if x)
        if job.get("isRemote") and "remote" not in location.lower():
            location = f"{location} (Remote)".strip()
        desc = job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml"))
        wp = (job.get("workplaceType") or "").lower()
        if wp == "onsite":
            desc = f"[Workplace: onsite]\n{desc}"
        p = Posting(
            source="Ashby",
            company=company,
            title=(job.get("title") or "").strip(),
            url=job.get("jobUrl") or "",
            location=location,
            description=desc,
            posted_at=_iso_date(job.get("publishedAt")),
        )
        comp = job.get("compensation") or {}
        for c in comp.get("summaryComponents") or []:
            if (c.get("compensationType") or "").lower() == "salary":
                mult = _interval_multiplier(c.get("interval"))
                p.pay_min = int(c["minValue"] * mult) if c.get("minValue") else None
                p.pay_max = int(c["maxValue"] * mult) if c.get("maxValue") else None
                p.pay_currency = c.get("currencyCode")
                break
        if p.pay_max is None:
            p.pay_min, p.pay_max, p.pay_currency = parse_pay(
                (comp.get("compensationTierSummary") or "") + "\n" + desc
            )
        out.append(p)
    return out


def parse_smartrecruiters(data: dict, company: str, slug: str) -> list[Posting]:
    """The list endpoint has no description; the run's review step reads the page."""
    out = []
    for job in expect(data, dict, "content", "SmartRecruiters"):
        loc = job.get("location") or {}
        parts = [loc.get("city"), loc.get("region"), loc.get("country", "").upper() or None]
        location = ", ".join(x for x in parts if x)
        if loc.get("remote") and "remote" not in location.lower():
            location = f"{location} (Remote)".strip()
        out.append(Posting(
            source="SmartRecruiters",
            company=(job.get("company") or {}).get("name") or company,
            title=(job.get("name") or "").strip(),
            url=f"https://jobs.smartrecruiters.com/{slug}/{job.get('id')}",
            location=location,
            posted_at=_iso_date(job.get("releasedDate")),
        ))
    return out


def parse_workable(data: dict, company: str) -> list[Posting]:
    out = []
    for job in expect(data, dict, "jobs", "Workable"):
        parts = [job.get("city"), job.get("state"), job.get("country")]
        location = ", ".join(x for x in parts if x)
        if job.get("telecommuting") and "remote" not in location.lower():
            location = f"{location} (Remote)".strip()
        desc = html_to_text(job.get("description"))
        p = Posting(
            source="Workable",
            company=data.get("name") or company,
            title=(job.get("title") or "").strip(),
            url=job.get("url") or job.get("shortlink") or "",
            location=location,
            description=desc,
            posted_at=_iso_date(job.get("published_on") or job.get("created_at")),
        )
        p.pay_min, p.pay_max, p.pay_currency = parse_pay(desc)
        out.append(p)
    return out


def _interval_multiplier(interval: str | None) -> float:
    i = (interval or "").lower()
    if "hour" in i:
        return 2080
    if "month" in i:
        return 12
    return 1


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    return value[:10]


def _ms_date(ms) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()
