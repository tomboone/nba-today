"""The healthchecks.io client: a ping after each send, `/fail` when a send gives up (spec §3.2)."""

import logging

import httpx

TIMEOUT_SECONDS = 10.0

log = logging.getLogger(__name__)


class Heartbeat:
    """Pings `url` on success and `{url}/fail` on failure; does nothing when `url` is `None`.

    A ping that cannot be delivered is logged and swallowed: monitoring must never be the reason
    a send is retried or the scheduler stops. The URL is never logged, since anyone holding it
    can ping the check.
    """

    def __init__(self, url: str | None, client: httpx.Client) -> None:
        self._url = url.rstrip("/") if url else None
        self._client = client

    def ok(self) -> None:
        self._ping("GET", "")

    def fail(self, reason: str) -> None:
        """Ping `/fail` with `reason` as the body, which healthchecks.io shows beside the ping."""
        self._ping("POST", "/fail", reason)

    def _ping(self, method: str, suffix: str, body: str | None = None) -> None:
        if self._url is None:
            log.debug("heartbeat disabled", extra={"ping": suffix or "ok"})
            return
        try:
            response = self._client.request(
                method, self._url + suffix, content=body, timeout=TIMEOUT_SECONDS
            )
            response.raise_for_status()
        # `InvalidURL` is not an `HTTPError`; a malformed `HEALTHCHECKS_URL` must not crash a send.
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            # A status error's message quotes the URL, so log only its status code.
            error = (
                f"HTTP {exc.response.status_code}"
                if isinstance(exc, httpx.HTTPStatusError)
                else str(exc)
            )
            log.warning("heartbeat ping failed", extra={"ping": suffix or "ok", "error": error})
