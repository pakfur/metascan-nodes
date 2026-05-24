# Metascan · Move Media

Relocate any media file produced upstream — typically by VHS Combine Video, but any node that emits a `VHS_FILENAMES` socket works — into a directory metascan is already watching. Metascan's filesystem watcher (or its next manual scan) picks the file up automatically and indexes it — no API call happens at save time.

Pair with [Metascan · Save Image](save-image.md): SaveImage handles `IMAGE` tensors directly; MoveMedia handles files that already exist on disk.

## What it pulls from metascan

- **At node-load time (dropdown population):** `GET /api/config` to list the directories metascan is watching, so the `directory` dropdown only offers valid targets.
- **At execute time:** nothing. The relocation is a pure local file write.

## Inputs

| Name            | Type             | Default       | Description                                                                                                                                |
|-----------------|------------------|---------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| `filenames`     | `VHS_FILENAMES`  | —             | Typed list of paths from an upstream node. Carries `(save_flag, [paths])` — both image and video formats are supported.                    |
| `directory`     | (dropdown)       | —             | One of metascan's watched directories. Populated from `/api/config`.                                                                       |
| `subpath`       | `STRING`         | *(empty)*     | Subdirectory under `directory`, with [strftime](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes) placeholders. e.g. `%Y-%m-%d/` → `2026-05-23/`. Created if missing. |
| `operation`     | enum             | `move`        | `move` — source disappears (use when VHS wrote to its temp dir). `copy` — leaves the source in place too.                                  |
| `save_metadata` | enum             | `if_missing`  | `always` — overwrite container/PNG metadata with the current prompt + workflow. `if_missing` — only write if existing metadata is absent.   |

### Filename collisions

The collision counter is `max(existing index) + 1`, not `len(existing)`. The original basename from the upstream node is preserved when the destination is free; on collision, `_NN` is appended (zero-padded, 2 digits, widening to 3 past 99). A deletion-induced gap in the sequence does not cause us to reuse a name.

### Atomic writes

Each file is staged as `<name>.partial` in the destination dir, then `os.replace`d to the final name. Metascan's filesystem watcher never sees a half-written file. The same staging pattern protects against mid-write crashes on cross-device copies.

### Cross-platform path handling

If metascan runs in WSL (reporting `/mnt/d/...` paths) and ComfyUI runs on Windows, both `directory` and each source path get translated from `/mnt/<drive>/...` to `<DRIVE>:\...` automatically. No-op on Linux/macOS.

## Metadata dispatch (per-extension)

| Extension                              | Tool                                  | `always`                                  | `if_missing`                                                                |
|----------------------------------------|---------------------------------------|-------------------------------------------|-----------------------------------------------------------------------------|
| `.png`                                  | PIL                                   | Re-save with new `prompt` / `workflow` tEXt chunks | Skip if either tEXt chunk is already present                       |
| `.mp4` `.mov` `.mkv` `.webm` `.gif`    | ffmpeg `-c copy -metadata comment=…`  | Re-mux with new JSON-encoded comment      | ffprobe first; skip if `comment`, `prompt`, or `workflow` tag present       |
| anything else                          | —                                     | No-op (debug log only)                    | No-op (debug log only)                                                      |

The video re-mux uses `-c copy` — no re-encode, fast and lossless. The `comment=` key matches what VHS writes, so metascan's video extractor reads either without changes.

### ffmpeg is best-effort

If `ffmpeg` is not on `PATH`, the move/copy still succeeds — metadata embedding is logged as skipped and the per-file UI text on the node face shows `[skipped_no_ffmpeg]`. The save never fails because metadata can't be written. If you're using VHS Combine upstream, you already have ffmpeg installed (VHS requires it) and no extra setup is needed.

If ffmpeg exits non-zero on a particular file, the relocated file is kept (it's already in the metascan dir at that point) and the metadata step is logged as `[skipped_error]` with a tail of ffmpeg's stderr in the Python log.

## Outputs

| Name        | Type             | Description                                                                                                |
|-------------|------------------|------------------------------------------------------------------------------------------------------------|
| `filenames` | `VHS_FILENAMES`  | `(save_flag, [new_paths])`. The `save_flag` is passed through unchanged; paths are the post-move locations.|
| `file_path` | `STRING`         | Absolute path of the **first** relocated file. Useful for downstream logging or notes.                     |

The node is an `OUTPUT_NODE`. It surfaces one text line per file on the node face:

```
move /tmp/comfy/clip_00001.mp4 → /data/comfy-out/2026-05/clip_00001.mp4 [embedded]
```

The status in brackets is one of `embedded`, `skipped_present`, `skipped_unsupported`, `skipped_no_ffmpeg`, or `skipped_error`.

## Typical workflow

1. **VHS Combine Video** with `save_output = False` and a `filename_prefix` you don't care about — writes to ComfyUI's temp dir.
2. Wire its `VHS_FILENAMES` output into **Metascan · Move Media**.
3. Pick a metascan-watched `directory`; optionally set a `subpath` like `%Y-%m`.
4. Leave `operation = move` and `save_metadata = if_missing` for the default workflow.
5. Run. Metascan's watcher picks the file up from its watched dir; the prompt/workflow JSON travels in the container metadata so metascan's extractor reads it.

## Common errors

- **`Metascan is offline — cannot resolve a watched directory`** — the dropdown was populated from a stale cache (60s TTL) and metascan has since gone down. Bring metascan back up, or restart the workflow page to repopulate the dropdown.
- **`MetascanMoveMedia: source not found: <path>`** — the upstream node reported a path it didn't actually write, or wrote to a path that's already been moved. Check the upstream node's `save_output` and `filename_prefix` settings.
- **`OSError` on destination write** — the metascan-watched directory isn't writable from ComfyUI's user. Check filesystem permissions on the target dir.

## Behavior notes

- Metascan is not contacted at execute time, so relocation works even if metascan goes offline mid-workflow (as long as the directory dropdown was populated successfully when the node was added).
- PNG re-saves through PIL are lossless but re-encode the file bytes; existing image quality and bit depth are preserved.
- Empty `filenames` input (e.g. VHS legitimately skipped output) is not an error — the node returns an empty list and logs a debug line.
