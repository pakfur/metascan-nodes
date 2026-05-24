# MetascanMoveMedia Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `MetascanMoveMedia`, a ComfyUI node that consumes a `VHS_FILENAMES` input, moves or copies each file into a metascan-watched directory (with optional `subpath` strftime expansion), and embeds prompt/workflow metadata using a per-extension dispatch (PIL for PNG, ffmpeg `-c copy` for video containers, no-op for everything else).

**Architecture:** New node lives in `mscan_nodes/move_media.py`. Filesystem-only and clock helpers (`resolve_target_dir`, `wsl_to_native_path`, `_utc_now`, `_build_client`, `_comfy_temp_dir`) get lifted out of `save_image.py` into a new `mscan_nodes/_shared.py` so both nodes share one copy. All file writes go through a `.partial` staging name then `os.replace` so a filesystem watcher (or a mid-write crash) never sees a half-written file. ffmpeg is best-effort: if it's missing or fails, the file still lands in the metascan dir and the metadata step is logged as skipped.

**Tech Stack:** Python 3.12 / ComfyUI custom-node API / PIL + ffmpeg(+ffprobe) subprocess / pytest with `tmp_path` and `monkeypatch`; respx for the metascan-API mocks used by the dropdown.

**Working directory for all commands:** `/Users/jk/gws/metascan-nodes/`. Activate the venv first: `source .venv/bin/activate`.

**Reference spec:** `docs/superpowers/specs/2026-05-23-metascan-move-media-design.md`.

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `mscan_nodes/_shared.py` | Create | Filesystem + clock + client helpers lifted from `save_image.py` (no behavior change). |
| `mscan_nodes/save_image.py` | Modify | Re-import the lifted helpers from `_shared`; no other changes. PIL-specific helpers (`tensor_to_pil`, `build_png_info`) stay here. |
| `mscan_nodes/move_media.py` | Create | `MetascanMoveMedia` class + the four helpers: `relocate_file`, `dispatch_metadata`, `embed_png_metadata`, `embed_video_metadata` (+ `_video_has_metadata`, `_tail_stderr`). |
| `__init__.py` | Modify | Import and register `MetascanMoveMedia` in `NODE_CLASS_MAPPINGS` and `NODE_DISPLAY_NAME_MAPPINGS`. |
| `tests/test_move_media.py` | Create | Unit + integration tests for the helpers and the node. Mirrors `tests/test_save_image.py` patterns. |
| `docs/nodes/move-media.md` | Create | User-facing node documentation. Same shape as `docs/nodes/save-image.md`. |
| `docs/nodes/save-image.md` | Modify | Add a one-line "See also" pointer to Move Media at the top. |
| `README.md` | Modify | Add the new node to the node list; refresh the stale "Four nodes:" header (currently understated — verify exact count vs `NODE_CLASS_MAPPINGS` before editing). |

---

## Task 1: Lift shared helpers into `mscan_nodes/_shared.py`

This is a pure refactor — code moves, public API unchanged. `tests/test_save_image.py` is the safety net.

**Files:**
- Create: `mscan_nodes/_shared.py`
- Modify: `mscan_nodes/save_image.py` (delete the lifted defs, replace with imports)

- [ ] **Step 1: Create `mscan_nodes/_shared.py` with the lifted helpers**

Create the file with exactly this content:

```python
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
```

- [ ] **Step 2: Slim `mscan_nodes/save_image.py` to import from `_shared`**

Replace the top of `save_image.py` (lines 1–112 in the current file — the helper definitions and the `_utc_now` / `_build_client` / `_comfy_temp_dir` block) with the leaner version below. Keep everything from `def _write_previews(` onward unchanged.

The new top of `save_image.py`:

```python
"""MetascanSaveImage — writes PNG batches into a metascan-watched dir.

Filesystem and clock helpers live in mscan_nodes._shared so MoveMedia
can reuse them. This module owns the PIL/torch glue (``tensor_to_pil``,
``build_png_info``) and the node class itself."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Optional

import numpy as np
import torch
from PIL import Image
from PIL.PngImagePlugin import PngInfo

# Re-export the helpers from _shared so existing imports
# ``from mscan_nodes.save_image import wsl_to_native_path`` (used by
# tests/test_save_image.py) keep working unchanged.
from mscan_nodes._shared import (
    wsl_to_native_path,
    resolve_target_dir,
    _utc_now,
    _build_client,
    _comfy_temp_dir,
)


def tensor_to_pil(image: torch.Tensor) -> Image.Image:
    """Convert a single H×W×3 float tensor (ComfyUI's IMAGE convention,
    range [0, 1]) to an 8-bit RGB PIL image. Out-of-range values clamp
    silently — upstream nodes occasionally produce slightly negative or
    slightly >1 values from sampler noise and we don't want to fail
    the save over a rounding artifact."""
    arr = image.detach().cpu().numpy()
    arr = np.clip(arr * 255.0, 0.0, 255.0).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def build_png_info(prompt: Optional[dict], workflow: Optional[dict]) -> PngInfo:
    """Build a ``PngInfo`` carrying ComfyUI's ``prompt`` and (optionally)
    ``workflow`` tEXt chunks. The format matches what ComfyUI's core
    SaveImage writes."""
    info = PngInfo()
    if prompt is not None:
        info.add_text("prompt", json.dumps(prompt))
    if workflow is not None:
        info.add_text("workflow", json.dumps(workflow))
    return info


# --- ComfyUI node integration --------------------------------------------

from mscan_client.cache import combo_directories, OFFLINE_SENTINEL
```

Then leave `def _write_previews(`, `class MetascanSaveImage`, and the rest of the file untouched.

The test file imports `wsl_to_native_path`, `resolve_target_dir`, etc. from `mscan_nodes.save_image` (see `tests/test_save_image.py:12-17`); those re-exports keep working because we imported them at module top.

- [ ] **Step 3: Run the SaveImage test suite to verify the refactor is invisible**

Run: `pytest tests/test_save_image.py -v`
Expected: All existing tests pass (same count as before).

- [ ] **Step 4: Commit the refactor**

```bash
git add mscan_nodes/_shared.py mscan_nodes/save_image.py
git commit -m "Lift shared helpers out of save_image into _shared module"
```

---

## Task 2: Stub `MetascanMoveMedia` with INPUT_TYPES and offline guard

Smallest possible class that loads in ComfyUI and raises the right error when called offline. Lets the editor pick up the node before we write any business logic.

**Files:**
- Create: `mscan_nodes/move_media.py`
- Modify: `__init__.py`
- Test: `tests/test_move_media.py`

- [ ] **Step 1: Create `tests/test_move_media.py` with the offline-sentinel test**

```python
"""Tests for MetascanMoveMedia.

Helpers (relocate_file, dispatch_metadata, embed_*) are tested as pure
functions with tmp_path and mocked subprocess. The node class is tested
end-to-end with the shared respx/conftest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from mscan_client.cache import OFFLINE_SENTINEL


def test_process_offline_sentinel_raises():
    """When metascan was unreachable at INPUT_TYPES time and the user
    runs the workflow anyway, fail loudly with the same wording as
    MetascanSaveImage."""
    from mscan_nodes.move_media import MetascanMoveMedia
    with pytest.raises(RuntimeError, match="offline"):
        MetascanMoveMedia().process(
            filenames=(True, []),
            directory=OFFLINE_SENTINEL,
            subpath="",
            operation="move",
            save_metadata="if_missing",
            prompt=None,
            extra_pnginfo=None,
        )
```

