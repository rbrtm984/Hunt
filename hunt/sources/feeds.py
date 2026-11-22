"""Cross-company job feeds: one request covers many employers.

Only official, documented APIs or feeds are used here. Boards whose terms bar
automated access (LinkedIn, Indeed, Dice, Built In, Wellfound, Glassdoor,
ZipRecruiter) come in only through their own job alert emails in Gmail, which
ROUTINE.md reads. Nothing here scrapes HTML or signs in anywhere.

Each feed returns a list of Posting. Terms each feed asks for:
- Remotive: a few requests a day at most, credit Remotive and link back.
- Remote OK: credit Remote OK and link to the job on remoteok.com.
- Himalayas: public API, 20 jobs a request, link back to the job page.
- Jobicy: at most hourly, at a random minute, credit and link back.
- HN Who's Hiring: Algolia's public HN API.
- Adzuna, Jooble: free API keys, set as environment variables in the cloud
  environment (never in this repo). Skipped quietly when no key is set.
- Google Jobs through SerpApi: needs SERPAPI_API_KEY, set by Rob in the
  cloud environment. At most 5 searches a run, so two runs a weekday stay
  inside SerpApi's free 250 searches a month.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from datetime import datetime, timezone

from ..health import expect
from ..models import Posting
from ..pay import parse_pay
from .http import get_json, html_to_text

REMOTIVE = "https://remotive.com/api/remote-jobs?category=software-dev"
REMOTEOK = "https://remoteok.com/api"
HIMALAYAS = "https://himalayas.app/jobs/api?limit=20"
JOBICY = "https://jobicy.com/api/v2/remote-jobs?count=100&geo=usa&industry=engineering"
HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=10"
HN_ITEM = "https://hn.algolia.com/api/v1/items/{id}"
ADZUNA = "https://api.adzuna.com/v1/api/jobs/us/search/1"
JOOBLE = "https://jooble.org/api/{key}"
SERPAPI = "https://serpapi.com/search.json"
SERPAPI_MAX_SEARCHES = 5  # per run; 2 runs x ~22 weekdays x 5 = 220, under the free 250/month
HN_MAX_AGE_DAYS = 40  # the monthly thread goes up on the first weekday of each month


class NotConfigured(Exception):
    """A keyed feed with no key set. Not an error: it's simply off."""


def fetch(name: str, opts: dict, fetcher=get_json, env=None) -> list[Posting]:
    env = os.environ if env is None else env
    name = name.lower()
    if name == "remotive":
        return parse_remotive(fetcher(REMOTIVE))
    if name == "remoteok":
        return parse_remoteok(fetcher(REMOTEOK))
    if name == "himalayas":
        return parse_himalayas(fetcher(HIMALAYAS))
    if name == "jobicy":
        return parse_jobicy(fetcher(JOBICY))
    if name == "hn":
        return fetch_hn(fetcher)
    if name == "adzuna":
        return fetch_adzuna(opts, fetcher, env)
    if name == "jooble":
        return fetch_jooble(opts, fetcher, env)
    if name == "serpapi":
        return fetch_serpapi(opts, fetcher, env)
    raise ValueError(f"unknown feed {name!r}")


def parse_remotive(data: dict) -> list[Posting]:
    out = []
    for job in expect(data, dict, "jobs", "Remotive"):
        desc = html_to_text(job.get("description"))
        p = Posting(
            source="Remotive",
            company=(job.get("company_name") or "").strip(),
            title=(job.get("title") or "").strip(),
            url=job.get("url") or "",
            location=f"Remote ({job.get('candidate_required_location') or 'anywhere'})",
            description=desc,
            tags=list(job.get("tags") or [])[:12],
            posted_at=(job.get("publication_date") or "")[:10] or None,
        )
        p.pay_min, p.pay_max, p.pay_currency = parse_pay(f"{job.get('salary') or ''}\n{desc}")
        out.append(p)
    return out


def parse_remoteok(data: list) -> list[Posting]:
    out = []
    for job in expect(data, list, None, "Remote OK"):
        if not isinstance(job, dict) or "position" not in job:
            continue  # the first item is Remote OK's legal notice
        desc = html_to_text(job.get("description"))
        p = Posting(
            source="Remote OK",
            company=(job.get("company") or "").strip(),
            title=(job.get("position") or "").strip(),
            url=job.get("url") or "",
            location=f"Remote ({job.get('location') or 'anywhere'})",
            description=desc,
            tags=list(job.get("tags") or [])[:12],
            posted_at=(job.get("date") or "")[:10] or None,
        )
        if job.get("salary_max"):
            p.pay_min = int(job["salary_min"]) if job.get("salary_min") else None
            p.pay_max = int(job["salary_max"])
            p.pay_currency = "USD"
        else:
            p.pay_min, p.pay_max, p.pay_currency = parse_pay(desc)
        out.append(p)
    return out


