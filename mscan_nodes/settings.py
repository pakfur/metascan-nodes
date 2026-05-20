"""MetascanSettings sentinel node.

Drop one of these into a ComfyUI workflow to override the URL and/or
API key for that workflow run. The node's apply() method stores the
values in a module-level _OVERRIDE; downstream nodes call
``get_current_override()`` and pass the result to ``resolve_config``,
which gives the override top priority over env vars and the config
file (see client/config.py for the resolution chain).

Empty-string inputs are treated as "no override" so a node with blank
fields doesn't blank out lower-priority sources. Module-level state is
a deliberate trade-off: ComfyUI executes nodes within one process per
workflow run; the alternative (threading values through hidden inputs
on every consumer node) makes the rest of the wiring much noisier.
"""

from __future__ import annotations

from typing import Optional

from mscan_client.config import ClientConfig

_OVERRIDE: Optional[ClientConfig] = None


def get_current_override() -> Optional[ClientConfig]:
    return _OVERRIDE


class MetascanSettings:
    CATEGORY = "metascan"
    OUTPUT_NODE = True
    RETURN_TYPES: tuple = ()
    FUNCTION = "apply"

    @classmethod
    def INPUT_TYPES(cls) -> dict:
        return {
            "required": {
                "url": ("STRING", {"default": "http://localhost:8700"}),
                "api_key": ("STRING", {"default": ""}),
            }
        }

    def apply(self, url: str, api_key: str) -> tuple:
        global _OVERRIDE
        if not url and not api_key:
            _OVERRIDE = None
        else:
            _OVERRIDE = ClientConfig(url=url or "", api_key=api_key or None)
        return ()
