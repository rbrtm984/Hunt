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
import urllib.parse
import urllib.request

USER_AGENT = "hunt-job-pipeline/0.1 (personal job search; public job board APIs)"


class FetchError(Exception):
    pass


_SECRET_PARAM = re.compile(r"((?:api_key|app_key|app_id|key|token|secret)=)[^&\s]+", re.I)
_JOOBLE_PATH = re.compile(r"(jooble\.org/api/)[^/?\s]+", re.I)


def redact(text: str) -> str:
    """Hide API keys in a URL or error message, so they never reach a log,
    the run summary or Rob's phone."""
    return _JOOBLE_PATH.sub(r"\1[key]", _SECRET_PARAM.sub(r"\1[key]", text))


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
                raise FetchError(redact(f"HTTP {e.code} for {url}")) from e
            last = e
        except urllib.error.URLError as e:
            if "Tunnel connection failed: 403" in str(e.reason):
                # The cloud environment's network doesn't allow this host. Retrying won't help.
                raise FetchError(f"HTTP 403, host not on this environment's allowed list: {urllib.parse.urlsplit(url).hostname}") from e
            last = e
        except (TimeoutError, json.JSONDecodeError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise FetchError(redact(f"{type(last).__name__}: {last} for {url}"))


def get_page(url: str, timeout: float = 15.0, max_bytes: int = 400_000) -> tuple[int, str, str]:
    """Fetch a job page once, for the liveness check. Returns (status, final
    URL after redirects, HTML). A 4xx/5xx comes back as its status, not an
    exception, because a 404 is the answer we're looking for. Network trouble
    and blocked hosts raise FetchError (the caller treats that as "unknown")."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(max_bytes).decode("utf-8", "replace")
            return resp.status, resp.geturl(), body
    except urllib.error.HTTPError as e:
        return e.code, e.geturl() or url, ""
    except urllib.error.URLError as e:
        if "Tunnel connection failed: 403" in str(e.reason):
            raise FetchError(f"host not on this environment's allowed list: {urllib.parse.urlsplit(url).hostname}") from e
        raise FetchError(redact(f"{e.reason} for {url}")) from e
    except (TimeoutError, OSError) as e:
        raise FetchError(redact(f"{type(e).__name__}: {e} for {url}")) from e


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