- [ ] **Step 2: Run the test to verify it fails with ImportError**

Run: `pytest tests/test_move_media.py::test_process_offline_sentinel_raises -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mscan_nodes.move_media'`.

- [ ] **Step 3: Create the minimal `mscan_nodes/move_media.py`**

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_move_media.py::test_process_offline_sentinel_raises -v`
Expected: PASS.

- [ ] **Step 5: Register the node in `__init__.py`**

Edit `__init__.py`. Add the import next to the others:

```python
from mscan_nodes.move_media import MetascanMoveMedia
```

Add the class to `NODE_CLASS_MAPPINGS`:

```python
"MetascanMoveMedia": MetascanMoveMedia,
```

Add the display name to `NODE_DISPLAY_NAME_MAPPINGS`:

```python
"MetascanMoveMedia": "Metascan · Move Media",
```

- [ ] **Step 6: Verify all existing tests still pass after registering the new class**

Run: `pytest tests/ -v`
Expected: All existing tests pass; `test_process_offline_sentinel_raises` passes.

- [ ] **Step 7: Commit**

```bash
git add mscan_nodes/move_media.py tests/test_move_media.py __init__.py
git commit -m "Add MetascanMoveMedia skeleton with offline-sentinel guard"
```

---

## Task 3: Implement `relocate_file` with TDD

Pure-Python helper. Move/copy a source path into a destination dir using `.partial` staging then `os.replace`. Collision counter uses `max + 1` over zero-padded `_NN` suffixes.

**Files:**
- Modify: `mscan_nodes/move_media.py`
- Modify: `tests/test_move_media.py`

- [ ] **Step 1: Write the failing test for plain move**

Append to `tests/test_move_media.py`:

```python
# ----- relocate_file -----

def test_relocate_file_move_removes_source(tmp_path):
    from mscan_nodes.move_media import relocate_file
    src = tmp_path / "src.bin"
    src.write_bytes(b"hello")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    out = relocate_file(src, dst_dir, "move")

    assert out == dst_dir / "src.bin"
    assert out.read_bytes() == b"hello"
    assert not src.exists()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_move_media.py::test_relocate_file_move_removes_source -v`
Expected: FAIL with `ImportError: cannot import name 'relocate_file'`.

- [ ] **Step 3: Implement `relocate_file`**

Add to `mscan_nodes/move_media.py` (above the class definition, below the imports):

```python
import os
import shutil


def relocate_file(src: Path, dst_dir: Path, operation: str) -> Path:
    """Move or copy ``src`` into ``dst_dir``, returning the final path.

    Writes to a ``<name>.partial`` staging path first, then ``os.replace``s
    to the final name. The staging step means a filesystem watcher (or
    a mid-write crash on a cross-device copy) never sees a half-written
    file under its real name. On destination name collision, appends
    ``_NN`` using max-existing-index + 1 (never reuses a gap, so we
    never overwrite a surviving file)."""
    src = Path(src)
    dst_dir = Path(dst_dir)
    final_name = _pick_final_name(src.name, dst_dir)
    staging = dst_dir / (final_name + ".partial")
    if operation == "move":
        shutil.move(str(src), str(staging))
    elif operation == "copy":
        shutil.copy2(str(src), str(staging))
    else:
        raise ValueError(f"operation must be 'move' or 'copy', got {operation!r}")
    final_path = dst_dir / final_name
    os.replace(staging, final_path)
    return final_path


def _pick_final_name(name: str, dst_dir: Path) -> str:
    """Return ``name`` if ``dst_dir / name`` is free; otherwise append
    ``_NN`` using max(existing index for this stem) + 1. Widens to 3
    digits if 99 is exhausted."""
    target = dst_dir / name
    if not target.exists():
        return name
    stem = Path(name).stem
    suffix = Path(name).suffix
    max_idx = -1
    pattern = f"{stem}_*{suffix}"
    for p in dst_dir.glob(pattern):
        tail = p.stem[len(stem) + 1:]  # the bit after "<stem>_"
        try:
            idx = int(tail)
        except ValueError:
            continue
        if idx > max_idx:
            max_idx = idx
    next_idx = max_idx + 1
    width = 2 if next_idx < 100 else 3
    return f"{stem}_{next_idx:0{width}d}{suffix}"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_move_media.py::test_relocate_file_move_removes_source -v`
Expected: PASS.

- [ ] **Step 5: Add the copy test**

Append:

```python
def test_relocate_file_copy_preserves_source(tmp_path):
    from mscan_nodes.move_media import relocate_file
    src = tmp_path / "src.bin"
    src.write_bytes(b"hello")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    out = relocate_file(src, dst_dir, "copy")

    assert out.read_bytes() == b"hello"
    assert src.exists()  # copy leaves source intact
    assert src.read_bytes() == b"hello"
```

- [ ] **Step 6: Run the copy test**

Run: `pytest tests/test_move_media.py::test_relocate_file_copy_preserves_source -v`
Expected: PASS.

- [ ] **Step 7: Add the collision-counter test**

Append:

```python
def test_relocate_file_collision_uses_max_plus_one(tmp_path):
    """If foo.mp4 and foo_00.mp4 already exist, the next save becomes
    foo_01.mp4 — max+1, not len+0."""
    from mscan_nodes.move_media import relocate_file
    src = tmp_path / "foo.mp4"
    src.write_bytes(b"new")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    (dst_dir / "foo.mp4").write_bytes(b"first")
    (dst_dir / "foo_00.mp4").write_bytes(b"second")

    out = relocate_file(src, dst_dir, "move")

    assert out.name == "foo_01.mp4"
    assert out.read_bytes() == b"new"
```

- [ ] **Step 8: Run the collision test**

Run: `pytest tests/test_move_media.py::test_relocate_file_collision_uses_max_plus_one -v`
Expected: PASS.

- [ ] **Step 9: Add the gap-skipping test**

Append:

```python
def test_relocate_file_collision_skips_gaps(tmp_path):
    """Gap in the existing sequence (00, 05) must not cause us to reuse
    01 — we always go past the max, so a deletion-induced gap doesn't
    overwrite a surviving file in some other downstream save run."""
    from mscan_nodes.move_media import relocate_file
    src = tmp_path / "foo.mp4"
    src.write_bytes(b"new")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    (dst_dir / "foo.mp4").write_bytes(b"a")
    (dst_dir / "foo_00.mp4").write_bytes(b"b")
    (dst_dir / "foo_05.mp4").write_bytes(b"c")

    out = relocate_file(src, dst_dir, "move")

    assert out.name == "foo_06.mp4"
```

- [ ] **Step 10: Run the gap-skipping test**

Run: `pytest tests/test_move_media.py::test_relocate_file_collision_skips_gaps -v`
Expected: PASS.

- [ ] **Step 11: Add the staging-cleanup test**

Append:

```python
def test_relocate_file_no_partial_left_on_success(tmp_path):
    from mscan_nodes.move_media import relocate_file
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    relocate_file(src, dst_dir, "copy")

    assert list(dst_dir.glob("*.partial")) == []
```

- [ ] **Step 12: Run the staging-cleanup test**

Run: `pytest tests/test_move_media.py::test_relocate_file_no_partial_left_on_success -v`
Expected: PASS.

- [ ] **Step 13: Add the crash-leaves-partial test**

Append:

```python
def test_relocate_file_partial_visible_on_crash(tmp_path, monkeypatch):
    """If os.replace blows up mid-rename, the .partial file is left in
    place (for forensics) and the final name is NOT created."""
    from mscan_nodes import move_media
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    def boom(a, b):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(move_media.os, "replace", boom)
    with pytest.raises(OSError, match="simulated rename failure"):
        move_media.relocate_file(src, dst_dir, "copy")

    assert (dst_dir / "src.bin.partial").exists()
    assert not (dst_dir / "src.bin").exists()
