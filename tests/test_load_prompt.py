from __future__ import annotations

import pytest
import respx
import httpx

from client.cache import clear_cache, OFFLINE_SENTINEL
from nodes.load_prompt import MetascanLoadPrompt, select_prompt


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
    import nodes.settings
    nodes.settings._OVERRIDE = None

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
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import nodes.settings
    nodes.settings._OVERRIDE = None

    pos, neg, name, src = MetascanLoadPrompt().load(
        folder="Portraits",
        target_model="any",
        selection_mode="by_name",
        prompt_name="cinematic",
        seed=0,
    )
    assert pos == "p2"
    assert neg == "n2"
    assert name == "cinematic"
    assert src == "/b.png"
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
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import nodes.settings
    nodes.settings._OVERRIDE = None

    pos, neg, name, src = MetascanLoadPrompt().load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="hero", seed=0,
    )
    assert neg == ""


def test_execute_raises_on_offline_sentinel():
    with pytest.raises(RuntimeError, match="offline"):
        MetascanLoadPrompt().load(
            folder=OFFLINE_SENTINEL, target_model="qwen", selection_mode="random",
            prompt_name="", seed=0,
        )
