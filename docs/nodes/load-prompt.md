# Metascan · Load Prompt

Load a saved prompt from metascan's prompt library, plus the source image the prompt was saved against, plus a recommended `(width, height)` for image generation sized to the chosen diffusion model and quality tier.

Supports per-instance caching so you can iterate on quality / target_model without re-hitting metascan.

## What it pulls from metascan

- **At node-load time:**
  - `GET /api/folders` → manual folder list for the `folder` dropdown.
  - `GET /api/prompt/target-models` → diffusion model list for the `target_model` dropdown.
- **At execute time when `live_load = True`:**
  - `GET /api/folders` (again, for ID lookup) → resolves the chosen folder name to its ID.
  - `POST /api/prompt/search` with `{folder_id, target_model, name, limit=500}` → returns matching saved prompts.
  - `GET /api/stream/{path}` → raw bytes of the source image, decoded to a tensor.
- **At execute time when `live_load = False`:** nothing. The cached `image`, `name`, and `source_file_path` from the most recent live fetch are reused, prompt text comes from the editable widgets, and `width` / `height` are recomputed from the current `quality` / `target_model`.

This node depends on the companion metascan endpoints `POST /api/prompt/search` and `GET /api/prompt/target-models`, which were added in a separate PR to the metascan repo.

## Inputs

