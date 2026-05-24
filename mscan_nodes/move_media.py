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
from pathlib import Path
from typing import Optional

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from mscan_client.cache import combo_directories, OFFLINE_SENTINEL
from mscan_nodes._shared import _build_client

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
    watcher-safety reason as the relocation step."""
    img = Image.open(path)
    # PIL surfaces tEXt chunks in img.info as a plain dict
    if mode == "if_missing" and ("prompt" in img.info or "workflow" in img.info):
        img.close()
        return "skipped_present"
    info = PngInfo()
    if prompt is not None:
        info.add_text("prompt", json.dumps(prompt))
    if workflow is not None:
        info.add_text("workflow", json.dumps(workflow))
    img.load()  # decode pixel data before we close the source file handle
    staging = path.with_suffix(path.suffix + ".meta.partial")
    img.save(staging, pnginfo=info, format="PNG")
    img.close()
    os.replace(staging, path)
    return "embedded"


def embed_video_metadata(
    path: Path,
    prompt,
    workflow,
    mode: str,
) -> str:
    raise NotImplementedError  # implemented in Task 5


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
        # Real logic lands in Task 6.
        raise NotImplementedError
