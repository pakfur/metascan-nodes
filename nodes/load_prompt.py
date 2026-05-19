"""MetascanLoadPrompt — load a saved prompt from metascan's prompt library.

Pure ``select_prompt`` helper + ComfyUI integration class. No image or
tensor work — this node returns four strings (positive, negative, the
chosen prompt's name, and the source media path it was saved against).
"""

from __future__ import annotations

from typing import Literal, Optional

from client.api import MetascanClient
from client.cache import (
    combo_folders,
    combo_target_models,
    OFFLINE_SENTINEL,
)
from client.config import resolve_config
from nodes.settings import get_current_override


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
    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("positive", "negative", "name", "source_file_path")
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
            },
        }

    def load(
        self,
        folder: str,
        target_model: str,
        selection_mode: SelectionMode,
        prompt_name: str,
        seed: int,
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

        return (
            chosen.get("prompt", "") or "",
            chosen.get("negative") or "",   # SQL NULL → ""
            chosen.get("name", "") or "",
            chosen.get("file_path", "") or "",
        )
