"""Shared retrying HTTP transport with short-lived in-process response caching."""
from __future__ import annotations

import json
import threading
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_CACHE: dict[str, tuple[float, int, dict[str, str], bytes, str]] = {}
_CACHE_LOCK = threading.Lock()
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "ChartedNewsEngine/1.0 (public-data collector)"})
_RETRY = Retry(
    total=3,
    connect=3,
    read=2,
    status=3,
    backoff_factor=0.5,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"GET", "HEAD", "OPTIONS", "POST"}),
    respect_retry_after_header=True,
)
_SESSION.mount("https://", HTTPAdapter(max_retries=_RETRY))


def _cache_key(url: str, params: dict[str, Any] | None) -> str:
    return url + "?" + json.dumps(params or {}, sort_keys=True, separators=(",", ":"))


def request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: int = 30,
    cache_seconds: int = 0,
) -> requests.Response:
    key = _cache_key(url, params)
    now = time.monotonic()
    if method.upper() == "GET" and cache_seconds:
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
        if cached and cached[0] > now:
            response = requests.Response()
            response.status_code = cached[1]
            response.headers.update(cached[2])
            response._content = cached[3]
            response.url = cached[4]
            return response

    response = _SESSION.request(
        method,
        url,
        params=params,
        json=json_body,
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text.strip().replace("\r", " ").replace("\n", " ")[:500]
        message = f"HTTP {response.status_code} {response.reason}"
        if detail:
            message += f": {detail}"
        raise requests.HTTPError(
            message,
            request=response.request,
            response=response,
        ) from exc
    if method.upper() == "GET" and cache_seconds:
        with _CACHE_LOCK:
            _CACHE[key] = (
                now + cache_seconds,
                response.status_code,
                dict(response.headers),
                response.content,
                response.url,
            )
    return response


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
