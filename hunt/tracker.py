"""Turn scored postings into rows for the tracker's Raw tab.

The Raw tab's columns (A to M), as they are in Rob's sheet today:
    A Date added to sheet   B Source Site      C Company
    D Title                 E Location         F Job Link
    G X/100 at baseline     H X/100 at submit  I Cover Letter
    J Outreach              K Status           L Comments
    M Why I picked

The Live tab sorts Raw by column A, newest first, so the date goes in as
YYYY-MM-DD and is written with valueInputOption USER_ENTERED so Sheets reads
it as a date and the link as a clickable "Link", like the rows Rob typed.
"""

from __future__ import annotations

from datetime import date

from .models import Posting

HEADERS = [
    "Date added to sheet", "Source Site", "Company", "Title", "Location", "Job Link",
    "X/100 at baseline", "X/100 at submit", "Cover Letter", "Outreach", "Status",
    "Comments", "Why I picked",
]

STATUS_NEW = "New (pipeline)"


def to_row(p: Posting, today: str | None = None) -> list[str]:
    link = p.url.replace('"', "%22")
    return [
        today or date.today().isoformat(),
        p.source,
        p.company,
        p.title,
        p.location or "Not listed",
        f'=HYPERLINK("{link}","Link")',
        f"{p.score}/100",
        "-",
        "",
        "",
        STATUS_NEW,
        comments(p),
        p.why,
    ]


def comments(p: Posting) -> str:
    """Column L: Tim's verdict, then the gap, then any flags."""
    parts = [f"{p.verdict}." if p.verdict else "", f"Gap: {p.gap}."]
    if p.flags:
        parts.append("Flags: " + "; ".join(p.flags) + ".")
    return " ".join(x for x in parts if x).replace("..", ".")


def to_rows(postings: list[Posting], today: str | None = None) -> list[list[str]]:
    return [to_row(p, today) for p in postings]
