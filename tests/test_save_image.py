from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import torch
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from mscan_nodes.save_image import (
    resolve_target_dir,
    tensor_to_pil,
    build_png_info,
    wsl_to_native_path,
)


# ----- resolve_target_dir -----

def test_resolve_target_dir_joins_paths(tmp_path):
    out = resolve_target_dir(directory=str(tmp_path), subpath="comfy/out", now=dt.datetime(2026, 5, 18))
    assert out == tmp_path / "comfy" / "out"


def test_resolve_target_dir_expands_strftime(tmp_path):
    now = dt.datetime(2026, 5, 18, 13, 0)
    out = resolve_target_dir(directory=str(tmp_path), subpath="%Y-%m/comfyui", now=now)
    assert out == tmp_path / "2026-05" / "comfyui"


def test_resolve_target_dir_creates_dirs(tmp_path):
    target = resolve_target_dir(directory=str(tmp_path), subpath="a/b/c", now=dt.datetime(2026, 5, 18))
    assert target.exists()
    assert target.is_dir()


def test_resolve_target_dir_empty_subpath_returns_directory(tmp_path):
    out = resolve_target_dir(directory=str(tmp_path), subpath="", now=dt.datetime(2026, 5, 18))
    assert out == tmp_path


# ----- wsl_to_native_path -----

def test_wsl_to_native_passthrough_on_non_windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    assert wsl_to_native_path("/mnt/d/foo") == "/mnt/d/foo"
    assert wsl_to_native_path("/data/bar") == "/data/bar"


def test_wsl_to_native_translates_on_windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    assert wsl_to_native_path("/mnt/d/Media/Staging") == "D:\\Media\\Staging"
    assert wsl_to_native_path("/mnt/c/Users/jk") == "C:\\Users\\jk"


