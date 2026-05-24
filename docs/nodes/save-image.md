# Metascan · Save Image

Save a batch of generated images into a directory metascan is already watching. Metascan's filesystem watcher (or its next manual scan) picks the file up automatically and indexes it — no API call happens at save time.

**See also:** For files already on disk (e.g. VHS Combine Video output), use [Metascan · Move Media](move-media.md).

## What it pulls from metascan

- **At node-load time (dropdown population):** `GET /api/config` to list the directories metascan is watching, so the `directory` dropdown only offers valid targets.
- **At execute time:** nothing. The save is a pure local file write.

## Inputs

| Name              | Type      | Default     | Description                                                                                                                                |
|-------------------|-----------|-------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| `images`          | `IMAGE`   | —           | Tensor batch from upstream (e.g. VAE Decode). Shape `[N, H, W, 3]`, float32 in `[0, 1]`.                                                   |
| `directory`       | (dropdown) | —          | One of metascan's watched directories. Populated from `/api/config`.                                                                       |
| `subpath`         | `STRING`  | *(empty)*   | Subdirectory under `directory`, with [strftime](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes) placeholders. e.g. `%Y-%m-%d/` → `2026-05-20/`. Created if missing. |
| `filename_prefix` | `STRING`  | `ComfyUI`   | Prefix for saved files. May contain forward slashes to create further subdirectories (e.g. `runs/sdxl` → files in `runs/sdxl/`). Saved as `<prefix>_NNNNN.png` with a collision-counter suffix. |
| `embed_workflow`  | `BOOLEAN` | `True`      | If true, embeds the workflow JSON as a tEXt chunk so the file can be drag-dropped back into ComfyUI to restore the graph. Always embeds the prompt JSON for metascan's extractor regardless of this flag. |

### Cross-platform path handling

If metascan runs in WSL (reporting `/mnt/d/...` paths) and ComfyUI runs on Windows, the node translates `/mnt/<drive>/...` to `<DRIVE>:\...` automatically. No-op on Linux/macOS.

### Filename collisions

The collision counter is `max(existing index) + 1`, not `len(existing)`. If `ComfyUI_00000.png` and `ComfyUI_00002.png` exist (gap from a deletion at index 1), the next save writes `ComfyUI_00003.png` — never overwrites a surviving file.

## Outputs

| Name        | Type     | Description                                                                                          |
|-------------|----------|------------------------------------------------------------------------------------------------------|
| `images`    | `IMAGE`  | The input tensor batch, passed through unchanged. Lets you chain another save/preview downstream.    |
| `file_path` | `STRING` | Absolute path of the **first** image written in this batch. Useful for downstream logging or notes.  |

The node is an `OUTPUT_NODE` and also renders saved-image thumbnails directly on the node face — same UX as ComfyUI's built-in Save Image. Thumbnails are lightweight PNG copies written into ComfyUI's temp directory; the canonical PNG (with prompt/workflow tEXt chunks) is what lands in the metascan-watched dir.

## Behavior notes

- Metascan is not contacted at execute time, so the save still works if metascan goes offline mid-workflow (as long as the directory dropdown was populated successfully when the node was first added to the graph).
- Metascan's filesystem watcher picks up the new file within seconds. The node does **not** trigger upscale, embedding, or VLM analysis — those run from metascan's own workflow if the user wants them.
- The embedded prompt tEXt chunk matches what ComfyUI core's SaveImage writes, so metascan's `enhanced_comfyui` extractor reads it correctly with no changes.

## Common errors

- **`'\mnt\d\...\file.png': No such file or directory` on Windows**: WSL-path translation is in place; this error means the path was *almost* native but the `/mnt/<drive>/` regex didn't match. Check that the directory comes from metascan and not from a manually-typed string.
- **`No such file or directory` with no path translation issue**: the subdirectory implied by `filename_prefix` (e.g. `runs/sdxl`) couldn't be created. Check write permissions on `directory`.
- **Metascan is offline — cannot resolve a watched directory**: the dropdown was populated from a stale cache (60s TTL) and metascan has since gone down. Either bring metascan up or restart ComfyUI's workflow page to repopulate the dropdown.
