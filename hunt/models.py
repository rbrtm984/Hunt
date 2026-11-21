"""The one data shape every source turns its postings into."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field


@dataclass
class Posting:
    source: str  # where it came from, e.g. "Greenhouse", "YC Work at a Startup"
    company: str
    title: str
    url: str
    location: str = ""
    description: str = ""  # plain text, may be empty for email alerts
    tags: list[str] = field(default_factory=list)  # stack hints, e.g. ["TypeScript"]
    pay_min: int | None = None  # yearly, USD
    pay_max: int | None = None
    pay_currency: str | None = None
    posted_at: str | None = None  # ISO date if the source gives one
    # Filled in later by the pipeline.
    score: int | None = None
    gap: str = ""
    why: str = ""
    skip_reason: str = ""

    @property
    def key(self) -> str:
        """Stable id used to remember what has been seen.

        Company + normalized title catches the same role posted on two boards;
        the URL hash keeps two genuinely different openings with the same title
        apart when the company reposts with a new link.
        """
        return f"{norm(self.company)}|{norm(self.title)}"

    @property
    def url_key(self) -> str:
        return hashlib.sha1(self.url.strip().lower().encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Posting":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


def norm(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"\(.*?\)", " ", text)  # drop "(Remote)", "(W24)" and the like
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())
