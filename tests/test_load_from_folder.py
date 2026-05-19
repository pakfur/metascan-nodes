from __future__ import annotations

import pytest
import torch

from nodes.load_from_folder import (
    filter_paths,
    select_path,
    bytes_to_tensor,
)


SAMPLE_PATHS = [
    "/data/a/img1.png",
    "/data/a/img2.jpg",
    "/data/a/clip.mp4",
    "/data/a/note.txt",
    "/data/a/img3.webp",
]


# ----- filter_paths -----

def test_filter_paths_image_only_drops_video_and_other(tmp_path):
    out = filter_paths(SAMPLE_PATHS, image_only=True, filename_filter="")
    assert out == ["/data/a/img1.png", "/data/a/img2.jpg", "/data/a/img3.webp"]


def test_filter_paths_image_only_false_includes_video(tmp_path):
    out = filter_paths(SAMPLE_PATHS, image_only=False, filename_filter="")
    # .txt still excluded — it's never a supported media type.
    assert "/data/a/clip.mp4" in out
    assert "/data/a/note.txt" not in out


def test_filter_paths_applies_substring_filter():
    out = filter_paths(SAMPLE_PATHS, image_only=True, filename_filter="img2")
    assert out == ["/data/a/img2.jpg"]


def test_filter_paths_sorts_deterministically():
    out = filter_paths(["/z.png", "/a.png", "/m.png"], image_only=True, filename_filter="")
    assert out == ["/a.png", "/m.png", "/z.png"]


# ----- select_path -----

def test_select_path_random_reproducible_with_seed():
    paths = ["/a.png", "/b.png", "/c.png"]
    p1, next_seed1 = select_path(paths, mode="random", seed=42, index=0)
    p2, next_seed2 = select_path(paths, mode="random", seed=42, index=0)
    assert p1 == p2
    assert next_seed1 == next_seed2


def test_select_path_sequential_advances_seed():
    paths = ["/a.png", "/b.png", "/c.png"]
    chosen, next_seed = select_path(paths, mode="sequential", seed=1, index=0)
    assert chosen == "/b.png"
    assert next_seed == 2


def test_select_path_sequential_wraps_around():
    paths = ["/a.png", "/b.png"]
    chosen, next_seed = select_path(paths, mode="sequential", seed=5, index=0)
    assert chosen == "/b.png"  # 5 % 2 == 1
    assert next_seed == 0      # (5 + 1) % 2 == 0


def test_select_path_specific_uses_index_not_seed():
    paths = ["/a.png", "/b.png", "/c.png"]
    chosen, next_seed = select_path(paths, mode="specific", seed=999, index=1)
    assert chosen == "/b.png"
    assert next_seed == 1


def test_select_path_specific_clamps_oversize_index():
    paths = ["/a.png", "/b.png", "/c.png"]
    chosen, _ = select_path(paths, mode="specific", seed=0, index=10)
    assert chosen == "/b.png"  # 10 % 3 == 1


def test_select_path_empty_list_raises():
    with pytest.raises(RuntimeError, match="no matching"):
        select_path([], mode="random", seed=0, index=0)


# ----- bytes_to_tensor -----

def test_bytes_to_tensor_returns_nhwc_float():
    # Build an actual 4×4 PNG and feed its bytes in.
    from io import BytesIO
    from PIL import Image
    pil = Image.new("RGB", (8, 4), color=(128, 64, 192))
    buf = BytesIO()
    pil.save(buf, format="PNG")
    t = bytes_to_tensor(buf.getvalue())
    assert isinstance(t, torch.Tensor)
    assert t.dtype == torch.float32
    assert t.shape == (1, 4, 8, 3)
    assert 0.0 <= t.min() <= t.max() <= 1.0
    # Red channel 128/255 ≈ 0.502.
    assert abs(float(t[0, 0, 0, 0]) - 128/255) < 0.01
