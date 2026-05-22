"""Tests for MetascanSelectPrompt.

This node intentionally has no live-prompt-fetch path at execute
time: the JS picker writes prompt_name / source_file_path /
positive_prompt / negative_prompt at pick-time, and `load()` just
streams the source image bytes (or reuses the cache) and recomputes
resolution. So the tests exercise:

- Error paths (no pick yet, offline sentinel).
- Image cache reuse when source_file_path is unchanged.
- Widget values flow through to the result tuple verbatim.
- Width/height recompute on quality changes without an extra fetch.
"""

from __future__ import annotations

from io import BytesIO

import httpx
import pytest
import respx
from PIL import Image

from mscan_client.cache import OFFLINE_SENTINEL, clear_cache
from mscan_nodes.select_prompt import MetascanSelectPrompt


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
# INPUT_TYPES
# ---------------------------------------------------------------------------


@respx.mock
def test_input_types_offline_returns_sentinel_for_folder(monkeypatch, base_url):
    """When metascan is unreachable, ``folder`` falls back to the
    offline sentinel. ``target_model`` falls back to the hardcoded
    canonical list (combo_target_models substitutes the fallback list
    rather than the sentinel — see mscan_client/cache.py) so editor
    rendering doesn't crater before the metascan-side endpoint exists."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(side_effect=httpx.ConnectError("down"))
    respx.get(f"{base_url}/api/prompt/target-models").mock(side_effect=httpx.ConnectError("down"))
    _set_env(monkeypatch, base_url)

    spec = MetascanSelectPrompt.INPUT_TYPES()
    assert spec["required"]["folder"][0] == [OFFLINE_SENTINEL]
    target_models = spec["required"]["target_model"][0]
    assert target_models[-1] == "any"
    assert "qwen" in target_models
    # State + editable fields are always present.
    for key in ("prompt_name", "source_file_path", "positive_prompt", "negative_prompt", "quality"):
        assert key in spec["required"]


# ---------------------------------------------------------------------------
# load() — error paths
# ---------------------------------------------------------------------------


def test_load_raises_when_no_prompt_picked(monkeypatch, base_url):
    """Empty source_file_path / prompt_name means the JS picker hasn't
    fired yet — surface a clear message rather than silently failing."""
    _set_env(monkeypatch, base_url)
    with pytest.raises(RuntimeError, match="No prompt selected"):
        MetascanSelectPrompt().load(
            folder="Portraits", target_model="qwen",
            prompt_name="", source_file_path="",
            quality="Balanced",
            positive_prompt="", negative_prompt="",
        )


def test_load_raises_on_offline_sentinel(monkeypatch, base_url):
    """Offline sentinel in either dropdown short-circuits before any
    HTTP attempt — same as Load Prompt."""
    _set_env(monkeypatch, base_url)
    with pytest.raises(RuntimeError, match="offline"):
        MetascanSelectPrompt().load(
            folder=OFFLINE_SENTINEL, target_model="qwen",
            prompt_name="hero", source_file_path="/a.png",
            quality="Balanced",
            positive_prompt="p", negative_prompt="n",
        )


# ---------------------------------------------------------------------------
# load() — happy path
# ---------------------------------------------------------------------------


@respx.mock
def test_load_streams_image_and_returns_widget_values(monkeypatch, base_url):
    """Widgets are the truth: positive/negative/name/source_file_path
    pass through unchanged; image comes from the stream endpoint;
    width/height are computed from image dims + quality + target_model."""
    clear_cache()
    respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
        return_value=httpx.Response(200, content=_png_bytes(size=(1920, 1080)))
    )
    _set_env(monkeypatch, base_url)

    out = MetascanSelectPrompt().load(
        folder="Portraits", target_model="qwen",
        prompt_name="cinematic", source_file_path="/b.png",
        quality="Balanced",
        positive_prompt="user-edited positive",
        negative_prompt="user-edited negative",
    )
    image, pos, neg, name, src, w, h = out["result"]
    assert image.shape == (1, 1080, 1920, 3)
    assert pos == "user-edited positive"
    assert neg == "user-edited negative"
    assert name == "cinematic"
    assert src == "/b.png"
    # qwen + Balanced for a 1920x1080 source → official 1664x928 bucket.
    assert (w, h) == (1664, 928)


@respx.mock
def test_load_reuses_image_cache_on_same_source_file_path(monkeypatch, base_url):
    """Two back-to-back loads with the same source_file_path stream the
    image bytes exactly once. Cache is keyed on source_file_path, not
    prompt_name (since two prompts can share an image)."""
    clear_cache()
    stream = respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    _set_env(monkeypatch, base_url)

    node = MetascanSelectPrompt()
    node.load(
        folder="Portraits", target_model="qwen",
        prompt_name="cinematic", source_file_path="/b.png",
        quality="Balanced",
        positive_prompt="", negative_prompt="",
    )
    assert stream.call_count == 1

    node.load(
        folder="Portraits", target_model="qwen",
        prompt_name="cinematic", source_file_path="/b.png",
        quality="Balanced",
        positive_prompt="", negative_prompt="",
    )
    assert stream.call_count == 1  # cache reused


@respx.mock
def test_load_refetches_when_source_file_path_changes(monkeypatch, base_url):
    """Picking a different prompt → different source_file_path →
    cache miss → one new stream call."""
    clear_cache()
    respx.get(f"{base_url}/api/stream/%2Fa.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    _set_env(monkeypatch, base_url)

    node = MetascanSelectPrompt()
    node.load(
        folder="Portraits", target_model="qwen",
        prompt_name="hero", source_file_path="/a.png",
        quality="Balanced",
        positive_prompt="", negative_prompt="",
    )
    out2 = node.load(
        folder="Portraits", target_model="qwen",
        prompt_name="cinematic", source_file_path="/b.png",
        quality="Balanced",
        positive_prompt="", negative_prompt="",
    )
    _, _, _, name2, src2, _, _ = out2["result"]
    assert (name2, src2) == ("cinematic", "/b.png")


@respx.mock
def test_load_recomputes_resolution_on_quality_change(monkeypatch, base_url):
    """Sweeping `quality` Fast → Balanced → Ultra reuses the cached
    image but produces different width/height each time."""
    clear_cache()
    stream = respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
        return_value=httpx.Response(200, content=_png_bytes(size=(1920, 1080)))
    )
    _set_env(monkeypatch, base_url)

    node = MetascanSelectPrompt()
    out_f = node.load(
        folder="Portraits", target_model="qwen",
        prompt_name="x", source_file_path="/b.png",
        quality="Fast", positive_prompt="", negative_prompt="",
    )
    out_b = node.load(
        folder="Portraits", target_model="qwen",
        prompt_name="x", source_file_path="/b.png",
        quality="Balanced", positive_prompt="", negative_prompt="",
    )
    out_u = node.load(
        folder="Portraits", target_model="qwen",
        prompt_name="x", source_file_path="/b.png",
        quality="Ultra", positive_prompt="", negative_prompt="",
    )
    assert stream.call_count == 1
    wf, hf = out_f["result"][5], out_f["result"][6]
    wb, hb = out_b["result"][5], out_b["result"][6]
    wu, hu = out_u["result"][5], out_u["result"][6]
    assert (wf, hf) != (wb, hb)
    assert wu * hu > wf * hf


def test_load_has_no_ui_dict_so_widgets_arent_overwritten(monkeypatch, base_url):
    """Unlike MetascanLoadPrompt.live_load=True, this node never pushes
    text back into the editable widgets — the user's edits to
    positive_prompt / negative_prompt are the canonical truth from
    pick-time onward."""
    clear_cache()
    with respx.mock:
        respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
            return_value=httpx.Response(200, content=_png_bytes())
        )
        _set_env(monkeypatch, base_url)
        out = MetascanSelectPrompt().load(
            folder="Portraits", target_model="qwen",
            prompt_name="x", source_file_path="/b.png",
            quality="Balanced",
            positive_prompt="my edit", negative_prompt="my neg edit",
        )
    assert "ui" not in out
