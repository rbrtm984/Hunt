"""Pull a yearly pay range out of free text like "$150K - $200K" or "$140,000—$175,000 USD"."""

from __future__ import annotations

import re

_SYMBOLS = {"$": "USD", "₹": "INR", "€": "EUR", "£": "GBP"}
_CODES = ("USD", "CAD", "INR", "EUR", "GBP", "AUD")

_NUM = r"(?P<sym{n}>[$₹€£])?\s?(?P<num{n}>\d{{1,3}}(?:,\d{{3}})+|\d+(?:\.\d+)?)\s?(?P<unit{n}>[kKmM])?"
_RANGE = re.compile(
    _NUM.format(n=1) + r"\s*(?:-|–|—|to)\s*" + _NUM.format(n=2) + r"(?P<tail>[^\n]{0,24})"
)


def _value(num: str, unit: str | None) -> float:
    v = float(num.replace(",", ""))
    if unit and unit.lower() == "k":
        v *= 1_000
    elif unit and unit.lower() == "m":
        v *= 1_000_000
    return v


def parse_pay(text: str | None) -> tuple[int | None, int | None, str | None]:
    """Return (min, max, currency) for the first plausible salary range, else Nones."""
    if not text:
        return None, None, None
    for m in _RANGE.finditer(text):
        sym = m.group("sym1") or m.group("sym2")
        tail = m.group("tail") or ""
        code = next((c for c in _CODES if c in tail.upper()), None)
        if not sym and not code:
            continue  # "3-5 years" and the like
        lo = _value(m.group("num1"), m.group("unit1") or m.group("unit2"))
        hi = _value(m.group("num2"), m.group("unit2"))
        hourly = bool(re.search(r"/\s?(hr|hour)|per hour|hourly", tail, re.I))
        if hourly:
            lo, hi = lo * 2080, hi * 2080
        if hi < 20_000 or lo > hi:
            continue
        currency = code or _SYMBOLS.get(sym or "", "USD")
        return int(lo), int(hi), currency
    return None, None, None
