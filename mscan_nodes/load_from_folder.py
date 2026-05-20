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


# --- ComfyUI node integration --------------------------------------------

from mscan_client.api import MetascanClient
from mscan_client.cache import combo_folders, OFFLINE_SENTINEL
from mscan_client.config import resolve_config
from mscan_nodes.settings import get_current_override


def _build_client() -> MetascanClient:
    cfg = resolve_config(settings_override=get_current_override())
    return MetascanClient(config=cfg, timeout=10.0)


def _folder_id_for_name(client: MetascanClient, name: str) -> str:
    """Resolve a human folder name to its (string) folder ID.

    The dropdown shows names; the API takes IDs. We re-fetch the folder
    list here rather than relying on the cached name list because the
    cached version doesn't carry IDs."""
    for folder in client.list_folders():
        if folder["name"] == name:
            return folder["id"]
    raise RuntimeError(f"folder not found in metascan: {name!r}")


class MetascanLoadFromFolder:
    CATEGORY = "metascan"
    RETURN_TYPES = ("IMAGE", "STRING", "STRING", "STRING", "INT")
    RETURN_NAMES = ("image", "file_path", "positive", "negative", "next_seed")
    FUNCTION = "load"

    @classmethod
    def INPUT_TYPES(cls) -> dict:
        try:
            folders = combo_folders(_build_client())
        except Exception:  # noqa: BLE001
            folders = [OFFLINE_SENTINEL]
        return {
            "required": {
                "folder": (folders,),
                "selection_mode": (["random", "sequential", "specific"],),
                "seed": ("INT", {"default": 0, "min": 0, "max": 2**31 - 1}),
                "index": ("INT", {"default": 0, "min": 0, "max": 2**31 - 1}),
                "filename_filter": ("STRING", {"default": ""}),
                "image_only": ("BOOLEAN", {"default": True}),
            },
        }

    def load(
        self,
        folder: str,
        selection_mode: SelectionMode,
        seed: int,
        index: int,
        filename_filter: str,
        image_only: bool,
    ) -> tuple:
        if folder == OFFLINE_SENTINEL:
            raise RuntimeError(
                "Metascan is offline — cannot list folders. Bring metascan "
                "up or correct the MetascanSettings URL."
            )

        client = _build_client()
        folder_id = _folder_id_for_name(client, folder)
        folder_detail = client.get_folder(folder_id)
        items = folder_detail.get("items", []) or []

        filtered = filter_paths(items, image_only=image_only, filename_filter=filename_filter)
        chosen, next_seed = select_path(filtered, mode=selection_mode, seed=seed, index=index)

        media = client.get_media_detail(chosen)
        data = media.get("data") or {}
        positive = data.get("prompt", "") or ""
        negative = data.get("negative_prompt", "") or ""

        raw = client.stream_bytes(chosen)
        tensor = bytes_to_tensor(raw)

        return (tensor, chosen, positive, negative, next_seed)