```

- [ ] **Step 14: Run the crash test**

Run: `pytest tests/test_move_media.py::test_relocate_file_partial_visible_on_crash -v`
Expected: PASS.

- [ ] **Step 15: Add the invalid-operation test**

Append:

```python
def test_relocate_file_rejects_unknown_operation(tmp_path):
    from mscan_nodes.move_media import relocate_file
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    with pytest.raises(ValueError, match="must be 'move' or 'copy'"):
        relocate_file(src, dst_dir, "yeet")
```

- [ ] **Step 16: Run the invalid-operation test**

Run: `pytest tests/test_move_media.py::test_relocate_file_rejects_unknown_operation -v`
Expected: PASS.

- [ ] **Step 17: Commit**

```bash
git add mscan_nodes/move_media.py tests/test_move_media.py
git commit -m "Add relocate_file helper with .partial staging and collision counter"
```

---

## Task 4: Implement `dispatch_metadata` and `embed_png_metadata`

Per-extension routing plus the PNG embed branch.

**Files:**
- Modify: `mscan_nodes/move_media.py`
- Modify: `tests/test_move_media.py`

- [ ] **Step 1: Write the dispatch-routes-by-extension test**

Append:

```python
# ----- dispatch_metadata -----

def test_dispatch_metadata_routes_png_to_png_helper(tmp_path, monkeypatch):
    from mscan_nodes import move_media
    called = {}

    def fake_png(path, prompt, workflow, mode):
        called["args"] = (path, prompt, workflow, mode)
        return "embedded"

    monkeypatch.setattr(move_media, "embed_png_metadata", fake_png)
    p = tmp_path / "x.png"
    p.touch()

    out = move_media.dispatch_metadata(p, {"a": 1}, {"w": 2}, "always")

    assert out == "embedded"
    assert called["args"] == (p, {"a": 1}, {"w": 2}, "always")


@pytest.mark.parametrize("ext", [".mp4", ".mov", ".mkv", ".webm", ".gif"])
def test_dispatch_metadata_routes_video_exts_to_video_helper(tmp_path, monkeypatch, ext):
    from mscan_nodes import move_media
    called = {}

    def fake_video(path, prompt, workflow, mode):
        called["args"] = (path, prompt, workflow, mode)
        return "embedded"

    monkeypatch.setattr(move_media, "embed_video_metadata", fake_video)
    p = tmp_path / f"x{ext}"
    p.touch()

    out = move_media.dispatch_metadata(p, None, None, "if_missing")

    assert out == "embedded"
    assert called["args"][0] == p


def test_dispatch_metadata_unknown_extension_returns_skipped(tmp_path):
    from mscan_nodes.move_media import dispatch_metadata
    p = tmp_path / "x.txt"
    p.touch()
    assert dispatch_metadata(p, None, None, "always") == "skipped_unsupported"


def test_dispatch_metadata_is_case_insensitive(tmp_path, monkeypatch):
    """A `.MP4` file (uppercase) should still hit the video branch."""
    from mscan_nodes import move_media
    monkeypatch.setattr(move_media, "embed_video_metadata", lambda *a, **kw: "embedded")
    p = tmp_path / "X.MP4"
    p.touch()
    assert move_media.dispatch_metadata(p, None, None, "always") == "embedded"
```

- [ ] **Step 2: Run the dispatch tests to verify they fail**

Run: `pytest tests/test_move_media.py -k dispatch_metadata -v`
Expected: FAIL with `ImportError: cannot import name 'dispatch_metadata'`.

- [ ] **Step 3: Implement `dispatch_metadata` and a stub `embed_png_metadata` / `embed_video_metadata`**

Add to `mscan_nodes/move_media.py` (after `_pick_final_name`):

```python
_VIDEO_EXTS = frozenset({".mp4", ".mov", ".mkv", ".webm", ".gif"})


def dispatch_metadata(
    path: Path,
    prompt,
    workflow,
    mode: str,
) -> str:
    """Route to the per-format embed helper. Returns a status string
    used by the node's UI text line: ``embedded``, ``skipped_present``,
    ``skipped_unsupported``, ``skipped_no_ffmpeg``, or ``skipped_error``."""
    ext = path.suffix.lower()
    if ext == ".png":
        return embed_png_metadata(path, prompt, workflow, mode)
    if ext in _VIDEO_EXTS:
        return embed_video_metadata(path, prompt, workflow, mode)
    return "skipped_unsupported"


def embed_png_metadata(
    path: Path,
    prompt,
    workflow,
    mode: str,
) -> str:
    raise NotImplementedError  # implemented in next step


def embed_video_metadata(
    path: Path,
    prompt,
    workflow,
    mode: str,
) -> str:
    raise NotImplementedError  # implemented in Task 5
```

- [ ] **Step 4: Run the dispatch tests to verify they pass**

Run: `pytest tests/test_move_media.py -k dispatch_metadata -v`
Expected: All four dispatch tests PASS.

- [ ] **Step 5: Write the PNG embed tests**

Append:

```python
# ----- embed_png_metadata -----

import json
from PIL import Image
from PIL.PngImagePlugin import PngInfo


def _png_with_text(path: Path, **text: str) -> None:
    info = PngInfo()
    for k, v in text.items():
        info.add_text(k, v)
    Image.new("RGB", (4, 4)).save(path, pnginfo=info)


def test_embed_png_metadata_always_overwrites_existing(tmp_path):
    from mscan_nodes.move_media import embed_png_metadata
    p = tmp_path / "x.png"
    _png_with_text(p, prompt='{"old": 1}', workflow='{"old": "w"}')

    out = embed_png_metadata(p, {"new": 2}, {"nodes": []}, "always")

    assert out == "embedded"
    reread = Image.open(p)
    reread.load()
    assert json.loads(reread.info["prompt"]) == {"new": 2}
    assert json.loads(reread.info["workflow"]) == {"nodes": []}


def test_embed_png_metadata_if_missing_skips_when_prompt_present(tmp_path):
    from mscan_nodes.move_media import embed_png_metadata
    p = tmp_path / "x.png"
    _png_with_text(p, prompt='{"keep": 1}')

    out = embed_png_metadata(p, {"new": 2}, {"nodes": []}, "if_missing")

    assert out == "skipped_present"
    reread = Image.open(p)
    reread.load()
    assert json.loads(reread.info["prompt"]) == {"keep": 1}
    assert "workflow" not in reread.info


def test_embed_png_metadata_if_missing_skips_when_only_workflow_present(tmp_path):
    """A PNG with workflow but no prompt also counts as present — we
    skip rather than partially overwriting."""
    from mscan_nodes.move_media import embed_png_metadata
    p = tmp_path / "x.png"
    _png_with_text(p, workflow='{"existing": true}')

    out = embed_png_metadata(p, {"p": 1}, {"w": 1}, "if_missing")

    assert out == "skipped_present"


def test_embed_png_metadata_if_missing_writes_when_absent(tmp_path):
    from mscan_nodes.move_media import embed_png_metadata
    p = tmp_path / "x.png"
    Image.new("RGB", (4, 4)).save(p)  # no tEXt chunks

    out = embed_png_metadata(p, {"p": 1}, {"w": 2}, "if_missing")

    assert out == "embedded"
    reread = Image.open(p)
    reread.load()
    assert json.loads(reread.info["prompt"]) == {"p": 1}
    assert json.loads(reread.info["workflow"]) == {"w": 2}


