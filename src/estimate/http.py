"""Small HTTP helper: JSON GET with retries, a custom User-Agent and optional rate limiting."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# Cloudflare in front of Polymarket rejects the default Python User-Agent.
USER_AGENT = "estimate-research/0.1"


class RateLimiter:
    """Allow at most `per_minute` calls per rolling minute, shared across threads."""

    def __init__(self, per_minute: float):
        self.interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next - now
            self._next = max(now, self._next) + self.interval
        if sleep_for > 0:
            time.sleep(sleep_for)


class HTTPStatusError(RuntimeError):
    def __init__(self, code: int, url: str, body: str):
        super().__init__(f"HTTP {code} for {url}: {body[:200]}")
        self.code = code


def get_json(url: str, params: dict | None = None, *, retries: int = 5,
             limiter: RateLimiter | None = None, timeout: float = 60):
    if params:
        url = f"{url}?{urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last: Exception | None = None
    for attempt in range(retries):
        if limiter:
            limiter.wait()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
            body = err.read().decode(errors="replace")
            last = HTTPStatusError(err.code, url, body)
            if err.code not in (429, 500, 502, 503, 504):
                raise last from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            last = err
        time.sleep(min(60, 2 ** attempt))
    raise last  # type: ignore[misc]
