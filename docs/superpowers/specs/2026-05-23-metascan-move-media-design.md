# Spec: MetascanMoveMedia — relocate any media file into a metascan-watched directory

**Date:** 2026-05-23
**Scope:** New `mscan_nodes/move_media.py`; new shared helpers module `mscan_nodes/_shared.py` (lifted from `save_image.py`); updates to `mscan_nodes/save_image.py` (re-imports only), `__init__.py` (registration), `README.md`, `docs/nodes/save-image.md` (see-also link); new `docs/nodes/move-media.md`; new `tests/test_move_media.py`.
**Out of scope:** Re-encoding or format conversion (we re-mux stream-copy only); video preview rendering on the node face; bulk batch moves to multiple destinations; sidecar `.json` files for formats with no container metadata slot; any input source other than `VHS_FILENAMES`.

## Motivation

Today `MetascanSaveImage` is the only way to deposit ComfyUI-produced media into a metascan-watched directory. It works for image tensors (`IMAGE`) but not for the typical video pipeline, where VHS Combine Video (or another node) produces an encoded file on disk and emits a `VHS_FILENAMES` socket.

The user wants the same UX — pick a metascan-watched directory from a dropdown, optionally land under a strftime-expanded subpath, embed prompt/workflow metadata so metascan's extractor reads it — but applied to files that already exist on disk. A two-node workflow (VHS Combine → MetascanMoveMedia) keeps encoding inside VHS and relocation inside metascan, which avoids re-implementing ffmpeg orchestration or welding to VHS's internal class layout.

The node is format-agnostic by design: relocation works on any extension, and metadata embedding dispatches per extension (PNG via PIL, video containers via ffmpeg, unknown formats no-op).

## Design overview

```
                                ┌──────────────────────────────────┐
                                │ MetascanMoveMedia (node)         │
VHS_FILENAMES ───────────────▶  │                                  │
  (save_flag, [paths])          │  required inputs                 │
                                │    directory      (dropdown)     │
                                │    subpath        STRING         │
                                │    operation      [move, copy]   │
                                │    save_metadata  [always,       │
                                │                    if_missing]   │
                                │                                  │
                                │  hidden: prompt, extra_pnginfo   │
                                │                                  │
                                │  process()                       │
                                │   for each path in filenames:    │
                                │     1. relocate to target_dir    │
                                │        via .partial → rename     │
                                │     2. dispatch_metadata(ext)    │
                                │        PNG / video / no-op       │
                                │                                  │
                                │  outputs:                        │
                                │    filenames (VHS_FILENAMES)     │
                                │    file_path (STRING — first)    │
                                │                                  │
                                │  ui: text lines per file         │
                                └──────────────────────────────────┘
```

## Shared-helpers refactor — `mscan_nodes/_shared.py`

Lift the following symbols from `save_image.py` into a new module so both nodes share one source of truth:

- `_WSL_MNT_RE`, `wsl_to_native_path`
- `resolve_target_dir`
- `_utc_now`
- `_build_client`
- `_comfy_temp_dir`

`save_image.py` re-imports them (`from mscan_nodes._shared import …`). Public-API shape unchanged: existing tests in `tests/test_save_image.py` continue to pass without modification (verified by running the suite after the move).

PIL-specific helpers (`tensor_to_pil`, `build_png_info`) stay in `save_image.py` — they're not needed by `move_media.py`. Image-file metadata writes in MoveMedia use PIL directly, but go through MoveMedia's own `embed_png_metadata` helper because the semantics differ: SaveImage *creates* a PNG; MoveMedia *re-writes* an existing one with mode-dependent overwrite behavior.

## Backend — `mscan_nodes/move_media.py`

### Class signature

```python
class MetascanMoveMedia:
    CATEGORY = "metascan"
    OUTPUT_NODE = True
    RETURN_TYPES = ("VHS_FILENAMES", "STRING")
    RETURN_NAMES = ("filenames", "file_path")
    FUNCTION = "process"
```

