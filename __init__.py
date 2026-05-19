"""ComfyUI custom_nodes entry point for metscan-nodes.

The actual NODE_CLASS_MAPPINGS / NODE_DISPLAY_NAME_MAPPINGS exports
are wired up in Task 20. This file exists now so ComfyUI's loader
sees a valid Python package and so test modules can `from client...`
imports succeed at import-time.
"""

NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}
WEB_DIRECTORY = None

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
