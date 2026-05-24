"""MetascanSelectImage — visually pick any image from a metascan folder.

A trimmed sibling of :class:`MetascanSelectPrompt` for I2I / I2V
workflows where the user just wants a source image, not a saved
prompt. Differences vs. Select Prompt:

- Picks from *all* image-type items in the folder (folder.items
  filtered by extension), not just images with saved prompts.
- No positive / negative outputs at all — there's no prompt state on
  this node, so the always-empty fields would just be noise.
- The frontend sets ``node.imgs = [thumbnail]`` on pick so the image
  appears on the node face immediately — before any workflow run —
  which is the point of this node for I2I / I2V prompt writing.

``target_model`` + ``quality`` are kept so width/height are
computed by the same ``compute_resolution`` rules as Load Prompt
and Select Prompt — downstream samplers expect generator-friendly
buckets, not the raw source dimensions.
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
from mscan_nodes.resolution import I2V_MODELS_FOR_RES, QUALITY_TIERS, compute_resolution


class MetascanSelectImage:
    CATEGORY = "metascan"
    RETURN_TYPES = ("IMAGE", "STRING", "STRING", "INT", "INT")
    RETURN_NAMES = ("image", "name", "source_file_path", "width", "height")
    FUNCTION = "load"

    def __init__(self) -> None:
        # Per-instance image cache keyed by source_file_path so reruns
        # don't re-stream the bytes.
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
        # I2V models (wan, ltx2) are surfaced ONLY on this node — the
        # other load nodes are prompt-oriented and metascan's saved
        # prompts are scoped to image models. Insert before the trailing
        # "any" so the virtual fallback stays at the end of the list.
        if target_models and target_models[-1] == "any":
            target_models = target_models[:-1] + I2V_MODELS_FOR_RES + ["any"]
        return {
            "required": {
                "folder": (folders,),
                "target_model": (target_models,),
                "quality": (QUALITY_TIERS,),
                # Hidden in the UI — the JS extension collapses both
                # via the converted-widget idiom. They still serialize
                # with the workflow JSON so the picked image survives
                # a page reload.
                "image_name": ("STRING", {"default": ""}),
                "source_file_path": ("STRING", {"default": ""}),
            },
        }

    def load(
        self,
        folder: str,
        target_model: str,
        quality: str,
        image_name: str,
        source_file_path: str,
    ) -> dict:
        if folder == OFFLINE_SENTINEL or target_model == OFFLINE_SENTINEL:
            raise RuntimeError(
                "Metascan is offline — bring it up or correct MetascanSettings."
            )
        if not source_file_path:
            raise RuntimeError(
                "No image selected — open the 🖼 Pick image picker to choose one."
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
                image, image_name, source_file_path, width, height,
            ),
        }
