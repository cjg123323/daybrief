"""공용 HTTP 클라이언트: 타임아웃, 재시도, 호스트별 요청 간격."""
from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx

USER_AGENT = "daybrief/0.1 (personal research digest; +https://github.com/)"
_last_hit: dict[str, float] = {}


def get(url: str, *, params: dict | None = None, min_interval: float = 1.0,
        retries: int = 3, timeout: float = 20.0) -> httpx.Response:
    host = urlparse(url).netloc
    wait = _last_hit.get(host, 0) + min_interval - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            r = httpx.get(url, params=params, timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT})
            _last_hit[host] = time.monotonic()
            if r.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
            r.raise_for_status()  # 404 등 나머지 4xx는 재시도해도 같으므로 바로 던진다
            return r
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in (429, 500, 502, 503, 504):
                raise
            last_exc = e
            time.sleep(2 ** attempt)
        except httpx.TransportError as e:
            last_exc = e
            time.sleep(2 ** attempt)
    assert last_exc is not None
    raise last_exc
