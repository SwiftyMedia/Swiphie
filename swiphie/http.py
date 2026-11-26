"""Small stdlib-only HTTP helpers.

Swiphie deliberately has no third-party Python dependencies.  ``urllib`` is
enough for the handful of plain GET/POST requests the registry needs.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping


class HttpError(RuntimeError):
    """A network request failed before a usable response was received."""


@dataclass(frozen=True)
class HttpResult:
    status: int
    url: str
    headers: Mapping[str, str]
    body: bytes

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None


def _read_limited(stream, max_bytes: int) -> bytes:
    body = stream.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise HttpError(f"response exceeded {max_bytes} bytes")
    return body


def request(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 10.0,
    max_bytes: int = 2_000_000,
) -> HttpResult:
    """Perform a request and return a bounded response.

    HTTP error statuses are returned as normal ``HttpResult`` values so callers
    can decide whether a 4xx/5xx is fatal.  Transport failures raise
    :class:`HttpError`.
    """

    request_obj = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request_obj.add_header(key, value)

    # Bound every response before handing data to parsers
    try:
        with urllib.request.urlopen(request_obj, timeout=timeout) as response:
            body = _read_limited(response, max_bytes)
            return HttpResult(
                status=int(response.status),
                url=response.geturl(),
                headers=dict(response.headers.items()),
                body=body,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read(min(max_bytes, 64 * 1024))
        return HttpResult(
            status=int(exc.code),
            url=exc.geturl(),
            headers=dict(exc.headers.items()) if exc.headers else {},
            body=body,
        )
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        raise HttpError(f"request failed: {exc}") from exc


def fetch_text(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 10.0,
    max_bytes: int = 2_000_000,
) -> str:
    """Fetch a URL and require a successful HTTP status."""

    result = request(url, headers=headers, timeout=timeout, max_bytes=max_bytes)
    if result.status < 200 or result.status >= 300:
        raise HttpError(f"fetch failed: HTTP {result.status} for {url}")
    return result.text()


def post_json(
    url: str,
    payload: object,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 10.0,
    max_bytes: int = 2_000_000,
) -> HttpResult:
    """POST a JSON body and return the raw response."""

    merged_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    merged_headers.update(headers or {})
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return request(
        url,
        method="POST",
        headers=merged_headers,
        data=body,
        timeout=timeout,
        max_bytes=max_bytes,
    )
