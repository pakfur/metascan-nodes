"""ComfyUI server-route proxy for the Load Prompt thumbnail picker.

Two routes registered on ``PromptServer.instance.routes``:

- ``GET /metscan/prompts?folder=<name>&target_model=<name>`` →
  ``[{"name": ..., "file_path": ...}, ...]`` for the picker overlay.
- ``GET /metscan/thumbnail/<file_path>`` → JPEG bytes proxied from
  metascan's ``/api/media/thumbnails/{file_path}``.

The browser never talks to metascan directly — auth + reachability
both stay server-side. The handlers are async aiohttp shims, but the
actual work runs in a thread because ``MetascanClient`` is sync (the
same client every other ComfyUI execute path uses).

Import this module from the root ``__init__.py`` so the route
decorators run at custom-node load time.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from mscan_client.cache import OFFLINE_SENTINEL
from mscan_client.errors import ApiError, OfflineError
from mscan_nodes.load_prompt import _build_client, _folder_id_for_name


# ---------------------------------------------------------------------------
# Pure sync helpers — these contain the actual logic and are unit-tested
# directly. The aiohttp handlers below are thin shims that call them via
# asyncio.to_thread.
# ---------------------------------------------------------------------------


def list_prompts_for_picker(folder: str, target_model: str) -> list[dict]:
    """Return ``[{"name", "file_path", "prompt", "negative"}, ...]`` for
    the chosen folder + target_model filter, ordered as metascan returns
    them.

    The Select Prompt node populates its positive/negative widgets at
    pick-time from this payload — no extra run-time fetch — so include
    the prompt text here rather than only ``{name, file_path}``.

    Mirrors the filter logic in ``MetascanLoadPrompt._fetch_live``:
    - ``target_model == "any"`` → no model filter (None).
    - Offline sentinel for either field → empty list (the UI can render
      an offline / empty state instead of a stack trace).
    """
    if not folder or folder == OFFLINE_SENTINEL or target_model == OFFLINE_SENTINEL:
        return []

    client = _build_client()
    folder_id = _folder_id_for_name(client, folder)
    wire_target: Optional[str] = None if target_model == "any" else target_model
    rows = client.search_prompts(
        folder_id=folder_id,
        target_model=wire_target,
        name=None,
        limit=500,
    )
    return [
        {
            "name": r.get("name", "") or "",
            "file_path": r.get("file_path", "") or "",
            "prompt": r.get("prompt", "") or "",
            "negative": r.get("negative") or "",  # SQL NULL → ""
        }
        for r in rows
        if r.get("file_path")
    ]


def fetch_thumbnail_bytes(file_path: str) -> bytes:
    """Return JPEG bytes for a metascan-cached thumbnail. Raises
    ``OfflineError`` / ``ApiError`` on transport / HTTP failure — the
    aiohttp shim maps those to 503 / passthrough status."""
    client = _build_client()
    return client.stream_thumbnail_bytes(file_path)


# Common still-image extensions metascan can serve. Videos are excluded
# even when the folder contains them — Select Image is for image input,
# and bytes_to_tensor only handles still images.
_IMAGE_EXTS = (
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".bmp", ".tif", ".tiff", ".heic", ".heif",
)


def _basename(path: str) -> str:
    """Last path segment, tolerant of both ``/`` and ``\\`` separators
    (metascan paths may be Windows-style even when ComfyUI runs under
    WSL or Linux)."""
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _is_image(path: str) -> bool:
    return path.lower().endswith(_IMAGE_EXTS)


def list_images_for_picker(folder: str) -> list[dict]:
    """Return ``[{"name", "file_path"}, ...]`` for every image-type
    item in the chosen folder, ordered as metascan returns them.

    Used by MetascanSelectImage's picker. Unlike
    :func:`list_prompts_for_picker`, this hits ``GET /api/folders/{id}``
    and walks ``folder.items`` directly — so images that don't have a
    saved prompt still show up.

    Videos and other non-image items are filtered out by extension
    (Select Image's downstream is always a still-image decode).
    """
    if not folder or folder == OFFLINE_SENTINEL:
        return []

    client = _build_client()
    folder_id = _folder_id_for_name(client, folder)
    folder_detail = client.get_folder(folder_id)
    items = folder_detail.get("items", []) or []
    return [
        {"name": _basename(p), "file_path": p}
        for p in items
        if isinstance(p, str) and p and _is_image(p)
    ]


# ---------------------------------------------------------------------------
# aiohttp shims — registered at import time when PromptServer is available
# (i.e. running inside ComfyUI, not under pytest).
# ---------------------------------------------------------------------------


async def _handle_list_prompts(request):
    from aiohttp import web

    folder = request.rel_url.query.get("folder", "")
    target_model = request.rel_url.query.get("target_model", "any")
    try:
        rows = await asyncio.to_thread(
            list_prompts_for_picker, folder, target_model,
        )
    except OfflineError as e:
        return web.json_response({"prompts": [], "error": str(e)}, status=503)
    except ApiError as e:
        return web.json_response(
            {"prompts": [], "error": e.body_excerpt}, status=502,
        )
    except RuntimeError as e:
        # e.g. folder name not found in metascan — return empty rather
        # than 500; the picker just shows an empty state.
        return web.json_response({"prompts": [], "error": str(e)}, status=200)
    return web.json_response({"prompts": rows})


async def _handle_list_images(request):
    from aiohttp import web

    folder = request.rel_url.query.get("folder", "")
    try:
        rows = await asyncio.to_thread(list_images_for_picker, folder)
    except OfflineError as e:
        return web.json_response({"images": [], "error": str(e)}, status=503)
    except ApiError as e:
        return web.json_response({"images": [], "error": e.body_excerpt}, status=502)
    except RuntimeError as e:
        # Folder name not found in metascan — return empty rather than
        # 500, same as the prompts handler.
        return web.json_response({"images": [], "error": str(e)}, status=200)
    return web.json_response({"images": rows})


async def _handle_thumbnail(request):
    from aiohttp import web

    # Query-string param rather than a path variable: file paths
    # contain forward slashes, and aiohttp's path-variable regex
    # handling of percent-encoded slashes ("%2F") is inconsistent
    # across versions — routing silently 404s before the handler
    # even fires. A query string sidesteps all that.
    file_path = request.rel_url.query.get("file_path", "")
    if not file_path:
        return web.Response(status=400, text="file_path query param required")
    try:
        data = await asyncio.to_thread(fetch_thumbnail_bytes, file_path)
    except OfflineError as e:
        return web.Response(status=503, text=str(e))
    except ApiError as e:
        return web.Response(status=e.status_code, text=e.body_excerpt[:500])
    return web.Response(
        body=data,
        content_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _register() -> bool:
    """Register routes if PromptServer is importable (i.e., we're
    running inside ComfyUI). Returns True if registration happened."""
    try:
        from server import PromptServer  # type: ignore[import-not-found]
    except ImportError:
        return False
    instance = getattr(PromptServer, "instance", None)
    if instance is None:
        return False
    routes = instance.routes
    routes.get("/metscan/prompts")(_handle_list_prompts)
    routes.get("/metscan/images")(_handle_list_images)
    routes.get("/metscan/thumbnail")(_handle_thumbnail)
    return True


_REGISTERED = _register()
