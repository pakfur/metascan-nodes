# Load Prompt Editable Text Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote `MetascanLoadPrompt`'s positive/negative prompt strings from read-only outputs into editable multiline-STRING widgets that the backend auto-populates on a `live_load=True` run via a small JS extension, enabling the iterate-by-editing-the-prompt workflow.

**Architecture:** When `live_load=True`, the Python `load()` method fetches from metascan, returns the fetched text as both outputs and as a `ui` dict; a new JS extension listens for the `executed` event on `MetascanLoadPrompt` nodes and writes `ui.positive_prompt` / `ui.negative_prompt` into the corresponding widgets. When `live_load=False`, `load()` echoes the widget values as outputs and reuses a slimmed-down `{image, name, source_file_path}` cache for everything that *isn't* text. Return shape switches from a bare tuple to ComfyUI's `{"ui": ..., "result": ...}` dict form, mirroring `MetascanSaveImage`.

**Tech Stack:** Python 3.12 / ComfyUI custom-node API / pytest+respx for backend; vanilla JS using ComfyUI's `app` + `api` globals for the frontend (no build step).

**Working directory for all commands:** `/mnt/c/Users/jtkli/gws/` (the canonical repo root — the git root and package root are both `gws/`, not the `metscan-nodes/` subdirectory). Activate the venv first: `source /mnt/c/Users/jtkli/gws/metscan-nodes/.venv/bin/activate`.

**Reference spec:** `docs/superpowers/specs/2026-05-21-load-prompt-editable-text-design.md`.

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `mscan_nodes/load_prompt.py` | Modify | Add `positive_prompt`/`negative_prompt` inputs; branch outputs on `live_load`; emit `ui` dict; slim cache to `{image, name, source_file_path}`; switch return shape to dict. |
| `tests/test_load_prompt.py` | Modify | Update existing 8 tests for new signature + return shape; add 5 new tests covering editable-widget behavior. |
| `__init__.py` (at gws root) | Modify | Change `WEB_DIRECTORY = None` to `WEB_DIRECTORY = "./web"` so ComfyUI mounts the frontend extension. |
| `web/metscan-load-prompt.js` | Create | Frontend extension listening for `executed` events on `MetascanLoadPrompt` and writing `ui.positive_prompt`/`ui.negative_prompt` into the matching widgets. |
| `docs/nodes/load-prompt.md` | Modify | Update Inputs table, Caching semantics, Outputs table, Typical workflow, and Common errors to reflect editable text. |

`mscan_client/*` is untouched — the API call shape doesn't change. `_fetch_live()` keeps returning all 5 fields; `load()` decides which subset goes into the cache.

---

## Task 1: Add new failing tests for editable-widget behavior

**Files:**
- Modify: `tests/test_load_prompt.py` (append after line 295)

These tests describe the target behavior. They will fail (most with `TypeError` for unexpected kwargs, some with `AttributeError`/`KeyError` for missing dict shape) until Task 3 lands.

- [ ] **Step 1: Add `test_live_load_true_returns_ui_dict_with_fetched_text`**

Append this test after the last existing test in `tests/test_load_prompt.py`:

```python
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
```

- [ ] **Step 2: Add `test_live_load_true_overrides_widget_values`**

Append:

```python
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
```

- [ ] **Step 3: Add `test_live_load_false_echoes_widget_values`**

Append:

```python
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
```

- [ ] **Step 4: Add `test_live_load_false_widget_values_independent_of_cache`**

Append:

```python
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
```

- [ ] **Step 5: Add `test_live_load_false_with_typed_text_still_requires_image_cache`**

Append:

```python
def test_live_load_false_with_typed_text_still_requires_image_cache(monkeypatch, base_url):
    """Typing into the widgets doesn't bypass the image-cache requirement —
    we still need a cached image+source_file_path to output."""
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    with pytest.raises(RuntimeError, match="no cached"):
        MetascanLoadPrompt().load(
            folder="Portraits", target_model="qwen", selection_mode="random",
            prompt_name="", seed=0, quality="Balanced", live_load=False,
            positive_prompt="text from user", negative_prompt="more text",
        )
```

