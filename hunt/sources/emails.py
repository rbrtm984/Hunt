"""Job alert emails.

The daily run reads Rob's alert emails through the Gmail connector (no Google
credentials live in this repo), saves each email's plain-text body to a file,
and hands the folder to this module.

Formats with a known layout get a real parser. For anything else, the run's
agent writes the postings as JSON in the shape of Posting (see ROUTINE.md),
and `load_postings_json` reads that.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..models import Posting
from ..pay import parse_pay

YC_JOB_URL = re.compile(r"^https://www\.workatastartup\.com/jobs/\d+\s*$")


def parse_yc_digest(body: str) -> list[Posting]:
    """YC Work at a Startup digest.

    Each job is a block of lines ending in its URL:
        Title
        Company (Batch)
        One-line pitch
        [Stack, e.g. "Next.js / LLMs / AI Agents"]
        Pay / equity, e.g. "$150K - $250K / 0.25% - 1.50%"
        https://www.workatastartup.com/jobs/12345
    """
    lines = [ln.strip() for ln in body.splitlines()]
    out: list[Posting] = []
    block: list[str] = []
    for ln in lines:
        if not ln:
            block = []
            continue
        if YC_JOB_URL.match(ln):
            if len(block) >= 3:
                out.append(_yc_block(block, ln))
            block = []
            continue
        block.append(ln)
    return out


def _yc_block(block: list[str], url: str) -> Posting:
    title, company_line, pitch = block[0], block[1], block[2]
    rest = block[3:]
    pay_line = next((x for x in rest if "/" in x and re.search(r"[$₹€£]|\d+%", x)), "")
    if not pay_line and rest and rest[-1].strip().startswith("/"):
        pay_line = rest[-1]
    stack_lines = [x for x in rest if x != pay_line]
    tags = [t.strip() for s in stack_lines for t in s.split("/") if t.strip()]
    company = re.sub(r"\s*\([A-Z]\d{2}\)\s*$", "", company_line).strip()
    salary_part = pay_line.split(" / ")[0] if " / " in pay_line else pay_line
    lo, hi, cur = parse_pay(salary_part)
    return Posting(
        source="YC Work at a Startup",
        company=company,
        title=title,
        url=url.strip(),
        location="",  # the digest doesn't say; the run's agent can open the job page
        description=pitch,
        tags=tags,
        pay_min=lo,
        pay_max=hi,
        pay_currency=cur,
    )


PARSERS = {
    "yc": parse_yc_digest,
}


def detect_format(body: str) -> str | None:
    if "workatastartup.com/jobs/" in body:
        return "yc"
    return None


def load_email_folder(folder: str | Path) -> tuple[list[Posting], list[str]]:
    """Parse every *.txt body in a folder. Returns postings and the names of files it couldn't parse."""
    postings: list[Posting] = []
    unparsed: list[str] = []
    for path in sorted(Path(folder).glob("*.txt")):
        body = path.read_text(encoding="utf-8", errors="replace")
        fmt = detect_format(body)
        if fmt is None:
            unparsed.append(path.name)
            continue
        postings.extend(PARSERS[fmt](body))
    for path in sorted(Path(folder).glob("*.json")):
        postings.extend(load_postings_json(path))
    return postings, unparsed


def load_postings_json(path: str | Path) -> list[Posting]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data["postings"] if isinstance(data, dict) else data
    out = []
    for d in items:
        d.setdefault("source", "Email alert")
        out.append(Posting.from_dict(d))
    return out