def parse_himalayas(data: dict) -> list[Posting]:
    out = []
    for job in expect(data, dict, "jobs", "Himalayas"):
        desc = html_to_text(job.get("description") or job.get("excerpt"))
        where = job.get("locationRestrictions") or []
        where = ", ".join(w if isinstance(w, str) else (w.get("name") or "") for w in where)
        p = Posting(
            source="Himalayas",
            company=(job.get("companyName") or "").strip(),
            title=(job.get("title") or "").strip(),
            url=job.get("applicationLink") or job.get("guid") or "",
            location=f"Remote ({where or 'anywhere'})",
            description=desc,
            tags=list(job.get("categories") or [])[:12],
            posted_at=_epoch_date(job.get("pubDate")),
        )
        if job.get("maxSalary"):
            p.pay_min = int(job["minSalary"]) if job.get("minSalary") else None
            p.pay_max = int(job["maxSalary"])
            p.pay_currency = job.get("currency") or "USD"
        else:
            p.pay_min, p.pay_max, p.pay_currency = parse_pay(desc)
        out.append(p)
    return out


def parse_jobicy(data: dict) -> list[Posting]:
    out = []
    for job in expect(data, dict, "jobs", "Jobicy"):
        desc = html_to_text(job.get("jobDescription") or job.get("jobExcerpt"))
        p = Posting(
            source="Jobicy",
            company=(job.get("companyName") or "").strip(),
            title=html_to_text(job.get("jobTitle")),
            url=job.get("url") or "",
            location=f"Remote ({job.get('jobGeo') or 'anywhere'})",
            description=desc,
            posted_at=(job.get("pubDate") or "")[:10] or None,
        )
        if job.get("annualSalaryMax"):
            p.pay_min = int(job["annualSalaryMin"]) if job.get("annualSalaryMin") else None
            p.pay_max = int(job["annualSalaryMax"])
            p.pay_currency = job.get("salaryCurrency") or "USD"
        else:
            p.pay_min, p.pay_max, p.pay_currency = parse_pay(desc)
        out.append(p)
    return out


_ROLE_WORDS = re.compile(r"engineer|developer|founding|full[- ]?stack|swe\b|mts\b", re.I)
_LOC_WORDS = re.compile(r"remote|onsite|on-site|hybrid|nyc|new york|san francisco|sf\b|bay area|\b[A-Z][a-z]+, [A-Z]{2}\b", re.I)


def fetch_hn(fetcher=get_json, now: datetime | None = None) -> list[Posting]:
    """This month's "Ask HN: Who is hiring?" thread. Location and role are
    filtered by the same skip rules as every other source (NYC or US
    remote, Rob's target titles)."""
    from ..health import ShapeError
    hits = expect(fetcher(HN_SEARCH), dict, "hits", "HN search")
    thread = next((h for h in hits if (h.get("title") or "").lower().startswith("ask hn: who is hiring")), None)
    if not thread:
        raise ShapeError("HN search: no 'Ask HN: Who is hiring?' thread in the latest results")
    posted = thread.get("created_at_i")
    now = now or datetime.now(timezone.utc)
    if isinstance(posted, (int, float)) and (now.timestamp() - posted) > HN_MAX_AGE_DAYS * 86400:
        raise ShapeError(f"HN: the newest Who's Hiring thread is from {_epoch_date(posted)}, so this month's isn't up yet")
    return parse_hn_thread(fetcher(HN_ITEM.format(id=thread["objectID"])))


def parse_hn_thread(item: dict) -> list[Posting]:
    """Each top-level comment is one company. Its first line is usually
    'Company | Role(s) | Location | Remote | Pay'. One posting per comment,
    titled with the first segment that names a role."""
    out = []
    for c in expect(item, dict, "children", "HN thread"):
        text = html_to_text(c.get("text"))
        if not text or not c.get("id"):
            continue
        first = text.splitlines()[0]
        segs = [s.strip() for s in first.split("|") if s.strip()]
        if len(segs) < 2:
            continue
        company = re.sub(r"\s*\(.*?\)\s*$", "", segs[0])[:80]
        role = next((s for s in segs[1:] if _ROLE_WORDS.search(s)), "")
        if not role:
            continue
        locs = [s for s in segs[1:] if s != role and _LOC_WORDS.search(s)]
        p = Posting(
            source="HN Who's Hiring",
            company=company,
            title=role[:120],
            url=f"https://news.ycombinator.com/item?id={c['id']}",
            location=" / ".join(locs),
            description=text,
            posted_at=(c.get("created_at") or "")[:10] or None,
        )
        p.pay_min, p.pay_max, p.pay_currency = parse_pay(first)
        out.append(p)
    return out


def fetch_adzuna(opts: dict, fetcher, env) -> list[Posting]:
    app_id, app_key = env.get("ADZUNA_APP_ID"), env.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        raise NotConfigured("ADZUNA_APP_ID and ADZUNA_APP_KEY not set")
    out = []
    for query in opts.get("queries", []):
        for where in opts.get("where", [""]):
            params = {
                "app_id": app_id, "app_key": app_key, "results_per_page": 50,
                "what": query, "max_days_old": opts.get("max_days_old", 3),
                "salary_min": opts.get("salary_min", 0) or None, "content-type": "application/json",
            }
            if where:
                params["where"] = where
            url = ADZUNA + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            out += parse_adzuna(fetcher(url))
    return out