### `INPUT_TYPES`

```python
@classmethod
def INPUT_TYPES(cls) -> dict:
    try:
        dirs = combo_directories(_build_client())
    except Exception:  # noqa: BLE001 — defensive at editor-load time
        dirs = [OFFLINE_SENTINEL]
    return {
        "required": {
            "filenames": ("VHS_FILENAMES",),
            "directory": (dirs,),
            "subpath": ("STRING", {"default": ""}),
            "operation": (["move", "copy"], {"default": "move"}),
            "save_metadata": (["always", "if_missing"], {"default": "if_missing"}),
        },
        "hidden": {
            "prompt": "PROMPT",
            "extra_pnginfo": "EXTRA_PNGINFO",
        },
    }
```

Same defensive `try/except` shape as `MetascanSaveImage` so the editor still loads when metascan is offline.

### `process()` signature

```python
def process(
    self,
    filenames,                              # (save_flag, [paths]) per VHS convention
    directory: str,
    subpath: str,
    operation: str,
    save_metadata: str,
    prompt: Optional[dict] = None,
    extra_pnginfo: Optional[dict] = None,
) -> dict:
```

### Execute-time flow

1. **Offline guard.** If `directory == OFFLINE_SENTINEL`, raise the same `RuntimeError` `MetascanSaveImage` raises.
2. **Unpack input.** `save_flag, paths = filenames` (tolerate `paths` being any iterable; coerce to `list`). If `paths` is empty, return `{"ui": {"text": ["no files to move"]}, "result": ((save_flag, []), "")}` — no error, debug-log.
3. **Resolve target directory.** `target_dir = resolve_target_dir(directory, subpath, _utc_now())` — strftime expansion + mkdir, same as SaveImage.
4. **Extract workflow blob.** `workflow_dict = extra_pnginfo.get("workflow") if isinstance(extra_pnginfo, dict) else None` — matches SaveImage's pattern. Always pass `prompt` through; only `workflow` is conditional.
5. **Per-file loop:**
   - `src_native = wsl_to_native_path(src)` — also translate the source path, not just the destination.
   - `new_path = relocate_file(src_native, target_dir, operation)` — see helper spec below.
   - `metadata_status = dispatch_metadata(new_path, prompt, workflow_dict, save_metadata)` — returns one of `"embedded"`, `"skipped_present"`, `"skipped_unsupported"`, `"skipped_no_ffmpeg"`, `"skipped_error"`.
   - Append a UI text line: `f"{operation} {src} → {new_path} [{metadata_status}]"`.
6. **Return.**

   ```python
   return {
       "ui": {"text": ui_lines},
       "result": ((save_flag, new_paths), str(new_paths[0]) if new_paths else ""),
   }
   ```

### Helper: `relocate_file(src, dst_dir, operation) -> Path`

Pure function, no ComfyUI imports.

```python
def relocate_file(src: Path, dst_dir: Path, operation: str) -> Path:
    """Move or copy `src` into `dst_dir`, returning the final path.

    Writes to a `.partial` staging name and `os.replace`s to the final
    name so a filesystem watcher (or a mid-write crash) never sees a
    half-written file. On destination name collision, appends `_NN`
    (zero-padded, 2 digits) using max-existing-index + 1.
    """
```

Behavior:
- Compute `final_name` from `src.name`. If `dst_dir / final_name` exists, find collision-counter free name: stem becomes `<stem>_NN`, where `NN` is `max(existing index for that stem) + 1`, two-digit zero-padded. Same `max + 1` strategy as `MetascanSaveImage` (never overwrite a surviving file). Width = 2 (00–99) is enough for normal collision rates; if 99 is exhausted, widen to 3 (this is an edge case worth a defensive test).
- `staging = dst_dir / (final_name + ".partial")`.
- `operation == "move"`: `shutil.move(src, staging)`. Source vanishes (or is copy+deleted by shutil for cross-device).
- `operation == "copy"`: `shutil.copy2(src, staging)`. Preserves mtime/permissions.
- `os.replace(staging, dst_dir / final_name)`. Atomic on POSIX same-filesystem; on Windows `os.replace` is also atomic for same-volume.
- Return the final `Path`.

