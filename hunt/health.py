"""Per-source health, so a broken source is loud instead of silently empty.

Every run records, for each source (each company board, each feed, each
kind of alert email): how many postings it returned, how many were missing
a title or link, and any error with its HTTP status. Compared with recent
runs kept in state/health.json, a source is flagged when it:

- errors (HTTP status, timeout, or a response shape the parser doesn't know),
- returns nothing when it usually returns postings,
- returns mostly broken postings (no title or no link).

Problems go at the top of the run's report.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

HISTORY_RUNS = 7  # how many past runs to remember per source


class ShapeError(ValueError):
    """The response wasn't the shape the parser expects: the source changed its format."""


def expect(data, kind: type, key: str | None = None, source: str = ""):
    if not isinstance(data, kind):
        raise ShapeError(f"{source}: expected a {kind.__name__}, got {type(data).__name__}")
    if key is not None and key not in data:
        raise ShapeError(f"{source}: response has no '{key}' field")
    return data[key] if key is not None else data


def http_status(error: str) -> str:
    m = re.search(r"HTTP (\d{3})", error)
    return m.group(1) if m else ""


def load_history(path: str | Path | None) -> dict:
    if not path or not Path(path).exists():
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def assess(sources: dict[str, dict], history: dict) -> list[str]:
    """Plain-language problem lines, worst first. Mutates each source's 'status'."""
    problems: list[tuple[int, str]] = []
    for name, s in sorted(sources.items()):
        past = [n for n in history.get(name, []) if isinstance(n, int)]
        if s.get("error"):
            s["status"] = "shape" if s.get("shape") else "error"
            code = s.get("http_status")
            if s.get("shape"):
                problems.append((0, f"{name}: response format changed ({s['error']}). Parser needs an update."))
            elif code == "404":
                problems.append((1, f"{name}: board not found (HTTP 404). Check its board name in config."))
            elif code in ("401", "403"):
                problems.append((1, f"{name}: access refused (HTTP {code}). Check the environment's allowed hosts or the API key."))
            else:
                problems.append((2, f"{name}: failed ({s['error']})."))
            continue
        n = s.get("fetched", 0)
        if n == 0 and len(past) >= 2 and sum(1 for x in past if x > 0) >= max(2, len(past) // 2):
            s["status"] = "empty"
            problems.append((3, f"{name}: returned nothing, but usually has about {round(sum(past) / len(past))}."))
            continue
        bad = s.get("malformed", 0)
        if n and bad / n > 0.2:
            s["status"] = "malformed"
            problems.append((2, f"{name}: {bad} of {n} postings had no title or link. Parser may be stale."))
            continue
        s["status"] = "ok"
    return [p for _, p in sorted(problems)]


def update_history(history: dict, sources: dict[str, dict]) -> dict:
    out = dict(history)
    for name, s in sources.items():
        if s.get("error"):
            continue  # an outage shouldn't teach us that 0 is normal
        out[name] = (list(out.get(name, [])) + [int(s.get("fetched", 0))])[-HISTORY_RUNS:]
    return out


def save_history(path: str | Path, history: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(history, indent=1, sort_keys=True), encoding="utf-8")
