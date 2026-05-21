from __future__ import annotations

import pytest
import torch

from mscan_nodes.load_from_folder import (
    filter_paths,
    select_path,
    bytes_to_tensor,
)


SAMPLE_PATHS = [
    "/data/a/img1.png",
    "/data/a/img2.jpg",
    "/data/a/clip.mp4",
    "/data/a/note.txt",
    "/data/a/img3.webp",
]


# ----- filter_paths -----

def test_filter_paths_image_only_drops_video_and_other(tmp_path):
    out = filter_paths(SAMPLE_PATHS, image_only=True, filename_filter="")
    assert out == ["/data/a/img1.png", "/data/a/img2.jpg", "/data/a/img3.webp"]


def test_filter_paths_image_only_false_includes_video(tmp_path):
    out = filter_paths(SAMPLE_PATHS, image_only=False, filename_filter="")
    # .txt still excluded — it's never a supported media type.
    assert "/data/a/clip.mp4" in out
    assert "/data/a/note.txt" not in out


def test_filter_paths_applies_substring_filter():
    out = filter_paths(SAMPLE_PATHS, image_only=True, filename_filter="img2")
    assert out == ["/data/a/img2.jpg"]


def test_filter_paths_sorts_deterministically():
    out = filter_paths(["/z.png", "/a.png", "/m.png"], image_only=True, filename_filter="")
    assert out == ["/a.png", "/m.png", "/z.png"]


# ----- select_path -----

def test_select_path_random_reproducible_with_seed():
    paths = ["/a.png", "/b.png", "/c.png"]
    p1, next_seed1 = select_path(paths, mode="random", seed=42, index=0)
    p2, next_seed2 = select_path(paths, mode="random", seed=42, index=0)
    assert p1 == p2
    assert next_seed1 == next_seed2


def test_select_path_sequential_advances_seed():
    paths = ["/a.png", "/b.png", "/c.png"]
    chosen, next_seed = select_path(paths, mode="sequential", seed=1, index=0)
    assert chosen == "/b.png"
    assert next_seed == 2


def test_select_path_sequential_wraps_around():
    paths = ["/a.png", "/b.png"]
    chosen, next_seed = select_path(paths, mode="sequential", seed=5, index=0)
    assert chosen == "/b.png"  # 5 % 2 == 1
    assert next_seed == 0      # (5 + 1) % 2 == 0


def test_select_path_specific_uses_index_not_seed():
    paths = ["/a.png", "/b.png", "/c.png"]
    chosen, next_seed = select_path(paths, mode="specific", seed=999, index=1)
    assert chosen == "/b.png"
    assert next_seed == 1


def test_select_path_specific_clamps_oversize_index():
    paths = ["/a.png", "/b.png", "/c.png"]
    chosen, _ = select_path(paths, mode="specific", seed=0, index=10)
    assert chosen == "/b.png"  # 10 % 3 == 1


def test_select_path_empty_list_raises():
    with pytest.raises(RuntimeError, match="no matching"):
        select_path([], mode="random", seed=0, index=0)


# ----- bytes_to_tensor -----

def test_bytes_to_tensor_returns_nhwc_float():
    # Build an actual 4×4 PNG and feed its bytes in.
    from io import BytesIO
    from PIL import Image
    pil = Image.new("RGB", (8, 4), color=(128, 64, 192))
    buf = BytesIO()
    pil.save(buf, format="PNG")
    t = bytes_to_tensor(buf.getvalue())
    assert isinstance(t, torch.Tensor)
    assert t.dtype == torch.float32
    assert t.shape == (1, 4, 8, 3)
    assert 0.0 <= t.min() <= t.max() <= 1.0
    # Red channel 128/255 ≈ 0.502.
    assert abs(float(t[0, 0, 0, 0]) - 128/255) < 0.01


# =============================================================================
# MetascanLoadFromFolder — class-level tests (Task 16)
# =============================================================================

import respx
import httpx
from io import BytesIO
from unittest.mock import patch
from PIL import Image

