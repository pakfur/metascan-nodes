# Metascan · Load From Folder

Load one image from a metascan **manual folder**, plus the prompt metadata metascan extracted from it during scan, plus a recommended `(width, height)` for image generation sized to the target diffusion model and quality tier.

Smart folders are intentionally not supported in MVP — their rule engine lives in metascan's frontend and isn't reachable from the API.

## What it pulls from metascan

- **At node-load time:**
  - `GET /api/folders` → manual folder list for the `folder` dropdown.
  - `GET /api/prompt/target-models` → list of supported diffusion models for the `target_model` dropdown.
- **At execute time:**
  - `GET /api/folders/{id}` → resolves the chosen folder name to the list of items it contains.
  - `GET /api/media/{path}` → metadata for the chosen item, including extracted positive/negative prompt under `data`.
  - `GET /api/stream/{path}` → raw image bytes, decoded to a tensor locally.

## Inputs

| Name              | Type      | Default       | Description                                                                                                                  |
|-------------------|-----------|---------------|------------------------------------------------------------------------------------------------------------------------------|
| `folder`          | (dropdown) | —            | Manual folder name from metascan. Smart folders are filtered out.                                                            |
| `selection_mode`  | (enum)    | `random`      | `random` = `paths[seed % N]`; `sequential` = same as random but returns `seed+1` so chaining advances; `specific` = use `index`. |
| `seed`            | `INT`     | `0`           | Selection seed (used by `random` and `sequential`).                                                                          |
| `index`           | `INT`     | `0`           | 0-based position used only when `selection_mode = specific`.                                                                 |
| `filename_filter` | `STRING`  | *(empty)*     | Substring match on the filename. Empty matches everything.                                                                   |
| `image_only`      | `BOOLEAN` | `True`        | Drop videos (`.mp4 .mov .mkv .webm .avi`) from the candidate list.                                                           |
| `target_model`    | (dropdown) | —            | Diffusion model for resolution rules. Options: `sd`, `pony`, `flux1`, `flux2`, `zimage`, `chroma`, `qwen`, `any`. See [resolution rules](#resolution-rules). |
| `quality`         | (enum)    | `Fast`        | `Fast` / `Balanced` / `High` / `Ultra`. Controls the target pixel budget. See [resolution rules](#resolution-rules).         |

### Selection mechanics

The candidate list is filtered (`image_only`, `filename_filter`) then **sorted lexicographically** before selection, so selection-by-seed is reproducible across runs even when metascan's listing order varies.

### Resolution rules

The `width` / `height` outputs are computed per-model using the user-provided rules table. Algorithm:

1. Look up the model's spec — divisor (8/16/32/64), per-tier pixel budgets, optional preset bucket lists.
2. If the model has a preset bucket list at the chosen tier (Qwen Balanced, Z-Image Balanced/High), pick the bucket whose aspect ratio is closest (in log space) to the source image's aspect ratio.
3. Otherwise: compute `(w, h)` from target pixels + source aspect, snap each side to the model's divisor.

`target_model = any` falls back to Flux-style rules: ~1 MP target, multiples of 16. Unknown model strings also fall back to `any`.

Tier sizing (representative pixel counts, actual W×H depends on source aspect):

| Tier      | sd/pony     | flux1       | flux2       | chroma      | zimage              | qwen                          | any         |
|-----------|-------------|-------------|-------------|-------------|---------------------|-------------------------------|-------------|
| Fast      | 512²        | 512²        | 512²        | 512²        | 512² (computed)     | 1024² (computed)              | 512²        |
| Balanced  | 1024²       | 1024²       | 1024²       | 1024²       | 1024-grid bucket    | **1328² official bucket**     | 1024²       |
| High      | 1280²       | 1280²       | 1664²       | 1280²       | 1280-grid bucket    | 1664² (computed)              | 1280²       |
| Ultra     | 1536²       | 1440²       | 2048²       | 1536²       | 1536² (computed)    | 2048² (computed, Qwen 2.0 2K) | 1536²       |

## Outputs

| Name         | Type     | Description                                                                                                                                  |
|--------------|----------|----------------------------------------------------------------------------------------------------------------------------------------------|
| `image`      | `IMAGE`  | Loaded image as a ComfyUI tensor: `[1, H, W, 3]`, float32, range `[0, 1]`. RGBA sources are converted to RGB.                                |
| `file_path`  | `STRING` | Absolute path of the chosen file as metascan reports it. On WSL/remote-metascan, this is the WSL-side or remote-side path, not the local one. |
| `positive`   | `STRING` | Positive prompt extracted from the file's embedded metadata (`data.prompt` in metascan's media detail). Empty string if not present.         |
| `negative`   | `STRING` | Negative prompt (`data.negative_prompt`). Empty string if not present.                                                                       |
| `next_seed`  | `INT`    | For `sequential` mode, this is `(seed + 1) % N` — wire back into `seed` to walk the folder. For `random`, returns the input seed unchanged. For `specific`, returns the input `index`. |
| `width`      | `INT`    | Recommended generation width for `target_model` + `quality`. Wire into `EmptyLatentImage`.                                                   |
| `height`     | `INT`    | Recommended generation height. Same source.                                                                                                  |

## Behavior notes

- The whole node always re-fetches on each execute. There's no cache toggle here — use [Load Prompt](load-prompt.md) for that workflow.
- Empty filtered list raises `RuntimeError("no matching items in folder")` — surfaces directly in ComfyUI's error panel.
- Folder name resolution is by exact match against metascan's `folder.name`. Renaming a folder in metascan invalidates existing workflows.

## Common errors

- **"no matching items in folder"**: filter is too strict, the folder is empty, or `image_only=True` and the folder only has videos.
- **"folder not found in metascan"**: the previously-selected folder was deleted or renamed in metascan. Re-pick from the dropdown.
- **"Metascan is offline"**: the dropdown shows the offline sentinel and you tried to execute. Bring metascan up or correct the URL via [Settings](settings.md).
