import json
from pathlib import Path

from hunt import pipeline
from hunt.config import Company, load_companies, load_profile, load_search
from hunt.filters import years_required
from hunt.pay import parse_pay
from hunt.sources import boards
from hunt.sources.emails import parse_yc_digest
from hunt.tracker import HEADERS, to_row

FX = Path(__file__).parent / "fixtures"
CFG = load_search()
PROFILE = load_profile()


def fixture(name):
    return json.loads((FX / name).read_text())


def fake_fetcher(url):
    if "greenhouse" in url:
        return fixture("greenhouse.json")
    if "lever" in url:
        return fixture("lever.json")
    if "ashby" in url:
        return fixture("ashby.json")
    raise AssertionError(url)


COMPANIES = [
    Company("Acme", "greenhouse", "acme", []),
    Company("Beta", "lever", "beta", []),
    Company("Gamma", "ashby", "gamma", []),
]


def run(**kw):
    return pipeline.run(companies=COMPANIES, cfg=CFG, profile=PROFILE, fetcher=fake_fetcher, **kw)


def by_title_company(postings):
    return {(p.company, p.title): p for p in postings}


def test_config_files_load():
    assert len(load_companies()) > 20
    assert CFG["pay"]["floor_usd"] == 150000


def test_parse_pay():
    assert parse_pay("$150K - $200K / 0.1%") == (150000, 200000, "USD")
    assert parse_pay("Salary range: $160,000 - $210,000 USD") == (160000, 210000, "USD")
    assert parse_pay("₹7M - ₹13M INR / 0.25%") == (7000000, 13000000, "INR")
    assert parse_pay("3-5 years of experience") == (None, None, None)
    assert parse_pay("$60 - $80/hr") == (124800, 166400, "USD")


def test_years_required():
    assert years_required("2+ years of experience building software") == 2
    assert years_required("8+ years of experience required") == 8
    assert years_required("We have 10 years in business") is None


def test_board_parsers_shapes():
    gh = boards.parse_greenhouse(fixture("greenhouse.json"), "Acme")
    assert gh[0].pay_max == 210000 and gh[0].location == "New York, NY"
    assert "agentic workflows" in gh[0].description and "<p>" not in gh[0].description
    lv = boards.parse_lever(fixture("lever.json"), "Beta")
    assert lv[0].pay_min == 150000 and "Remote" in lv[0].location and "MCP" in lv[0].description
    ab = boards.parse_ashby(fixture("ashby.json"), "Gamma")
    assert len(ab) == 2  # unlisted job dropped
    assert ab[0].pay_max == 185000 and "Remote - US" in ab[0].location


def test_skip_rules():
    res = run()
    skipped = {(p.company, p.title): p.skip_reason for p in res.skipped}
    kept = by_title_company(res.kept)
    assert ("Acme", "Applied AI Engineer") in kept
    assert ("Beta", "Forward Deployed Engineer") in kept
    assert ("Gamma", "AI Engineer, Product") in kept
    assert skipped[("Acme", "Staff Software Engineer, Platform")].startswith("title")
    assert skipped[("Gamma", "Software Engineer")].startswith("pay")
    assert skipped[("Beta", "Software Engineer")].startswith("PERM")
    assert skipped[("Acme", "Software Engineer, Backend")].startswith("stack")
    # The two "Full Stack Engineer" roles at Acme share a key, so only the first
    # (London) is judged in one run; it's dropped on location.
    assert skipped[("Acme", "Full Stack Engineer")].startswith("location")
    assert skipped[("Beta", "Full Stack Engineer")].startswith("seniority")


def test_onsite_rule():
    from hunt.filters import first_skip_reason
    from hunt.models import Posting
    p = Posting("Greenhouse", "Acme", "Full Stack Engineer", "u", "New York, NY",
                "This role is 5 days a week in the office. TypeScript, React.")
    assert first_skip_reason(p, CFG, PROFILE).startswith("fully onsite")


