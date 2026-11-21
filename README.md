# Hunt

A daily job search pipeline. Every weekday morning it collects new postings,
throws out what Rob's skip rules rule out, scores the rest, adds the best to
his tracker sheet and sends him the top 5. He picks one or two and applies
properly.

## How it flows

1. **Collect.** Public job board APIs (Greenhouse, Lever, Ashby) for the
   companies in `config/companies.toml`, plus job alert emails from Gmail
   (YC Work at a Startup today; LinkedIn and others when alerts exist).
   No scraping and no logins.
2. **Dedupe.** Skips anything seen on an earlier day (`state/seen.json`, kept
   on the `hunt-state` branch) and anything already in the tracker.
3. **Filter.** `config/search.toml`: target titles, NYC or remote US, pay
   ceiling at least $150K, no staffing agencies without a client, no
   Java/mobile/Go-only roles, no PERM postings, not fully onsite, not 7+ years.
4. **Score.** A free keyword pass, 0 to 100, with the biggest gap in one line
   (`hunt/score.py`, `profile/profile.toml`). The daily run's agent then
   rereads the top 15 against `profile/rubric.md` and adjusts.
5. **Deliver.** Up to 15 rows appended to the tracker's Raw tab in its own
   13 columns, Status "New (pipeline)", and a short top 5 message.

The Python only reads public APIs and files and writes to `--out`. Gmail and
the Google Sheet are handled by the scheduled Claude session through its
connectors, following [ROUTINE.md](ROUTINE.md). That's why there are no
Google credentials anywhere in this repo.

## Run it yourself

Python 3.11 or newer, standard library only.

```bash
python3 -m hunt check-companies            # confirm every board name works
python3 -m hunt run --out out/             # boards only, prints the top 5
python3 -m hunt run --no-boards --emails some/folder --out out/
python3 -m pytest -q                       # tests use saved sample data, no network
```

Nothing is remembered between runs unless you pass `--seen FILE --record-seen`.

## Changing what it looks for

- Add or pause companies: `config/companies.toml`.
- Titles, locations, pay floor, skip phrases: `config/search.toml`.
- Skills the scorer counts and the gap list: `profile/profile.toml`.
- The judgment pass: `profile/rubric.md`.
