"""Tests for MetascanSelectImage.

This node picks any image-typed item from the folder (not just images
with saved prompts) via the new ``/metscan/images`` proxy route. At
execute time it just streams the source bytes (cached) and computes
resolution by the same ``compute_resolution`` rules as the other
load nodes.

Outputs are intentionally narrower than Select Prompt — no
``positive`` / ``negative`` slots — so the result tuple has 5 fields,
not 7.
"""

from __future__ import annotations

from io import BytesIO

import httpx
import pytest
import respx
from PIL import Image

from mscan_client.cache import OFFLINE_SENTINEL, clear_cache
from mscan_nodes.select_image import MetascanSelectImage


def _png_bytes(size=(8, 8), color=(0, 255, 0)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def _set_env(monkeypatch, base_url):
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None


# ---------------------------------------------------------------------------
# INPUT_TYPES / class shape
# ---------------------------------------------------------------------------


def test_return_names_drop_positive_negative():
    """Outputs are (image, name, source_file_path, width, height) —
    no positive/negative slots since this node has no prompt state."""
    assert MetascanSelectImage.RETURN_NAMES == (
        "image", "name", "source_file_path", "width", "height",
    )


@respx.mock
def test_input_types_offline_returns_sentinel_for_folder(monkeypatch, base_url):
    """Same offline pattern as the other load nodes: folder falls back
    to the offline sentinel; target_model falls back to the hardcoded
    canonical list (combo_target_models substitutes a fallback, not
    the sentinel)."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(side_effect=httpx.ConnectError("down"))
    respx.get(f"{base_url}/api/prompt/target-models").mock(side_effect=httpx.ConnectError("down"))
    _set_env(monkeypatch, base_url)

    spec = MetascanSelectImage.INPUT_TYPES()
    assert spec["required"]["folder"][0] == [OFFLINE_SENTINEL]
    target_models = spec["required"]["target_model"][0]
    assert target_models[-1] == "any"
    # Select Image is the I2V entry point — wan/ltx2 must appear here,
    # inserted before the trailing "any" so the dropdown still ends in
    # the virtual fallback.
    assert "wan" in target_models
    assert "ltx2" in target_models
    assert target_models.index("wan") < target_models.index("any")
    assert target_models.index("ltx2") < target_models.index("any")
    for key in ("image_name", "source_file_path", "quality"):
        assert key in spec["required"]
    # No editable prompt widgets on this node.
    assert "positive_prompt" not in spec["required"]
    assert "negative_prompt" not in spec["required"]


# ---------------------------------------------------------------------------
# load() — error paths
# ---------------------------------------------------------------------------


def test_load_raises_when_no_image_picked(monkeypatch, base_url):
    """Empty source_file_path means the JS picker hasn't fired yet."""
    _set_env(monkeypatch, base_url)
    with pytest.raises(RuntimeError, match="No image selected"):
        MetascanSelectImage().load(
            folder="Portraits", target_model="qwen", quality="Balanced",
            image_name="", source_file_path="",
        )


def test_load_raises_on_offline_sentinel(monkeypatch, base_url):
    _set_env(monkeypatch, base_url)
    with pytest.raises(RuntimeError, match="offline"):
        MetascanSelectImage().load(
            folder=OFFLINE_SENTINEL, target_model="qwen", quality="Balanced",
            image_name="img.png", source_file_path="/a.png",
        )


# ---------------------------------------------------------------------------
# load() — happy path
# ---------------------------------------------------------------------------


@respx.mock
def test_load_streams_image_and_applies_resolution_rules(monkeypatch, base_url):
    """Width / height come from compute_resolution(src_w, src_h,
    target_model, quality) — the same path Load Prompt / Select Prompt
    use — so downstream samplers get the same generator-friendly
    buckets."""
    clear_cache()
    respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
        return_value=httpx.Response(200, content=_png_bytes(size=(1920, 1080)))
    )
    _set_env(monkeypatch, base_url)

    out = MetascanSelectImage().load(
        folder="Portraits", target_model="qwen", quality="Balanced",
        image_name="b.png", source_file_path="/b.png",
    )
    image, name, src, w, h = out["result"]
    assert image.shape == (1, 1080, 1920, 3)
    assert name == "b.png"
    assert src == "/b.png"
    # qwen + Balanced for a 1920x1080 source → official 1664x928 bucket.
    assert (w, h) == (1664, 928)


@respx.mock
def test_load_reuses_image_cache_on_same_source_file_path(monkeypatch, base_url):
    clear_cache()
    stream = respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    _set_env(monkeypatch, base_url)

    node = MetascanSelectImage()
    node.load(folder="Portraits", target_model="qwen", quality="Balanced",
              image_name="b.png", source_file_path="/b.png")
    assert stream.call_count == 1
    node.load(folder="Portraits", target_model="qwen", quality="Balanced",
              image_name="b.png", source_file_path="/b.png")
    assert stream.call_count == 1


@respx.mock
def test_load_recomputes_resolution_on_quality_change(monkeypatch, base_url):
    """Sweeping quality reuses the cached image but produces new
    width/height — same caching shape as Select Prompt."""
    clear_cache()
    stream = respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
        return_value=httpx.Response(200, content=_png_bytes(size=(1920, 1080)))
    )
    _set_env(monkeypatch, base_url)

    node = MetascanSelectImage()
    out_f = node.load(folder="Portraits", target_model="qwen", quality="Fast",
                      image_name="b.png", source_file_path="/b.png")
    out_u = node.load(folder="Portraits", target_model="qwen", quality="Ultra",
                      image_name="b.png", source_file_path="/b.png")
    assert stream.call_count == 1
    wf, hf = out_f["result"][3], out_f["result"][4]
    wu, hu = out_u["result"][3], out_u["result"][4]
    assert (wf, hf) != (wu, hu)
    assert wu * hu > wf * hf


def test_load_has_no_ui_dict(monkeypatch, base_url):
    """No widgets are pushed back from the Python side — the picker on
    the JS side is the only writer."""
    clear_cache()
    with respx.mock:
        respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
            return_value=httpx.Response(200, content=_png_bytes())
        )
        _set_env(monkeypatch, base_url)
        out = MetascanSelectImage().load(
            folder="Portraits", target_model="qwen", quality="Balanced",
            image_name="b.png", source_file_path="/b.png",
        )
    assert "ui" not in out
