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

    def __init__(self) -> None:
        # Per-node-instance cache of the last live fetch. Holds image,
        # name, and source_file_path — but NOT positive/negative (those
        # live in the editable widgets and are persisted with the
        # workflow JSON) and NOT width/height (always recomputed so
        # the user can tweak ``quality`` without re-hitting metascan).
        self._cache: Optional[dict] = None

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
                "live_load": ("BOOLEAN", {"default": True}),
                "positive_prompt": ("STRING", {"multiline": True, "default": ""}),
                "negative_prompt": ("STRING", {"multiline": True, "default": ""}),
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
        live_load: bool,
        positive_prompt: str,
        negative_prompt: str,
    ) -> dict:
        if live_load:
            fetched = self._fetch_live(
                folder=folder, target_model=target_model,
                selection_mode=selection_mode, prompt_name=prompt_name,
                seed=seed,
            )
            # Cache only what can't be edited via widgets. Prompt text
            # is the user's job from here on out (the JS extension
            # writes the fetched values into the widgets immediately
            # after this returns, so subsequent live_load=False runs
            # read them back from there).
            self._cache = {
                "image": fetched["image"],
                "name": fetched["name"],
                "source_file_path": fetched["source_file_path"],
            }
            positive = fetched["positive"]
            negative = fetched["negative"]
            ui: dict = {
                "positive_prompt": [positive],
                "negative_prompt": [negative],
            }
        else:
            if self._cache is None:
                raise RuntimeError(
                    "live_load is off but no cached prompt is available yet. "
                    "Enable live_load once to populate the cache, then disable."
                )
            positive = positive_prompt
            negative = negative_prompt
            ui = {}

        c = self._cache
        src_h, src_w = c["image"].shape[1], c["image"].shape[2]
        width, height = compute_resolution(src_w, src_h, target_model, quality)

        return {
            "ui": ui,
            "result": (
                c["image"], positive, negative, c["name"],
                c["source_file_path"], width, height,
            ),
        }

    def _fetch_live(
        self,
        folder: str,
        target_model: str,
        selection_mode: SelectionMode,
        prompt_name: str,
        seed: int,
    ) -> dict:
        """Hit metascan for prompt row + source image bytes and return
        the cache-shaped dict that ``load()`` consumes."""
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
        return {
            "image": image,
            "positive": chosen.get("prompt", "") or "",
            "negative": chosen.get("negative") or "",   # SQL NULL → ""
            "name": chosen.get("name", "") or "",
            "source_file_path": source_file_path,
        }
