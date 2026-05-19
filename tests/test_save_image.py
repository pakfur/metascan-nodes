from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import torch
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from nodes.save_image import (
    resolve_target_dir,
    tensor_to_pil,
    build_png_info,
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
