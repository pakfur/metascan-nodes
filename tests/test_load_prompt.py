from __future__ import annotations

from io import BytesIO

import pytest
import respx
import httpx
from PIL import Image

from mscan_client.cache import clear_cache, OFFLINE_SENTINEL
from mscan_nodes.load_prompt import MetascanLoadPrompt, select_prompt


def _png_bytes(size=(8, 8), color=(0, 255, 0)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


SAMPLE_ROWS = [
    {"id": 1, "file_path": "/a.png", "name": "hero",      "prompt": "p1",
     "negative": None, "target_model": "qwen", "architecture": "qwen", "styles": []},
    {"id": 2, "file_path": "/b.png", "name": "cinematic", "prompt": "p2",
     "negative": "n2", "target_model": "qwen", "architecture": "qwen", "styles": []},
    {"id": 3, "file_path": "/c.png", "name": "landscape", "prompt": "p3",
     "negative": "",   "target_model": "qwen", "architecture": "qwen", "styles": []},
]


# ----- select_prompt -----

def test_select_prompt_by_name_returns_matching_row():
    row = select_prompt(SAMPLE_ROWS, mode="by_name", name="cinematic", seed=0)
    assert row["id"] == 2


def test_select_prompt_by_name_missing_raises():
    with pytest.raises(RuntimeError, match="no saved prompt"):
        select_prompt(SAMPLE_ROWS, mode="by_name", name="missing", seed=0)


def test_select_prompt_random_reproducible_with_seed():
    r1 = select_prompt(SAMPLE_ROWS, mode="random", name="", seed=99)
    r2 = select_prompt(SAMPLE_ROWS, mode="random", name="", seed=99)
    assert r1["id"] == r2["id"]


def test_select_prompt_empty_rows_raises():
    with pytest.raises(RuntimeError, match="no saved prompts"):
        select_prompt([], mode="random", name="", seed=0)


# ----- ComfyUI class -----

@respx.mock
def test_input_types_lists_folders_and_target_models(monkeypatch, base_url, folders_payload):
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.get(f"{base_url}/api/prompt/target-models").mock(return_value=httpx.Response(200, json={
        "target_models": ["sd", "pony", "flux1", "flux2", "zimage", "chroma", "qwen"]
    }))
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    spec = MetascanLoadPrompt.INPUT_TYPES()
    folders = spec["required"]["folder"][0]
    target_models = spec["required"]["target_model"][0]
    assert folders == ["Portraits", "Landscapes"]
    assert target_models[-1] == "any"
    assert "qwen" in target_models


@respx.mock
def test_execute_maps_any_to_null_target_model(monkeypatch, base_url, folders_payload):
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    route = respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": SAMPLE_ROWS})
    )
    respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    out = MetascanLoadPrompt().load(
        folder="Portraits",
        target_model="any",
        selection_mode="by_name",
        prompt_name="cinematic",
        seed=0,
        quality="Balanced",
        live_load=True,
        positive_prompt="",
        negative_prompt="",
    )
    image, pos, neg, name, src, w, h = out["result"]
    assert pos == "p2"
    assert neg == "n2"
    assert name == "cinematic"
    assert src == "/b.png"
    assert image.shape == (1, 8, 8, 3)
    # 8x8 source, any+Balanced (flux-style) → 1024² @ multiples of 16.
    assert (w, h) == (1024, 1024)
    import json as _j
    body = _j.loads(route.calls.last.request.read().decode())
    assert body["target_model"] is None      # "any" → null
    assert body["folder_id"] == "fld_a"      # Portraits → fld_a
    assert body["name"] == "cinematic"


@respx.mock
def test_execute_null_negative_becomes_empty_string(monkeypatch, base_url, folders_payload):
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": [SAMPLE_ROWS[0]]})  # negative=None
    )
    respx.get(f"{base_url}/api/stream/%2Fa.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    out = MetascanLoadPrompt().load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="hero", seed=0, quality="Balanced", live_load=True,
        positive_prompt="", negative_prompt="",
    )
    _, _, neg, _, _, _, _ = out["result"]
    assert neg == ""


def test_execute_raises_on_offline_sentinel():
    with pytest.raises(RuntimeError, match="offline"):
        MetascanLoadPrompt().load(
            folder=OFFLINE_SENTINEL, target_model="qwen", selection_mode="random",
            prompt_name="", seed=0, quality="Balanced", live_load=True,
            positive_prompt="", negative_prompt="",
        )


