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
