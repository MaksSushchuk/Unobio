"""The single HTTP entry point for all connectors.

Provides: SQLite response cache (keyed by method + URL + params + JSON body),
per-source rate limiting, retry with exponential backoff, and timeouts.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from evidence_bundle.paths import CACHE_DIR

DEFAULT_CACHE_PATH = CACHE_DIR / "http_cache.db"  # server/data/cache (evidence_bundle/paths.py)
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
USER_AGENT = "unobio-researcher/0.1 (+https://github.com/)"

# Minimum seconds between requests, per source. PubMed allows 3 req/s without an API key.
RATE_LIMITS: dict[str, float] = {
    "pubmed": 0.34,
    "clinicaltrials": 0.2,
    "chembl": 0.2,
    "opentargets": 0.2,
    "openfda": 0.25,
}
DEFAULT_MIN_INTERVAL = 0.5

RETRY_STATUSES = {429, 500, 502, 503, 504}


class HttpError(Exception):
    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class CachedResponse:
    """A response body. `from_cache` is True when it was served from the SQLite cache (no network)."""

    def __init__(self, status: int, text: str, url: str, from_cache: bool):
        self.status = status
        self.text = text
        self.url = url
        self.from_cache = from_cache

    def json(self) -> Any:
        return json.loads(self.text)


def cache_key(method: str, url: str, params: dict[str, Any] | None, json_body: Any) -> str:
    payload = json.dumps(
        {"m": method.upper(), "u": url, "p": params or {}, "b": json_body},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class _RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_at: dict[str, float] = {}

    def wait(self, source: str) -> None:
        interval = RATE_LIMITS.get(source, DEFAULT_MIN_INTERVAL)
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_at.get(source, 0.0))
            self._next_at[source] = start + interval
        if start > now:
            time.sleep(start - now)


class HttpClient:
    def __init__(
        self,
        cache_path: Path | None = DEFAULT_CACHE_PATH,
        ttl_s: float | None = 7 * 24 * 3600,
        max_retries: int = 4,
        backoff_base_s: float = 1.0,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.ttl_s = ttl_s
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            transport=transport,
        )
        self._limiter = _RateLimiter()
        self._db_lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(cache_path, check_same_thread=False)
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS responses ("
                " key TEXT PRIMARY KEY, url TEXT, status INTEGER, body TEXT, fetched_at REAL)"
            )
            self._db.commit()

    def close(self) -> None:
        self._client.close()
        if self._db is not None:
            self._db.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, source: str, url: str, params: dict[str, Any] | None = None) -> CachedResponse:
        return self.request(source, "GET", url, params=params)

    def get_json(self, source: str, url: str, params: dict[str, Any] | None = None) -> Any:
        return self.get(source, url, params).json()

    def post(
        self, source: str, url: str, json_body: Any, params: dict[str, Any] | None = None
    ) -> CachedResponse:
        return self.request(source, "POST", url, params=params, json_body=json_body)

    def post_json(
        self, source: str, url: str, json_body: Any, params: dict[str, Any] | None = None
    ) -> Any:
        return self.post(source, url, json_body, params).json()

    def request(
        self,
        source: str,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        use_cache: bool = True,
    ) -> CachedResponse:
        key = cache_key(method, url, params, json_body)
        if use_cache:
            cached = self._cache_get(key)
            if cached is not None:
                return cached

        last_error: HttpError | None = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(self._backoff(attempt, last_error))
            self._limiter.wait(source)
            try:
                resp = self._client.request(method, url, params=params, json=json_body)
            except httpx.TransportError as e:
                last_error = HttpError(f"{type(e).__name__}: {e}")
                continue

            if resp.status_code in RETRY_STATUSES:
                last_error = HttpError(
                    f"HTTP {resp.status_code} for {resp.url}", resp.status_code, _retry_after(resp)
                )
                continue
            if resp.status_code >= 400:
                raise HttpError(f"HTTP {resp.status_code} for {resp.url}", resp.status_code)

            out = CachedResponse(resp.status_code, resp.text, str(resp.url), from_cache=False)
            if use_cache:
                self._cache_put(key, out)
            return out

        assert last_error is not None
        raise last_error

    def _backoff(self, attempt: int, err: HttpError | None) -> float:
        if err is not None and err.retry_after is not None:
            return min(err.retry_after, 60.0)
        return min(self.backoff_base_s * 2 ** (attempt - 1), 30.0)

    def _cache_get(self, key: str) -> CachedResponse | None:
        if self._db is None:
            return None
        with self._db_lock:
            row = self._db.execute(
                "SELECT url, status, body, fetched_at FROM responses WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        url, status, body, fetched_at = row
        if self.ttl_s is not None and time.time() - fetched_at > self.ttl_s:
            return None
        return CachedResponse(status, body, url, from_cache=True)

    def _cache_put(self, key: str, resp: CachedResponse) -> None:
        if self._db is None:
            return
        with self._db_lock:
            self._db.execute(
                "INSERT OR REPLACE INTO responses (key, url, status, body, fetched_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (key, resp.url, resp.status, resp.text, time.time()),
            )
            self._db.commit()


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        return None