def test_embed_png_metadata_no_meta_partial_left(tmp_path):
    from mscan_nodes.move_media import embed_png_metadata
    p = tmp_path / "x.png"
    Image.new("RGB", (4, 4)).save(p)

    embed_png_metadata(p, {"p": 1}, None, "always")

    assert list(tmp_path.glob("*.meta.partial")) == []
```

- [ ] **Step 6: Run the PNG tests to verify they fail**

Run: `pytest tests/test_move_media.py -k embed_png_metadata -v`
Expected: All five tests FAIL with `NotImplementedError`.

- [ ] **Step 7: Implement `embed_png_metadata`**

Replace the stub in `mscan_nodes/move_media.py` with the real implementation. Also add the necessary imports at the top of the module — extend the import block at the top with:

```python
import json
from PIL import Image
from PIL.PngImagePlugin import PngInfo
```

Then replace `embed_png_metadata`:

```python
def embed_png_metadata(
    path: Path,
    prompt,
    workflow,
    mode: str,
) -> str:
    """Re-save the PNG at ``path`` with new prompt/workflow tEXt chunks.

    In ``if_missing`` mode, skip if either chunk is already present —
    we don't partially overwrite. In ``always`` mode, overwrite both.
    Write via ``.meta.partial`` then ``os.replace`` for the same
    watcher-safety reason as the relocation step."""
    img = Image.open(path)
    existing = dict(img.info)  # PIL surfaces tEXt chunks here
    if mode == "if_missing" and ("prompt" in existing or "workflow" in existing):
        img.close()
        return "skipped_present"
    info = PngInfo()
    if prompt is not None:
        info.add_text("prompt", json.dumps(prompt))
    if workflow is not None:
        info.add_text("workflow", json.dumps(workflow))
    img.load()  # decode pixel data before we close the source file handle
    staging = path.with_suffix(path.suffix + ".meta.partial")
    img.save(staging, pnginfo=info, format="PNG")
    img.close()
    os.replace(staging, path)
    return "embedded"
```

- [ ] **Step 8: Run the PNG tests to verify they pass**

Run: `pytest tests/test_move_media.py -k embed_png_metadata -v`
Expected: All five tests PASS.

- [ ] **Step 9: Commit**

```bash
git add mscan_nodes/move_media.py tests/test_move_media.py
git commit -m "Add dispatch_metadata and PNG embed branch"
```

---

## Task 5: Implement `embed_video_metadata` (ffmpeg, mocked subprocess)

The ffmpeg branch. Subprocess is mocked throughout — we test our orchestration, not ffmpeg itself.

**Files:**
- Modify: `mscan_nodes/move_media.py`
- Modify: `tests/test_move_media.py`

- [ ] **Step 1: Write the no-ffmpeg test**

Append:

```python
# ----- embed_video_metadata -----

def test_embed_video_metadata_no_ffmpeg_returns_skipped(tmp_path, monkeypatch):
    from mscan_nodes import move_media
    p = tmp_path / "x.mp4"
    p.write_bytes(b"fake video bytes")

    monkeypatch.setattr(move_media.shutil, "which", lambda name: None)
    # subprocess.run should NOT be called when ffmpeg is missing.
    monkeypatch.setattr(
        move_media.subprocess, "run",
        lambda *a, **kw: pytest.fail("subprocess.run called despite missing ffmpeg"),
    )

    out = move_media.embed_video_metadata(p, {"p": 1}, {"w": 2}, "always")

    assert out == "skipped_no_ffmpeg"
    assert p.read_bytes() == b"fake video bytes"  # untouched
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_move_media.py::test_embed_video_metadata_no_ffmpeg_returns_skipped -v`
Expected: FAIL with `NotImplementedError` (or `AttributeError: module 'mscan_nodes.move_media' has no attribute 'subprocess'`).

- [ ] **Step 3: Add `subprocess` import and implement the missing-ffmpeg branch**

In `mscan_nodes/move_media.py`, extend the imports at the top:

```python
import subprocess
```

Replace the `embed_video_metadata` stub with:

```python
def embed_video_metadata(
    path: Path,
    prompt,
    workflow,
    mode: str,
) -> str:
    """Re-mux ``path`` with new comment metadata via ffmpeg ``-c copy``.

    Best-effort: if ffmpeg is missing or fails, the file is left
    untouched and we return a status the caller can surface in the UI.
    The comment payload is JSON of ``{"prompt": ..., "workflow": ...}``,
    matching the key VHS itself writes."""
    if shutil.which("ffmpeg") is None:
        logger.warning("ffmpeg not found on PATH — skipping metadata embed for %s", path)
        return "skipped_no_ffmpeg"
    if mode == "if_missing" and _video_has_metadata(path):
        return "skipped_present"
    payload = json.dumps({"prompt": prompt, "workflow": workflow})
    staging = path.with_suffix(path.suffix + ".meta.partial")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(path),
        "-c", "copy",
        "-metadata", f"comment={payload}",
        str(staging),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffmpeg metadata embed failed for %s: %s", path, _tail_stderr(exc))
        try:
            staging.unlink()
        except FileNotFoundError:
            pass
        return "skipped_error"
    os.replace(staging, path)
    return "embedded"


def _video_has_metadata(path: Path) -> bool:
    """Return True iff ffprobe sees any of comment/prompt/workflow in
    the file's format-level tags. Treat ffprobe-missing as 'no metadata
    detected' — the ffmpeg-missing check above prevents us from writing
    in that case anyway."""
    if shutil.which("ffprobe") is None:
        return False
    cmd = ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(path)]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    try:
        tags = json.loads(result.stdout).get("format", {}).get("tags", {}) or {}
    except json.JSONDecodeError:
        return False
    lowered = {k.lower() for k in tags.keys()}
    return bool(lowered & {"comment", "prompt", "workflow"})


def _tail_stderr(exc) -> str:
    stderr = getattr(exc, "stderr", b"") or b""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    return stderr[-500:]
