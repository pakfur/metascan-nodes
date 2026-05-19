# Example workflows

Three ComfyUI workflow JSONs demonstrate each node end-to-end. They are best generated from a running ComfyUI install:

1. Place each node in the editor, wire it up to a checkpoint loader + KSampler + VAE decode (Save flow), VAE encode + sampler (Load From Folder flow), or CLIPTextEncode + sampler (Load Prompt flow).
2. Click **Workflow → Save** in ComfyUI and commit the resulting JSON under one of these filenames:

- `save_and_pickup.json` — checkpoint loader → KSampler → VAE decode → MetascanSaveImage
- `load_and_generate.json` — MetascanLoadFromFolder → VAE encode → KSampler (img2img)
- `load_prompt_chain.json` — MetascanLoadPrompt → CLIPTextEncode (×2) → KSampler

The exact node IDs don't matter; ComfyUI's loader is tolerant of any saved workflow shape.

See `../tests/SMOKE.md` for the manual walkthrough that uses these workflows.
