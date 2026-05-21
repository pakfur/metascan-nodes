"""MetascanSaveImage — writes PNG batches into a metascan-watched dir.

This module is split into two layers. The pure helpers
(``resolve_target_dir``, ``tensor_to_pil``, ``build_png_info``) handle
filesystem-path + PIL plumbing and are fully testable with synthesized
torch tensors. The ``MetascanSaveImage`` class (added in Task 14) wires
the helpers into ComfyUI's node interface.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Optional

import numpy as np
import torch
from PIL import Image
from PIL.PngImagePlugin import PngInfo


_WSL_MNT_RE = re.compile(r"^/mnt/([a-zA-Z])(/.*)?$")


def wsl_to_native_path(path: str) -> str:
    """Translate WSL ``/mnt/<drive>/...`` paths to Windows ``<DRIVE>:\\...``
    when running on Windows. No-op on Linux/macOS and for paths that
    aren't WSL-style mounts. Needed when metascan runs in WSL (and
    therefore reports watched directories as ``/mnt/d/...``) but the
    ComfyUI process consuming those paths is on the Windows side."""
    if sys.platform != "win32":
        return path
    m = _WSL_MNT_RE.match(path)
    if not m:
        return path
    drive = m.group(1).upper()
    rest = m.group(2) or "\\"
    return f"{drive}:{rest}".replace("/", "\\")


def resolve_target_dir(directory: str, subpath: str, now: dt.datetime) -> Path:
    """Return ``Path(directory) / strftime(subpath, now)``, creating the
    directory tree if it doesn't exist. ``subpath`` may include strftime
    placeholders like ``%Y-%m``; an empty subpath returns ``directory``
    unchanged. WSL-style mounts (``/mnt/<drive>/...``) are translated to
    native Windows form first so ComfyUI on Windows can write to dirs
    that metascan-in-WSL reports."""
    base = Path(wsl_to_native_path(directory))
    if subpath:
        expanded = now.strftime(subpath)
        target = base / expanded
    else:
        target = base
    target.mkdir(parents=True, exist_ok=True)
    return target


def tensor_to_pil(image: torch.Tensor) -> Image.Image:
    """Convert a single H×W×3 float tensor (ComfyUI's IMAGE convention,
    range [0, 1]) to an 8-bit RGB PIL image. Out-of-range values clamp
    silently — upstream nodes occasionally produce slightly negative or
    slightly >1 values from sampler noise and we don't want to fail
    the save over a rounding artifact."""
    arr = image.detach().cpu().numpy()
    arr = np.clip(arr * 255.0, 0.0, 255.0).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def build_png_info(prompt: Optional[dict], workflow: Optional[dict]) -> PngInfo:
    """Build a ``PngInfo`` carrying ComfyUI's ``prompt`` and (optionally)
    ``workflow`` tEXt chunks. The format matches what ComfyUI's core
    SaveImage writes, which is what metascan's enhanced_comfyui extractor
    expects when it scans the directory later (see
    metascan/metascan/extractors/enhanced_comfyui.py)."""
    info = PngInfo()
    if prompt is not None:
        info.add_text("prompt", json.dumps(prompt))
    if workflow is not None:
        info.add_text("workflow", json.dumps(workflow))
    return info


# --- ComfyUI node integration --------------------------------------------

from mscan_client.api import MetascanClient
from mscan_client.cache import combo_directories, OFFLINE_SENTINEL
from mscan_client.config import resolve_config
from mscan_nodes.settings import get_current_override


def _utc_now() -> dt.datetime:
    """Indirection so tests can patch the clock for strftime checks."""
    return dt.datetime.now()


def _build_client() -> MetascanClient:
    cfg = resolve_config(settings_override=get_current_override())
    return MetascanClient(config=cfg, timeout=5.0)


def _comfy_temp_dir() -> Optional[Path]:
    """Return ComfyUI's temp directory, or None if we're not running under
    ComfyUI (tests, scripts). The preview-image side of save() is skipped
    when this is None."""
    try:
        import folder_paths  # ComfyUI puts its root on sys.path
        return Path(folder_paths.get_temp_directory())
    except Exception:  # noqa: BLE001 — any failure means no UI preview
        return None


def _write_previews(
    images: "torch.Tensor",
    prefix_name: str,
    next_idx: int,
    temp_dir: Path,
) -> list[dict]:
    """Save lightweight preview copies into ComfyUI's temp dir and return
    the ``ui.images`` payload (filename/subfolder/type tuples) that the
    frontend uses to render thumbnails via the ``/view`` endpoint.

    Previews use ``compress_level=1`` to match ComfyUI's PreviewImage
    node — fast write, slightly larger files; not the canonical save."""
    temp_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    for i in range(images.shape[0]):
        pil = tensor_to_pil(images[i])
        # Prefix with ``metascan_`` so we don't collide with files from
        # other nodes (PreviewImage uses random suffixes; we use the
        # node-name namespace instead — collisions across runs are
        # harmless since temp dir is wiped on ComfyUI restart).
        preview_name = f"metascan_{prefix_name}_{next_idx + i:05d}.png"
        pil.save(temp_dir / preview_name, compress_level=1)
        results.append({
            "filename": preview_name,
            "subfolder": "",
            "type": "temp",
        })
    return results


class MetascanSaveImage:
    """Save a batch of images into a metascan-watched directory.

    The node does NOT call metascan's API at execute time — the
    filesystem watcher (or next scan) picks the file up automatically.
    The only HTTP call happens at INPUT_TYPES() to populate the
    directory dropdown.
    """

    CATEGORY = "metascan"
    OUTPUT_NODE = True
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("images", "file_path")
    FUNCTION = "save"

    @classmethod
    def INPUT_TYPES(cls) -> dict:
        try:
            dirs = combo_directories(_build_client())
        except Exception:  # noqa: BLE001 — be defensive at editor-load time
            dirs = [OFFLINE_SENTINEL]
        return {
            "required": {
                "images": ("IMAGE",),
                "directory": (dirs,),
                "subpath": ("STRING", {"default": ""}),
                "filename_prefix": ("STRING", {"default": "ComfyUI"}),
                "embed_workflow": ("BOOLEAN", {"default": True}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    def save(
        self,
        images: "torch.Tensor",
        directory: str,
        subpath: str,
        filename_prefix: str,
        embed_workflow: bool,
        prompt: Optional[dict] = None,
        extra_pnginfo: Optional[dict] = None,
    ) -> tuple:
        if directory == OFFLINE_SENTINEL:
            raise RuntimeError(
                "Metascan is offline — cannot resolve a watched directory. "
                "Bring metascan up or add a MetascanSettings node with the "
                "correct URL."
            )

        now = _utc_now()
        target_dir = resolve_target_dir(directory=directory, subpath=subpath, now=now)

        # extra_pnginfo is what ComfyUI passes for the workflow blob;
        # canonical key is "workflow" inside the dict.
        workflow_dict: Optional[dict] = None
        if embed_workflow and isinstance(extra_pnginfo, dict):
            workflow_dict = extra_pnginfo.get("workflow")

        info = build_png_info(prompt=prompt, workflow=workflow_dict)

        # filename_prefix may contain forward slashes (e.g. "subfolder/run")
        # which ComfyUI core SaveImage treats as additional subdirectory
        # under target_dir. Split off the subdir part and ensure it exists.
        prefix_path = PurePosixPath(filename_prefix)
        prefix_subdir = str(prefix_path.parent) if prefix_path.parent != PurePosixPath(".") else ""
        prefix_name = prefix_path.name
        if prefix_subdir:
            target_dir = target_dir / prefix_subdir
            target_dir.mkdir(parents=True, exist_ok=True)

        # Collision-counter filename: ``<prefix>_<NNNNN>.png`` starting
        # at one past the highest existing index. We pick max+1 rather
        # than len(existing) so a deletion-induced gap doesn't cause us
        # to overwrite a surviving file.
        existing = list(target_dir.glob(f"{prefix_name}_*.png"))
        max_idx = -1
        for p in existing:
            try:
                idx = int(p.stem.rsplit("_", 1)[-1])
            except ValueError:
                continue  # not a numbered file we own; skip
            if idx > max_idx:
                max_idx = idx
        next_idx = max_idx + 1

        first_path: Optional[Path] = None
        for i in range(images.shape[0]):
            pil = tensor_to_pil(images[i])
            out_path = target_dir / f"{prefix_name}_{next_idx + i:05d}.png"
            pil.save(out_path, pnginfo=info)
            if first_path is None:
                first_path = out_path

        # Write previews into ComfyUI's temp dir so the node renders
        # thumbnails in the UI (matches core SaveImage's behavior). When
        # ComfyUI's folder_paths isn't importable — tests, scripts —
        # skip the preview write and return an empty ui.images list.
        ui_images: list[dict] = []
        temp_dir = _comfy_temp_dir()
        if temp_dir is not None:
            ui_images = _write_previews(images, prefix_name, next_idx, temp_dir)

        return {
            "ui": {"images": ui_images},
            "result": (images, str(first_path) if first_path else ""),
        }