def test_wsl_to_native_uppercases_drive_letter(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    assert wsl_to_native_path("/mnt/e/x") == "E:\\x"


def test_wsl_to_native_leaves_native_windows_path_alone(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    assert wsl_to_native_path("D:\\Media\\Staging") == "D:\\Media\\Staging"


def test_wsl_to_native_leaves_unrelated_posix_path_alone(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    # /data/foo isn't a /mnt/<drive>/... pattern; leave it to fail loudly
    # rather than guess a drive letter.
    assert wsl_to_native_path("/data/foo") == "/data/foo"


def test_wsl_to_native_handles_mnt_root(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    assert wsl_to_native_path("/mnt/d") == "D:\\"


# ----- tensor_to_pil -----

def test_tensor_to_pil_converts_nhwc_float_to_pil_image():
    t = torch.zeros((1, 32, 48, 3), dtype=torch.float32)
    t[0, 0, 0, 0] = 1.0  # one red pixel
    img = tensor_to_pil(t[0])
    assert isinstance(img, Image.Image)
    assert img.size == (48, 32)  # PIL is (W, H)
    assert img.getpixel((0, 0))[0] == 255  # red channel saturated


def test_tensor_to_pil_clamps_out_of_range_values():
    t = torch.full((16, 16, 3), -5.0, dtype=torch.float32)
    img = tensor_to_pil(t)
    assert img.getpixel((0, 0)) == (0, 0, 0)
    t2 = torch.full((16, 16, 3), 5.0, dtype=torch.float32)
    img2 = tensor_to_pil(t2)
    assert img2.getpixel((0, 0)) == (255, 255, 255)


# ----- build_png_info -----

def test_build_png_info_embeds_prompt_and_workflow():
    info = build_png_info(prompt={"a": 1}, workflow={"nodes": [{"id": 1}]})
    assert isinstance(info, PngInfo)
    # PngInfo doesn't expose its dict directly; serialize and re-read.
    img = Image.new("RGB", (4, 4))
    p = Path("/tmp/__pnginfo_test.png")
    img.save(p, pnginfo=info)
    reloaded = Image.open(p)
    reloaded.load()
    assert '"a": 1' in reloaded.info.get("prompt", "")
    assert '"nodes"' in reloaded.info.get("workflow", "")
    p.unlink()


def test_build_png_info_skips_workflow_when_disabled():
    info = build_png_info(prompt={"a": 1}, workflow=None)
    img = Image.new("RGB", (4, 4))
    p = Path("/tmp/__pnginfo_test2.png")
    img.save(p, pnginfo=info)
    reloaded = Image.open(p)
    reloaded.load()
    assert "workflow" not in reloaded.info
    assert "prompt" in reloaded.info
    p.unlink()


# ----- INPUT_TYPES dropdown -----

import datetime as dt
import shutil
import tempfile
from unittest.mock import patch

import respx
import httpx

from mscan_nodes.save_image import MetascanSaveImage
from mscan_client.cache import clear_cache, OFFLINE_SENTINEL


@respx.mock
def test_input_types_lists_directories_from_metascan(monkeypatch, base_url, config_payload):
    """MetascanSaveImage.INPUT_TYPES() hits combo_directories() which
    hits the real client which respx mocks here."""
    clear_cache()
    respx.get(f"{base_url}/api/config").mock(return_value=httpx.Response(200, json=config_payload))
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    # Settings node override must be cleared between tests.
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    spec = MetascanSaveImage.INPUT_TYPES()
    dirs = spec["required"]["directory"][0]
    assert "/data/comfy-out" in dirs
    assert "/data/photos" in dirs


@respx.mock
def test_input_types_shows_offline_sentinel_when_server_down(monkeypatch, base_url):
    clear_cache()
    respx.get(f"{base_url}/api/config").mock(side_effect=httpx.ConnectError("x"))
    monkeypatch.setenv("METASCAN_URL", base_url)
    monkeypatch.delenv("METASCAN_API_KEY", raising=False)
    import mscan_nodes.settings
    mscan_nodes.settings._OVERRIDE = None

    spec = MetascanSaveImage.INPUT_TYPES()
    dirs = spec["required"]["directory"][0]
    assert dirs == [OFFLINE_SENTINEL]


# ----- execute: file write + pass-through -----

def test_execute_writes_png_and_passes_through_tensor(tmp_path):
    images = torch.zeros((2, 16, 16, 3), dtype=torch.float32)
    node = MetascanSaveImage()
    out_images, out_path = node.save(
        images=images,
        directory=str(tmp_path),
        subpath="",
        filename_prefix="ComfyUI",
        embed_workflow=True,
        prompt=None,
        extra_pnginfo=None,
    )
    assert out_images is images  # identity pass-through
    p = Path(out_path)
    assert p.exists() and p.suffix == ".png"
    # Both batch entries should land on disk.
    written = sorted(p.parent.glob("ComfyUI_*.png"))
    assert len(written) == 2


def test_execute_strftime_subpath_expanded(tmp_path):
    images = torch.zeros((1, 8, 8, 3), dtype=torch.float32)
    with patch("mscan_nodes.save_image._utc_now") as mock_now:
        mock_now.return_value = dt.datetime(2026, 5, 18)
        MetascanSaveImage().save(
            images=images, directory=str(tmp_path), subpath="%Y-%m/comfy",
            filename_prefix="X", embed_workflow=False, prompt=None, extra_pnginfo=None,
        )
    assert (tmp_path / "2026-05" / "comfy").exists()
    assert list((tmp_path / "2026-05" / "comfy").glob("X_*.png"))


def test_execute_raises_on_offline_sentinel():
    images = torch.zeros((1, 8, 8, 3), dtype=torch.float32)
    with pytest.raises(RuntimeError, match="offline"):
        MetascanSaveImage().save(
            images=images, directory=OFFLINE_SENTINEL, subpath="",
            filename_prefix="X", embed_workflow=False, prompt=None, extra_pnginfo=None,
        )


def test_execute_skips_workflow_chunk_when_embed_false(tmp_path):
    images = torch.zeros((1, 8, 8, 3), dtype=torch.float32)
    MetascanSaveImage().save(
        images=images, directory=str(tmp_path), subpath="",
        filename_prefix="ComfyUI", embed_workflow=False,
        prompt={"x": 1}, extra_pnginfo={"workflow": {"nodes": []}},
    )
    p = next(tmp_path.glob("ComfyUI_*.png"))
    img = Image.open(p)
    img.load()
    assert "prompt" in img.info
    assert "workflow" not in img.info


def test_execute_creates_subdirs_from_slash_in_filename_prefix(tmp_path):
    """ComfyUI core SaveImage treats `subdir/prefix` as 'put files in
    subdir/, named prefix_NNNNN.png'. Match that behavior — the user
    might pass 'test/qwen' as filename_prefix and expect test/ to be
    created under target_dir."""
    images = torch.zeros((1, 8, 8, 3), dtype=torch.float32)
    out_images, out_path = MetascanSaveImage().save(
        images=images, directory=str(tmp_path), subpath="",
        filename_prefix="sub1/sub2/qwen", embed_workflow=False,
        prompt=None, extra_pnginfo=None,
    )
    p = Path(out_path)
    assert p.exists()
    assert p.parent == tmp_path / "sub1" / "sub2"
    assert p.name == "qwen_00000.png"


def test_execute_does_not_overwrite_when_gap_in_sequence(tmp_path):
    """If files 00000 and 00002 exist (gap from a deletion at 00001),
    the next save must write 00003 — not re-use 00002 via len() count."""
    (tmp_path / "ComfyUI_00000.png").write_bytes(b"x")
    (tmp_path / "ComfyUI_00002.png").write_bytes(b"y")
    images = torch.zeros((1, 8, 8, 3), dtype=torch.float32)
    MetascanSaveImage().save(
        images=images, directory=str(tmp_path), subpath="",
        filename_prefix="ComfyUI", embed_workflow=False, prompt=None,
        extra_pnginfo=None,
    )
    assert (tmp_path / "ComfyUI_00002.png").read_bytes() == b"y"  # untouched
    assert (tmp_path / "ComfyUI_00003.png").exists()
