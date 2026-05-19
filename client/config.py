"""Resolve metascan connection settings for the client.

Precedence (highest first):
  1. ``settings_override`` argument (typically from a MetascanSettings node)
  2. Env vars ``METASCAN_URL`` and ``METASCAN_API_KEY``
  3. ``~/.config/metscan-nodes/config.json``
  4. Defaults: http://localhost:8700, no API key

Empty strings in the settings override or env vars are treated as unset
so lower-priority sources can still supply a value. This matters because
ComfyUI's STRING widget on the settings node sends "" for blank inputs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_DEFAULT_URL = "http://localhost:8700"
_CONFIG_FILE = Path.home() / ".config" / "metscan-nodes" / "config.json"


@dataclass(frozen=True)
class ClientConfig:
    url: str
    api_key: Optional[str]


def _from_file() -> tuple[Optional[str], Optional[str]]:
    if not _CONFIG_FILE.exists():
        return None, None
    try:
        data = json.loads(_CONFIG_FILE.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None, None
    # Type-guard: a hand-edited config can legitimately contain
    # malformed values (e.g., `{"url": 8700}`). Treat anything that
    # isn't a non-empty string as unset rather than propagating it.
    raw_url = data.get("url") if isinstance(data, dict) else None
    raw_key = data.get("api_key") if isinstance(data, dict) else None
    url = raw_url if isinstance(raw_url, str) and raw_url else None
    api_key = raw_key if isinstance(raw_key, str) and raw_key else None
    return url, api_key


def _from_env() -> tuple[Optional[str], Optional[str]]:
    url = os.environ.get("METASCAN_URL") or None
    api_key = os.environ.get("METASCAN_API_KEY") or None
    return url, api_key


def resolve_config(settings_override: Optional[ClientConfig]) -> ClientConfig:
    """Apply the four-tier precedence and return the final config."""

    file_url, file_key = _from_file()
    env_url, env_key = _from_env()

    # Treat "" as unset for the override too.
    override_url = settings_override.url if settings_override and settings_override.url else None
    override_key = (
        settings_override.api_key if settings_override and settings_override.api_key else None
    )

    url = override_url or env_url or file_url or _DEFAULT_URL
    api_key = override_key or env_key or file_key  # may stay None

    return ClientConfig(url=url, api_key=api_key)