| Name             | Type      | Default     | Description                                                                                                                                          |
|------------------|-----------|-------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| `folder`         | (dropdown) | —          | Manual folder name. Saved prompts are searched within this folder.                                                                                   |
| `target_model`   | (dropdown) | —          | Filter to one diffusion model (`sd`, `pony`, `flux1`, `flux2`, `zimage`, `chroma`, `qwen`) or `any` to match any model. Also drives resolution rules. |
| `selection_mode` | (enum)    | `random`    | `random` / `by_name` / `select` / `increment`. See [Selection modes](#selection-modes) below.                                                        |
| `prompt_name`    | `STRING`  | *(empty)*   | Required when `selection_mode` is `by_name` or `select`. Ignored in `random` and `increment` modes. The 🖼 Pick prompt picker writes into this widget. |
| `seed`           | `INT`     | `0`         | Selection seed for `random` mode. Same seed + same filter set always picks the same row. Ignored in other modes.                                     |
| `index`          | `INT`     | `1`         | 1-based row index for `increment` mode. Auto-advances after each execute (wraps to 1 after the last row). Out-of-range values clamp to `[1, N]`. Ignored in other modes. |
| `quality`        | (enum)    | `Fast`      | `Fast` / `Balanced` / `High` / `Ultra`. See [resolution rules](load-from-folder.md#resolution-rules) — algorithm and per-tier pixel budgets are identical to Load From Folder. |
| `live_load`      | `BOOLEAN` | `True`      | If `True`, fetch from metascan, refresh the cache, and overwrite the `positive_prompt` / `negative_prompt` widgets with the fetched text. If `False`, reuse the cached image + name + path, take prompt text from the widgets, and recompute `width` / `height`. |
| `positive_prompt`| `STRING`  | *(empty)*   | Editable multiline text. On `live_load=True` this widget is overwritten with the fetched positive prompt; on `live_load=False` its contents are passed straight through to the `positive` output. |
| `negative_prompt`| `STRING`  | *(empty)*   | Editable multiline text. Same semantics as `positive_prompt` for the negative prompt.                                                                                                              |

### Selection modes

| Mode        | Picks which row?                                                                          | Driven by widget |
|-------------|-------------------------------------------------------------------------------------------|------------------|
| `random`    | `rows[seed % len(rows)]` — deterministic per seed.                                        | `seed`           |
| `by_name`   | The row whose `name` matches `prompt_name`. Case-sensitive exact match.                   | `prompt_name`    |
| `select`    | Same as `by_name`, but the 🖼 **Pick prompt** button opens a thumbnail picker overlay that writes the chosen name back into `prompt_name`. Pick visually instead of typing. | `prompt_name` (set via picker) |
| `increment` | `rows[index-1]` (1-based). After execute, `index` advances by 1 and wraps to 1 past the last row. Lets you step through a folder in order across runs. | `index`          |

The picker overlay (`select` mode and also usable as a shortcut in any mode) hits two ComfyUI-side proxy routes that this package registers — `GET /metscan/prompts` and `GET /metscan/thumbnail/<file_path>` — which then call metascan. Browsers never talk to metascan directly; the picker only works when ComfyUI itself can reach the metascan instance. If metascan is unreachable when you open the picker, the overlay shows an offline message rather than failing the workflow.

### Caching semantics

The node holds a per-instance cache containing the last-fetched `image`, `name`, and `source_file_path`. Prompt text is **not** cached — it lives in the editable `positive_prompt` / `negative_prompt` widgets and is persisted with the workflow JSON, so it survives a page reload. Width/height are also not cached — they're recomputed every execute so changing `quality` or `target_model` updates the sizing without re-fetching.

| `live_load` | Cache state | Output `positive`/`negative` | Behavior                                                                                       |
|-------------|-------------|------------------------------|------------------------------------------------------------------------------------------------|
| `True`      | any         | The fetched text             | Fetch from metascan, overwrite cache, push fetched text into the widgets, recompute resolution. |
| `False`     | populated   | The widget contents          | Skip all HTTP. Reuse cached image + name + path. Recompute resolution.                          |
| `False`     | empty       | —                            | Raise `RuntimeError("live_load is off but no cached prompt is available yet")`.                |

Two LoadPrompt nodes in the same workflow have **independent caches** (the cache is on the node instance, not module-level). The image cache resets on page reload or ComfyUI restart, so after reopening a saved workflow you'll need at least one `live_load=True` run to repopulate it before you can iterate offline — even though the prompt text in the widgets persisted.

Typical workflow: enable `live_load` once, run, then disable it and edit the `positive_prompt` / `negative_prompt` widgets directly to iterate on the prompt cheaply. While iterating you can also sweep `quality` (Fast → Balanced → High → Ultra) without re-hitting metascan.

## Outputs

| Name                | Type     | Description                                                                                                                                |
|---------------------|----------|--------------------------------------------------------------------------------------------------------------------------------------------|
| `image`             | `IMAGE`  | Source image the prompt was saved against. Tensor `[1, H, W, 3]`, float32, range `[0, 1]`. Decoded from metascan's stream bytes; RGBA → RGB. |
| `positive`          | `STRING` | The positive prompt text. Equal to the editable `positive_prompt` widget on `live_load=False`, or the fetched value (which simultaneously overwrites the widget) on `live_load=True`. |
| `negative`          | `STRING` | The negative prompt text. Same semantics as `positive`. SQL `NULL` from the metascan row is normalized to `""` on fetch.                                                              |
| `name`              | `STRING` | The saved-prompt's name (the unique-per-folder identifier shown in metascan's prompt library UI).                                          |
| `source_file_path`  | `STRING` | Path of the media file the prompt was saved against (metascan's view of it — WSL/remote path if applicable).                               |
| `width`             | `INT`    | Recommended generation width derived from the source image's dimensions, the chosen `target_model`, and `quality`.                         |
| `height`            | `INT`    | Recommended generation height. Same source.                                                                                                |

## Behavior notes

- `target_model = any` maps to `null` server-side in `POST /api/prompt/search`, so the API returns prompts from all models. The resolution calculator falls back to Flux-style rules for `any`.
- The `seed` input only affects `random` selection. In `by_name` and `select` modes the seed is ignored and the chosen name fully determines the row. In `increment` mode the seed is ignored and `index` drives selection.
- The companion `POST /api/prompt/search` endpoint hard-caps `limit` at 500 server-side; the node sends `limit=500`. Folders with more than 500 prompts may not surface the prompt you wanted via `random` / `increment` — narrow with `target_model` or use `by_name` / `select`.
- `increment` mode echoes the *next* index back to the widget via the ComfyUI `ui` channel, so opening the saved workflow on a fresh page lands on whatever index the last run advanced to.

## Common errors

- **"no saved prompts match the folder + target_model filter"**: search returned zero rows. Loosen `target_model` (try `any`) or check the folder actually contains saved prompts in metascan.
- **"no saved prompt named 'X' in the filtered set"**: `by_name` selection couldn't find the prompt. Names are case-sensitive exact-match. Check for trailing whitespace or rename mismatches.
- **"saved prompt 'X' has no source file path"**: the prompt row was saved without a `file_path` (rare, only possible if metascan's saver ever wrote a NULL there). Re-save the prompt from metascan against an actual media file.
- **"live_load is off but no cached prompt is available yet"**: you disabled `live_load` before ever running with it on. The widgets carry the prompt text but the *image* still has to come from a live fetch. Enable `live_load` once, run, then disable.
- **"Metascan is offline"**: same fix as the other nodes — bring metascan up or set the URL via [Settings](settings.md). If you have a populated cache, you can flip `live_load` off and keep working offline.
