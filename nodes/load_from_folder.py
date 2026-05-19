"""MetascanLoadFromFolder — pick an image from a metascan manual folder.

Module is split into pure helpers + a ComfyUI integration class
(Task 16). Helpers are testable in isolation with no HTTP and no
ComfyUI runtime.
"""

from __future__ import annotations

import io
from pathlib import PurePosixPath
from typing import Literal

import numpy as np
import torch
from PIL import Image

# Conservative whitelist — covers the formats metascan's extractors
# claim support for. Anything else gets dropped silently from the
# filter step so a random pick doesn't land on an unreadable file.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


def filter_paths(paths: list[str], image_only: bool, filename_filter: str) -> list[str]:
    """Filter then sort deterministically.

    1. Drop entries whose extension isn't in the supported sets.
    2. If ``image_only=True``, also drop video extensions.
    3. If ``filename_filter`` is non-empty, keep only paths whose
       ``PurePosixPath(p).name`` contains the filter substring.
    4. Sort ascending by path so selection-by-seed is stable across
       runs even when the upstream listing order isn't.
    """
    allowed = _IMAGE_EXTS if image_only else (_IMAGE_EXTS | _VIDEO_EXTS)
    out = [
        p for p in paths
        if PurePosixPath(p).suffix.lower() in allowed
        and (not filename_filter or filename_filter in PurePosixPath(p).name)
    ]
    out.sort()
    return out


SelectionMode = Literal["random", "sequential", "specific"]


def select_path(
    paths: list[str], mode: SelectionMode, seed: int, index: int
) -> tuple[str, int]:
    """Pick one path. Returns (chosen_path, next_seed).

    - ``random`` and ``sequential`` both index by ``seed % len(paths)``;
      ``random`` returns the same seed back, ``sequential`` returns
      ``(seed + 1) % len(paths)`` so chaining advances naturally.
    - ``specific`` indexes by ``index % len(paths)`` and returns
      ``index`` unchanged (next_seed is unused in this mode but kept
      for output-tuple symmetry).
    - Empty path list raises ``RuntimeError`` with a message the load
      node can surface in ComfyUI's UI without further wrapping.
    """
    if not paths:
        raise RuntimeError("no matching items in folder")
    n = len(paths)
    if mode == "specific":
        chosen_idx = index % n
        return paths[chosen_idx], index
    chosen_idx = seed % n
    next_seed = seed if mode == "random" else (seed + 1) % n
    return paths[chosen_idx], next_seed


def bytes_to_tensor(data: bytes) -> torch.Tensor:
    """Decode PNG/JPEG/WebP bytes to ComfyUI's IMAGE convention:
    float32, range [0, 1], shape ``[1, H, W, 3]`` (NHWC). RGBA inputs
    flatten to RGB by dropping the alpha channel — the load node is
    feeding samplers / preview chains that don't carry alpha."""
    pil = Image.open(io.BytesIO(data))
    pil.load()
    if pil.mode != "RGB":
        pil = pil.convert("RGB")
    arr = np.asarray(pil, dtype=np.float32) / 255.0
    tensor = torch.from_numpy(arr).unsqueeze(0)  # add batch dim
    return tensor
