"""ComfyUI custom_nodes entry point for metscan-nodes.

ComfyUI imports this package on startup and looks for the two
mapping dicts below to register the nodes in its menu.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from mscan_nodes.settings import MetascanSettings
from mscan_nodes.save_image import MetascanSaveImage
from mscan_nodes.load_from_folder import MetascanLoadFromFolder
from mscan_nodes.load_prompt import MetascanLoadPrompt

NODE_CLASS_MAPPINGS = {
    "MetascanSettings": MetascanSettings,
    "MetascanSaveImage": MetascanSaveImage,
    "MetascanLoadFromFolder": MetascanLoadFromFolder,
    "MetascanLoadPrompt": MetascanLoadPrompt,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MetascanSettings": "Metascan · Settings",
    "MetascanSaveImage": "Metascan · Save Image",
    "MetascanLoadFromFolder": "Metascan · Load From Folder",
    "MetascanLoadPrompt": "Metascan · Load Prompt",
}

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