- [ ] **Step 6: Run only the new tests, verify they fail**

```bash
source /mnt/c/Users/jtkli/gws/metscan-nodes/.venv/bin/activate
cd /mnt/c/Users/jtkli/gws
pytest tests/test_load_prompt.py -k "live_load_true_returns_ui_dict or live_load_true_overrides or live_load_false_echoes or live_load_false_widget or live_load_false_with_typed" -v
```

Expected: 5 failures. The first four fail on `TypeError: load() got an unexpected keyword argument 'positive_prompt'`. The fifth fails the same way before reaching the `RuntimeError` assertion.

- [ ] **Step 7: Commit**

```bash
cd /mnt/c/Users/jtkli/gws
git add tests/test_load_prompt.py
git commit -m "Add failing tests for editable positive/negative widgets on Load Prompt"
```

---

## Task 2: Update existing tests for new signature + return shape

**Files:**
- Modify: `tests/test_load_prompt.py:88-294`

The eight existing tests that call `node.load(...)` all need two mechanical changes:
1. Pass `positive_prompt=""`, `negative_prompt=""` as new keyword arguments.
2. For tests that read tuple outputs, change `image, pos, neg, ... = node.load(...)` to `out = node.load(...); image, pos, neg, ... = out["result"]`.

Tests that raise before unpack (`test_execute_raises_on_offline_sentinel`, `test_execute_raises_when_saved_prompt_has_no_file_path`, `test_live_load_off_with_empty_cache_raises`) only need change #1.

- [ ] **Step 1: Edit `test_execute_maps_any_to_null_target_model` (line 90)**

Replace this block in `tests/test_load_prompt.py`:

```python
    image, pos, neg, name, src, w, h = MetascanLoadPrompt().load(
        folder="Portraits",
        target_model="any",
        selection_mode="by_name",
        prompt_name="cinematic",
        seed=0,
        quality="Balanced",
        live_load=True,
    )
```

with:

```python
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
```

- [ ] **Step 2: Edit `test_execute_null_negative_becomes_empty_string` (line 128)**

Replace:

```python
    _, _, neg, _, _, _, _ = MetascanLoadPrompt().load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="hero", seed=0, quality="Balanced", live_load=True,
    )
```

with:

```python
    out = MetascanLoadPrompt().load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="hero", seed=0, quality="Balanced", live_load=True,
        positive_prompt="", negative_prompt="",
    )
    _, _, neg, _, _, _, _ = out["result"]
```

- [ ] **Step 3: Edit `test_execute_raises_on_offline_sentinel` (line 137)**

Replace:

```python
        MetascanLoadPrompt().load(
            folder=OFFLINE_SENTINEL, target_model="qwen", selection_mode="random",
            prompt_name="", seed=0, quality="Balanced", live_load=True,
        )
```

with:

```python
        MetascanLoadPrompt().load(
            folder=OFFLINE_SENTINEL, target_model="qwen", selection_mode="random",
            prompt_name="", seed=0, quality="Balanced", live_load=True,
            positive_prompt="", negative_prompt="",
        )
```

- [ ] **Step 4: Edit `test_execute_loads_source_image_and_emits_resolution` (line 161)**

Replace:

```python
    image, _, _, _, src, w, h = MetascanLoadPrompt().load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=True,
    )
```

with:

```python
    out = MetascanLoadPrompt().load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=True,
        positive_prompt="", negative_prompt="",
    )
    image, _, _, _, src, w, h = out["result"]
```

- [ ] **Step 5: Edit `test_execute_raises_when_saved_prompt_has_no_file_path` (line 186)**

Replace:

```python
        MetascanLoadPrompt().load(
            folder="Portraits", target_model="qwen", selection_mode="by_name",
            prompt_name="cinematic", seed=0, quality="Balanced", live_load=True,
        )
```

with:

```python
        MetascanLoadPrompt().load(
            folder="Portraits", target_model="qwen", selection_mode="by_name",
            prompt_name="cinematic", seed=0, quality="Balanced", live_load=True,
            positive_prompt="", negative_prompt="",
        )
```

