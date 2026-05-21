"""MetascanLoadPrompt — load a saved prompt from metascan's prompt library.

Returns the prompt strings (positive, negative, name), the saved
``source_file_path``, the source IMAGE tensor (fetched from metascan
by file_path), and recommended (width, height) for image generation
sized to the chosen target_model and quality tier.
"""

from __future__ import annotations

from typing import Literal, Optional

from mscan_client.api import MetascanClient
from mscan_client.cache import (
    combo_folders,
    combo_target_models,
    OFFLINE_SENTINEL,
)
from mscan_client.config import resolve_config
from mscan_nodes.load_from_folder import bytes_to_tensor
from mscan_nodes.resolution import QUALITY_TIERS, compute_resolution
from mscan_nodes.settings import get_current_override


SelectionMode = Literal["random", "by_name"]


def select_prompt(
    rows: list[dict], mode: SelectionMode, name: str, seed: int
) -> dict:
    """Pick one row from a search result.

    - ``by_name``: return the row where ``row["name"] == name``. If no
      row matches, raise ``RuntimeError`` with a message the node
      surfaces directly.
    - ``random``: return ``rows[seed % len(rows)]`` (reproducible by
      seed so workflow re-runs yield the same prompt).
    - Empty ``rows`` raises ``RuntimeError`` regardless of mode.
    """
    if not rows:
        raise RuntimeError("no saved prompts match the folder + target_model filter")
    if mode == "by_name":
        for r in rows:
            if r.get("name") == name:
                return r
        raise RuntimeError(f"no saved prompt named {name!r} in the filtered set")
    return rows[seed % len(rows)]


# ---------------------------------------------------------------------------
# ComfyUI node
# ---------------------------------------------------------------------------


def _build_client() -> MetascanClient:
    cfg = resolve_config(settings_override=get_current_override())
    return MetascanClient(config=cfg, timeout=10.0)


def _folder_id_for_name(client: MetascanClient, name: str) -> str:
    for folder in client.list_folders():
        if folder["name"] == name:
            return folder["id"]
    raise RuntimeError(f"folder not found in metascan: {name!r}")


class MetascanLoadPrompt:
    CATEGORY = "metascan"
    RETURN_TYPES = ("IMAGE", "STRING", "STRING", "STRING", "STRING", "INT", "INT")
    RETURN_NAMES = ("image", "positive", "negative", "name", "source_file_path", "width", "height")
    FUNCTION = "load"

    @classmethod
    def INPUT_TYPES(cls) -> dict:
        try:
            client = _build_client()
            folders = combo_folders(client)
            target_models = combo_target_models(client)
        except Exception:  # noqa: BLE001
            folders = [OFFLINE_SENTINEL]
            target_models = [OFFLINE_SENTINEL]
        return {
            "required": {
                "folder": (folders,),
                "target_model": (target_models,),
                "selection_mode": (["random", "by_name"],),
                "prompt_name": ("STRING", {"default": ""}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 2**31 - 1}),
                "quality": (QUALITY_TIERS,),
            },
        }

    def load(
        self,
        folder: str,
        target_model: str,
        selection_mode: SelectionMode,
        prompt_name: str,
        seed: int,
        quality: str,
    ) -> tuple:
        if folder == OFFLINE_SENTINEL or target_model == OFFLINE_SENTINEL:
            raise RuntimeError(
                "Metascan is offline — bring it up or correct MetascanSettings."
            )

        client = _build_client()
        folder_id = _folder_id_for_name(client, folder)
        # "any" is a virtual UI option — map to null filter.
        wire_target: Optional[str] = None if target_model == "any" else target_model

        rows = client.search_prompts(
            folder_id=folder_id,
            target_model=wire_target,
            name=prompt_name if selection_mode == "by_name" and prompt_name else None,
            limit=500,
        )
        chosen = select_prompt(rows, mode=selection_mode, name=prompt_name, seed=seed)

        source_file_path = chosen.get("file_path", "") or ""
        if not source_file_path:
            raise RuntimeError(
                f"saved prompt {chosen.get('name', '?')!r} has no source "
                "file path — cannot compute target resolution"
            )

        raw = client.stream_bytes(source_file_path)
        image = bytes_to_tensor(raw)
        src_h, src_w = image.shape[1], image.shape[2]
        width, height = compute_resolution(src_w, src_h, target_model, quality)

        return (
            image,
            chosen.get("prompt", "") or "",
            chosen.get("negative") or "",   # SQL NULL → ""
            chosen.get("name", "") or "",
            source_file_path,
            width,
            height,
        )
