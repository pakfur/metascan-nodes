"""Two error types raised by MetascanClient and consumed by the nodes."""

from __future__ import annotations


class ApiError(Exception):
    """Metascan responded with a non-2xx status or unparseable JSON.

    Carries the HTTP status code and a 500-char-capped excerpt of the
    response body so callers can surface a useful message in ComfyUI's
    node-error UI without leaking giant payloads into the log.
    """

    _MAX_BODY = 500

    def __init__(self, status_code: int, body_excerpt: str) -> None:
        self.status_code = status_code
        self.body_excerpt = (body_excerpt or "")[: self._MAX_BODY]
        super().__init__(f"metascan API error {status_code}: {self.body_excerpt}")


class OfflineError(Exception):
    """Metascan is unreachable (connection refused, DNS, or timeout)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"metascan offline: {reason}")
