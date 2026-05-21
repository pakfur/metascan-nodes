# metscan-nodes

ComfyUI custom nodes for the [metascan](https://github.com/pakfur/metascan) AI media browser.

Four nodes:

- **[Metascan · Save Image](docs/nodes/save-image.md)** — write a PNG batch into a metascan-watched directory. Metascan's filesystem watcher picks the file up on its own; no API call at save time.
- **[Metascan · Load From Folder](docs/nodes/load-from-folder.md)** — load a random / sequential / specific image from a metascan manual folder, with extracted prompts and a model-aware recommended `(width, height)` as bonus outputs.
- **[Metascan · Load Prompt](docs/nodes/load-prompt.md)** — load a saved prompt from metascan's prompt library plus its source image and recommended resolution. Supports per-instance caching so you can sweep quality tiers without re-fetching.
- **[Metascan · Settings](docs/nodes/settings.md)** — sentinel node for overriding URL / API key per workflow.

See the per-node docs above for inputs, outputs, errors, and the model-specific resolution rules.

## Install

Clone into ComfyUI's `custom_nodes` directory, then install three Python dependencies (`httpx`, `pillow`, `numpy`) *into the same Python environment ComfyUI itself uses*. The exact pip command depends on how ComfyUI is installed.

### ComfyUI Windows Portable

From a Command Prompt or PowerShell in the ComfyUI portable root (the folder containing `ComfyUI\`, `python_embeded\`, and the `run_*.bat` files):

```
cd ComfyUI\custom_nodes
git clone <this-repo-url> metscan-nodes
cd ..\..
.\python_embeded\python.exe -m pip install httpx pillow numpy
```

Portable ships an embedded interpreter — there's no venv to activate; you invoke `python_embeded\python.exe` by full path.

### ComfyUI installed via venv (Linux / macOS / Windows manual install)

Activate ComfyUI's venv first, then install:

```
source /path/to/ComfyUI/venv/bin/activate     # Linux / macOS
# or on Windows:    \path\to\ComfyUI\venv\Scripts\activate

cd /path/to/ComfyUI/custom_nodes
git clone <this-repo-url> metscan-nodes
pip install httpx pillow numpy
```

### After installing

Restart ComfyUI. The four nodes appear under the `metascan` category.

## Configure

Three ways to point the nodes at your metascan instance, in priority order:

1. Drop a **Metascan · Settings** node into your workflow with URL + API key.
2. Set env vars `METASCAN_URL` / `METASCAN_API_KEY` before launching ComfyUI.
3. Edit `~/.config/metscan-nodes/config.json`:

```json
{ "url": "http://localhost:8700", "api_key": "your-key-or-omit" }
```

If nothing is set, the nodes default to `http://localhost:8700` with no API key.

## Shared-host operations (ComfyUI + metascan on the same GPU)

These nodes do not load any models themselves — the design keeps GPU work out of the workflow path so the two processes don't fight over VRAM. However, metascan's *background* workers can still hold VRAM. If you run both on the same single GPU:

- Set `similarity.device = "cpu"` in metascan's `config.json` so its live CLIP inference uses CPU instead of holding VRAM.
- Don't trigger metascan VLM operations while ComfyUI is generating.
- If you queue an upscale in metascan, pause the queue (`POST /api/upscale/pause-all` or the UI's Pause button) while ComfyUI is busy, then resume when it's idle.
- If you have a second GPU, use `CUDA_VISIBLE_DEVICES` to pin metascan to it.

If the rigs collide, the failure mode is a clear `CUDA out of memory` in ComfyUI's log.

## Companion metascan changes

`Metascan · Load Prompt` depends on two endpoints added in a companion PR to the metascan repo:

- `POST /api/prompt/search`
- `GET /api/prompt/target-models`

Save Image and Load From Folder rely only on endpoints metascan already ships.

## Development

```
git clone <this-repo>
cd metscan-nodes
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
pytest tests/
```
