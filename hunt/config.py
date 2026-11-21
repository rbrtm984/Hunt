from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Company:
    name: str
    platform: str  # greenhouse | lever | ashby
    slug: str
    tags: list[str]


def load_search(path: str | Path | None = None) -> dict:
    with open(path or ROOT / "config" / "search.toml", "rb") as f:
        return tomllib.load(f)


def load_companies(path: str | Path | None = None) -> list[Company]:
    with open(path or ROOT / "config" / "companies.toml", "rb") as f:
        data = tomllib.load(f)
    return [
        Company(name=c["name"], platform=c["platform"], slug=c["slug"], tags=c.get("tags", []))
        for c in data.get("company", [])
        if c.get("enabled", True)
    ]


def load_profile(path: str | Path | None = None) -> dict:
    with open(path or ROOT / "profile" / "profile.toml", "rb") as f:
        return tomllib.load(f)