### Helper: `dispatch_metadata(path, prompt, workflow, mode) -> str`

Pure dispatch by lowercase suffix:

```python
def dispatch_metadata(path: Path, prompt, workflow, mode: str) -> str:
    ext = path.suffix.lower()
    if ext == ".png":
        return embed_png_metadata(path, prompt, workflow, mode)
    if ext in {".mp4", ".mov", ".mkv", ".webm", ".gif"}:
        return embed_video_metadata(path, prompt, workflow, mode)
    return "skipped_unsupported"
```

Returns the status string used in the UI text line and log messages. `.gif` belongs in the video bucket because VHS emits animated GIFs through ffmpeg and ffmpeg-style comment metadata round-trips on GIF containers via the `Comment` extension block.

### Helper: `embed_png_metadata(path, prompt, workflow, mode) -> str`

```python
def embed_png_metadata(path: Path, prompt, workflow, mode: str) -> str:
    img = Image.open(path)
    existing = img.info  # PIL surfaces tEXt chunks as a dict
    if mode == "if_missing" and ("prompt" in existing or "workflow" in existing):
        return "skipped_present"
    info = PngInfo()
    if prompt is not None:
        info.add_text("prompt", json.dumps(prompt))
    if workflow is not None:
        info.add_text("workflow", json.dumps(workflow))
    img.load()  # decode before we close the underlying file
    staging = path.with_suffix(path.suffix + ".meta.partial")
    img.save(staging, pnginfo=info, format="PNG")
    os.replace(staging, path)
    return "embedded"
```

Two-step `.meta.partial` → `os.replace` here for the same reason as the relocation step.

### Helper: `embed_video_metadata(path, prompt, workflow, mode) -> str`

```python
def embed_video_metadata(path: Path, prompt, workflow, mode: str) -> str:
    if shutil.which("ffmpeg") is None:
        return "skipped_no_ffmpeg"
    if mode == "if_missing":
        if _video_has_metadata(path):       # uses ffprobe; returns False if ffprobe missing
            return "skipped_present"
    payload = json.dumps({"prompt": prompt, "workflow": workflow})
    staging = path.with_suffix(path.suffix + ".meta.partial")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(path),
        "-c", "copy",
        "-metadata", f"comment={payload}",
        str(staging),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffmpeg metadata embed failed for %s: %s", path, _tail_stderr(exc))
        staging.unlink(missing_ok=True)
        return "skipped_error"
    os.replace(staging, path)
    return "embedded"
```

`_video_has_metadata(path)` shells out to `ffprobe -show_format -of json` and inspects `format.tags` for any of `comment`, `prompt`, `workflow` (case-insensitive). If `ffprobe` is missing, return `False` (which causes "if_missing" mode to attempt embed; ffmpeg-missing check above stops it from running, so the result is `"skipped_no_ffmpeg"`).

`_tail_stderr(exc)` returns the last ~500 bytes of `exc.stderr` for the log line.

The `comment=` key matches what VHS writes to MP4/WebM containers, so metascan's video extractor reads either without changes. Stream-copy (`-c copy`) means no re-encode — fast, lossless, no codec dependencies.

### Errors and warnings

| Condition | Behavior |
|---|---|
| `directory == OFFLINE_SENTINEL` | `RuntimeError` (matches SaveImage wording) |
| `filenames` is `(save_flag, [])` | No-op; return empty list; debug log |
| Source path missing / unreadable | `RuntimeError("MetascanMoveMedia: source not found: <path>")` |
| Target dir unwritable | Propagate `OSError` from `mkdir` / `os.replace` |
| `ffmpeg` missing | Per-file `"skipped_no_ffmpeg"` status; warning log once per `process()` call |
| `ffprobe` missing (if_missing mode) | Treat as "no existing metadata"; attempt embed (which then hits the ffmpeg check) |
| `ffmpeg` non-zero exit | Per-file `"skipped_error"` status; warning log with stderr tail; file already relocated |
| Unknown extension | Per-file `"skipped_unsupported"` status; debug log only |