def test_scores_rank_ai_roles_first():
    res = run()
    top = res.kept[0]
    assert top.title in {"Applied AI Engineer", "AI Engineer, Product", "Forward Deployed Engineer"}
    assert all(0 <= p.score <= 100 for p in res.kept)
    assert all(p.gap for p in res.kept)
    edu = by_title_company(res.kept)[("Gamma", "AI Engineer, Product")]
    assert edu.score >= 75  # K-12 domain bonus on top of the AI title


def test_yc_digest_and_tracker_dedupe(tmp_path):
    jobs = parse_yc_digest((FX / "emails" / "yc-digest.txt").read_text())
    assert [j.company for j in jobs] == ["Examplecorp", "Overseas AI", "Lowpay Medical", "Logistics Co", "Curri"]
    assert jobs[0].tags == ["Next.js", "LLMs", "AI Agents"] and jobs[0].pay_max == 250000
    res = pipeline.run(companies=[], cfg=CFG, profile=PROFILE, use_boards=False,
                       email_dir=str(FX / "emails"), tracked_path=str(FX / "tracked.csv"))
    kept = [p.company for p in res.kept]
    skipped = {p.company: p.skip_reason for p in res.skipped}
    assert kept == ["Examplecorp"]
    assert skipped["Overseas AI"] == "pay: in INR"
    assert skipped["Lowpay Medical"].startswith("pay: tops out")
    assert skipped["Logistics Co"].startswith("title")
    assert res.already_tracked == 1  # Curri is already in the tracker


def test_seen_store_dedupes_across_days(tmp_path):
    seen = tmp_path / "seen.json"
    first = run(seen_path=str(seen), record_seen=True, today="2026-11-20")
    assert first.kept and seen.exists()
    second = run(seen_path=str(seen), record_seen=True, today="2026-11-21")
    assert second.kept == [] and second.skipped == []
    assert second.already_seen == len(first.kept) + len(first.skipped)


def test_dry_run_does_not_write_seen(tmp_path):
    seen = tmp_path / "seen.json"
    run(seen_path=str(seen))
    assert not seen.exists()


def test_tracker_rows_match_sheet_columns(tmp_path):
    res = run()
    row = to_row(res.kept[0], "2026-11-21")
    assert len(row) == len(HEADERS) == 13
    assert row[0] == "2026-11-21"
    assert row[5].startswith('=HYPERLINK("https://') and row[5].endswith(',"Link")')
    assert row[6].endswith("/100") and row[10] == "New (pipeline)"
    log = pipeline.write_outputs(res, tmp_path, top=5, max_rows=2, today="2026-11-21")
    assert log["rows_for_tracker"] == 2
    assert len(json.loads((tmp_path / "rows.json").read_text())["values"]) == 2
    assert "Top " in (tmp_path / "summary.md").read_text()


def test_bad_board_is_reported_not_fatal():
    from hunt.sources.http import FetchError

    def flaky(url):
        if "lever" in url:
            raise FetchError("HTTP 404")
        return fake_fetcher(url)

    res = pipeline.run(companies=COMPANIES, cfg=CFG, profile=PROFILE, fetcher=flaky)
    assert any("Beta" in e for e in res.errors)
    assert res.kept


# --- Phase 2: more sources and Tim's rules -------------------------------

from hunt.config import load_feeds
from hunt.score import verdict
from hunt.sources import feeds

FEEDS = FX / "feeds"


def feed(name):
    return json.loads((FEEDS / name).read_text())


def feed_fetcher(url, body=None):
    if "remoteok" in url:
        return feed("remoteok.json")
    if "remotive" in url:
        return feed("remotive.json")
    if "search_by_date" in url:
        return {"hits": [{"objectID": "100", "title": "Ask HN: Who is hiring? (November 2026)"}]}
    if "jobicy" in url:
        return feed("jobicy.json")
    if "items/100" in url:
        return feed("hn_item.json")
    raise AssertionError(url)


