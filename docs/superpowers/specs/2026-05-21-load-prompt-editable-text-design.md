# Spec: Editable positive/negative prompt fields on MetascanLoadPrompt

**Date:** 2026-05-21
**Scope:** `mscan_nodes/load_prompt.py`, new `web/metscan-load-prompt.js`, `__init__.py`, `docs/nodes/load-prompt.md`, `tests/test_load_prompt.py`.
**Out of scope:** Other nodes; persistence beyond ComfyUI's existing workflow-JSON widget storage; new nodes; UI affordances beyond text-population.

## Motivation

Today, `MetascanLoadPrompt` emits the saved positive and negative prompt strings as outputs only — they originate from metascan and the user cannot edit them in the workflow. To iterate on a generation, the user has to round-trip back to metascan (or build awkward override plumbing in the graph).

The desired iteration loop:

1. `live_load = True`, run the workflow → the metascan-saved positive and negative prompts appear as editable text in the node, and the image generates from them.
2. `live_load = False` → the editable text now drives generation. The user tweaks the prompt and re-runs.
3. Repeat (2) until the prompt is right.

This spec makes that loop possible by turning `positive` / `negative` into editable widgets that the backend populates on a live fetch.

## Design overview

```
                              ┌───────────────────────────────┐
                              │ MetascanLoadPrompt (node)     │
                              │                               │
folder, target_model,         │  required inputs              │
selection_mode, prompt_name,  │    (existing controls)        │
seed, quality, live_load  ───▶│                               │
                              │  positive_prompt   (STRING,   │ ◀─── editable textarea
                              │  negative_prompt    multiline)│ ◀─── editable textarea
                              │                               │
                              │  load()                       │
                              │   ├ live_load=True            │
                              │   │   fetch metascan          │
                              │   │   cache img/name/path     │
                              │   │   output = fetched text   │
                              │   │   ui = {positive_prompt,  │ ──▶ JS extension
                              │   │         negative_prompt}  │     writes into widgets
                              │   │                           │
                              │   └ live_load=False           │
                              │       reuse cache             │
                              │       output = widget text    │
                              │                               │
                              │  outputs: image, positive,    │
                              │    negative, name,            │
                              │    source_file_path, w, h     │
                              └───────────────────────────────┘
```

## Backend changes — `mscan_nodes/load_prompt.py`

### New inputs

Add to the `required` block in `INPUT_TYPES`:

```python
"positive_prompt": ("STRING", {"multiline": True, "default": ""}),
"negative_prompt": ("STRING", {"multiline": True, "default": ""}),
```

### `load()` signature

Two new keyword args:

```python
def load(
    self,
    folder, target_model, selection_mode, prompt_name, seed, quality, live_load,
    positive_prompt: str,
    negative_prompt: str,
) -> dict:
```

### Cache shape

Drop `positive` and `negative` from the cache. New shape:

```python
{
    "image": tensor,
    "name": str,
    "source_file_path": str,
}
```

When `live_load=False`, prompt text comes from the widgets, not the cache.

### Execute-time behavior

| `live_load` | Output `positive` / `negative` | UI push | Cache mutation |
|---|---|---|---|
| `True` | Fetched text (widget values ignored) | `ui = {"positive_prompt": [pos], "negative_prompt": [neg]}` | Writes `image`, `name`, `source_file_path` |
| `False` | Widget values (`positive_prompt`, `negative_prompt`) | `ui = {}` | Reuses cached image/name/path; raises if empty |

The `live_load=False` cache check still applies: if `self._cache is None`, raise the existing `"live_load is off but no cached prompt is available yet…"` error. The user still needs a populated image cache to produce an `IMAGE` output, even if they've typed prompt text directly.

### Return shape

Switch from a bare tuple to the dict form already used by `MetascanSaveImage` (`save_image.py:250-253`):

```python
return {
    "ui": ui_dict,           # {} or {"positive_prompt": [...], "negative_prompt": [...]}
    "result": (
        image, positive, negative, name, source_file_path, width, height,
    ),
}
```

`OUTPUT_NODE` stays `False` (unset). The `ui` dict still rides the `executed` websocket event for non-output nodes, which is what the JS extension consumes.

### Output ordering

Unchanged: `("image", "positive", "negative", "name", "source_file_path", "width", "height")`. Downstream graphs that already consume these outputs continue to work.

## Frontend extension — `web/metscan-load-prompt.js`

A new (and first) frontend extension in this repo.

```javascript
import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

app.registerExtension({
    name: "metscan.load-prompt.widget-sync",
    async setup() {
        api.addEventListener("executed", (event) => {
            const { node: nodeId, output } = event.detail;
            if (!output) return;
            const node = app.graph.getNodeById(nodeId);
            if (!node || node.comfyClass !== "MetascanLoadPrompt") return;

            for (const key of ["positive_prompt", "negative_prompt"]) {
                const value = output[key];
                if (value === undefined) continue;
                const widget = node.widgets?.find((w) => w.name === key);
                if (widget) {
                    widget.value = Array.isArray(value) ? value[0] : value;
                }
            }
            node.setDirtyCanvas?.(true, true);
        });
    },
});
```

Behavior notes:
- Filtered on `node.comfyClass` so events from other nodes are no-ops.
- `output[key]` is undefined when `live_load=False`, so the widget is left alone.
- Unwraps a single-element array (the ComfyUI convention for `ui.<key>: [value]`) but tolerates a raw string for robustness.
- `setDirtyCanvas` forces a re-render so the new text shows up immediately.

