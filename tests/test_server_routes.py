"""Tests for the ComfyUI server-route proxy that powers the Load Prompt
thumbnail picker.

The aiohttp shim handlers are exercised via their pure sync helpers
(``list_prompts_for_picker`` / ``fetch_thumbnail_bytes``) — those carry
all the metascan-client logic. The shims themselves just unwrap query
strings and map exceptions to HTTP status codes.
"""

from __future__ import annotations

from io import BytesIO

import httpx
import pytest
import respx
from PIL import Image

from mscan_client.cache import OFFLINE_SENTINEL, clear_cache
from mscan_client.errors import ApiError, OfflineError
from mscan_nodes import server_routes


def _jpeg_bytes(size=(8, 8), color=(0, 255, 0)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


SAMPLE_ROWS = [
    {"id": 1, "file_path": "/a.png", "name": "hero",
     "prompt": "p1", "negative": None, "target_model": "qwen"},
    {"id": 2, "file_path": "/b.png", "name": "cinematic",
     "prompt": "p2", "negative": "n2", "target_model": "qwen"},
    {"id": 3, "file_path": "", "name": "no-file-path",  # filtered out below
     "prompt": "p3", "negative": "", "target_model": "qwen"},
]


# ---------------------------------------------------------------------------
# list_prompts_for_picker
# ---------------------------------------------------------------------------


@respx.mock
def test_list_prompts_returns_picker_shape(monkeypatch, base_url, folders_payload):
    """Picker payload carries name + file_path + prompt + negative so
    the Select Prompt node can populate all four widgets at pick-time
    without a follow-up fetch. Rows with empty file_path are dropped
    (the picker can't render or stream them)."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": SAMPLE_ROWS})
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    rows = server_routes.list_prompts_for_picker(folder="Portraits", target_model="qwen")
    assert rows == [
        {"name": "hero", "file_path": "/a.png", "prompt": "p1", "negative": ""},
        {"name": "cinematic", "file_path": "/b.png", "prompt": "p2", "negative": "n2"},
    ]


@respx.mock
def test_list_prompts_maps_any_to_null_target_model(monkeypatch, base_url, folders_payload):
    """``any`` is a virtual UI option — the wire filter must be null
    so metascan returns prompts from all target_models."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    route = respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": []})
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    server_routes.list_prompts_for_picker(folder="Portraits", target_model="any")
    import json as _j
    body = _j.loads(route.calls.last.request.read().decode())
    assert body["target_model"] is None
    assert body["folder_id"] == "fld_a"
    assert body["name"] is None


def test_list_prompts_offline_sentinel_returns_empty():
    """If either field is the offline sentinel, return [] silently so
    the picker can render an empty state rather than 500."""
    rows = server_routes.list_prompts_for_picker(
        folder=OFFLINE_SENTINEL, target_model="qwen",
    )
    assert rows == []
    rows = server_routes.list_prompts_for_picker(
        folder="Portraits", target_model=OFFLINE_SENTINEL,
    )
    assert rows == []


def test_list_prompts_empty_folder_returns_empty():
    """Empty folder string (e.g., from a newly created node) → [] —
    don't even attempt a search."""
    assert server_routes.list_prompts_for_picker(folder="", target_model="qwen") == []


# ---------------------------------------------------------------------------
# list_images_for_picker
# ---------------------------------------------------------------------------


@respx.mock
def test_list_images_returns_folder_items_filtered_to_images(monkeypatch, base_url):
    """Walks the folder's ``items`` list straight off the list-folders
    payload (metascan doesn't expose GET /api/folders/{id}) and returns
    {name, file_path} for image-typed paths only. Videos / non-image
    extensions are dropped."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(
        return_value=httpx.Response(200, json=[
            {
                "id": "fld_a", "name": "Portraits", "kind": "manual",
                "icon": "pi-folder", "sort_order": 0, "count": 6,
                "items": [
                    "/data/a/img1.png",
                    "/data/a/img2.JPG",  # case-insensitive
                    "/data/a/clip.mp4",  # video — dropped
                    "/data/a/snap.webp",
                    "/data/a/readme.txt",  # non-media — dropped
                    "/data/a/c:\\windows\\path.jpeg",  # Windows path — basename works
                ],
                "created_at": "2026-05-01T00:00:00",
                "updated_at": "2026-05-01T00:00:00",
            },
        ])
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    rows = server_routes.list_images_for_picker(folder="Portraits")
    assert rows == [
        {"name": "img1.png", "file_path": "/data/a/img1.png"},
        {"name": "img2.JPG", "file_path": "/data/a/img2.JPG"},
        {"name": "snap.webp", "file_path": "/data/a/snap.webp"},
        {"name": "path.jpeg", "file_path": "/data/a/c:\\windows\\path.jpeg"},
    ]


def test_list_images_offline_sentinel_returns_empty():
    """Same short-circuit as list_prompts_for_picker."""
    assert server_routes.list_images_for_picker(folder=OFFLINE_SENTINEL) == []
    assert server_routes.list_images_for_picker(folder="") == []


# ---------------------------------------------------------------------------
# fetch_thumbnail_bytes
# ---------------------------------------------------------------------------


@respx.mock
def test_fetch_thumbnail_proxies_metascan_endpoint(monkeypatch, base_url):
    """Calls metascan's ``/api/media/thumbnails/{file_path}`` and
    returns the JPEG bytes unchanged."""
    clear_cache()
    payload = _jpeg_bytes()
    respx.get(f"{base_url}/api/thumbnails/%2Fb.png").mock(
        return_value=httpx.Response(200, content=payload, headers={"Content-Type": "image/jpeg"})
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    assert server_routes.fetch_thumbnail_bytes("/b.png") == payload


@respx.mock
def test_fetch_thumbnail_404_raises_api_error(monkeypatch, base_url):
    """Metascan returns 404 → ApiError with the status preserved so the
    aiohttp shim can re-emit it to the browser."""
    clear_cache()
    respx.get(f"{base_url}/api/thumbnails/%2Fmissing.png").mock(
        return_value=httpx.Response(404, text="not found")
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    with pytest.raises(ApiError) as ctx:
        server_routes.fetch_thumbnail_bytes("/missing.png")
    assert ctx.value.status_code == 404


# ---------------------------------------------------------------------------
# Registration guard
# ---------------------------------------------------------------------------


def test_register_returns_false_outside_comfyui():
    """Under pytest there's no ``server`` module — _register() must
    no-op cleanly so the package imports without ComfyUI installed."""
    assert server_routes._REGISTERED is False
