"""MetascanMoveMedia — relocate any media file produced upstream into
a metascan-watched directory and embed prompt/workflow metadata.

Two-node workflow: a producer (e.g. VHS Combine Video) emits a
VHS_FILENAMES socket; this node consumes it, moves or copies each file
into a metascan-watched dir, and best-effort embeds metadata via PIL
(PNG) or ffmpeg (video containers)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from mscan_client.cache import combo_directories, OFFLINE_SENTINEL
from mscan_nodes._shared import (
    _build_client,
    _utc_now,
    resolve_target_dir,
    wsl_to_native_path,
)

logger = logging.getLogger(__name__)


def relocate_file(src: Path, dst_dir: Path, operation: str) -> Path:
    """Move or copy ``src`` into ``dst_dir``, returning the final path.

    Writes to a ``<name>.partial`` staging path first, then ``os.replace``s
    to the final name. The staging step means a filesystem watcher (or
    a mid-write crash on a cross-device copy) never sees a half-written
    file under its real name. On destination name collision, appends
    ``_NN`` using max-existing-index + 1 (never reuses a gap, so we
    never overwrite a surviving file)."""
    src = Path(src)
    dst_dir = Path(dst_dir)
    final_name = _pick_final_name(src.name, dst_dir)
    staging = dst_dir / (final_name + ".partial")
    if operation == "move":
        shutil.move(str(src), str(staging))
    elif operation == "copy":
        shutil.copy2(str(src), str(staging))
    else:
        raise ValueError(f"operation must be 'move' or 'copy', got {operation!r}")
    final_path = dst_dir / final_name
    os.replace(staging, final_path)
    return final_path


def _pick_final_name(name: str, dst_dir: Path) -> str:
    """Return ``name`` if ``dst_dir / name`` is free; otherwise append
    ``_NN`` using max(existing index for this stem) + 1. Widens to 3
    digits if 99 is exhausted.

    For compound extensions (``foo.tar.gz``), the counter is inserted
    before the final suffix only (``foo.tar_00.gz``), because we use
    ``Path.stem`` / ``Path.suffix`` which split on the last dot. Accepted
    in scope because the upstream producers we support (VHS Combine,
    image savers) only emit single-extension files."""
    target = dst_dir / name
    if not target.exists():
        return name
    stem = Path(name).stem
    suffix = Path(name).suffix
    max_idx = -1
    pattern = f"{stem}_*{suffix}"
    for p in dst_dir.glob(pattern):
        tail = p.stem[len(stem) + 1:]  # the bit after "<stem>_"
        try:
            idx = int(tail)
        except ValueError:
            continue
        if idx > max_idx:
            max_idx = idx
    next_idx = max_idx + 1
    width = 2 if next_idx < 100 else 3
    return f"{stem}_{next_idx:0{width}d}{suffix}"


_VIDEO_EXTS = frozenset({".mp4", ".mov", ".mkv", ".webm", ".gif"})


def _drop_video_preview_pngs(paths: list[str]) -> list[str]:
    """Drop ``.png`` entries that share a directory + stem with a video
    entry in the same list. VHS Combine reports a preview PNG path
    alongside the saved video, but only the video is actually written to
    disk — iterating the raw list would fail with 'source not found' on
    the phantom preview. Stem matching is case-insensitive (Windows
    filesystems are case-insensitive; PNG suffix can appear as ``.PNG``).
    Non-PNG entries and PNGs with no sibling video are preserved in
    original order."""
    video_keys = set()
    for p in paths:
        pp = Path(p)
        if pp.suffix.lower() in _VIDEO_EXTS:
            video_keys.add((pp.parent, pp.stem.lower()))
    out: list[str] = []
    for p in paths:
        pp = Path(p)
        if pp.suffix.lower() == ".png" and (pp.parent, pp.stem.lower()) in video_keys:
            continue
        out.append(p)
    return out


def dispatch_metadata(
    path: Path,
    prompt,
    workflow,
    mode: str,
) -> str:
    """Route to the per-format embed helper. Returns a status string
    used by the node's UI text line: ``embedded``, ``skipped_present``,
    ``skipped_unsupported``, ``skipped_no_ffmpeg``, or ``skipped_error``.

    Dispatch is by lowercased ``path.suffix``:
    - ``.png`` → ``embed_png_metadata`` (PIL tEXt chunks)
    - any extension in ``_VIDEO_EXTS`` → ``embed_video_metadata`` (ffmpeg ``-c copy``)
    - anything else → ``"skipped_unsupported"``"""
    ext = path.suffix.lower()
    if ext == ".png":
        return embed_png_metadata(path, prompt, workflow, mode)
    if ext in _VIDEO_EXTS:
        return embed_video_metadata(path, prompt, workflow, mode)
    return "skipped_unsupported"


def embed_png_metadata(
    path: Path,
    prompt,
    workflow,
    mode: str,
) -> str:
    """Re-save the PNG at ``path`` with new prompt/workflow tEXt chunks.

    In ``if_missing`` mode, skip if either chunk is already present —
    we don't partially overwrite. In ``always`` mode, overwrite both.
    Write via ``.meta.partial`` then ``os.replace`` for the same
    watcher-safety reason as the relocation step. The ``with`` block
    ensures the file handle is released even if ``img.save`` raises —
    important on Windows, where a leaked handle keeps the source file
    locked."""
    with Image.open(path) as img:
        # PIL surfaces tEXt chunks in img.info as a plain dict
        if mode == "if_missing" and ("prompt" in img.info or "workflow" in img.info):
            return "skipped_present"
        info = PngInfo()
        if prompt is not None:
            info.add_text("prompt", json.dumps(prompt))
        if workflow is not None:
            info.add_text("workflow", json.dumps(workflow))
        img.load()  # decode pixel data before the context exits
        staging = path.with_suffix(path.suffix + ".meta.partial")
        img.save(staging, pnginfo=info, format="PNG")
    os.replace(staging, path)
    return "embedded"


def embed_video_metadata(
    path: Path,
    prompt,
    workflow,
    mode: str,
) -> str:
    """Re-mux ``path`` with new comment metadata via ffmpeg ``-c copy``.

    Best-effort: if ffmpeg is missing or fails, the file is left
    untouched and we return a status the caller can surface in the UI.
    The comment payload is JSON of ``{"prompt": ..., "workflow": ...}``,
    matching the key VHS itself writes."""
    if shutil.which("ffmpeg") is None:
        logger.warning("ffmpeg not found on PATH — skipping metadata embed for %s", path)
        return "skipped_no_ffmpeg"
    if mode == "if_missing" and _video_has_metadata(path):
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


def _video_has_metadata(path: Path) -> bool:
    """Return True iff ffprobe sees any of comment/prompt/workflow in
    the file's format-level tags. Treat ffprobe-missing as 'no metadata
    detected' — the ffmpeg-missing check above prevents us from writing
    in that case anyway."""
    if shutil.which("ffprobe") is None:
        return False
    cmd = ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(path)]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    try:
        tags = json.loads(result.stdout).get("format", {}).get("tags", {}) or {}
    except json.JSONDecodeError:
        return False
    lowered = {k.lower() for k in tags.keys()}
    return bool(lowered & {"comment", "prompt", "workflow"})


def _tail_stderr(exc) -> str:
    stderr = getattr(exc, "stderr", b"") or b""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    return stderr[-500:]


class MetascanMoveMedia:
    CATEGORY = "metascan"
    OUTPUT_NODE = True
    RETURN_TYPES = ("VHS_FILENAMES", "STRING")
    RETURN_NAMES = ("filenames", "file_path")
    FUNCTION = "process"

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

    def process(
        self,
        filenames,
        directory: str,
        subpath: str,
        operation: str,
        save_metadata: str,
        prompt: Optional[dict] = None,
        extra_pnginfo: Optional[dict] = None,
    ) -> dict:
        if directory == OFFLINE_SENTINEL:
            raise RuntimeError(
                "Metascan is offline — cannot resolve a watched directory. "
                "Bring metascan up or add a MetascanSettings node with the "
                "correct URL."
            )

        save_flag, paths = filenames
        paths = _drop_video_preview_pngs(list(paths))
        if not paths:
            logger.debug("MetascanMoveMedia: empty filenames input — nothing to move")
            return {
                "ui": {"text": ["no files to move"]},
                "result": ((save_flag, []), ""),
            }

        target_dir = resolve_target_dir(
            directory=directory, subpath=subpath, now=_utc_now()
        )

        workflow_dict: Optional[dict] = None
        if isinstance(extra_pnginfo, dict):
            workflow_dict = extra_pnginfo.get("workflow")

        new_paths: list[str] = []
        ui_lines: list[str] = []
        for src in paths:
            src_native = Path(wsl_to_native_path(str(src)))
            if not src_native.exists():
                raise RuntimeError(
                    f"MetascanMoveMedia: source not found: {src_native}"
                )
            new_path = relocate_file(src_native, target_dir, operation)
            status = dispatch_metadata(new_path, prompt, workflow_dict, save_metadata)
            new_paths.append(str(new_path))
            ui_lines.append(f"{operation} {src_native} → {new_path} [{status}]")

        return {
            "ui": {"text": ui_lines},
            "result": ((save_flag, new_paths), new_paths[0] if new_paths else ""),
        }
