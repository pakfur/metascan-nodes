"""MetascanSelectPrompt — visually pick a saved prompt from metascan.

A simpler sibling of :class:`MetascanLoadPrompt`. The frontend extension
adds a 🖼 picker button; clicking it opens a thumbnail overlay that
fetches prompts for the current folder + target_model. When the user
clicks a row the JS writes four widget values:

- ``prompt_name`` (hidden)
- ``source_file_path`` (hidden — what the workflow run uses to fetch
  the source image)
- ``positive_prompt`` (editable, populated from the saved prompt)
- ``negative_prompt`` (editable, populated from the saved negative)

Workflow execute then just streams the source image bytes for
``source_file_path`` (cached on the instance) and recomputes resolution
from the current ``quality`` + ``target_model`` — no run-time prompt
search and no ``ui`` overrides, so the user's edits to positive /
negative are the canonical truth from pick-time onward.

If no prompt has been picked yet (empty ``source_file_path``) the node
raises ``RuntimeError`` with a clear message rather than silently
returning garbage.
"""

from __future__ import annotations

from typing import Optional

from mscan_client.cache import (
    combo_folders,
    combo_target_models,
    OFFLINE_SENTINEL,
)
from mscan_nodes.load_from_folder import bytes_to_tensor
from mscan_nodes.load_prompt import _build_client
from mscan_nodes.resolution import QUALITY_TIERS, compute_resolution


class MetascanSelectPrompt:
    CATEGORY = "metascan"
    RETURN_TYPES = ("IMAGE", "STRING", "STRING", "STRING", "STRING", "INT", "INT")
    RETURN_NAMES = ("image", "positive", "negative", "name", "source_file_path", "width", "height")
    FUNCTION = "load"

    def __init__(self) -> None:
        # Per-instance image cache keyed by source_file_path so reruns
        # don't re-stream the bytes. Prompt text doesn't live here —
        # it's in the editable positive_prompt / negative_prompt
        # widgets, persisted with the workflow JSON.
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
                # Hidden in the UI — the JS extension collapses both
                # via the converted-widget idiom. They still serialize
                # with the workflow JSON so the picked prompt survives
                # a page reload.
                "prompt_name": ("STRING", {"default": ""}),
                "source_file_path": ("STRING", {"default": ""}),
                "quality": (QUALITY_TIERS,),
                "positive_prompt": ("STRING", {"multiline": True, "default": ""}),
                "negative_prompt": ("STRING", {"multiline": True, "default": ""}),
            },
        }

    def load(
        self,
        folder: str,
        target_model: str,
        prompt_name: str,
        source_file_path: str,
        quality: str,
        positive_prompt: str,
        negative_prompt: str,
    ) -> dict:
        if folder == OFFLINE_SENTINEL or target_model == OFFLINE_SENTINEL:
            raise RuntimeError(
                "Metascan is offline — bring it up or correct MetascanSettings."
            )
        if not source_file_path or not prompt_name:
            raise RuntimeError(
                "No prompt selected — open the 🖼 Pick prompt picker to choose one."
            )

        if self._cache and self._cache.get("source_file_path") == source_file_path:
            image = self._cache["image"]
        else:
            client = _build_client()
            raw = client.stream_bytes(source_file_path)
            image = bytes_to_tensor(raw)
            self._cache = {"source_file_path": source_file_path, "image": image}

        src_h, src_w = image.shape[1], image.shape[2]
        width, height = compute_resolution(src_w, src_h, target_model, quality)

        return {
            "result": (
                image, positive_prompt, negative_prompt, prompt_name,
                source_file_path, width, height,
            ),
        }