The relocation never silently fails. Metadata embedding is best-effort by design — if the user is in `save_metadata="always"` mode and ffmpeg is missing, they still see the file land in the metascan dir; the warning in the UI text + log tells them metadata didn't get written. This is intentional: making save fail when metadata fails would break workflows running on systems without ffmpeg.

### WSL path translation

Both `directory` (destination) and each entry in `filenames` (source) get run through `wsl_to_native_path`. VHS in WSL with ComfyUI on Windows emits `/mnt/d/...` paths, same hazard `MetascanSaveImage` handles. No-op on Linux/macOS and on paths that don't match the `/mnt/<drive>/` regex.

## Package-init change — `__init__.py`

Add two lines:

```python
from mscan_nodes.move_media import MetascanMoveMedia
```

```python
NODE_CLASS_MAPPINGS = {
    # ... existing entries ...
    "MetascanMoveMedia": MetascanMoveMedia,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    # ... existing entries ...
    "MetascanMoveMedia": "Metascan · Move Media",
}
```

## Docs

### New file: `docs/nodes/move-media.md`

Follows the shape of `docs/nodes/save-image.md`. Sections:

- **What it pulls from metascan** — same dropdown-population story; nothing at execute time.
- **Inputs table** — `filenames`, `directory`, `subpath`, `operation`, `save_metadata` per the design above.
- **Outputs table** — `filenames` (passes the input through with rewritten paths), `file_path` (first new path).
- **Metadata dispatch table** — per-extension behavior:

  | Extension | Tool | `always` | `if_missing` |
  |---|---|---|---|
  | `.png` | PIL | Re-save with new tEXt chunks | Skip if `prompt` or `workflow` tEXt present |
  | `.mp4` `.mov` `.mkv` `.webm` `.gif` | ffmpeg `-c copy -metadata` | Re-mux unconditionally | Skip if `comment`/`prompt`/`workflow` tag present (ffprobe-checked) |
  | other | — | No-op | No-op |

- **Cross-platform path handling** — same WSL paragraph as SaveImage, but applied to both source and destination paths.
- **Filename collisions** — same `max + 1` strategy, with the `_NN` zero-padded suffix.
- **Atomic writes** — explain the `.partial` → `os.replace` pattern and why (watcher safety).
- **ffmpeg dependency** — best-effort; "always" mode without ffmpeg still relocates the file and surfaces a warning.
- **Common errors** — offline sentinel, source-missing, unwritable target.

### Edit: `docs/nodes/save-image.md`

Add a "See also" line near the top: *"For files already on disk (e.g. VHS Combine Video output), see [Metascan · Move Media](move-media.md)."*

### Edit: `README.md`

