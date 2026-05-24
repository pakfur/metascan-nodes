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


def test_relocate_file_no_partial_left_on_success(tmp_path):
    from mscan_nodes.move_media import relocate_file
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    relocate_file(src, dst_dir, "copy")

    assert list(dst_dir.glob("*.partial")) == []


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


def test_relocate_file_rejects_unknown_operation(tmp_path):
    from mscan_nodes.move_media import relocate_file
    src = tmp_path / "src.bin"
    src.write_bytes(b"x")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    with pytest.raises(ValueError, match="must be 'move' or 'copy'"):
        relocate_file(src, dst_dir, "yeet")
