"""Tests for MetascanMoveMedia.

Helpers (relocate_file, dispatch_metadata, embed_*) are tested as pure
functions with tmp_path and mocked subprocess. The node class is tested
end-to-end with the shared respx/conftest fixtures."""

from __future__ import annotations

import subprocess
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


def test_relocate_file_first_collision_starts_at_zero(tmp_path):
    """When the basename collides but no _NN siblings exist, the
    counter starts at 00."""
    from mscan_nodes.move_media import relocate_file
    src = tmp_path / "foo.mp4"
    src.write_bytes(b"new")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    (dst_dir / "foo.mp4").write_bytes(b"existing")

    out = relocate_file(src, dst_dir, "move")

    assert out.name == "foo_00.mp4"
    assert out.read_bytes() == b"new"
    assert (dst_dir / "foo.mp4").read_bytes() == b"existing"


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


def test_embed_video_metadata_timeout_logs_and_cleans_staging(tmp_path, monkeypatch):
    """TimeoutExpired path mirrors the CalledProcessError path: staging
    cleaned up, original untouched, returns skipped_error."""
    from mscan_nodes import move_media
    p = tmp_path / "x.mp4"
    p.write_bytes(b"original")

    def fake_run(cmd, check, capture_output, timeout):
        # ffmpeg may have written some bytes before the timeout fired
        Path(cmd[-1]).write_bytes(b"partial")
        raise subprocess.TimeoutExpired(cmd, timeout, stderr=b"timed out")

    monkeypatch.setattr(move_media.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(move_media.subprocess, "run", fake_run)

    out = move_media.embed_video_metadata(p, {"p": 1}, None, "always")

    assert out == "skipped_error"
    assert p.read_bytes() == b"original"
    assert not (tmp_path / "x.mp4.meta.partial").exists()


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


def test_process_empty_filenames_preserves_save_flag_false(tmp_path):
    """The empty-input short-circuit must pass save_flag through too,
    not hardcode it. Regression guard against a future 'simplification'
    that hardcodes True."""
    from mscan_nodes.move_media import MetascanMoveMedia
    out = MetascanMoveMedia().process(
        filenames=(False, []),
        directory=str(tmp_path),
        subpath="",
        operation="move",
        save_metadata="if_missing",
        prompt=None,
        extra_pnginfo=None,
    )
    assert out["result"][0] == (False, [])


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


def test_process_skips_sibling_preview_png_when_video_present(tmp_path, monkeypatch):
    """VHS Combine emits VHS_FILENAMES like (True, [preview.png, video.mp4])
    where the preview PNG path is reported but never physically written.
    Without filtering we hit 'source not found' on the PNG. With filtering,
    the sibling PNG (same dir + stem as a video in the list) is dropped
    and the video gets moved; file_path STRING returns the video path."""
    from mscan_nodes import move_media

    src_dir = tmp_path / "in"
    src_dir.mkdir()
    # Only the video physically exists; the PNG sibling is a VHS preview
    # path that never got written.
    video = src_dir / "104332_00001.mp4"
    video.write_bytes(b"video bytes")
    missing_png = src_dir / "104332_00001.png"

    dst = tmp_path / "out"
    dst.mkdir()

    # Stub ffmpeg/ffprobe so the video metadata embed succeeds.
    monkeypatch.setattr(move_media.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(cmd, check, capture_output, timeout):
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
        filenames=(True, [str(missing_png), str(video)]),
        directory=str(dst),
        subpath="",
        operation="move",
        save_metadata="if_missing",
        prompt={"p": 1},
        extra_pnginfo={"workflow": {"nodes": []}},
    )

    (save_flag, new_paths), file_path = out["result"]
    assert save_flag is True
    assert len(new_paths) == 1
    assert new_paths[0].endswith("104332_00001.mp4")
    assert file_path == new_paths[0]  # STRING output is the video, not the PNG
    assert not video.exists()  # actually moved


def test_process_keeps_standalone_png_with_no_matching_video(tmp_path):
    """A PNG with no sibling video in the same list is a real image to
    move (e.g. user wired in a SaveImage-style upstream). The preview-
    filter must not eat it."""
    from mscan_nodes import move_media

    src = tmp_path / "standalone.png"
    Image.new("RGB", (4, 4)).save(src)
    dst = tmp_path / "out"
    dst.mkdir()

    out = move_media.MetascanMoveMedia().process(
        filenames=(True, [str(src)]),
        directory=str(dst),
        subpath="",
        operation="copy",
        save_metadata="always",
        prompt={"p": 1},
        extra_pnginfo=None,
    )

    (_, new_paths), file_path = out["result"]
    assert len(new_paths) == 1
    assert new_paths[0].endswith("standalone.png")
    assert file_path == new_paths[0]


def test_process_drops_preview_png_only_when_stem_matches_video(tmp_path, monkeypatch):
    """PNG with a stem that doesn't match any video in the list is kept;
    only the *sibling* (same parent + stem) is treated as a VHS preview."""
    from mscan_nodes import move_media

    src_dir = tmp_path / "in"
    src_dir.mkdir()
    unrelated_png = src_dir / "screenshot.png"
    Image.new("RGB", (4, 4)).save(unrelated_png)
    video = src_dir / "clip.mp4"
    video.write_bytes(b"v")

    dst = tmp_path / "out"
    dst.mkdir()

    monkeypatch.setattr(move_media.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(cmd, check, capture_output, timeout):
        if cmd[0] == "ffprobe":
            class R:
                stdout = b'{"format":{"tags":{}}}'
                stderr = b""
                returncode = 0
            return R()
        Path(cmd[-1]).write_bytes(b"r")
        class R:
            stdout = b""
            stderr = b""
            returncode = 0
        return R()

    monkeypatch.setattr(move_media.subprocess, "run", fake_run)

    out = move_media.MetascanMoveMedia().process(
        filenames=(True, [str(unrelated_png), str(video)]),
        directory=str(dst),
        subpath="",
        operation="copy",
        save_metadata="if_missing",
        prompt=None,
        extra_pnginfo=None,
    )

    (_, new_paths), _ = out["result"]
    assert len(new_paths) == 2
    names = sorted(Path(p).name for p in new_paths)
    assert names == ["clip.mp4", "screenshot.png"]


def test_process_translates_source_paths_and_delegates_dest_to_resolver(tmp_path, monkeypatch):
    """process() owns WSL translation for source paths (each entry in
    filenames) but delegates destination translation to
    resolve_target_dir (in _shared). This test verifies the source
    paths go through wsl_to_native_path and that the raw directory
    string is handed to resolve_target_dir unmodified — destination
    translation itself is tested by tests/test_save_image.py against
    resolve_target_dir directly."""
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