def test_feeds_config_and_keys_optional():
    names = [n for n, _ in load_feeds()]
    assert "hn" in names and "remotive" in names
    adz = dict(load_feeds())["adzuna"]
    try:
        feeds.fetch("adzuna", adz, fetcher=feed_fetcher, env={})  # no key, no call
        raise AssertionError("expected NotConfigured")
    except feeds.NotConfigured:
        pass


def test_new_parsers():
    hn = feeds.parse_hn_thread(feed("hn_item.json"))
    assert [p.company for p in hn] == ["Tessellate"]  # no-pipe and non-engineering comments dropped
    assert hn[0].title == "Founding Full Stack Engineer" and hn[0].pay_max == 220000
    assert "NYC" in hn[0].location
    w = boards.parse_workable(feed("workable.json"), "x")[0]
    assert (w.company, w.pay_max, w.location) == ("Quillbook", 185000, "New York, NY, United States")
    s = boards.parse_smartrecruiters(feed("smartrecruiters.json"), "x", "MosaicHealth")[0]
    assert s.url == "https://jobs.smartrecruiters.com/MosaicHealth/744000" and s.company == "Mosaic Health"
    ok = feeds.parse_remoteok(feed("remoteok.json"))
    assert len(ok) == 2 and ok[0].pay_max == 210000


def test_tim_rules_flags_verdicts_and_scam():
    res = pipeline.run(companies=[], feeds=[("remoteok", {}), ("remotive", {}), ("hn", {}), ("jobicy", {})],
                       cfg=CFG, profile=PROFILE, fetcher=feed_fetcher)
    kept = by_title_company(res.kept)
    skipped = {p.company: p.skip_reason for p in res.skipped}
    assert skipped["Payfast"].startswith("scam marker")
    edu = kept[("Brightpath Learning", "Full Stack Engineer")]
    assert "work authorization or sponsorship terms" in edu.flags
    assert "says no AI-written applications" in edu.flags
    hn = kept[("Tessellate", "Founding Full Stack Engineer")]
    assert "hidden AI-detection instruction in the posting" in hn.flags
    assert ("Jobicy Co", "Applied AI Engineer") in kept
    assert all(p.verdict for p in res.kept)
    row = to_row(edu, "2026-11-21")
    assert row[11].startswith(edu.verdict + ". Gap:") and "Flags:" in row[11]


def test_verdict_scale():
    assert [verdict(s) for s in (90, 80, 70, 55, 30)] == [
        "Priority Submit", "Submit", "Conditional Submit", "Reach", "Do Not Submit"]
    assert verdict(90, thin=True) == "Read the posting"


def test_health_flags_errors_shape_and_quiet_sources(tmp_path):
    from hunt import health

    def broken(url, body=None):
        if "greenhouse" in url:
            return {"postings": []}  # format changed
        if "lever" in url:
            raise pipeline.FetchError("HTTP 404 for x")
        if "ashby" in url:
            return {"jobs": []}  # usually has postings
        return feed_fetcher(url, body)

    hp = tmp_path / "health.json"
    health.save_history(hp, {"Ashby: Gamma": [4, 5, 3]})
    res = pipeline.run(companies=COMPANIES, feeds=[("hn", {}), ("adzuna", {"queries": ["x"]})], cfg=CFG,
                       profile=PROFILE, fetcher=broken, health_path=str(hp), record_seen=True,
                       seen_path=str(tmp_path / "seen.json"), env={})
    text = " ".join(res.problems)
    assert "Greenhouse: Acme: response format changed" in text
    assert "Lever: Beta: board not found (HTTP 404)" in text
    assert "Ashby: Gamma: returned nothing, but usually has about 4" in text
    assert "adzuna" not in text  # no key is "not set up", not a problem
    assert res.sources["Feed: hn"]["status"] == "ok"
    assert pipeline.summarize(res, 5).startswith("3 source problem(s):")
    hist = json.loads(hp.read_text())
    assert hist["Ashby: Gamma"] == [4, 5, 3, 0] and "Lever: Beta" not in hist


