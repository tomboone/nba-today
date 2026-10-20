"""The one way sources reach NBA.com: curl_cffi imitating Chrome, with nba.com headers (ADR-0004).

`cdn.nba.com` and `stats.nba.com` sit behind Akamai, which fingerprints the TLS handshake. Probed
from the home IP on 2026-10-20: curl, httpx, and requests got a 403; curl_cffi with
`impersonate="chrome"` and the `Origin`/`Referer` below got a 200; the same without those headers
got a 403 again. Both are required.

This is the only module that imports curl_cffi (spec §3.1). The session keeps `make_client`'s
timeout and retry policy: curl_cffi's own `retry` retries every request error, read timeouts
included, and never a 5xx, so the loop here mirrors `RetryTransport` instead.
"""

import logging
import time
from types import TracebackType
from typing import Self

from curl_cffi import requests

from nba_today.sources.http import BACKOFF_SECONDS, RETRIES, TIMEOUT_SECONDS

IMPERSONATE = "chrome"
NBA_HEADERS = {
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "Accept": "application/json",
}

log = logging.getLogger(__name__)


class NbaSession:
    """A curl_cffi session that retries connection errors and 5xx responses.

    Sleeps `backoff * 2**attempt` between tries. After the last retry a 5xx response is returned
    as-is and a connection error is re-raised, as `RetryTransport` does for httpx.
    """

    def __init__(
        self,
        curl: requests.Session[requests.Response],
        *,
        retries: int = RETRIES,
        backoff: float = BACKOFF_SECONDS,
    ) -> None:
        self.curl = curl
        self._retries = retries
        self._backoff = backoff

    def get(self, url: str, params: dict[str, str] | None = None) -> requests.Response:
        for attempt in range(self._retries + 1):
            last = attempt == self._retries
            try:
                response = self.curl.get(url, params=params)
            except requests.exceptions.ConnectionError as exc:
                if last:
                    raise
                reason = type(exc).__name__
            else:
                if response.status_code < 500 or last:
                    return response
                reason = str(response.status_code)
            delay = self._backoff * 2**attempt
            log.warning(
                "retrying request", extra={"url": url, "reason": reason, "attempt": attempt + 1}
            )
            time.sleep(delay)
        raise AssertionError("unreachable")

    def close(self) -> None:
        self.curl.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def make_nba_session() -> NbaSession:
    return NbaSession(
        requests.Session(impersonate=IMPERSONATE, headers=NBA_HEADERS, timeout=TIMEOUT_SECONDS)
    )
