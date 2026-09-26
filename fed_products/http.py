"""Polite HTTP client: one pacing clock per host, bounded retries, and a stop at the first bot challenge."""

import threading
import time
from urllib.parse import urlsplit

import httpx

USER_AGENT = "fed-research-service-products/0.1 (+https://github.com/incrediblecrab/fed-research-service-products)"

# Minimum seconds between requests to one host within one process. Neither www.federalreserve.gov nor www.minneapolisfed.org publishes a rate limit (the Board's robots.txt answers 404; Minneapolis's disallows only /api/, search and a few other paths, measured September 25, 2026), so each gets one request a second.
HOST_INTERVAL = {}
DEFAULT_INTERVAL = 1.0


class QuotaExhausted(RuntimeError):
    """The host keeps answering 429; the caller should stop using it for this run."""


class Blocked(RuntimeError):
    """The host answered 403: a bot challenge (Cloudflare's at www.federalreserve.gov, Akamai's at www.minneapolisfed.org) or a refusal, never the page."""


class Unavailable(RuntimeError):
    """Server errors or network failures outlasted every retry."""


class PageMissing(RuntimeError):
    """A page the pipeline relies on (a listing, or a section page it was told about) answered something other than 200."""


class Fetcher:
    def __init__(self, intervals=None, max_retries=5, timeout=120.0):
        self.intervals = dict(HOST_INTERVAL, **(intervals or {}))
        self.max_retries = max_retries
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)
        self._next_at = {}
        self._lock = threading.Lock()
        self.requests = {}
        # A host that ran out of quota stays unusable for the rest of the run.
        self.dead = {}

    def _pace(self, host):
        interval = self.intervals.get(host, DEFAULT_INTERVAL)
        with self._lock:
            now = time.monotonic()
            at = max(now, self._next_at.get(host, 0.0))
            self._next_at[host] = at + interval
        if at > now:
            time.sleep(at - now)

    def get(self, url, headers=None):
        """GET with pacing and retries. Every status below 500 other than 403 and 429 is returned, not raised, so the caller sees 304 and 404."""
        host = urlsplit(url).hostname or ""
        if host in self.dead:
            raise self.dead[host]
        last_error = None
        for attempt in range(self.max_retries + 1):
            self._pace(host)
            self.requests[host] = self.requests.get(host, 0) + 1
            try:
                response = self.client.get(url, headers=headers)
            except httpx.TransportError as error:
                last_error = Unavailable(f"{type(error).__name__} from {host}{urlsplit(url).path}")
                time.sleep(min(300, 2 ** (attempt + 2)))
                continue
            status = response.status_code
            if status == 403:
                raise Blocked(f"HTTP 403 from {host}{urlsplit(url).path}")
            if status == 429:
                last_error = QuotaExhausted(f"429 from {host}")
                if attempt >= 2:
                    self.dead[host] = last_error
                    raise last_error
                time.sleep(_retry_after(response, default=60 * (attempt + 1)))
                continue
            if status >= 500:
                last_error = Unavailable(f"HTTP {status} from {host}{urlsplit(url).path}")
                time.sleep(min(300, 2 ** (attempt + 2)))
                continue
            return response
        raise last_error

    def page(self, url):
        """The body of a page that must exist: any status but 200 raises."""
        response = self.get(url)
        if response.status_code != 200:
            raise PageMissing(f"HTTP {response.status_code} from {url}")
        return response.content

    def close(self):
        self.client.close()


def _retry_after(response, default):
    value = response.headers.get("Retry-After", "")
    return min(3600, int(value)) if value.isdigit() else default
