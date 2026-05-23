# Metascan · Select Image

Visually pick any image from a metascan folder — for I2I (image-to-image) and I2V (image-to-video) workflows where you want a reference image, not a saved prompt. The node face shows the picked image **immediately** (before any workflow run), so you can see what you're writing prompts against.

This is a trimmed sibling of [Metascan · Select Prompt](select-prompt.md). Differences:

- Picks from **all images in the folder**, not just images that have a saved prompt in metascan.
- No `positive` / `negative` outputs — there's no prompt state on this node.
- Still uses `target_model` + `quality` for the same `width` / `height` rules as the other load nodes.

## What it pulls from metascan

- **At node-load time:** `GET /api/folders` (cached) and `GET /api/prompt/target-models` (cached) for the two dropdowns.
- **On 🖼 Pick image:** `GET /metscan/images?folder=` (ComfyUI-side proxy → `GET /api/folders/{id}` for the folder's items) for the list, plus `GET /metscan/thumbnail?file_path=…` per visible row.
- **On pick (immediate preview):** `GET /metscan/thumbnail?file_path=…` once more, rendered on the node face via `node.imgs`.
- **At execute time:** `GET /api/stream/{source_file_path}` for the full source bytes. Cached per-instance so reruns with the same pick skip the network.

The browser never talks to metascan directly — both `/metscan/*` proxy routes run inside ComfyUI so auth and reachability stay server-side.

## Image filter

Items in the folder are filtered by extension: `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`, `.tif`, `.tiff`, `.heic`, `.heif`. Videos and other non-image items don't appear in the picker — Select Image's downstream is always a still-image decode.

## Inputs

| Name              | Type       | Default   | Description                                                                                                       |
|-------------------|------------|-----------|-------------------------------------------------------------------------------------------------------------------|
| `folder`          | (dropdown) | —         | Manual folder. The picker lists all image-typed items inside.                                                      |
| `target_model`    | (dropdown) | —         | Diffusion model that drives the `width` / `height` resolution rules. Same options as the other load nodes.        |
| `quality`         | (enum)     | `Fast`    | `Fast` / `Balanced` / `High` / `Ultra`. See [resolution rules](load-from-folder.md#resolution-rules).             |
| `image_name`      | `STRING`   | *(empty)* | Hidden widget. Set by the picker to the image's filename; serialized so the pick survives a page reload.          |
| `source_file_path`| `STRING`   | *(empty)* | Hidden widget. Set by the picker. The execute-time image fetch reads from here directly.                          |

The 🖼 picker button is added by the frontend extension and isn't a serialized widget — it's the action that writes into the hidden widgets and sets the node-face preview.

## Outputs

| Name               | Type     | Description                                                                                       |
|--------------------|----------|---------------------------------------------------------------------------------------------------|
| `image`            | `IMAGE`  | Source image. Tensor `[1, H, W, 3]`, float32, range `[0, 1]`.                                     |
| `name`             | `STRING` | Image filename (basename of `source_file_path`).                                                  |
| `source_file_path` | `STRING` | Full metascan path of the picked image.                                                           |
| `width`            | `INT`    | Recommended generation width (`compute_resolution` for the chosen `target_model` + `quality`).    |
| `height`           | `INT`    | Recommended generation height.                                                                    |

## Node-face preview

When you pick an image, the JS extension sets `node.imgs = [thumbnail]` — the same hook ComfyUI's built-in `LoadImage` and `PreviewImage` nodes use. The image renders on the node face below the widgets, immediately, without running the workflow. Saved workflows restore the preview from the persisted `source_file_path` on reload.

Strictly speaking the `IMAGE` output pin still only fires on execute (that's how ComfyUI graph execution works) — downstream nodes don't receive the tensor until the workflow runs. The preview is purely a visual aid for the user while they write their I2I / I2V prompts.

## Common errors

- **"No image selected — open the 🖼 Pick image picker to choose one."**: the workflow ran before any pick happened. Open the picker, click a row, then run.
- **"Metascan is offline"**: bring metascan up or set the URL via [Settings](settings.md).
- **Picker shows "No images in this folder."**: the folder is empty, or contains only videos / non-image files.

## When to use which load node

| Use case                                                          | Node                                |
|-------------------------------------------------------------------|-------------------------------------|
| Iterate through prompts in order (1, 2, 3, …)                     | [Load Prompt](load-prompt.md) (`increment` mode) |
| Pick a random prompt per seed                                     | [Load Prompt](load-prompt.md) (`random` mode)    |
| Type a known prompt name into a text field                        | [Load Prompt](load-prompt.md) (`by_name` mode)   |
| Pick a prompt visually + tweak its positive/negative text         | [Select Prompt](select-prompt.md)                |
| Pick **any image** from a folder (for I2I / I2V), see it on the node | **Select Image**                                 |