```

- [ ] **Step 4: Run the no-ffmpeg test to verify it passes**

Run: `pytest tests/test_move_media.py::test_embed_video_metadata_no_ffmpeg_returns_skipped -v`
Expected: PASS.

- [ ] **Step 5: Add the always-mode invokes-ffmpeg test**

Append:

```python
def test_embed_video_metadata_always_invokes_ffmpeg(tmp_path, monkeypatch):
    """Always mode should shell out to ffmpeg with -c copy and -metadata,
    then os.replace the staging file over the original."""
    from mscan_nodes import move_media
    p = tmp_path / "x.mp4"
    p.write_bytes(b"original")
    recorded = {}

    def fake_run(cmd, check, capture_output, timeout):
        recorded["cmd"] = cmd
        # Simulate ffmpeg writing the staging file
        Path(cmd[-1]).write_bytes(b"remuxed")
        class R:
            returncode = 0
            stdout = b""
            stderr = b""
        return R()

    monkeypatch.setattr(move_media.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(move_media.subprocess, "run", fake_run)

    out = move_media.embed_video_metadata(p, {"p": 1}, {"w": 2}, "always")

    assert out == "embedded"
    assert recorded["cmd"][:6] == ["ffmpeg", "-y", "-loglevel", "error", "-i", str(p)]
    assert "-c" in recorded["cmd"] and "copy" in recorded["cmd"]
    # The -metadata arg should carry our JSON payload
    meta_idx = recorded["cmd"].index("-metadata")
    assert recorded["cmd"][meta_idx + 1].startswith("comment=")
    assert '"prompt"' in recorded["cmd"][meta_idx + 1]
    # Original file should now have the "remuxed" bytes (os.replace ran).
    assert p.read_bytes() == b"remuxed"
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `pytest tests/test_move_media.py::test_embed_video_metadata_always_invokes_ffmpeg -v`
Expected: PASS.

- [ ] **Step 7: Add the ffmpeg-fails test**

Append:

```python
def test_embed_video_metadata_ffmpeg_nonzero_logs_and_skips(tmp_path, monkeypatch):
    from mscan_nodes import move_media
    p = tmp_path / "x.mp4"
    p.write_bytes(b"original")

    def fake_run(cmd, check, capture_output, timeout):
        # Create the staging file so we can verify it gets cleaned up
        Path(cmd[-1]).write_bytes(b"half-written")
        raise subprocess.CalledProcessError(
            returncode=1, cmd=cmd, output=b"", stderr=b"ffmpeg blew up"
        )

    monkeypatch.setattr(move_media.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(move_media.subprocess, "run", fake_run)

    out = move_media.embed_video_metadata(p, {"p": 1}, None, "always")

    assert out == "skipped_error"
    assert p.read_bytes() == b"original"  # original survives
    assert not (tmp_path / "x.mp4.meta.partial").exists()  # staging cleaned up
```

- [ ] **Step 8: Run the failure test**

Run: `pytest tests/test_move_media.py::test_embed_video_metadata_ffmpeg_nonzero_logs_and_skips -v`
Expected: PASS.

- [ ] **Step 9: Add the if_missing probes-first test**

Append:

```python
def test_embed_video_metadata_if_missing_probes_first_and_skips(tmp_path, monkeypatch):
    from mscan_nodes import move_media
    p = tmp_path / "x.mp4"
    p.write_bytes(b"original")

    monkeypatch.setattr(move_media.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(move_media, "_video_has_metadata", lambda path: True)
    monkeypatch.setattr(
        move_media.subprocess, "run",
        lambda *a, **kw: pytest.fail("ffmpeg should not run when metadata present"),
    )

    out = move_media.embed_video_metadata(p, {"p": 1}, None, "if_missing")

    assert out == "skipped_present"
    assert p.read_bytes() == b"original"


def test_embed_video_metadata_if_missing_writes_when_absent(tmp_path, monkeypatch):
    from mscan_nodes import move_media
    p = tmp_path / "x.mp4"
    p.write_bytes(b"original")

    monkeypatch.setattr(move_media.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(move_media, "_video_has_metadata", lambda path: False)

    def fake_run(cmd, check, capture_output, timeout):
        Path(cmd[-1]).write_bytes(b"remuxed")
        class R:
            stdout = b""
            stderr = b""
            returncode = 0
        return R()

    monkeypatch.setattr(move_media.subprocess, "run", fake_run)

    out = move_media.embed_video_metadata(p, {"p": 1}, None, "if_missing")

    assert out == "embedded"
    assert p.read_bytes() == b"remuxed"
```

- [ ] **Step 10: Run the if_missing tests**

Run: `pytest tests/test_move_media.py -k if_missing -v`
Expected: Both PASS.

- [ ] **Step 11: Add the _video_has_metadata probe tests**

Append:

```python
def test_video_has_metadata_returns_false_when_ffprobe_missing(tmp_path, monkeypatch):
    from mscan_nodes import move_media
    p = tmp_path / "x.mp4"
    p.write_bytes(b"x")
    monkeypatch.setattr(move_media.shutil, "which", lambda name: None)
    assert move_media._video_has_metadata(p) is False


def test_video_has_metadata_true_when_comment_tag_present(tmp_path, monkeypatch):
    from mscan_nodes import move_media
    p = tmp_path / "x.mp4"
    p.write_bytes(b"x")
    monkeypatch.setattr(move_media.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(cmd, check, capture_output, timeout):
        class R:
            stdout = json.dumps({"format": {"tags": {"comment": "blah"}}}).encode()
            stderr = b""
            returncode = 0
        return R()

    monkeypatch.setattr(move_media.subprocess, "run", fake_run)
    assert move_media._video_has_metadata(p) is True


def test_video_has_metadata_false_when_no_matching_tags(tmp_path, monkeypatch):
    from mscan_nodes import move_media
    p = tmp_path / "x.mp4"
    p.write_bytes(b"x")
    monkeypatch.setattr(move_media.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(cmd, check, capture_output, timeout):
        class R:
            stdout = json.dumps({"format": {"tags": {"title": "movie"}}}).encode()
            stderr = b""
            returncode = 0
        return R()

    monkeypatch.setattr(move_media.subprocess, "run", fake_run)
    assert move_media._video_has_metadata(p) is False


def test_video_has_metadata_case_insensitive(tmp_path, monkeypatch):
    """Container tags can be uppercase (Matroska uses TITLE/COMMENT);
    match case-insensitively."""
    from mscan_nodes import move_media
    p = tmp_path / "x.mkv"
    p.write_bytes(b"x")
    monkeypatch.setattr(move_media.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(cmd, check, capture_output, timeout):
        class R:
            stdout = json.dumps({"format": {"tags": {"COMMENT": "blah"}}}).encode()
            stderr = b""
            returncode = 0
        return R()

    monkeypatch.setattr(move_media.subprocess, "run", fake_run)
    assert move_media._video_has_metadata(p) is True
```

- [ ] **Step 12: Run the probe tests**

Run: `pytest tests/test_move_media.py -k video_has_metadata -v`
Expected: All four PASS.

- [ ] **Step 13: Commit**

```bash
git add mscan_nodes/move_media.py tests/test_move_media.py
git commit -m "Add ffmpeg-backed video metadata embed with ffprobe presence check"
```

---

## Task 6: Wire `process()` and add node integration tests

End-to-end through the node class. Real filesystem ops in `tmp_path`; subprocess mocked.

**Files:**
- Modify: `mscan_nodes/move_media.py`
- Modify: `tests/test_move_media.py`

- [ ] **Step 1: Write the empty-filenames test**

Append:

```python
# ----- process() integration -----

def test_process_empty_filenames_returns_empty_and_no_error(tmp_path):
    from mscan_nodes.move_media import MetascanMoveMedia
    out = MetascanMoveMedia().process(
        filenames=(True, []),
        directory=str(tmp_path),
        subpath="",
        operation="move",
        save_metadata="if_missing",
        prompt=None,
        extra_pnginfo=None,
    )
    assert out["result"][0] == (True, [])
    assert out["result"][1] == ""
    assert out["ui"]["text"]  # at least one line — the "no files" debug line
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_move_media.py::test_process_empty_filenames_returns_empty_and_no_error -v`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement `process()`**

Replace the `process()` body in `mscan_nodes/move_media.py`:

```python
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

    save_flag, paths = filenames
    paths = list(paths)
    if not paths:
        logger.debug("MetascanMoveMedia: empty filenames input — nothing to move")
        return {
            "ui": {"text": ["no files to move"]},
            "result": ((save_flag, []), ""),
        }

    target_dir = resolve_target_dir(
        directory=directory, subpath=subpath, now=_utc_now()
    )

    workflow_dict: Optional[dict] = None
    if isinstance(extra_pnginfo, dict):
        workflow_dict = extra_pnginfo.get("workflow")

    new_paths: list[str] = []
    ui_lines: list[str] = []
    for src in paths:
        src_native = Path(wsl_to_native_path(str(src)))
        if not src_native.exists():
            raise RuntimeError(
                f"MetascanMoveMedia: source not found: {src_native}"
            )
        new_path = relocate_file(src_native, target_dir, operation)
        status = dispatch_metadata(new_path, prompt, workflow_dict, save_metadata)
        new_paths.append(str(new_path))
        ui_lines.append(f"{operation} {src_native} → {new_path} [{status}]")

    return {
        "ui": {"text": ui_lines},
        "result": ((save_flag, new_paths), new_paths[0] if new_paths else ""),
    }
```

Make sure `resolve_target_dir`, `wsl_to_native_path`, and `_utc_now` are imported from `_shared` at the top of `move_media.py` — extend the existing import block:

```python
from mscan_nodes._shared import (
    _build_client,
    _utc_now,
    resolve_target_dir,
    wsl_to_native_path,
)
```

- [ ] **Step 4: Run the empty-filenames test to verify it passes**

Run: `pytest tests/test_move_media.py::test_process_empty_filenames_returns_empty_and_no_error -v`
Expected: PASS.

- [ ] **Step 5: Re-run the offline-sentinel test to confirm the rewrite didn't break it**

Run: `pytest tests/test_move_media.py::test_process_offline_sentinel_raises -v`
Expected: PASS.

- [ ] **Step 6: Add the move-pipeline end-to-end test**

Append:

```python
def test_process_move_pipeline_end_to_end(tmp_path, monkeypatch):
    """Real filesystem move into a real metascan dir + mocked ffmpeg
    re-mux. Asserts the file lands, source disappears, returned
    VHS_FILENAMES carries the new path, and UI text says 'embedded'."""
    from mscan_nodes import move_media
    src = tmp_path / "in" / "clip.mp4"
    src.parent.mkdir()
    src.write_bytes(b"original")
    dst = tmp_path / "out"
    dst.mkdir()

    monkeypatch.setattr(move_media.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(cmd, check, capture_output, timeout):
        # ffprobe path returns "no tags"; ffmpeg path writes staging.
        if cmd[0] == "ffprobe":
            class R:
                stdout = b'{"format":{"tags":{}}}'
                stderr = b""
                returncode = 0
            return R()
        Path(cmd[-1]).write_bytes(b"remuxed")
        class R:
            stdout = b""
            stderr = b""
            returncode = 0
        return R()

    monkeypatch.setattr(move_media.subprocess, "run", fake_run)

    out = move_media.MetascanMoveMedia().process(
        filenames=(True, [str(src)]),
        directory=str(dst),
        subpath="",
        operation="move",
        save_metadata="if_missing",
        prompt={"p": 1},
        extra_pnginfo={"workflow": {"nodes": []}},
    )

    (save_flag, new_paths), first = out["result"]
    assert save_flag is True
    assert len(new_paths) == 1
    assert Path(new_paths[0]).read_bytes() == b"remuxed"
    assert not src.exists()  # moved, not copied
    assert "embedded" in out["ui"]["text"][0]
    assert first == new_paths[0]
```

- [ ] **Step 7: Run the end-to-end test**

Run: `pytest tests/test_move_media.py::test_process_move_pipeline_end_to_end -v`
Expected: PASS.

- [ ] **Step 8: Add the copy preserves save_flag test**

Append:

```python
def test_process_copy_preserves_save_flag_false(tmp_path, monkeypatch):
    """save_flag from VHS_FILENAMES is opaque to us; pass it through."""
    from mscan_nodes import move_media
    src = tmp_path / "clip.png"
    Image.new("RGB", (4, 4)).save(src)
    dst = tmp_path / "out"
    dst.mkdir()

    out = move_media.MetascanMoveMedia().process(
        filenames=(False, [str(src)]),
        directory=str(dst),
        subpath="",
        operation="copy",
        save_metadata="always",
        prompt={"p": 1},
        extra_pnginfo=None,
    )

    (save_flag, _), _ = out["result"]
    assert save_flag is False
    assert src.exists()  # copy
```

- [ ] **Step 9: Run the save_flag test**

Run: `pytest tests/test_move_media.py::test_process_copy_preserves_save_flag_false -v`
Expected: PASS.

- [ ] **Step 10: Add the strftime subpath test**

Append:

```python
def test_process_subpath_strftime_expansion(tmp_path, monkeypatch):
    from mscan_nodes import move_media
    src = tmp_path / "clip.png"
    Image.new("RGB", (4, 4)).save(src)
    dst = tmp_path / "out"
    dst.mkdir()

    import datetime as dt
    monkeypatch.setattr(move_media, "_utc_now", lambda: dt.datetime(2026, 5, 23))

    out = move_media.MetascanMoveMedia().process(
        filenames=(True, [str(src)]),
        directory=str(dst),
        subpath="%Y-%m",
        operation="copy",
        save_metadata="if_missing",
        prompt=None,
        extra_pnginfo=None,
    )

    new_path = Path(out["result"][0][1][0])
    assert new_path.parent == dst / "2026-05"
    assert new_path.name == "clip.png"
```

- [ ] **Step 11: Run the strftime test**

Run: `pytest tests/test_move_media.py::test_process_subpath_strftime_expansion -v`
Expected: PASS.

- [ ] **Step 12: Add the missing-source test**

Append:

```python
def test_process_missing_source_raises_runtime(tmp_path):
    from mscan_nodes.move_media import MetascanMoveMedia
    dst = tmp_path / "out"
    dst.mkdir()
    with pytest.raises(RuntimeError, match="source not found"):
        MetascanMoveMedia().process(
            filenames=(True, [str(tmp_path / "does-not-exist.mp4")]),
            directory=str(dst),
            subpath="",
            operation="move",
            save_metadata="if_missing",
            prompt=None,
            extra_pnginfo=None,
        )
```

- [ ] **Step 13: Run the missing-source test**

Run: `pytest tests/test_move_media.py::test_process_missing_source_raises_runtime -v`
Expected: PASS.

- [ ] **Step 14: Add the WSL translation test**

Append:

```python
def test_process_wsl_translation_on_source_and_destination(tmp_path, monkeypatch):
    """When sys.platform is win32 and either the directory or a source
    path comes in as /mnt/<drive>/..., both get translated through
    wsl_to_native_path before the filesystem call."""
    from mscan_nodes import move_media
    monkeypatch.setattr("sys.platform", "win32")

    seen = {}

    def fake_resolve(directory, subpath, now):
        seen["directory"] = directory
        # Hand back a real tmp dir so the move can succeed
        return tmp_path

    monkeypatch.setattr(move_media, "resolve_target_dir", fake_resolve)

    # Pre-create a "translated" source path
    src = tmp_path / "src.png"
    Image.new("RGB", (4, 4)).save(src)

    def fake_wsl(path: str) -> str:
        seen.setdefault("translated", []).append(path)
        if path == "/mnt/d/dst":
            return "D:\\dst"
        if path.startswith("/mnt/d/src/"):
            return str(src)
        return path

    monkeypatch.setattr(move_media, "wsl_to_native_path", fake_wsl)

    move_media.MetascanMoveMedia().process(
        filenames=(True, ["/mnt/d/src/clip.png"]),
        directory="/mnt/d/dst",
        subpath="",
        operation="copy",
        save_metadata="if_missing",
        prompt=None,
        extra_pnginfo=None,
    )

    # Source went through wsl_to_native_path; directory went through
    # the (stubbed) resolve_target_dir which receives the raw string.
    assert "/mnt/d/src/clip.png" in seen["translated"]
    assert seen["directory"] == "/mnt/d/dst"
```

- [ ] **Step 15: Run the WSL test**

Run: `pytest tests/test_move_media.py::test_process_wsl_translation_on_source_and_destination -v`
Expected: PASS.

- [ ] **Step 16: Add the metadata-plumbing test**

Append:

```python
def test_process_passes_prompt_and_workflow_to_dispatch(tmp_path, monkeypatch):
    """prompt and extra_pnginfo['workflow'] must flow through to
    dispatch_metadata unchanged. Without this plumbing, metascan's
    extractor reads back empty values."""
    from mscan_nodes import move_media
    src = tmp_path / "x.png"
    Image.new("RGB", (4, 4)).save(src)
    dst = tmp_path / "out"
    dst.mkdir()

    captured = {}

    def fake_dispatch(path, prompt, workflow, mode):
        captured["prompt"] = prompt
        captured["workflow"] = workflow
        captured["mode"] = mode
        return "embedded"

    monkeypatch.setattr(move_media, "dispatch_metadata", fake_dispatch)

    move_media.MetascanMoveMedia().process(
        filenames=(True, [str(src)]),
        directory=str(dst),
        subpath="",
        operation="copy",
        save_metadata="always",
        prompt={"the": "prompt"},
        extra_pnginfo={"workflow": {"the": "workflow"}, "other": "ignored"},
    )

    assert captured["prompt"] == {"the": "prompt"}
    assert captured["workflow"] == {"the": "workflow"}
    assert captured["mode"] == "always"
```

- [ ] **Step 17: Run the metadata-plumbing test**

Run: `pytest tests/test_move_media.py::test_process_passes_prompt_and_workflow_to_dispatch -v`
Expected: PASS.

- [ ] **Step 18: Add the multi-file UI text test**

Append:

```python
def test_process_returns_ui_text_line_per_file(tmp_path, monkeypatch):
    from mscan_nodes import move_media
    srcs = []
    for i in range(3):
        p = tmp_path / f"in_{i}.png"
        Image.new("RGB", (4, 4)).save(p)
        srcs.append(str(p))
    dst = tmp_path / "out"
    dst.mkdir()

    out = move_media.MetascanMoveMedia().process(
        filenames=(True, srcs),
        directory=str(dst),
        subpath="",
        operation="move",
        save_metadata="always",
        prompt={"p": 1},
        extra_pnginfo=None,
    )

    text = out["ui"]["text"]
    assert len(text) == 3
    for i, line in enumerate(text):
        assert line.startswith("move ")
        assert f"in_{i}.png" in line
        assert "[embedded]" in line
```

- [ ] **Step 19: Run the multi-file test**

Run: `pytest tests/test_move_media.py::test_process_returns_ui_text_line_per_file -v`
Expected: PASS.

- [ ] **Step 20: Run the full file**

Run: `pytest tests/test_move_media.py -v`
Expected: All tests in the file pass.

- [ ] **Step 21: Run the full suite to verify no regressions**

Run: `pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 22: Commit**

```bash
git add mscan_nodes/move_media.py tests/test_move_media.py
git commit -m "Wire MetascanMoveMedia.process() end-to-end with integration tests"
```

---

## Task 7: Add INPUT_TYPES dropdown test

Mirror the SaveImage pattern of testing that the directory dropdown gets populated from metascan and falls back to the offline sentinel on connection failure.

**Files:**
- Modify: `tests/test_move_media.py`

- [ ] **Step 1: Add the dropdown-from-metascan test**

Append to `tests/test_move_media.py`:

```python
# ----- INPUT_TYPES dropdown -----

import respx
import httpx
from mscan_client.cache import clear_cache


@respx.mock
def test_input_types_lists_directories_from_metascan(monkeypatch, base_url, config_payload):
    """INPUT_TYPES() hits combo_directories() which hits the real
    client which respx mocks here. Same pattern as SaveImage."""
    from mscan_nodes.move_media import MetascanMoveMedia
    clear_cache()
    respx.get(f"{base_url}/api/config").mock(return_value=httpx.Response(200, json=config_payload))
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    spec = MetascanMoveMedia.INPUT_TYPES()
    dirs = spec["required"]["directory"][0]
    assert "/data/comfy-out" in dirs
    assert "/data/photos" in dirs


@respx.mock
def test_input_types_shows_offline_sentinel_when_server_down(monkeypatch, base_url):
    from mscan_nodes.move_media import MetascanMoveMedia
    from mscan_client.cache import OFFLINE_SENTINEL
    clear_cache()
    respx.get(f"{base_url}/api/config").mock(side_effect=httpx.ConnectError("x"))
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    spec = MetascanMoveMedia.INPUT_TYPES()
    dirs = spec["required"]["directory"][0]
    assert dirs == [OFFLINE_SENTINEL]
```

- [ ] **Step 2: Run the dropdown tests**

Run: `pytest tests/test_move_media.py -k input_types -v`
Expected: Both PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_move_media.py
git commit -m "Test MetascanMoveMedia INPUT_TYPES directory dropdown"
```

---

## Task 8: Write `docs/nodes/move-media.md` and update `docs/nodes/save-image.md`

**Files:**
- Create: `docs/nodes/move-media.md`
- Modify: `docs/nodes/save-image.md`

- [ ] **Step 1: Create `docs/nodes/move-media.md`**

```markdown
# Metascan · Move Media

Relocate any media file produced upstream — typically by VHS Combine Video, but any node that emits a `VHS_FILENAMES` socket works — into a directory metascan is already watching. Metascan's filesystem watcher (or its next manual scan) picks the file up automatically and indexes it — no API call happens at save time.

Pair with [Metascan · Save Image](save-image.md): SaveImage handles `IMAGE` tensors directly; MoveMedia handles files that already exist on disk.

## What it pulls from metascan

- **At node-load time (dropdown population):** `GET /api/config` to list the directories metascan is watching, so the `directory` dropdown only offers valid targets.
- **At execute time:** nothing. The relocation is a pure local file write.

## Inputs

| Name            | Type             | Default       | Description                                                                                                                                |
|-----------------|------------------|---------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| `filenames`     | `VHS_FILENAMES`  | —             | Typed list of paths from an upstream node. Carries `(save_flag, [paths])` — both image and video formats are supported.                    |
| `directory`     | (dropdown)       | —             | One of metascan's watched directories. Populated from `/api/config`.                                                                       |
| `subpath`       | `STRING`         | *(empty)*     | Subdirectory under `directory`, with [strftime](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes) placeholders. e.g. `%Y-%m-%d/` → `2026-05-23/`. Created if missing. |
| `operation`     | enum             | `move`        | `move` — source disappears (use when VHS wrote to its temp dir). `copy` — leaves the source in place too.                                  |
| `save_metadata` | enum             | `if_missing`  | `always` — overwrite container/PNG metadata with the current prompt + workflow. `if_missing` — only write if existing metadata is absent.   |

### Filename collisions

The collision counter is `max(existing index) + 1`, not `len(existing)`. The original basename from the upstream node is preserved when the destination is free; on collision, `_NN` is appended (zero-padded, 2 digits, widening to 3 past 99). A deletion-induced gap in the sequence does not cause us to reuse a name.

### Atomic writes

Each file is staged as `<name>.partial` in the destination dir, then `os.replace`d to the final name. Metascan's filesystem watcher never sees a half-written file. The same staging pattern protects against mid-write crashes on cross-device copies.

### Cross-platform path handling

If metascan runs in WSL (reporting `/mnt/d/...` paths) and ComfyUI runs on Windows, both `directory` and each source path get translated from `/mnt/<drive>/...` to `<DRIVE>:\...` automatically. No-op on Linux/macOS.

## Metadata dispatch (per-extension)

| Extension                              | Tool                                  | `always`                                  | `if_missing`                                                                |
|----------------------------------------|---------------------------------------|-------------------------------------------|-----------------------------------------------------------------------------|
| `.png`                                  | PIL                                   | Re-save with new `prompt` / `workflow` tEXt chunks | Skip if either tEXt chunk is already present                       |
| `.mp4` `.mov` `.mkv` `.webm` `.gif`    | ffmpeg `-c copy -metadata comment=…`  | Re-mux with new JSON-encoded comment      | ffprobe first; skip if `comment`, `prompt`, or `workflow` tag present       |
| anything else                          | —                                     | No-op (debug log only)                    | No-op (debug log only)                                                      |

The video re-mux uses `-c copy` — no re-encode, fast and lossless. The `comment=` key matches what VHS writes, so metascan's video extractor reads either without changes.

### ffmpeg is best-effort

If `ffmpeg` is not on `PATH`, the move/copy still succeeds — metadata embedding is logged as skipped and the per-file UI text on the node face shows `[skipped_no_ffmpeg]`. The save never fails because metadata can't be written. If you're using VHS Combine upstream, you already have ffmpeg installed (VHS requires it) and no extra setup is needed.

If ffmpeg exits non-zero on a particular file, the relocated file is kept (it's already in the metascan dir at that point) and the metadata step is logged as `[skipped_error]` with a tail of ffmpeg's stderr in the Python log.

## Outputs

| Name        | Type             | Description                                                                                                |
|-------------|------------------|------------------------------------------------------------------------------------------------------------|
| `filenames` | `VHS_FILENAMES`  | `(save_flag, [new_paths])`. The `save_flag` is passed through unchanged; paths are the post-move locations.|
| `file_path` | `STRING`         | Absolute path of the **first** relocated file. Useful for downstream logging or notes.                     |

The node is an `OUTPUT_NODE`. It surfaces one text line per file on the node face:

```
move /tmp/comfy/clip_00001.mp4 → /data/comfy-out/2026-05/clip_00001.mp4 [embedded]
```

The status in brackets is one of `embedded`, `skipped_present`, `skipped_unsupported`, `skipped_no_ffmpeg`, or `skipped_error`.

## Typical workflow

1. **VHS Combine Video** with `save_output = False` and a `filename_prefix` you don't care about — writes to ComfyUI's temp dir.
2. Wire its `VHS_FILENAMES` output into **Metascan · Move Media**.
3. Pick a metascan-watched `directory`; optionally set a `subpath` like `%Y-%m`.
4. Leave `operation = move` and `save_metadata = if_missing` for the default workflow.
5. Run. Metascan's watcher picks the file up from its watched dir; the prompt/workflow JSON travels in the container metadata so metascan's extractor reads it.

## Common errors

- **`Metascan is offline — cannot resolve a watched directory`** — the dropdown was populated from a stale cache (60s TTL) and metascan has since gone down. Bring metascan back up, or restart the workflow page to repopulate the dropdown.
- **`MetascanMoveMedia: source not found: <path>`** — the upstream node reported a path it didn't actually write, or wrote to a path that's already been moved. Check the upstream node's `save_output` and `filename_prefix` settings.
- **`OSError` on destination write** — the metascan-watched directory isn't writable from ComfyUI's user. Check filesystem permissions on the target dir.

## Behavior notes

- Metascan is not contacted at execute time, so relocation works even if metascan goes offline mid-workflow (as long as the directory dropdown was populated successfully when the node was added).
- PNG re-saves through PIL are lossless but re-encode the file bytes; existing image quality and bit depth are preserved.
- Empty `filenames` input (e.g. VHS legitimately skipped output) is not an error — the node returns an empty list and logs a debug line.
```

- [ ] **Step 2: Add the "See also" line at the top of `docs/nodes/save-image.md`**

Edit `docs/nodes/save-image.md`. Find the first line (`# Metascan · Save Image`) and the paragraph that follows. Add this line as a new paragraph right after the existing intro paragraph:

```markdown
**See also:** For files already on disk (e.g. VHS Combine Video output), use [Metascan · Move Media](move-media.md).
```

- [ ] **Step 3: Commit**

```bash
git add docs/nodes/move-media.md docs/nodes/save-image.md
git commit -m "Document MetascanMoveMedia node + cross-link from save-image"
```

---

## Task 9: Refresh `README.md`

Add the new node and correct the stale node-count header.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Verify the current node count**

The README currently says "Four nodes:" but `__init__.py` registers more. Count the entries in `NODE_CLASS_MAPPINGS` after Task 2's registration (should be 7: Settings, SaveImage, LoadFromFolder, LoadPrompt, SelectPrompt, SelectImage, MoveMedia). Use that count in the next step.

Run: `grep -cE '"Metascan[A-Za-z]+": Metascan[A-Za-z]+,' __init__.py`
Expected: prints `7` (counts only the `NODE_CLASS_MAPPINGS` lines, not the display-name lines).

- [ ] **Step 2: Edit the header and bullets in `README.md`**

Find the line `Four nodes:` (or whatever count is currently there) and the bullet list that follows. Replace the header word with `Seven nodes:` and add this bullet at the end of the existing list (after the last bullet, before the `See the per-node docs above…` paragraph):

```markdown
- **[Metascan · Move Media](docs/nodes/move-media.md)** — relocate a media file produced upstream (e.g. by VHS Combine Video) into a metascan-watched directory and embed prompt/workflow metadata. Works with anything that emits `VHS_FILENAMES`. ffmpeg required for video metadata; relocation still succeeds without it.
```

If the existing bullets don't already mention Settings, Select Prompt, and Select Image, add them too (one bullet each) — the README has drifted behind the actual node set. Verify against `NODE_DISPLAY_NAME_MAPPINGS` in `__init__.py` and add any missing entries. Keep the existing bullets verbatim.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Add MetascanMoveMedia to README; refresh stale node count"
```

---

## Task 10: Final verification

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass. No skips. No warnings about deprecated APIs we introduced.

- [ ] **Step 2: Confirm the node loads in ComfyUI**

In a running ComfyUI: open the workflow editor, search the node menu for `Metascan · Move Media`. It should appear under the `metascan` category with the inputs from `INPUT_TYPES`. The `directory` dropdown should be populated if metascan is reachable, or show the offline sentinel otherwise.

If you cannot run ComfyUI as part of the implementation session, say so explicitly. Do not claim "verified in ComfyUI" without running it.

- [ ] **Step 3: Smoke-test the canonical workflow (manual, if ComfyUI is available)**

1. Build a workflow: VHS Combine Video (set `save_output = False`) → MetascanMoveMedia (pick a watched directory, `subpath = %Y-%m`, leave defaults otherwise).
2. Run the workflow.
3. Assert: the file landed in `<directory>/<YYYY-MM>/<basename>.mp4`; metascan's UI shows the new file within seconds; ffprobe on the file shows a `comment` tag with the prompt+workflow JSON.

If the manual smoke fails, **do not amend earlier commits to fix.** Add a follow-up commit with the fix and a clear message explaining what was wrong.
