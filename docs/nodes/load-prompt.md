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
- **At execute time when `live_load = False`:** nothing. The cached values from the most recent live fetch are reused; only `width` / `height` are recomputed from the current `quality` / `target_model`.

This node depends on the companion metascan endpoints `POST /api/prompt/search` and `GET /api/prompt/target-models`, which were added in a separate PR to the metascan repo.

## Inputs

| Name             | Type      | Default     | Description                                                                                                                                          |
|------------------|-----------|-------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| `folder`         | (dropdown) | —          | Manual folder name. Saved prompts are searched within this folder.                                                                                   |
| `target_model`   | (dropdown) | —          | Filter to one diffusion model (`sd`, `pony`, `flux1`, `flux2`, `zimage`, `chroma`, `qwen`) or `any` to match any model. Also drives resolution rules. |
| `selection_mode` | (enum)    | `random`    | `random` returns `rows[seed % N]`. `by_name` returns the row whose `name` matches `prompt_name`.                                                     |
| `prompt_name`    | `STRING`  | *(empty)*   | Required when `selection_mode = by_name`. Ignored in `random` mode.                                                                                  |
| `seed`           | `INT`     | `0`         | Selection seed for `random` mode. Same seed + same filter set always picks the same row.                                                             |
| `quality`        | (enum)    | `Fast`      | `Fast` / `Balanced` / `High` / `Ultra`. See [resolution rules](load-from-folder.md#resolution-rules) — algorithm and per-tier pixel budgets are identical to Load From Folder. |
| `live_load`      | `BOOLEAN` | `True`      | If `True`, fetch from metascan and refresh the cache. If `False`, reuse the cached image + prompt strings and only recompute `width` / `height`.     |

### Caching semantics

The node holds a per-instance cache containing the last-fetched `image`, `positive`, `negative`, `name`, and `source_file_path`. Width/height are deliberately **not** cached — they're recomputed every execute so changing `quality` or `target_model` updates the sizing without re-fetching.

| `live_load` | Cache state | Behavior                                                                 |
|-------------|-------------|--------------------------------------------------------------------------|
| `True`      | any         | Fetch from metascan, overwrite cache, compute resolution from new image. |
| `False`     | populated   | Skip all HTTP. Reuse cached image + strings. Recompute resolution.       |
| `False`     | empty       | Raise `RuntimeError("live_load is off but no cached prompt is available yet")`. |

Two LoadPrompt nodes in the same workflow have **independent caches** (the cache is on the node instance, not module-level). Cache survives across ComfyUI executions but resets if you reload the page or restart ComfyUI.

Typical workflow: enable `live_load` for the first run to populate the cache, then disable it to sweep `Fast` → `Balanced` → `High` → `Ultra` cheaply.

## Outputs

| Name                | Type     | Description                                                                                                                                |
|---------------------|----------|--------------------------------------------------------------------------------------------------------------------------------------------|
| `image`             | `IMAGE`  | Source image the prompt was saved against. Tensor `[1, H, W, 3]`, float32, range `[0, 1]`. Decoded from metascan's stream bytes; RGBA → RGB. |
| `positive`          | `STRING` | The saved positive prompt text. `prompt` field on the prompt row.                                                                          |
| `negative`          | `STRING` | The saved negative prompt text. `negative` field on the row, with SQL `NULL` normalized to `""`.                                           |
| `name`              | `STRING` | The saved-prompt's name (the unique-per-folder identifier shown in metascan's prompt library UI).                                          |
| `source_file_path`  | `STRING` | Path of the media file the prompt was saved against (metascan's view of it — WSL/remote path if applicable).                               |
| `width`             | `INT`    | Recommended generation width derived from the source image's dimensions, the chosen `target_model`, and `quality`.                         |
| `height`            | `INT`    | Recommended generation height. Same source.                                                                                                |

## Behavior notes

- `target_model = any` maps to `null` server-side in `POST /api/prompt/search`, so the API returns prompts from all models. The resolution calculator falls back to Flux-style rules for `any`.
- The `seed` input only affects `random` selection — in `by_name` mode the seed is ignored, and the same `prompt_name` always selects the same prompt deterministically.
- The companion `POST /api/prompt/search` endpoint hard-caps `limit` at 500 server-side; the node sends `limit=500`. Folders with more than 500 prompts may not surface the prompt you wanted via `random` — narrow with `target_model` or use `by_name`.

## Common errors

- **"no saved prompts match the folder + target_model filter"**: search returned zero rows. Loosen `target_model` (try `any`) or check the folder actually contains saved prompts in metascan.
- **"no saved prompt named 'X' in the filtered set"**: `by_name` selection couldn't find the prompt. Names are case-sensitive exact-match. Check for trailing whitespace or rename mismatches.
- **"saved prompt 'X' has no source file path"**: the prompt row was saved without a `file_path` (rare, only possible if metascan's saver ever wrote a NULL there). Re-save the prompt from metascan against an actual media file.
- **"live_load is off but no cached prompt is available yet"**: you disabled `live_load` before ever running with it on. Enable once, run, then disable.
- **"Metascan is offline"**: same fix as the other nodes — bring metascan up or set the URL via [Settings](settings.md). If you have a populated cache, you can flip `live_load` off and keep working offline.
