"""Command line.

    python -m hunt run --emails DIR --tracked tracked.csv --seen state/seen.json --out out/
    python -m hunt run --no-boards --emails DIR          # email alerts only
    python -m hunt check-companies                       # confirm every board name works

`run` never touches Gmail or the Google Sheet. It reads files and public job
boards and writes results to --out. The daily routine (ROUTINE.md) does the
Gmail and Sheets parts through Claude's connectors.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import pipeline
from .config import load_companies, load_feeds, load_profile, load_search
from .sources import boards
from .sources.http import FetchError, get_page


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hunt")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="collect, filter and score today's postings")
    r.add_argument("--emails", help="folder of alert email bodies (*.txt) and/or postings JSON (*.json)")
    r.add_argument("--tracked", help="CSV or JSON of company,title already in the tracker")
    r.add_argument("--seen", help="seen.json path (dedupe across days)")
    r.add_argument("--record-seen", action="store_true", help="update --seen (and --health) with today's run")
    r.add_argument("--health", help="health.json path: per-source counts from past runs, to spot a source that went quiet")
    r.add_argument("--no-boards", action="store_true", help="skip every API: company boards and feeds")
    r.add_argument("--no-feeds", action="store_true", help="skip the cross-company feeds (HN, Remotive, Adzuna...)")
    r.add_argument("--feeds", help="feeds TOML (default config/feeds.toml)")
    r.add_argument("--companies", help="companies TOML (default config/companies.toml)")
    r.add_argument("--search", help="search rules TOML (default config/search.toml)")
    r.add_argument("--only", help="comma-separated company names to fetch, for testing")
    r.add_argument("--out", default="out", help="output folder")
    r.add_argument("--top", type=int, default=5)
    r.add_argument("--max-rows", type=int, default=15, help="most rows to hand the tracker per day")
    r.add_argument("--no-liveness", action="store_true", help="don't open job pages to check they're still open")
    r.add_argument("--live-limit", type=int, default=60, help="most job pages to check per run")
    r.add_argument("--date", help="override today's date (YYYY-MM-DD)")

    c = sub.add_parser("check-companies", help="try every company's board and report bad names")
    c.add_argument("--companies")

    args = ap.parse_args(argv)
    if args.cmd == "run":
        return _run(args)
    return _check(args)


def _run(args) -> int:
    companies = load_companies(args.companies)
    if args.only:
        wanted = {x.strip().lower() for x in args.only.split(",")}
        companies = [c for c in companies if c.name.lower() in wanted]
    feeds = [] if args.no_feeds else load_feeds(args.feeds)
    if args.only:
        feeds = [f for f in feeds if f[0] in wanted]
    res = pipeline.run(
        companies=companies,
        feeds=feeds,
        cfg=load_search(args.search),
        profile=load_profile(),
        email_dir=args.emails,
        seen_path=args.seen,
        tracked_path=args.tracked,
        use_boards=not args.no_boards,
        record_seen=args.record_seen,
        today=args.date,
        health_path=args.health,
        page_fetcher=None if (args.no_liveness or args.no_boards) else get_page,
        live_limit=args.live_limit,
    )
    log = pipeline.write_outputs(res, args.out, args.top, args.max_rows, args.date)
    print(pipeline.summarize(res, args.top))
    print(json.dumps({k: v for k, v in log.items() if k != "sources"}, indent=1))
    return 0


def _check(args) -> int:
    bad = 0
    for c in load_companies(args.companies):
        try:
            n = len(boards.fetch(c.platform, c.slug, c.name))
            print(f"ok    {c.name:28} {c.platform}/{c.slug}: {n} open roles")
        except (FetchError, ValueError) as e:
            bad += 1
            print(f"FAIL  {c.name:28} {c.platform}/{c.slug}: {e}")
    print(f"\n{bad} board name(s) need fixing" if bad else "\nAll board names work")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