def parse_adzuna(data: dict) -> list[Posting]:
    out = []
    for job in expect(data, dict, "results", "Adzuna"):
        desc = html_to_text(job.get("description"))
        p = Posting(
            source="Adzuna",
            company=((job.get("company") or {}).get("display_name") or "").strip(),
            title=html_to_text(job.get("title")),
            url=job.get("redirect_url") or "",
            location=((job.get("location") or {}).get("display_name") or "").strip(),
            description=desc,
            posted_at=(job.get("created") or "")[:10] or None,
        )
        if job.get("salary_max") and str(job.get("salary_is_predicted")) != "1":
            p.pay_min = int(job["salary_min"]) if job.get("salary_min") else None
            p.pay_max = int(job["salary_max"])
            p.pay_currency = "USD"
        out.append(p)
    return out


def fetch_jooble(opts: dict, fetcher, env) -> list[Posting]:
    key = env.get("JOOBLE_API_KEY")
    if not key:
        raise NotConfigured("JOOBLE_API_KEY not set")
    out = []
    for query in opts.get("queries", []):
        for where in opts.get("where", [""]):
            body = {"keywords": query, "location": where, "page": 1}
            out += parse_jooble(fetcher(JOOBLE.format(key=key), body=body))
    return out


def parse_jooble(data: dict) -> list[Posting]:
    out = []
    for job in expect(data, dict, "jobs", "Jooble"):
        desc = html_to_text(job.get("snippet"))
        p = Posting(
            source=f"Jooble ({job.get('source') or 'aggregated'})",
            company=(job.get("company") or "").strip(),
            title=html_to_text(job.get("title")),
            url=job.get("link") or "",
            location=(job.get("location") or "").strip(),
            description=desc,
            posted_at=(job.get("updated") or "")[:10] or None,
        )
        p.pay_min, p.pay_max, p.pay_currency = parse_pay(job.get("salary") or "")
        out.append(p)
    return out


# Job boards whose own pages we never fetch. When Google Jobs offers an
# employer's own apply link, that's used instead (Tim: apply direct).
_AGGREGATORS = (
    "linkedin.com", "indeed.com", "ziprecruiter.com", "glassdoor.com", "dice.com", "monster.com",
    "simplyhired.com", "talent.com", "builtin.com", "wellfound.com", "jooble.org", "adzuna.com",
    "lensa.com", "jobright.ai", "snagajob.com", "careerbuilder.com", "salary.com", "learn4good.com",
    "bebee.com", "jobgether.com", "teal.com", "whatjobs.com", "recruit.net", "trabajo.org",
)


def _is_aggregator(link: str) -> bool:
    host = (urllib.parse.urlsplit(link).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _AGGREGATORS)


def fetch_serpapi(opts: dict, fetcher, env) -> list[Posting]:
    key = env.get("SERPAPI_API_KEY")
    if not key:
        raise NotConfigured("SERPAPI_API_KEY not set")
    searches = [(q, w) for q in opts.get("queries", []) for w in opts.get("where", ["New York, New York, United States"])]
    limit = min(int(opts.get("max_searches", SERPAPI_MAX_SEARCHES)), SERPAPI_MAX_SEARCHES)
    out = []
    for query, where in searches[:limit]:
        params = {"engine": "google_jobs", "q": query, "hl": "en", "gl": "us", "api_key": key}
        if where:
            params["location"] = where
        out += parse_serpapi(fetcher(SERPAPI + "?" + urllib.parse.urlencode(params)))
    return out


def parse_serpapi(data: dict) -> list[Posting]:
    if isinstance(data, dict) and data.get("error") and "jobs_results" not in data:
        # "Google hasn't returned any results for this query." is a normal empty answer.
        if "hasn't returned any results" in str(data["error"]):
            return []
        raise ValueError(f"SerpApi: {data['error']}")
    out = []
    for job in expect(data, dict, "jobs_results", "SerpApi Google Jobs"):
        ext = job.get("detected_extensions") or {}
        links = [o.get("link") or "" for o in (job.get("apply_options") or []) if o.get("link")]
        direct = next((x for x in links if not _is_aggregator(x)), "")
        url = direct or (links[0] if links else "") or job.get("share_link") or ""
        loc = (job.get("location") or "").strip()
        if ext.get("work_from_home") and "remote" not in loc.lower():
            loc = f"{loc} (Remote)".strip() if loc else "Remote"
        desc = (job.get("description") or "").strip()
        p = Posting(
            source="Google Jobs",
            company=(job.get("company_name") or "").strip(),
            title=(job.get("title") or "").strip(),
            url=url,
            location=loc,
            description=desc,
            posted_at=None,  # Google gives "3 days ago", not a date
        )
        p.pay_min, p.pay_max, p.pay_currency = parse_pay(f"{ext.get('salary') or ''}\n{desc}")
        out.append(p)
    return out


def _epoch_date(value) -> str | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
    return str(value)[:10]
