"""Retry wrapper for outbound POSTs, written for a specific observed
failure: PythonAnywhere's free-tier outbound proxy intermittently returns
"Tunnel connection failed: 503 Service Unavailable" against api.telegram.org
even though that host is on the free-tier allowlist — the block is a
transient proxy hiccup, not a real restriction, so a short retry clears it.
"""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 2


def post_with_retry(url: str, **kwargs) -> requests.Response:
    """requests.post with retries on connection/proxy errors and 5xx responses.

    Mirrors requests.post's contract: returns the Response (caller can still
    call raise_for_status()) or raises the underlying RequestException if
    every attempt fails.
    """
    last_exc: requests.RequestException | None = None
    last_resp: requests.Response | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(url, **kwargs)
        except requests.RequestException as exc:
            last_exc, last_resp = exc, None
            log.warning("POST %s failed (attempt %d/%d): %s", url, attempt, _MAX_ATTEMPTS, exc)
        else:
            if resp.status_code < 500:
                return resp
            last_exc, last_resp = None, resp
            log.warning("POST %s returned %d (attempt %d/%d)", url, resp.status_code, attempt, _MAX_ATTEMPTS)

        if attempt < _MAX_ATTEMPTS:
            time.sleep(_RETRY_DELAY_SECONDS)

    if last_exc is not None:
        raise last_exc
    return last_resp