@respx.mock
def test_execute_loads_source_image_and_emits_resolution(monkeypatch, base_url, folders_payload):
    """End-to-end: a 1920x1080 source under qwen+Balanced should produce
    the IMAGE tensor matching the streamed bytes and the official Qwen
    1664x928 bucket for width/height."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": [SAMPLE_ROWS[1]]})  # /b.png
    )
    respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
        return_value=httpx.Response(200, content=_png_bytes(size=(1920, 1080)))
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    out = MetascanLoadPrompt().load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=True,
        positive_prompt="", negative_prompt="",
    )
    image, _, _, _, src, w, h = out["result"]
    assert image.shape == (1, 1080, 1920, 3)
    assert src == "/b.png"
    assert (w, h) == (1664, 928)


@respx.mock
def test_execute_raises_when_saved_prompt_has_no_file_path(monkeypatch, base_url, folders_payload):
    """A saved prompt with file_path="" can't yield source dimensions —
    surface a clear error instead of fetching '' or returning garbage."""
    clear_cache()
    bad_row = {**SAMPLE_ROWS[1], "file_path": ""}
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": [bad_row]})
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    with pytest.raises(RuntimeError, match="no source file path"):
        MetascanLoadPrompt().load(
            folder="Portraits", target_model="qwen", selection_mode="by_name",
            prompt_name="cinematic", seed=0, quality="Balanced", live_load=True,
            positive_prompt="", negative_prompt="",
        )


# ----- live_load toggle / per-instance cache -----

@respx.mock
def test_live_load_off_with_empty_cache_raises(monkeypatch, base_url):
    """If the user disables live_load before ever doing a live fetch,
    surface a clear error — don't silently succeed with garbage."""
    clear_cache()
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    with pytest.raises(RuntimeError, match="no cached prompt"):
        MetascanLoadPrompt().load(
            folder="Portraits", target_model="qwen", selection_mode="random",
            prompt_name="", seed=0, quality="Balanced", live_load=False,
            positive_prompt="", negative_prompt="",
        )


@respx.mock
def test_cached_load_reuses_image_and_skips_http(monkeypatch, base_url, folders_payload):
    """After one live_load=True populating the cache, live_load=False
    must reuse the cached image — no HTTP calls allowed."""
    clear_cache()
    folders_route = respx.get(f"{base_url}/api/folders").mock(
        return_value=httpx.Response(200, json=folders_payload)
    )
    search_route = respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": [SAMPLE_ROWS[1]]})
    )
    stream_route = respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
        return_value=httpx.Response(200, content=_png_bytes(size=(16, 16), color=(7, 7, 7)))
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    node = MetascanLoadPrompt()

    # Warm cache with one live fetch.
    out1 = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=True,
        positive_prompt="", negative_prompt="",
    )
    img1, _, _, name1, src1, _, _ = out1["result"]
    calls_after_live = (folders_route.call_count, search_route.call_count, stream_route.call_count)
    assert all(c >= 1 for c in calls_after_live)

    # Cached reuse — call counts must NOT advance.
    out2 = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=False,
        positive_prompt="", negative_prompt="",
    )
    img2, _, _, name2, src2, _, _ = out2["result"]
    assert (folders_route.call_count, search_route.call_count, stream_route.call_count) == calls_after_live
    assert img2 is img1   # identity reuse, not just equality
    assert name2 == name1
    assert src2 == src1


@respx.mock
def test_cached_load_recomputes_resolution_on_quality_change(monkeypatch, base_url, folders_payload):
    """The whole point of caching at this layer: user can sweep through
    Fast / Balanced / High / Ultra without re-fetching anything, and
    width/height should change each time."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": [SAMPLE_ROWS[1]]})
    )
    stream_route = respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
        return_value=httpx.Response(200, content=_png_bytes(size=(1920, 1080)))
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    node = MetascanLoadPrompt()
    # Warm cache at Balanced.
    out_b = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=True,
        positive_prompt="", negative_prompt="",
    )
    _, _, _, _, _, w_balanced, h_balanced = out_b["result"]
    stream_calls_after_warm = stream_route.call_count

    # Now sweep tiers with cache.
    out_f = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Fast", live_load=False,
        positive_prompt="", negative_prompt="",
    )
    _, _, _, _, _, w_fast, h_fast = out_f["result"]
    out_u = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Ultra", live_load=False,
        positive_prompt="", negative_prompt="",
    )
    _, _, _, _, _, w_ultra, h_ultra = out_u["result"]

    # No further stream fetches happened.
    assert stream_route.call_count == stream_calls_after_warm
    # Resolutions for 1920x1080 source under qwen:
    assert (w_balanced, h_balanced) == (1664, 928)   # official bucket
    assert (w_fast, h_fast) != (w_balanced, h_balanced)
    assert (w_ultra, h_ultra) != (w_balanced, h_balanced)
    # Ultra should produce more pixels than Fast.
    assert w_ultra * h_ultra > w_fast * h_fast


@respx.mock
def test_live_load_true_returns_ui_dict_with_fetched_text(monkeypatch, base_url, folders_payload):
    """live_load=True must return a dict-shaped result whose ui carries
    the fetched positive/negative text — that's what the JS extension
    reads to populate the widgets."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": [SAMPLE_ROWS[1]]})  # p2 / n2
    )
    respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    out = MetascanLoadPrompt().load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=True,
        positive_prompt="", negative_prompt="",
    )

    assert isinstance(out, dict)
    assert out["ui"] == {"positive_prompt": ["p2"], "negative_prompt": ["n2"]}
    image, pos, neg, name, src, w, h = out["result"]
    assert (pos, neg, name, src) == ("p2", "n2", "cinematic", "/b.png")


