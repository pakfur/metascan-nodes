# Metascan · Select Prompt

Visually pick a saved prompt from metascan: the node face shows a 🖼 **Pick prompt…** button that opens a thumbnail overlay filtered by the chosen `folder` + `target_model`. Click a thumbnail → the prompt's `name`, source `file_path`, positive text, and negative text all populate as widget values. At workflow run time the node streams the source image and computes a target `(width, height)` for the chosen `quality`.

This is a deliberately simpler sibling of [Metascan · Load Prompt](load-prompt.md). There's no `selection_mode`, no `live_load`, and no `seed` / `index` — picking implies loading, and your edits to the positive / negative text are the canonical truth from pick-time onward.

## What it pulls from metascan

- **At node-load time:** `GET /api/folders` and `GET /api/prompt/target-models` (cached) to populate the two dropdowns.
- **On 🖼 Pick prompt:** `GET /metscan/prompts?folder=&target_model=` (ComfyUI-side proxy → metascan's `POST /api/prompt/search`) for the row list, plus `GET /metscan/thumbnail?file_path=…` per visible row.
- **At execute time:** `GET /api/stream/{source_file_path}` for the source image bytes. Cached per-instance, so reruns with the same picked prompt skip the network.

The browser never talks to metascan directly — the two `/metscan/*` proxy routes run inside ComfyUI so auth and reachability stay server-side.

## Inputs

| Name              | Type       | Default   | Description                                                                                                       |
|-------------------|------------|-----------|-------------------------------------------------------------------------------------------------------------------|
| `folder`          | (dropdown) | —         | Manual folder name. Filters which prompts appear in the picker overlay.                                            |
| `target_model`    | (dropdown) | —         | Diffusion model filter (`sd`, `pony`, `flux1`, `flux2`, `zimage`, `chroma`, `qwen`) or `any`. Also drives resolution. |
| `prompt_name`     | `STRING`   | *(empty)* | Hidden widget. Set by the picker; serialized with the workflow so the pick survives reload.                       |
| `source_file_path`| `STRING`   | *(empty)* | Hidden widget. Set by the picker. The execute-time image fetch reads from here directly.                          |
| `quality`         | (enum)     | `Fast`    | `Fast` / `Balanced` / `High` / `Ultra`. See [resolution rules](load-from-folder.md#resolution-rules).             |
| `positive_prompt` | `STRING`   | *(empty)* | Multiline editable. Populated by the picker; edits persist and pass straight through to the `positive` output.    |
| `negative_prompt` | `STRING`   | *(empty)* | Multiline editable. Same semantics as `positive_prompt` for the negative prompt.                                  |

The 🖼 picker button is added by the frontend extension and is not a serialized widget — it's an action that writes into the widgets above.

## Outputs

Same shape as [Load Prompt](load-prompt.md):

| Name               | Type     | Description                                                                                       |
|--------------------|----------|---------------------------------------------------------------------------------------------------|
| `image`            | `IMAGE`  | Source image. Tensor `[1, H, W, 3]`, float32, range `[0, 1]`.                                     |
| `positive`         | `STRING` | The current `positive_prompt` widget value verbatim.                                              |
| `negative`         | `STRING` | The current `negative_prompt` widget value verbatim.                                              |
| `name`             | `STRING` | The picked prompt's name (from `prompt_name`).                                                    |
| `source_file_path` | `STRING` | Path of the media file the prompt was saved against.                                              |
| `width`            | `INT`    | Recommended generation width.                                                                     |
| `height`           | `INT`    | Recommended generation height.                                                                    |

## Caching semantics

The image is cached on the node instance, keyed by `source_file_path`. Reruns with the same picked prompt do not re-stream the image — only `width` / `height` are recomputed from the current `quality` + `target_model`. Pick a different prompt → cache miss → one new stream call.

The cache resets on page reload or ComfyUI restart. After reopening a saved workflow, the first run streams the source image once; subsequent runs reuse it.

## Common errors

- **"No prompt selected — open the 🖼 Pick prompt picker to choose one."**: the workflow ran before any pick happened. Click the picker, choose a row, then run.
- **"Metascan is offline"**: bring metascan up or set the URL via [Settings](settings.md). The picker overlay also surfaces this when you click it while metascan is unreachable.
- **Picker shows "No prompts match this folder + target_model"**: loosen `target_model` (try `any`) or confirm the folder actually contains saved prompts.

## When to use Load Prompt vs. Select Prompt

| Use case                                                    | Node              |
|-------------------------------------------------------------|-------------------|
| Iterate through a folder in order (1, 2, 3, …)              | Load Prompt (`increment` mode) |
| Pick a random prompt per seed                               | Load Prompt (`random` mode)    |
| Type a known name into a text field                         | Load Prompt (`by_name` mode)   |
| Pick visually from thumbnails, then tweak positive/negative | **Select Prompt**              |
