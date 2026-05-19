"""Synchronous HTTP client for talking to a running metascan instance.

Sync only — ComfyUI's node execute interface is synchronous. Each
MetascanClient owns one httpx.Client; reuse it for the lifetime of the
workflow run.
"""

from __future__ import annotations

import httpx

from .config import ClientConfig
from .errors import ApiError, OfflineError

__version__ = "0.1.0"


class MetascanClient:
    def __init__(self, config: ClientConfig, timeout: float = 10.0) -> None:
        headers = {"X-Client": f"metscan-nodes/{__version__}"}
        if config.api_key:
            headers["X-API-Key"] = config.api_key
        self._http = httpx.Client(
            base_url=config.url.rstrip("/"),
            headers=headers,
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    def ping(self) -> bool:
        """Quick aliveness check. Hits /api/config (cheap, always exists).
        Returns True only on a 2xx response. Connection / timeout / non-2xx
        all return False — callers use this to decide whether to populate
        dropdowns vs show an offline sentinel."""
        try:
            r = self._http.get("/api/config")
            return 200 <= r.status_code < 300
        except httpx.TransportError:
            return False

    # ------------------------------------------------------------------
    # Shared error mapping
    # ------------------------------------------------------------------
    def _request_json(self, method: str, path: str, *, json_body=None, params=None):
        try:
            r = self._http.request(method, path, json=json_body, params=params)
        except httpx.TimeoutException as e:
            raise OfflineError(reason=f"timeout: {e}") from e
        except httpx.TransportError as e:
            raise OfflineError(reason=str(e)) from e
        if r.status_code >= 400:
            raise ApiError(status_code=r.status_code, body_excerpt=r.text)
        try:
            return r.json()
        except ValueError as e:
            raise ApiError(
                status_code=r.status_code,
                body_excerpt=f"invalid JSON: {r.text[:200]}",
            ) from e

    # ------------------------------------------------------------------
    # Config + folders
    # ------------------------------------------------------------------
    def get_config(self) -> dict:
        """Return metascan's full config payload. Callers typically only
        consume the `directories` array (for the save node dropdown)."""
        return self._request_json("GET", "/api/config")

    def list_folders(self) -> list[dict]:
        """Return only `kind=='manual'` folders. Smart folders are filtered
        client-side because metascan's smart-folder rule engine lives in
        the frontend; the nodes can't resolve smart membership without a
        Python evaluator (deferred — see spec §2)."""
        folders = self._request_json("GET", "/api/folders")
        return [f for f in folders if f.get("kind") == "manual"]

    def get_folder(self, folder_id: str) -> dict:
        """Return a single folder record. For manual folders the record
        already includes a resolved `items: [path, ...]` list."""
        return self._request_json("GET", f"/api/folders/{folder_id}")
