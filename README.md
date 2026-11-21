# Hunt

A daily job search pipeline. Every weekday morning it collects new postings,
throws out what Rob's skip rules rule out, scores the rest, adds the best to
his tracker sheet and sends him the top 5. He picks one or two and applies
properly.

## How it flows

1. **Collect.** Three kinds of source, no scraping and no logins:
   - Company boards (Greenhouse, Lever, Ashby, SmartRecruiters, Workable) for
     the companies in `config/companies.toml`.
   - Cross-company feeds in `config/feeds.toml`: HN Who's Hiring, Remotive,
     Remote OK, Himalayas, Jobicy, and Adzuna once a free key is set.
   - Job alert emails in Gmail: YC Work at a Startup, LinkedIn, Indeed, Dice,
     Built In, Wellfound, Glassdoor and others. Those sites bar automated
     access, so their alerts are the only way in.
2. **Dedupe.** Skips anything seen on an earlier day (`state/seen.json`, kept
   on the `hunt-state` branch) and anything already in the tracker.
3. **Filter.** `config/search.toml`: target titles, NYC or remote US, pay
   ceiling at least $150K, no staffing agencies without a client, no
   Java/mobile/Go-only roles, no PERM postings, not fully onsite, not 7+ years.
4. **Score.** A free keyword pass, 0 to 100, on Tim's weighted dimensions,
   with his verdict (Priority Submit to Do Not Submit), his pre-scoring flags
   (sponsorship, clearance, no-AI policies, hidden AI-detection text) and the
   biggest gap in one line (`hunt/score.py`, `profile/profile.toml`). Scam
   markers are skipped. The run's agent then rereads the top 15 against
   `profile/rubric.md` and adjusts.
5. **Deliver.** Up to 15 rows appended to the tracker's Raw tab in its own
   13 columns, Status "New (pipeline)", and a short top 5 message.

The Python only reads public APIs and files and writes to `--out`. Gmail and
the Google Sheet are handled by the scheduled Claude session through its
connectors, following [ROUTINE.md](ROUTINE.md). That's why there are no
Google credentials anywhere in this repo.

## Where it runs and how often

In the `hunt` cloud environment, which allows only the job APIs above, twice
each weekday: about 7:30 AM New York time (full run, top 5 message) and
1:00 PM (new rows only, a message only if something rates Submit or better).
Early runs matter: about 41% of applications arrive in a posting's first two
days, and Monday and Tuesday are the busiest. HN's thread mostly fills in the
first week of each month.

## Ideas borrowed from other projects

- [career-ops](https://github.com/santifer/career-ops): a posting-legitimacy
  check kept apart from the fit score (our scam check), refusing sponsorship
  as a flag, checking whether a posting is still open only after dedupe, and
  never submitting anything.
- [JobSpy](https://github.com/speedyapply/JobSpy): one posting shape across
  every source and per-run errors in the log. Ideas only: it scrapes LinkedIn
  and Indeed, which breaks their terms.
- [findajob](https://github.com/brockamer/findajob): reading LinkedIn alert
  emails instead of LinkedIn itself, and keeping the cost of a daily run low.
- [open-jobs-data](https://github.com/ConorsCode/open-jobs-data): a plain
  company list of platform plus board name, run on a schedule.
- A 3,570-listing comparison of job sources found HN Who's Hiring gave the
  most applications per listing and big aggregators the fewest, so Jooble is
  off and Adzuna is capped to a few searches a day.

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
- Turn feeds on or off: `config/feeds.toml`.
- Titles, locations, pay floor, skip phrases: `config/search.toml`.
- Skills the scorer counts and the gap list: `profile/profile.toml`.
- The judgment pass: `profile/rubric.md`.