def test_clean_run_says_all_sources_worked():
    res = run()
    assert not res.problems
    assert pipeline.summarize(res, 5).startswith("All 3 sources worked.")


# --- Liveness, scoring retry, Google Jobs, HN thread age ---------------------

from datetime import datetime, timezone

from hunt import liveness
from hunt.models import Posting
from hunt.sources.http import FetchError, redact


def test_liveness_reads_pages_without_false_alarms():
    url = "https://jobs.lever.co/acme/1234-abcd"
    v = liveness.verdict_from_page
    assert v(url, 404, url, "")[0] == "closed"
    assert v(url, 200, "https://jobs.lever.co/acme", "<html>jobs</html>")[0] == "closed"
    gh = "https://boards.greenhouse.io/acme/jobs/1"
    assert v(gh, 200, "https://boards.greenhouse.io/acme?error=true", "")[0] == "closed"
    assert v(url, 200, url, "<p>Sorry, this job is no longer available.</p>")[0] == "closed"
    # A phrase that only sits in the page's JavaScript bundle is not a signal.
    assert v(url, 200, url, '<script>t={closed:"This job is no longer available"}</script><h1>Engineer</h1>')[0] == "open"
    assert v(url, 200, "https://jobs.lever.co/login", "")[0] == "unknown"
    assert v(url, 503, url, "")[0] == "unknown"


def test_liveness_skips_board_apis_and_barred_sites():
    gh = Posting(source="Greenhouse", company="A", title="AI Engineer", url="https://boards.greenhouse.io/a/jobs/1")
    li = Posting(source="LinkedIn alert", company="B", title="AI Engineer", url="https://www.linkedin.com/jobs/view/9")
    hn = Posting(source="HN Who's Hiring", company="C", title="AI Engineer", url="https://news.ycombinator.com/item?id=1")
    rm = Posting(source="Remotive", company="D", title="AI Engineer", url="https://remotive.com/jobs/1")
    calls = []

    def pages(url):
        calls.append(url)
        raise FetchError("host not on this environment's allowed list: remotive.com")

    counts = liveness.check_all([gh, li, hn, rm], page_fetcher=pages)
    assert calls == ["https://remotive.com/jobs/1"]
    assert (gh.live, li.live, hn.live, rm.live) == ("open", "not checked", "not checked", "unknown")
    assert counts["unknown"] == 1


def feed_run(**kw):
    return pipeline.run(companies=[], feeds=[("remotive", {}), ("remoteok", {})], cfg=CFG, profile=PROFILE,
                        fetcher=feed_fetcher, **kw)


def test_closed_postings_are_dropped_before_scoring(tmp_path):
    checked = []

    def pages(url):
        checked.append(url)
        if url.endswith("/x-9"):  # Brightpath's Remotive posting has closed
            return 404, url, ""
        return 200, url, "<h1>Open role</h1>"

    seen_path = tmp_path / "seen.json"
    res = feed_run(page_fetcher=pages, seen_path=str(seen_path), record_seen=True)
    assert [p.company for p in res.closed] == ["Brightpath Learning"]
    assert res.closed[0].score is None and res.closed[0].skip_reason.startswith("closed: ")
    assert "Brightpath Learning" not in {p.company for p in res.kept}
    log = pipeline.write_outputs(res, tmp_path / "out", 5, 15)
    assert log["dropped_as_closed"] == 1 and "returns 404" in log["closed"][0]
    # Remembered, so the next run neither checks nor scores it again.
    checked.clear()
    again = feed_run(page_fetcher=pages, seen_path=str(seen_path))
    assert not any(u.endswith("/x-9") for u in checked) and not again.closed


