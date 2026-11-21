"""Tiny HTTP + HTML helpers. Standard library only, so the cloud run needs no installs.

urllib reads HTTPS_PROXY and the system CA store on its own, which is what
cloud sessions need.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.request

USER_AGENT = "hunt-job-pipeline/0.1 (personal job search; public job board APIs)"


class FetchError(Exception):
    pass


def get_json(url: str, timeout: float = 20.0, retries: int = 2, body: dict | None = None):
    """GET a URL as JSON, or POST `body` as JSON when given (Jooble)."""
    last: Exception | None = None
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 404 means a wrong board name: retrying won't help.
            if e.code in (400, 401, 403, 404):
                raise FetchError(f"HTTP {e.code} for {url}") from e
            last = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise FetchError(f"{type(last).__name__}: {last} for {url}")


_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr)[^>]*>", re.I)


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)  # Greenhouse double-escapes its HTML
    text = _BLOCK.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()
