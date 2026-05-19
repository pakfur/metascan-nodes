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
from pathlib import Path
from typing import Optional

import numpy as np
import torch
from PIL import Image
from PIL.PngImagePlugin import PngInfo


def resolve_target_dir(directory: str, subpath: str, now: dt.datetime) -> Path:
    """Return ``Path(directory) / strftime(subpath, now)``, creating the
    directory tree if it doesn't exist. ``subpath`` may include strftime
    placeholders like ``%Y-%m``; an empty subpath returns ``directory``
    unchanged. Always uses POSIX-style joining via pathlib so Windows
    paths work transparently."""
    base = Path(directory)
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

from client.api import MetascanClient
from client.cache import combo_directories, OFFLINE_SENTINEL
from client.config import resolve_config
from nodes.settings import get_current_override


def _utc_now() -> dt.datetime:
    """Indirection so tests can patch the clock for strftime checks."""
    return dt.datetime.now()


def _build_client() -> MetascanClient:
    cfg = resolve_config(settings_override=get_current_override())
    return MetascanClient(config=cfg, timeout=5.0)


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

        # Collision-counter filename: ``<prefix>_<NNNNN>.png`` starting
        # from the first unused N. Cheap O(n) probe; metascan rigs don't
        # accumulate millions of files in a single output dir.
        existing = list(target_dir.glob(f"{filename_prefix}_*.png"))
        next_idx = len(existing)

        first_path: Optional[Path] = None
        for i in range(images.shape[0]):
            pil = tensor_to_pil(images[i])
            out_path = target_dir / f"{filename_prefix}_{next_idx + i:05d}.png"
            pil.save(out_path, pnginfo=info)
            if first_path is None:
                first_path = out_path

        return (images, str(first_path) if first_path else "")