def test_scoring_failure_is_reported_not_zero(monkeypatch, tmp_path):
    real = pipeline.score
    tries = {"n": 0}

    def flaky(p, cfg, profile):
        if p.company == "Brightpath Learning":
            tries["n"] += 1
            raise RuntimeError("model timed out")
        return real(p, cfg, profile)

    monkeypatch.setattr(pipeline, "score", flaky)
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    res = feed_run()
    assert [p.company for p in res.score_errors] == ["Brightpath Learning"]
    assert tries["n"] == 3 * len(res.score_errors)  # three tries each
    for p in res.score_errors:
        assert p.score is None and "model timed out" in p.score_error
        assert p not in res.kept
    log = pipeline.write_outputs(res, tmp_path / "out", 5, 15)
    assert log["score_errors"] and "couldn't be scored" in pipeline.summarize(res, 5)


def test_google_jobs_prefers_employer_link_and_is_capped():
    jobs = feeds.parse_serpapi(feed("serpapi.json"))
    nw, co = jobs
    assert nw.url == "https://jobs.northwind.example/careers/42"  # not the LinkedIn link
    assert (nw.pay_min, nw.pay_max) == (170000, 210000)
    assert co.location == "Anywhere (Remote)" and co.url.startswith("https://www.indeed.com/")
    calls = []

    def fetcher(url, body=None):
        calls.append(url)
        return feed("serpapi.json")

    opts = {"queries": [f"q{i}" for i in range(8)], "where": ["New York", ""], "max_searches": 50}
    feeds.fetch("serpapi", opts, fetcher=fetcher, env={"SERPAPI_API_KEY": "sk_test_123"})
    assert len(calls) == feeds.SERPAPI_MAX_SEARCHES == 5
    try:
        feeds.fetch("serpapi", opts, fetcher=fetcher, env={})
        raise AssertionError("expected NotConfigured")
    except feeds.NotConfigured:
        pass
    assert feeds.parse_serpapi({"error": "Google hasn't returned any results for this query."}) == []


def test_api_keys_never_reach_logs():
    msg = redact("HTTP 401 for https://serpapi.com/search.json?engine=google_jobs&q=ai&api_key=sk_live_SECRET")
    assert "SECRET" not in msg and "api_key=[key]" in msg
    assert "abc123" not in redact("HTTP 403 for https://jooble.org/api/abc123")
    assert "SECRET" not in redact("https://api.adzuna.com/v1/api/jobs/us/search/1?app_id=SECRET&app_key=SECRET")


def test_hn_uses_only_this_months_thread():
    old = {"hits": [{"objectID": "100", "title": "Ask HN: Who is hiring? (September 2026)",
                     "created_at_i": int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())}]}
    try:
        feeds.fetch_hn(lambda url, body=None: old, now=datetime(2026, 11, 22, tzinfo=timezone.utc))
        raise AssertionError("expected a stale-thread problem")
    except Exception as e:  # noqa: BLE001
        assert "this month's isn't up yet" in str(e)
    fresh = {"hits": [dict(old["hits"][0], created_at_i=int(datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp()))]}
    got = feeds.fetch_hn(lambda url, body=None: fresh if "search_by_date" in url else feed("hn_item.json"),
                         now=datetime(2026, 11, 22, tzinfo=timezone.utc))
    assert got and all(p.source == "HN Who's Hiring" for p in got)


def test_remote_abroad_is_not_mistaken_for_us():
    from hunt.filters import location_check
    eu = Posting(source="Ashby", company="Cohere", title="Engineer",
                 url="x", location="Europe / Brussels / Munich / London (Remote)")
    us = Posting(source="Ashby", company="Sayari", title="Engineer", url="x", location="Remote (USA)")
    both = Posting(source="Ashby", company="Hex", title="Engineer", url="x", location="Remote - US / London")
    assert location_check(eu, CFG).startswith("location: remote outside the US")
    assert location_check(us, CFG) == "" and location_check(both, CFG) == ""