@respx.mock
def test_live_load_true_overrides_widget_values(monkeypatch, base_url, folders_payload):
    """live_load=True ignores whatever the user typed into the widgets —
    the fetch is the source of truth on a refresh."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": [SAMPLE_ROWS[1]]})  # p2 / n2
    )
    respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    out = MetascanLoadPrompt().load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=True,
        positive_prompt="USER EDIT — IGNORE ME",
        negative_prompt="USER EDIT — IGNORE ME TOO",
    )

    _, pos, neg, _, _, _, _ = out["result"]
    assert pos == "p2"
    assert neg == "n2"
    assert out["ui"]["positive_prompt"] == ["p2"]
    assert out["ui"]["negative_prompt"] == ["n2"]


@respx.mock
def test_live_load_false_echoes_widget_values(monkeypatch, base_url, folders_payload):
    """live_load=False outputs whatever the widget contains, not the
    cached fetched text — the widget IS the source of truth."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": [SAMPLE_ROWS[1]]})  # p2 / n2
    )
    respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    node = MetascanLoadPrompt()
    # Warm cache.
    node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=True,
        positive_prompt="", negative_prompt="",
    )

    # Now iterate.
    out = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=False,
        positive_prompt="my edited positive",
        negative_prompt="my edited negative",
    )
    _, pos, neg, _, _, _, _ = out["result"]
    assert pos == "my edited positive"
    assert neg == "my edited negative"
    # No widget push on live_load=False — the widget is already the source.
    assert out["ui"] == {}


@respx.mock
def test_live_load_false_widget_values_independent_of_cache(monkeypatch, base_url, folders_payload):
    """After a warm fetch of p2/n2, a live_load=False call with empty
    widget strings must output empty strings — confirming the cache no
    longer carries prompt text."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": [SAMPLE_ROWS[1]]})  # p2 / n2
    )
    respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    node = MetascanLoadPrompt()
    node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=True,
        positive_prompt="", negative_prompt="",
    )

    out = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=False,
        positive_prompt="", negative_prompt="",
    )
    _, pos, neg, _, _, _, _ = out["result"]
    assert pos == ""
    assert neg == ""
    # And the cache should not carry positive/negative keys.
    assert "positive" not in node._cache
    assert "negative" not in node._cache
    assert set(node._cache.keys()) == {"image", "name", "source_file_path"}


@respx.mock
def test_live_load_false_with_typed_text_still_requires_image_cache(monkeypatch, base_url):
    """Typing into the widgets doesn't bypass the image-cache requirement —
    we still need a cached image+source_file_path to output."""
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    with pytest.raises(RuntimeError, match="no cached prompt"):
        MetascanLoadPrompt().load(
            folder="Portraits", target_model="qwen", selection_mode="random",
            prompt_name="", seed=0, quality="Balanced", live_load=False,
            positive_prompt="text from user", negative_prompt="more text",
        )


# ----- increment mode -----

@respx.mock
def test_execute_increment_picks_by_index_and_advances(monkeypatch, base_url, folders_payload):
    """``increment`` picks row at 1-based index and surfaces next_index
    via the ui dict so the JS extension can advance the widget."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": SAMPLE_ROWS})
    )
    respx.get(f"{base_url}/api/stream/%2Fb.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    out = MetascanLoadPrompt().load(
        folder="Portraits", target_model="qwen", selection_mode="increment",
        prompt_name="", seed=0, quality="Balanced", live_load=True,
        positive_prompt="", negative_prompt="", index=2,
    )
    _, pos, _, name, src, _, _ = out["result"]
    assert (pos, name, src) == ("p2", "cinematic", "/b.png")
    assert out["ui"]["index"] == [3]