- Bump node count from "four" to "five" (it's actually six already after the recent Select nodes; verify and correct in passing if the README is stale).
- Add a bullet: **Metascan · Move Media** — relocate any media file produced upstream (e.g. by VHS Combine Video) into a metascan-watched directory, embedding prompt/workflow metadata. Works with anything that emits `VHS_FILENAMES`.

## Tests — `tests/test_move_media.py`

New file. Mirrors `tests/test_save_image.py` patterns: pure helpers tested with real tmpfiles where the helper is filesystem-only; mocked subprocess where ffmpeg/ffprobe are invoked.

### Helper tests (no ComfyUI imports)

1. **`test_relocate_file_move_removes_source`** — create a tmpfile, `relocate_file(..., "move")`, assert source gone and destination present with original bytes.
2. **`test_relocate_file_copy_preserves_source`** — same, with `"copy"`; source still present.
3. **`test_relocate_file_collision_uses_max_plus_one`** — pre-create `foo.mp4` and `foo_00.mp4` in dst; call with a new `foo.mp4` source; assert result is `foo_01.mp4` (not `foo_02.mp4`).
4. **`test_relocate_file_collision_skips_gaps`** — pre-create `foo.mp4`, `foo_00.mp4`, `foo_05.mp4`; assert next is `foo_06.mp4` (max + 1, not gap-filling).
5. **`test_relocate_file_staging_cleaned_on_success`** — assert no `.partial` files remain in dst after a successful relocate.
6. **`test_relocate_file_staging_visible_on_crash`** — monkeypatch `os.replace` to raise; assert `.partial` is present (forensics) and final name is not.
7. **`test_dispatch_metadata_routes_by_extension`** — parametrize over `.png`, `.mp4`, `.webm`, `.gif`, `.txt`; assert each goes to the right helper (or returns `"skipped_unsupported"` for `.txt`). Helpers are monkeypatched.
8. **`test_embed_png_metadata_always_overwrites`** — write a PNG with existing `prompt` tEXt chunk; call in `"always"` mode; assert the chunk now has new value.
9. **`test_embed_png_metadata_if_missing_skips_when_present`** — same setup; call in `"if_missing"` mode; assert chunk unchanged and status is `"skipped_present"`.
10. **`test_embed_png_metadata_if_missing_writes_when_absent`** — PNG with no tEXt; `"if_missing"` mode; assert chunk now present.
11. **`test_embed_video_metadata_no_ffmpeg_returns_skipped`** — monkeypatch `shutil.which` to return `None`; assert status `"skipped_no_ffmpeg"` and no subprocess call.
12. **`test_embed_video_metadata_ffmpeg_nonzero_logs_and_skips`** — monkeypatch `subprocess.run` to raise `CalledProcessError`; assert status `"skipped_error"` and source file still has its original bytes (staging cleaned up).
13. **`test_embed_video_metadata_always_invokes_ffmpeg`** — monkeypatch `subprocess.run` to a recorder; assert command starts with `["ffmpeg", "-y", "-loglevel", "error", "-i", ...]` and contains `-c copy` and `-metadata comment=...`.
14. **`test_embed_video_metadata_if_missing_probes_first`** — monkeypatch `_video_has_metadata` to return `True`; assert no `ffmpeg` call and status `"skipped_present"`.

### Node-integration tests (ComfyUI shim from conftest)

15. **`test_process_offline_sentinel_raises`** — set `directory = OFFLINE_SENTINEL`; assert `RuntimeError`.
16. **`test_process_empty_filenames_returns_empty_and_no_error`** — pass `(True, [])`; assert `result["result"][0] == (True, [])` and `result["result"][1] == ""`.
17. **`test_process_move_pipeline_end_to_end`** — create a real tmpfile, run a `move` + `if_missing` `.mp4` pipeline with `subprocess.run` mocked to succeed; assert source gone, destination present, returned `VHS_FILENAMES` has the new path, `ui` text mentions `"embedded"`.
18. **`test_process_copy_preserves_save_flag`** — call with `(False, [path])`; assert returned `filenames[0] == False` (save_flag unchanged).
19. **`test_process_subpath_strftime_expansion`** — pass `subpath = "%Y-%m"` with `_utc_now` patched to a fixed date; assert destination dir is `<dir>/2026-05`.
20. **`test_process_wsl_translation_on_source_and_destination`** — monkeypatch `sys.platform = "win32"`; pass `directory = "/mnt/d/dst"` and a source path of `/mnt/d/src/file.mp4`; mock `shutil.move` to record its args; assert it was called with the translated native forms (`D:\src\file.mp4` and a destination under `D:\dst\`).
21. **`test_process_passes_prompt_and_workflow_to_metadata_helper`** — record args to a monkeypatched `dispatch_metadata`; assert `prompt` and `extra_pnginfo["workflow"]` flow through correctly.
22. **`test_process_returns_ui_text_per_file`** — multi-file batch; assert `result["ui"]["text"]` has one line per file with the format `"<op> <src> → <dst> [<status>]"`.

### Sanity check on the shared-helpers refactor

23. Run `tests/test_save_image.py` unchanged after the lift to `_shared.py`. No new test needed; existing pass is the assertion.

## Risks and edge cases

- **ffprobe present without ffmpeg (and vice versa).** In practice the two ship together — both Homebrew's `ffmpeg`, the Windows static builds, and apt's `ffmpeg` package install both binaries. The handling above tolerates either one being missing without crashing, but the combination "ffmpeg present, ffprobe missing" in `if_missing` mode would skip the probe and unconditionally re-mux. We accept this edge case rather than checking both binaries up-front, because the realistic failure mode users hit is "neither is installed" — which the `shutil.which("ffmpeg")` check catches.
- **ffmpeg version skew.** `-metadata comment=...` syntax is stable across ffmpeg 4.x and 5.x. WebM/Matroska store the tag as a `COMMENT` tag in the global header. Older ffmpeg builds (< 4.0) may not honor `-c copy` with `-metadata` cleanly on some MOV variants — the failure mode is non-zero exit, which we already log and skip. Documented as a known limitation in `docs/nodes/move-media.md`.
- **Container-write atomicity on Windows.** `os.replace` is atomic same-volume on Windows (NTFS), but a cross-volume relocate falls back to copy + delete inside `shutil.move`. The `.partial` staging guards against the watcher seeing a partial copy mid-flight. The Windows case where the source is on `/mnt/d` (translated to `D:`) and the destination is on `C:` is exactly the cross-volume case the staging is designed for.
- **VHS_FILENAMES schema drift.** VHS has historically used `(save_flag, [paths])` — a two-tuple. If a future VHS version changes the shape, the unpack in `process()` raises `ValueError`, which surfaces in the workflow as a clear error. Documented in the spec; we accept the coupling.
- **Filesystem watcher race on rename.** Some inotify-style watchers fire on `IN_MOVED_TO` reliably (`os.replace` rename), but on macOS FSEvents the rename event sometimes arrives as `Created`. metascan's watcher handles both — confirmed by inspection of metascan's watcher implementation during the spec phase, no special handling needed here.
- **Metadata payload size on video containers.** Comment tags in MP4/WebM are practically unbounded (held in the moov atom / Matroska tags element) but some players truncate display past ~32 KB. Our payloads are typically a few KB. No truncation in our write path.
- **PNG metadata round-trip.** Re-saving a PNG through PIL re-encodes the pixel data (lossless). Existing image quality and bit depth are preserved. This is a re-encode in the sense of "re-emit the file bytes," not "reduce quality." Worth a one-line note in the docs.

## Build sequence

1. **Lift shared helpers** into `mscan_nodes/_shared.py`; update `save_image.py` to import from there; run `tests/test_save_image.py` to confirm green.
2. **Stub `MetascanMoveMedia`** with `INPUT_TYPES`, `process()` signature, and the offline-sentinel guard; register in `__init__.py`. Lets the editor load the node.
3. **Implement `relocate_file`** and its tests (helper tests 1–6). Pure-Python, fast iteration.
4. **Implement `dispatch_metadata` + `embed_png_metadata`** and tests 7–10.
5. **Implement `embed_video_metadata` + `_video_has_metadata`** and tests 11–14. Subprocess mocked throughout.
6. **Wire helpers into `process()`** and add node-integration tests 15–22.
7. **Write `docs/nodes/move-media.md`** and update `README.md`, `docs/nodes/save-image.md`.
8. **Manual smoke in ComfyUI:** wire VHS Combine → MetascanMoveMedia; verify file lands in metascan dir with `.partial` never visible to the user; verify metascan picks it up via watcher; verify metadata round-trips through ffprobe.
