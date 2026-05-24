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
