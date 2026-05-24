"""Filesystem, clock, and client helpers shared by metascan node modules.

Lifted out of save_image.py so move_media.py (and any future node) can
reuse the same code path. Keep this module dependency-light — no PIL,
no torch — so anything pure-Python can import it for fast tests."""

from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path
from typing import Optional


_WSL_MNT_RE = re.compile(r"^/mnt/([a-zA-Z])(/.*)?$")


def wsl_to_native_path(path: str) -> str:
    """Translate WSL ``/mnt/<drive>/...`` paths to Windows ``<DRIVE>:\\...``
    when running on Windows. No-op on Linux/macOS and for paths that
    aren't WSL-style mounts."""
    if sys.platform != "win32":
        return path
    m = _WSL_MNT_RE.match(path)
    if not m:
        return path
    drive = m.group(1).upper()
    rest = m.group(2) or "\\"
    return f"{drive}:{rest}".replace("/", "\\")


def resolve_target_dir(directory: str, subpath: str, now: dt.datetime) -> Path:
    """Return ``Path(directory) / strftime(subpath, now)``, creating the
    directory tree if it doesn't exist."""
    base = Path(wsl_to_native_path(directory))
    if subpath:
        expanded = now.strftime(subpath)
        target = base / expanded
    else:
        target = base
    target.mkdir(parents=True, exist_ok=True)
    return target


def _utc_now() -> dt.datetime:
    """Indirection so tests can patch the clock for strftime checks."""
    return dt.datetime.now()


def _build_client():
    """Construct a MetascanClient using the current settings override
    (or env / config file fallbacks). Imported lazily because the client
    pulls in httpx and we want this module's import to stay cheap."""
    from mscan_client.api import MetascanClient
    from mscan_client.config import resolve_config
    from mscan_nodes.settings import get_current_override
    cfg = resolve_config(settings_override=get_current_override())
    return MetascanClient(config=cfg, timeout=5.0)


def _comfy_temp_dir() -> Optional[Path]:
    """Return ComfyUI's temp directory, or None if we're not running
    under ComfyUI (tests, scripts)."""
    try:
        import folder_paths  # ComfyUI puts its root on sys.path
        return Path(folder_paths.get_temp_directory())
    except Exception:  # noqa: BLE001 — any failure means no UI temp dir
        return None
