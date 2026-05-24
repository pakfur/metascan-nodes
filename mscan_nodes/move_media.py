"""MetascanMoveMedia — relocate any media file produced upstream into
a metascan-watched directory and embed prompt/workflow metadata.

Two-node workflow: a producer (e.g. VHS Combine Video) emits a
VHS_FILENAMES socket; this node consumes it, moves or copies each file
into a metascan-watched dir, and best-effort embeds metadata via PIL
(PNG) or ffmpeg (video containers)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from mscan_client.cache import combo_directories, OFFLINE_SENTINEL
from mscan_nodes._shared import _build_client

logger = logging.getLogger(__name__)


class MetascanMoveMedia:
    CATEGORY = "metascan"
    OUTPUT_NODE = True
    RETURN_TYPES = ("VHS_FILENAMES", "STRING")
    RETURN_NAMES = ("filenames", "file_path")
    FUNCTION = "process"

    @classmethod
    def INPUT_TYPES(cls) -> dict:
        try:
            dirs = combo_directories(_build_client())
        except Exception:  # noqa: BLE001 — defensive at editor-load time
            dirs = [OFFLINE_SENTINEL]
        return {
            "required": {
                "filenames": ("VHS_FILENAMES",),
                "directory": (dirs,),
                "subpath": ("STRING", {"default": ""}),
                "operation": (["move", "copy"], {"default": "move"}),
                "save_metadata": (["always", "if_missing"], {"default": "if_missing"}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    def process(
        self,
        filenames,
        directory: str,
        subpath: str,
        operation: str,
        save_metadata: str,
        prompt: Optional[dict] = None,
        extra_pnginfo: Optional[dict] = None,
    ) -> dict:
        if directory == OFFLINE_SENTINEL:
            raise RuntimeError(
                "Metascan is offline — cannot resolve a watched directory. "
                "Bring metascan up or add a MetascanSettings node with the "
                "correct URL."
            )
        # Real logic lands in Task 6.
        raise NotImplementedError