## Package-init change — `__init__.py`

Replace:

```python
WEB_DIRECTORY = None
```

with:

```python
WEB_DIRECTORY = "./web"
```

ComfyUI mounts the declared `WEB_DIRECTORY` and serves any `*.js` files at its root to the editor (no `extensions/` subfolder required). No build step.

## Docs — `docs/nodes/load-prompt.md`

Edits, not a rewrite:

- **Inputs table:** add `positive_prompt` and `negative_prompt` rows (type `STRING`, default empty, "Editable text. On `live_load=True` the field is overwritten with the fetched value; on `live_load=False` the field's contents become the `positive`/`negative` outputs.").
- **Caching semantics paragraph:** update the cache contents to `image`, `name`, `source_file_path` only. Add a note: "Prompt text is no longer cached on the backend — it lives in the editable widgets and is persisted with the workflow JSON, so it survives page reload. The image cache still doesn't survive a reload, so after reopening a saved workflow you'll need one `live_load=True` run to re-populate the image cache before you can iterate with `live_load=False`."
- **Outputs table:** clarify `positive` / `negative` rows — "Comes from the editable `positive_prompt` / `negative_prompt` widget when `live_load=False`, or from the fetched metascan row when `live_load=True` (and the widget is overwritten with that value)."
- **Typical workflow paragraph:** rewrite the iteration story per the Motivation section above.
- **Common errors:** keep the existing "live_load is off but no cached prompt is available yet" entry but reword to reference the *image* cache, not the prompt cache.

## Tests — `tests/test_load_prompt.py`

### Adjust existing tests

Every test that destructures the return value of `node.load(...)` as a 7-tuple needs to unpack `result["result"]`. The following existing tests are affected: `test_execute_maps_any_to_null_target_model`, `test_execute_null_negative_becomes_empty_string`, `test_execute_loads_source_image_and_emits_resolution`, `test_cached_load_reuses_image_and_skips_http`, `test_cached_load_recomputes_resolution_on_quality_change`. Each call site also needs to pass `positive_prompt=""`, `negative_prompt=""` (or other values, per the test's intent).

`test_execute_raises_on_offline_sentinel`, `test_execute_raises_when_saved_prompt_has_no_file_path`, and `test_live_load_off_with_empty_cache_raises` already raise before any unpack — they only need the two new keyword args.

### New tests

1. **`test_live_load_true_returns_ui_dict_with_fetched_text`** — assert the return is a dict with `ui = {"positive_prompt": ["p2"], "negative_prompt": ["n2"]}` and `result[1] == "p2"`, `result[2] == "n2"`, even when the call passes non-empty widget values that differ.

2. **`test_live_load_true_overrides_widget_values`** — call with `positive_prompt="USER EDITED"`; assert `result[1]` is the fetched value, not the user-supplied value. Confirms `live_load=True` semantics.

3. **`test_live_load_false_echoes_widget_values`** — warm cache with one `live_load=True` call, then a `live_load=False` call with `positive_prompt="my edit"`, `negative_prompt="my neg edit"`; assert `result[1] == "my edit"`, `result[2] == "my neg edit"`, and `ui == {}` (or omitted).

4. **`test_live_load_false_widget_values_independent_of_cache`** — after warming the cache from a row with `positive="p2"`, a `live_load=False` call with `positive_prompt=""` should output `""` (not `"p2"` from cache). Confirms the cache no longer holds prompt text.

5. **`test_cached_load_still_requires_warmed_image_cache`** — variant of the existing empty-cache test, but with `positive_prompt="text"` to make sure non-empty widget values don't bypass the image-cache requirement.

## Risks and edge cases

- **User edits during a `live_load=True` run:** Overwritten. This is the intended semantics (`live_load=True` ≡ "refresh"). Documented in the inputs table.
- **Workflow JSON now embeds the prompt text:** sharing or committing workflows that came from sensitive prompts will leak that text. This is no different from any other multiline STRING widget in ComfyUI (e.g., `CLIPTextEncode`'s text field), and is out of scope to mitigate.
- **`executed` event for non-`OUTPUT_NODE`s:** ComfyUI fires the `executed` websocket event whenever a node returns a dict with a non-empty `ui` value, regardless of `OUTPUT_NODE`. This is the same mechanism that allows non-output preview nodes to surface UI. Verified by inspection of `MetascanSaveImage`'s pattern, which is `OUTPUT_NODE = True`; for our case we still emit `ui = {}` on `live_load=False`, which is a safe no-op.
- **Empty `ui` dict shape:** ComfyUI accepts `ui = {}` (no-op on the frontend). Returning the dict form unconditionally keeps the code uniform and the tests simple.

## Build sequence

1. Backend `INPUT_TYPES`, `load()`, cache shape, return shape — make tests fail in the expected way.
2. Update existing tests to the dict-result form and the new args.
3. Add new tests.
4. Add `web/metscan-load-prompt.js`.
5. Flip `WEB_DIRECTORY` in `__init__.py`.
6. Update `docs/nodes/load-prompt.md`.
7. Verify in ComfyUI (manual smoke): live_load=True populates widgets; live_load=False echoes widgets; toggling between them iterates the way the workflow expects.