- [ ] **Step 6: Edit `test_live_load_off_with_empty_cache_raises` (line 205)**

Replace:

```python
        MetascanLoadPrompt().load(
            folder="Portraits", target_model="qwen", selection_mode="random",
            prompt_name="", seed=0, quality="Balanced", live_load=False,
        )
```

with:

```python
        MetascanLoadPrompt().load(
            folder="Portraits", target_model="qwen", selection_mode="random",
            prompt_name="", seed=0, quality="Balanced", live_load=False,
            positive_prompt="", negative_prompt="",
        )
```

- [ ] **Step 7: Edit `test_cached_load_reuses_image_and_skips_http` (line 233)**

Replace the two `node.load(...)` blocks. First block (line 233):

```python
    img1, _, _, name1, src1, _, _ = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=True,
    )
```

becomes:

```python
    out1 = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=True,
        positive_prompt="", negative_prompt="",
    )
    img1, _, _, name1, src1, _, _ = out1["result"]
```

Second block (line 241):

```python
    img2, _, _, name2, src2, _, _ = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=False,
    )
```

becomes:

```python
    out2 = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=False,
        positive_prompt="", negative_prompt="",
    )
    img2, _, _, name2, src2, _, _ = out2["result"]
```

- [ ] **Step 8: Edit `test_cached_load_recomputes_resolution_on_quality_change` (line 271)**

Replace the three `node.load(...)` blocks. First (line 271, Balanced/live):

```python
    _, _, _, _, _, w_balanced, h_balanced = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=True,
    )
```

becomes:

```python
    out_b = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Balanced", live_load=True,
        positive_prompt="", negative_prompt="",
    )
    _, _, _, _, _, w_balanced, h_balanced = out_b["result"]
```

Second (line 278, Fast):

```python
    _, _, _, _, _, w_fast, h_fast = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Fast", live_load=False,
    )
```

becomes:

```python
    out_f = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Fast", live_load=False,
        positive_prompt="", negative_prompt="",
    )
    _, _, _, _, _, w_fast, h_fast = out_f["result"]
```

Third (line 282, Ultra):

```python
    _, _, _, _, _, w_ultra, h_ultra = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Ultra", live_load=False,
    )
```

becomes:

```python
    out_u = node.load(
        folder="Portraits", target_model="qwen", selection_mode="by_name",
        prompt_name="cinematic", seed=0, quality="Ultra", live_load=False,
        positive_prompt="", negative_prompt="",
    )
    _, _, _, _, _, w_ultra, h_ultra = out_u["result"]
```

- [ ] **Step 9: Run the entire load_prompt test file, confirm everything is failing the same way**

```bash
source /mnt/c/Users/jtkli/gws/metscan-nodes/.venv/bin/activate
cd /mnt/c/Users/jtkli/gws
pytest tests/test_load_prompt.py -v
```