from mscan_nodes.load_from_folder import MetascanLoadFromFolder
from mscan_client.cache import clear_cache, OFFLINE_SENTINEL


def _png_bytes(color=(0, 255, 0), size=(8, 8)):
    buf = BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


# ----- INPUT_TYPES -----

@respx.mock
def test_input_types_lists_manual_folder_names(monkeypatch, base_url, folders_payload):
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    spec = MetascanLoadFromFolder.INPUT_TYPES()
    folder_list = spec["required"]["folder"][0]
    assert folder_list == ["Portraits", "Landscapes"]


# ----- execute -----

@respx.mock
def test_execute_loads_image_and_metadata(monkeypatch, base_url, folders_payload):
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    # Portraits folder detail (id fld_a, items list).
    respx.get(f"{base_url}/api/folders/fld_a").mock(
        return_value=httpx.Response(200, json=folders_payload[0])
    )
    # Pick will land on img1.png (sorted-first). Stub its media + bytes.
    respx.get(f"{base_url}/api/media/%2Fdata%2Fa%2Fimg1.png").mock(
        return_value=httpx.Response(200, json={
            "file_path": "/data/a/img1.png",
            "data": {"prompt": "POSITIVE", "negative_prompt": "NEGATIVE"},
        })
    )
    respx.get(f"{base_url}/api/stream/%2Fdata%2Fa%2Fimg1.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    node = MetascanLoadFromFolder()
    image, path, positive, negative, next_seed, width, height = node.load(
        folder="Portraits",
        selection_mode="sequential",
        seed=0,
        index=0,
        filename_filter="",
        image_only=True,
        target_model="sd",
        quality="Balanced",
    )
    assert image.shape == (1, 8, 8, 3)
    assert path == "/data/a/img1.png"
    assert positive == "POSITIVE"
    assert negative == "NEGATIVE"
    assert next_seed == 1
    # 8x8 square source → SDXL Balanced 1024² at aspect 1:1, snap to 64.
    assert (width, height) == (1024, 1024)


@respx.mock
def test_execute_raises_on_empty_folder(monkeypatch, base_url, folders_payload):
    clear_cache()
    empty_folder = {**folders_payload[1]}   # Landscapes — already empty
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.get(f"{base_url}/api/folders/fld_b").mock(return_value=httpx.Response(200, json=empty_folder))
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    with pytest.raises(RuntimeError, match="no matching"):
        MetascanLoadFromFolder().load(
            folder="Landscapes", selection_mode="random", seed=0, index=0,
            filename_filter="", image_only=True,
            target_model="sd", quality="Balanced",
        )


def test_execute_raises_on_offline_sentinel():
    with pytest.raises(RuntimeError, match="offline"):
        MetascanLoadFromFolder().load(
            folder=OFFLINE_SENTINEL, selection_mode="random", seed=0, index=0,
            filename_filter="", image_only=True,
            target_model="sd", quality="Balanced",
        )


@respx.mock
def test_execute_emits_resolution_for_landscape_qwen(monkeypatch, base_url, folders_payload):
    """End-to-end check that target_model + quality drive the width/height
    output. A 1920x1080 source under qwen+Balanced must pick the official
    1664x928 bucket."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.get(f"{base_url}/api/folders/fld_a").mock(
        return_value=httpx.Response(200, json=folders_payload[0])
    )
    respx.get(f"{base_url}/api/media/%2Fdata%2Fa%2Fimg1.png").mock(
        return_value=httpx.Response(200, json={
            "file_path": "/data/a/img1.png",
            "data": {"prompt": "", "negative_prompt": ""},
        })
    )
    respx.get(f"{base_url}/api/stream/%2Fdata%2Fa%2Fimg1.png").mock(
        return_value=httpx.Response(200, content=_png_bytes(size=(1920, 1080)))
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    _, _, _, _, _, w, h = MetascanLoadFromFolder().load(
        folder="Portraits", selection_mode="sequential", seed=0, index=0,
        filename_filter="", image_only=True,
        target_model="qwen", quality="Balanced",
    )
    assert (w, h) == (1664, 928)
