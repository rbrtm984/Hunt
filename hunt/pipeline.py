"""Collect, dedupe, filter, score. No side effects outside the output folder
unless record_seen is set."""

from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import Company
from .filters import first_skip_reason
from .models import Posting
from .score import rank_key, score
from .sources import boards, feeds as feedsrc
from .sources.emails import load_email_folder
from .sources.http import FetchError, get_json
from .store import SeenStore, Tracked
from .tracker import to_rows


@dataclass
class RunResult:
    kept: list[Posting] = field(default_factory=list)
    skipped: list[Posting] = field(default_factory=list)
    already_seen: int = 0
    already_tracked: int = 0
    per_source: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    unparsed_emails: list[str] = field(default_factory=list)

    def skip_counts(self) -> Counter:
        return Counter(p.skip_reason.split(":")[0] for p in self.skipped)


def collect_boards(companies: list[Company], fetcher=get_json, workers: int = 6) -> tuple[list[Posting], list[str]]:
    postings: list[Posting] = []
    errors: list[str] = []

    def one(c: Company):
        try:
            return c, boards.fetch(c.platform, c.slug, c.name, fetcher=fetcher), None
        except (FetchError, ValueError, KeyError, TypeError) as e:
            return c, [], f"{c.name} ({c.platform}/{c.slug}): {e}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for c, found, err in pool.map(one, companies):
            if err:
                errors.append(err)
            for p in found:
                p.tags = list(dict.fromkeys(p.tags + c.tags))
            postings.extend(found)
    return postings, errors


def collect_feeds(feeds: list[tuple[str, dict]], fetcher=get_json) -> tuple[list[Posting], list[str]]:
    postings: list[Posting] = []
    errors: list[str] = []
    for name, opts in feeds:
        try:
            postings += feedsrc.fetch(name, opts, fetcher=fetcher)
        except (FetchError, ValueError, KeyError, TypeError, AttributeError) as e:
            errors.append(f"feed {name}: {e}")
    return postings, errors


def run(
    *,
    companies: list[Company],
    feeds: list[tuple[str, dict]] | None = None,
    cfg: dict,
    profile: dict,
    email_dir: str | None = None,
    seen_path: str | None = None,
    tracked_path: str | None = None,
    use_boards: bool = True,
    fetcher=get_json,
    record_seen: bool = False,
    today: str | None = None,
) -> RunResult:
    today = today or date.today().isoformat()
    res = RunResult()
    collected: list[Posting] = []

    if use_boards and companies:
        found, errs = collect_boards(companies, fetcher=fetcher)
        collected += found
        res.errors += errs
    if use_boards and feeds:
        found, errs = collect_feeds(feeds, fetcher=fetcher)
        collected += found
        res.errors += errs
    if email_dir:
        found, unparsed = load_email_folder(email_dir)
        collected += found
        res.unparsed_emails = unparsed

    seen = SeenStore(seen_path)
    tracked = Tracked(tracked_path)
    batch_keys: set[str] = set()

    for p in collected:
        res.per_source[p.source] += 1
        if p.key in batch_keys:
            continue  # same role from two sources in one run
        batch_keys.add(p.key)
        if p in seen:
            res.already_seen += 1
            continue
        if tracked.has_role(p):
            res.already_tracked += 1
            continue
        p.skip_reason = first_skip_reason(p, cfg, profile)
        if p.skip_reason:
            res.skipped.append(p)
        else:
            score(p, cfg, profile)
            if tracked.has_company(p):
                p.why = (p.why + "; " if p.why else "") + "you've applied here before"
            res.kept.append(p)

    res.kept.sort(key=rank_key)
    if record_seen:
        for p in res.kept + res.skipped:
            seen.add(p, today)
        seen.save()
    return res


def write_outputs(res: RunResult, out_dir: str | Path, top: int, max_rows: int, today: str | None = None) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = to_rows(res.kept[:max_rows], today)
    (out / "results.json").write_text(
        json.dumps({"kept": [p.to_dict() for p in res.kept], "skipped": [p.to_dict() for p in res.skipped]}, indent=1),
        encoding="utf-8",
    )
    (out / "rows.json").write_text(json.dumps({"values": rows}, indent=1), encoding="utf-8")
    summary = summarize(res, top)
    (out / "summary.md").write_text(summary, encoding="utf-8")
    log = {
        "date": today or date.today().isoformat(),
        "collected_per_source": dict(res.per_source),
        "already_seen": res.already_seen,
        "already_in_tracker": res.already_tracked,
        "skipped": len(res.skipped),
        "skipped_by_reason": dict(res.skip_counts()),
        "kept": len(res.kept),
        "rows_for_tracker": len(rows),
        "errors": res.errors,
        "unparsed_emails": res.unparsed_emails,
    }
    (out / "run-log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    return log


def summarize(res: RunResult, top: int) -> str:
    lines = [f"Top {min(top, len(res.kept))} new roles today ({len(res.kept)} passed your skip rules)", ""]
    for i, p in enumerate(res.kept[:top], 1):
        pay = f", ${p.pay_min // 1000 if p.pay_min else '?'}K to ${p.pay_max // 1000}K" if p.pay_max else ""
        loc = p.location or "location not listed"
        lines.append(f"{i}. {p.company}, {p.title} ({p.score}/100, {p.verdict}). {loc}{pay}.")
        lines.append(f"   Why: {p.why or 'title and stack fit'}. Gap: {p.gap}.")
        if p.flags:
            lines.append(f"   Flags: {'; '.join(p.flags)}.")
        lines.append(f"   {p.url}")
    if not res.kept:
        lines.append("Nothing new passed your rules today.")
    return "\n".join(lines) + "\n"