Expected: All 13 of the existing-but-modified + 5 new tests fail with `TypeError: load() got an unexpected keyword argument 'positive_prompt'`. The 4 `select_prompt` unit tests at the top of the file (which don't touch `load()`) still pass.

- [ ] **Step 10: Commit**

```bash
cd /mnt/c/Users/jtkli/gws
git add tests/test_load_prompt.py
git commit -m "Update Load Prompt tests for new signature and dict return shape"
```

---

## Task 3: Backend — implement the editable-widget behavior

**Files:**
- Modify: `mscan_nodes/load_prompt.py:67-176`

- [ ] **Step 1: Update the docstring on `MetascanLoadPrompt.__init__` to describe the slimmer cache**

In `mscan_nodes/load_prompt.py`, replace the existing `__init__` (lines 73-78):

```python
    def __init__(self) -> None:
        # Per-node-instance cache of the last live-fetched payload.
        # Holds image tensor + prompt strings + source path, but NOT
        # width/height — those are always recomputed so the user can
        # tweak ``quality`` without re-hitting metascan.
        self._cache: Optional[dict] = None
```

with:

```python
    def __init__(self) -> None:
        # Per-node-instance cache of the last live fetch. Holds image,
        # name, and source_file_path — but NOT positive/negative (those
        # live in the editable widgets and are persisted with the
        # workflow JSON) and NOT width/height (always recomputed so
        # the user can tweak ``quality`` without re-hitting metascan).
        self._cache: Optional[dict] = None
```

- [ ] **Step 2: Add `positive_prompt` and `negative_prompt` to `INPUT_TYPES`**

Replace the `required` block in `INPUT_TYPES` (lines 90-98):

```python
            "required": {
                "folder": (folders,),
                "target_model": (target_models,),
                "selection_mode": (["random", "by_name"],),
                "prompt_name": ("STRING", {"default": ""}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 2**31 - 1}),
                "quality": (QUALITY_TIERS,),
                "live_load": ("BOOLEAN", {"default": True}),
            },
```

with:

```python
            "required": {
                "folder": (folders,),
                "target_model": (target_models,),
                "selection_mode": (["random", "by_name"],),
                "prompt_name": ("STRING", {"default": ""}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 2**31 - 1}),
                "quality": (QUALITY_TIERS,),
                "live_load": ("BOOLEAN", {"default": True}),
                "positive_prompt": ("STRING", {"multiline": True, "default": ""}),
                "negative_prompt": ("STRING", {"multiline": True, "default": ""}),
            },
```

- [ ] **Step 3: Rewrite `load()` to accept the new args, branch on live_load, and return the dict form**

Replace `load()` and its body (lines 101-130):

```python
    def load(
        self,
        folder: str,
        target_model: str,
        selection_mode: SelectionMode,
        prompt_name: str,
        seed: int,
        quality: str,
        live_load: bool,
    ) -> tuple:
        if live_load:
            self._cache = self._fetch_live(
                folder=folder, target_model=target_model,
                selection_mode=selection_mode, prompt_name=prompt_name,
                seed=seed,
            )
        elif self._cache is None:
            raise RuntimeError(
                "live_load is off but no cached prompt is available yet. "
                "Enable live_load once to populate the cache, then disable."
            )

        c = self._cache
        src_h, src_w = c["image"].shape[1], c["image"].shape[2]
        width, height = compute_resolution(src_w, src_h, target_model, quality)

        return (
            c["image"], c["positive"], c["negative"], c["name"],
            c["source_file_path"], width, height,
        )
```

with:

```python
    def load(
        self,
        folder: str,
        target_model: str,
        selection_mode: SelectionMode,
        prompt_name: str,
        seed: int,
        quality: str,
        live_load: bool,
        positive_prompt: str,
        negative_prompt: str,
    ) -> dict:
        if live_load:
            fetched = self._fetch_live(
                folder=folder, target_model=target_model,
                selection_mode=selection_mode, prompt_name=prompt_name,
                seed=seed,
            )
            # Cache only what can't be edited via widgets. Prompt text
            # is the user's job from here on out (the JS extension
            # writes the fetched values into the widgets immediately
            # after this returns, so subsequent live_load=False runs
            # read them back from there).
            self._cache = {
                "image": fetched["image"],
                "name": fetched["name"],
                "source_file_path": fetched["source_file_path"],
            }
            positive = fetched["positive"]
            negative = fetched["negative"]
            ui: dict = {
                "positive_prompt": [positive],
                "negative_prompt": [negative],
            }
        else:
            if self._cache is None:
                raise RuntimeError(
                    "live_load is off but no cached prompt is available yet. "
                    "Enable live_load once to populate the cache, then disable."
                )
            positive = positive_prompt
            negative = negative_prompt
            ui = {}

        c = self._cache
        src_h, src_w = c["image"].shape[1], c["image"].shape[2]
        width, height = compute_resolution(src_w, src_h, target_model, quality)

        return {
            "ui": ui,
            "result": (
                c["image"], positive, negative, c["name"],
                c["source_file_path"], width, height,
            ),
        }
```

Note: `_fetch_live` still returns the full 5-field dict (including `positive`/`negative`) — we keep its shape stable since other callers may grow in the future, and `load()` is responsible for the cache-slimming.

- [ ] **Step 4: Run the full load_prompt test suite, confirm everything passes**

```bash
source /mnt/c/Users/jtkli/gws/metscan-nodes/.venv/bin/activate
cd /mnt/c/Users/jtkli/gws
pytest tests/test_load_prompt.py -v
```

Expected: All tests pass (4 `select_prompt` unit tests + 8 modified-existing tests + 5 new tests = 17 total).

- [ ] **Step 5: Run the full test suite to make sure nothing else regressed**

```bash
cd /mnt/c/Users/jtkli/gws
pytest -q
```

Expected: All tests pass. If a non-load-prompt test fails, stop and investigate before continuing.

- [ ] **Step 6: Commit**

```bash
cd /mnt/c/Users/jtkli/gws
git add mscan_nodes/load_prompt.py
git commit -m "Make positive/negative editable widgets on MetascanLoadPrompt"
```

---

## Task 4: Frontend extension + WEB_DIRECTORY wiring

**Files:**
- Create: `web/metscan-load-prompt.js`
- Modify: `__init__.py:33`

- [ ] **Step 1: Create the `web/` directory**

```bash
mkdir -p /mnt/c/Users/jtkli/gws/web
```

- [ ] **Step 2: Create `web/metscan-load-prompt.js`**

Write the file `/mnt/c/Users/jtkli/gws/web/metscan-load-prompt.js`:

```javascript
import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

app.registerExtension({
    name: "metscan.load-prompt.widget-sync",
    async setup() {
        api.addEventListener("executed", (event) => {
            const { node: nodeId, output } = event.detail || {};
            if (!output) return;
            const node = app.graph.getNodeById(nodeId);
            if (!node || node.comfyClass !== "MetascanLoadPrompt") return;

            let touched = false;
            for (const key of ["positive_prompt", "negative_prompt"]) {
                const value = output[key];
                if (value === undefined) continue;
                const widget = node.widgets?.find((w) => w.name === key);
                if (widget) {
                    widget.value = Array.isArray(value) ? value[0] : value;
                    touched = true;
                }
            }
            if (touched) {
                node.setDirtyCanvas?.(true, true);
            }
        });
    },
});
```

- [ ] **Step 3: Flip `WEB_DIRECTORY` in `__init__.py`**

In `/mnt/c/Users/jtkli/gws/__init__.py`, replace:

```python
WEB_DIRECTORY = None
```

with:

```python
WEB_DIRECTORY = "./web"
```

- [ ] **Step 4: Confirm Python tests still pass (sanity check that __init__ didn't break import)**

```bash
source /mnt/c/Users/jtkli/gws/metscan-nodes/.venv/bin/activate
cd /mnt/c/Users/jtkli/gws
pytest -q
```

Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Users/jtkli/gws
git add __init__.py web/metscan-load-prompt.js
git commit -m "Wire JS extension that auto-populates Load Prompt widgets after live_load"
```

---

## Task 5: Update node-level docs

**Files:**
- Modify: `docs/nodes/load-prompt.md`

- [ ] **Step 1: Add two rows to the Inputs table**

In `docs/nodes/load-prompt.md`, find the line that ends with `If True, fetch from metascan...` (the `live_load` row, around line 30) and insert two new rows directly after it:

```markdown
| `positive_prompt`| `STRING`  | *(empty)*   | Editable multiline text. On `live_load=True` this widget is overwritten with the fetched positive prompt; on `live_load=False` its contents are passed straight through to the `positive` output. |
| `negative_prompt`| `STRING`  | *(empty)*   | Editable multiline text. Same semantics as `positive_prompt` for the negative prompt.                                                                                                              |
```

- [ ] **Step 2: Rewrite the "Caching semantics" section**

Replace the current "Caching semantics" subsection (the paragraph + table + two-paragraph workflow note, roughly lines 32-44) with:

```markdown
### Caching semantics

The node holds a per-instance cache containing the last-fetched `image`, `name`, and `source_file_path`. Prompt text is **not** cached — it lives in the editable `positive_prompt` / `negative_prompt` widgets and is persisted with the workflow JSON, so it survives a page reload. Width/height are also not cached — they're recomputed every execute so changing `quality` or `target_model` updates the sizing without re-fetching.

| `live_load` | Cache state | Output `positive`/`negative` | Behavior                                                                                       |
|-------------|-------------|------------------------------|------------------------------------------------------------------------------------------------|
| `True`      | any         | The fetched text             | Fetch from metascan, overwrite cache, push fetched text into the widgets, recompute resolution. |
| `False`     | populated   | The widget contents          | Skip all HTTP. Reuse cached image + name + path. Recompute resolution.                          |
| `False`     | empty       | —                            | Raise `RuntimeError("live_load is off but no cached prompt is available yet")`.                |

Two LoadPrompt nodes in the same workflow have **independent caches** (the cache is on the node instance, not module-level). The image cache resets on page reload or ComfyUI restart, so after reopening a saved workflow you'll need at least one `live_load=True` run to repopulate it before you can iterate offline — even though the prompt text in the widgets persisted.

Typical workflow: enable `live_load` once, run, then disable it and edit the `positive_prompt` / `negative_prompt` widgets directly to iterate on the prompt cheaply. While iterating you can also sweep `quality` (Fast → Balanced → High → Ultra) without re-hitting metascan.
```

- [ ] **Step 3: Update the Outputs table's `positive` and `negative` rows**

Find the two output rows for `positive` and `negative` (around lines 51-52) and replace them with:

```markdown
| `positive`          | `STRING` | The positive prompt text. Equal to the editable `positive_prompt` widget on `live_load=False`, or the fetched value (which simultaneously overwrites the widget) on `live_load=True`. |
| `negative`          | `STRING` | The negative prompt text. Same semantics as `positive`. SQL `NULL` from the metascan row is normalized to `""` on fetch.                                                              |
```

- [ ] **Step 4: Update the matching error entry under "Common errors"**

Find the entry beginning `**"live_load is off but no cached prompt is available yet"**` (around line 69) and replace it with:

```markdown
- **"live_load is off but no cached prompt is available yet"**: you disabled `live_load` before ever running with it on. The widgets carry the prompt text but the *image* still has to come from a live fetch. Enable `live_load` once, run, then disable.
```

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/Users/jtkli/gws
git add docs/nodes/load-prompt.md
git commit -m "Document editable positive/negative widgets on Load Prompt"
```

---

## Task 6: Manual smoke test in ComfyUI

Tests can't cover the JS extension end-to-end — the executor mocks don't run a real browser. This task verifies the round trip.

- [ ] **Step 1: Confirm the user has refreshed the install dir**

Per the user's workflow, the canonical repo (`/mnt/c/Users/jtkli/gws/`) gets pushed to GitHub and the user pulls into `/mnt/d/ComfyUI_windows_portable/ComfyUI/custom_nodes/metascan-nodes/` themselves. Pause here and confirm with the user that the install dir is up to date before proceeding.

- [ ] **Step 2: Verify `live_load=True` populates the widgets**

Drop a `Metascan · Load Prompt` node into ComfyUI. Pick a folder + target_model + selection_mode that has saved prompts. Leave `positive_prompt` and `negative_prompt` empty. Queue the workflow. Expected: after execution, both textareas contain the saved positive/negative text from metascan.

- [ ] **Step 3: Verify `live_load=False` echoes user edits**

Toggle `live_load` to off. Edit the `positive_prompt` text. Queue again. Inspect a node downstream of `positive` (e.g., a Show Text or a CLIPTextEncode). Expected: the downstream node sees the edited text, not the originally fetched text.

- [ ] **Step 4: Verify quality sweep still works**

With `live_load=False`, change `quality` from Balanced to Ultra and queue again. Expected: width/height outputs change, no re-fetch from metascan (no spinner on the metascan side, fast execution).

- [ ] **Step 5: Verify `live_load=True` overwrites user edits**

Flip `live_load` back to on. Queue. Expected: the textareas are overwritten with the freshly-fetched values, replacing whatever the user typed.

If any step fails, stop and report the failure — do not attempt to patch from this plan.
