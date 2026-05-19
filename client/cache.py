"""60-second TTL cache for dropdown-feeding client calls.

ComfyUI calls INPUT_TYPES() at editor load and again whenever the user
opens a node's settings, so an uncached call would hammer metascan for
no good reason. We cache only the calls that feed dropdowns; per-execute
calls in nodes/*.py go through the client directly.

Cache misses for unreachable metascan return OFFLINE_SENTINEL in a
single-entry list so the node still renders in the editor; we do NOT
cache the failure so the dropdown recovers immediately when metascan
comes back online.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from .api import MetascanClient
from .errors import ApiError, OfflineError

OFFLINE_SENTINEL = "<metascan offline — check MetascanSettings>"
_TTL_SECONDS = 60.0

T = TypeVar("T")

# key: (base_url, method_name) -> (fetched_at, value)
_CACHE: dict[tuple[str, str], tuple[float, list[str]]] = {}


def clear_cache() -> None:
    """Test helper. Production code should never need this — entries
    naturally expire after _TTL_SECONDS."""
    _CACHE.clear()


def _cached(client: MetascanClient, method_name: str, fetch: Callable[[], list[str]]) -> list[str]:
    base = str(client._http.base_url).rstrip("/")
    key = (base, method_name)
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and (now - hit[0]) < _TTL_SECONDS:
        return hit[1]
    try:
        value = fetch()
    except (OfflineError, ApiError):
        # Don't cache failures — let next call retry immediately.
        return [OFFLINE_SENTINEL]
    _CACHE[key] = (now, value)
    return value


def combo_directories(client: MetascanClient) -> list[str]:
    """Watched-directory filepaths from GET /api/config, for the
    MetascanSaveImage `directory` dropdown."""
    def fetch() -> list[str]:
        cfg = client.get_config()
        return [d["filepath"] for d in cfg.get("directories", [])]
    return _cached(client, "combo_directories", fetch)


def combo_folders(client: MetascanClient) -> list[str]:
    """Manual-folder names from GET /api/folders (smart folders already
    filtered by the client). Used by the load nodes' folder dropdowns."""
    def fetch() -> list[str]:
        return [f["name"] for f in client.list_folders()]
    return _cached(client, "combo_folders", fetch)


def combo_target_models(client: MetascanClient) -> list[str]:
    """Canonical TargetModel literals from GET /api/prompt/target-models
    plus the virtual 'any' option appended client-side. The node maps
    'any' → None when issuing search_prompts()."""
    def fetch() -> list[str]:
        return [*client.target_models(), "any"]
    return _cached(client, "combo_target_models", fetch)