@respx.mock
def test_execute_increment_wraps_at_end(monkeypatch, base_url, folders_payload):
    """When index == len(rows) the next_index wraps to 1."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": SAMPLE_ROWS})
    )
    respx.get(f"{base_url}/api/stream/%2Fc.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    out = MetascanLoadPrompt().load(
        folder="Portraits", target_model="qwen", selection_mode="increment",
        prompt_name="", seed=0, quality="Balanced", live_load=True,
        positive_prompt="", negative_prompt="", index=3,
    )
    _, _, _, name, _, _, _ = out["result"]
    assert name == "landscape"
    assert out["ui"]["index"] == [1]


@respx.mock
def test_execute_increment_clamps_low(monkeypatch, base_url, folders_payload):
    """index <= 0 clamps to 1 (use first row, advance to 2)."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": SAMPLE_ROWS})
    )
    respx.get(f"{base_url}/api/stream/%2Fa.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    out = MetascanLoadPrompt().load(
        folder="Portraits", target_model="qwen", selection_mode="increment",
        prompt_name="", seed=0, quality="Balanced", live_load=True,
        positive_prompt="", negative_prompt="", index=0,
    )
    _, _, _, name, _, _, _ = out["result"]
    assert name == "hero"
    assert out["ui"]["index"] == [2]


@respx.mock
def test_execute_increment_clamps_high(monkeypatch, base_url, folders_payload):
    """index > len clamps to len (use last row, then wrap to 1)."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": SAMPLE_ROWS})
    )
    respx.get(f"{base_url}/api/stream/%2Fc.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    out = MetascanLoadPrompt().load(
        folder="Portraits", target_model="qwen", selection_mode="increment",
        prompt_name="", seed=0, quality="Balanced", live_load=True,
        positive_prompt="", negative_prompt="", index=999,
    )
    _, _, _, name, _, _, _ = out["result"]
    assert name == "landscape"
    assert out["ui"]["index"] == [1]


@respx.mock
def test_execute_increment_empty_rows_raises(monkeypatch, base_url, folders_payload):
    """Same empty-rows guard as the other modes."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": []})
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    with pytest.raises(RuntimeError, match="no saved prompts"):
        MetascanLoadPrompt().load(
            folder="Portraits", target_model="qwen", selection_mode="increment",
            prompt_name="", seed=0, quality="Balanced", live_load=True,
            positive_prompt="", negative_prompt="", index=1,
        )


@respx.mock
def test_execute_increment_single_row_wraps_to_self(monkeypatch, base_url, folders_payload):
    """Edge case: with one row, next_index is always 1."""
    clear_cache()
    respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=folders_payload))
    respx.post(f"{base_url}/api/prompt/search").mock(
        return_value=httpx.Response(200, json={"prompts": [SAMPLE_ROWS[0]]})
    )
    respx.get(f"{base_url}/api/stream/%2Fa.png").mock(
        return_value=httpx.Response(200, content=_png_bytes())
    )
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    out = MetascanLoadPrompt().load(
        folder="Portraits", target_model="qwen", selection_mode="increment",
        prompt_name="", seed=0, quality="Balanced", live_load=True,
        positive_prompt="", negative_prompt="", index=1,
    )
    assert out["ui"]["index"] == [1]


def test_input_types_contains_modes_and_index_widget(monkeypatch, base_url):
    """The combo lists random / by_name / increment (no `select` —
    that lives on the separate MetascanSelectPrompt node) and there's
    an ``index`` widget with sensible defaults."""
    import respx as _respx
    with _respx.mock:
        from mscan_client.cache import clear_cache as _clear
        _clear()
        _respx.get(f"{base_url}/api/folders").mock(return_value=httpx.Response(200, json=[]))
        _respx.get(f"{base_url}/api/prompt/target-models").mock(
            return_value=httpx.Response(200, json={"target_models": ["qwen"]})
        )
        monkeypatch.setenv("METASCAN_URL", base_url)
        monkeypatch.delenv("METASCAN_API_KEY", raising=False)
        import mscan_nodes.settings
        mscan_nodes.settings._OVERRIDE = None

        spec = MetascanLoadPrompt.INPUT_TYPES()
        modes = spec["required"]["selection_mode"][0]
        assert modes == ["random", "by_name", "increment"]
        idx_type, idx_opts = spec["required"]["index"]
        assert idx_type == "INT"
        assert idx_opts["default"] == 1
        assert idx_opts["min"] == 1
