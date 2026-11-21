"""What the pipeline has already seen, and what's already in the tracker.

seen.json is a plain JSON file so it can live on a git branch between cloud
runs (cloud containers are thrown away after each run). See ROUTINE.md.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from .models import Posting, norm


class SeenStore:
    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        self.data: dict[str, dict] = {}
        if self.path and self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        self.urls = {v.get("url_key") for v in self.data.values()}

    def __contains__(self, p: Posting) -> bool:
        return p.key in self.data or p.url_key in self.urls

    def add(self, p: Posting, today: str | None = None) -> None:
        self.data[p.key] = {
            "first_seen": today or date.today().isoformat(),
            "url_key": p.url_key,
            "score": p.score,
            "skip": p.skip_reason or None,
        }
        self.urls.add(p.url_key)

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")


class Tracked:
    """Company + title pairs already in the tracker's Raw tab (columns C and D).

    The daily run exports them with the Sheets connector to a two-column CSV
    (company,title) or a JSON list of [company, title] pairs.
    """

    def __init__(self, path: str | Path | None):
        self.pairs: set[tuple[str, str]] = set()
        self.companies: set[str] = set()
        if not path or not Path(path).exists():
            return
        path = Path(path)
        if path.suffix == ".json":
            rows = json.loads(path.read_text(encoding="utf-8"))
            rows = rows.get("values", rows) if isinstance(rows, dict) else rows
        else:
            rows = list(csv.reader(path.open(encoding="utf-8")))
        for row in rows:
            if len(row) < 2 or not (row[0] or "").strip():
                continue
            c, t = norm(row[0]), norm(row[1])
            self.pairs.add((c, t))
            self.companies.add(c)

    def has_role(self, p: Posting) -> bool:
        return (norm(p.company), norm(p.title)) in self.pairs

    def has_company(self, p: Posting) -> bool:
        return norm(p.company) in self.companies
