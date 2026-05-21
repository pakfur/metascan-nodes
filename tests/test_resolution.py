"""Tests for mscan_nodes/resolution.py — model-specific resolution
calculator. Pure-Python module so no torch / no HTTP needed."""

from __future__ import annotations

import math

import pytest

from mscan_nodes.resolution import (
    MODEL_SPECS,
    QUALITY_TIERS,
    TARGET_MODELS_FOR_RES,
    compute_from_aspect,
    compute_resolution,
    pick_closest_bucket,
    snap_to_multiple,
)


# ----- snap_to_multiple -----

def test_snap_to_multiple_exact():
    assert snap_to_multiple(1024, 64) == 1024
    assert snap_to_multiple(1024, 16) == 1024


def test_snap_to_multiple_rounds_to_nearest():
    assert snap_to_multiple(1000, 64) == 1024  # 1000/64 = 15.625 -> 16
    assert snap_to_multiple(1050, 64) == 1024  # 1050/64 = 16.406 -> 16
    assert snap_to_multiple(1057, 64) == 1088  # 1057/64 = 16.516 -> 17


def test_snap_to_multiple_minimum_is_divisor():
    """Even sub-divisor inputs must round up to ``m`` so we never emit
    a zero-sided latent. round(8/16) = 0 by banker's rounding, but the
    max() clamp saves us."""
    assert snap_to_multiple(0, 16) == 16
    assert snap_to_multiple(1, 16) == 16
    assert snap_to_multiple(8, 16) == 16
    assert snap_to_multiple(7, 64) == 64


def test_snap_to_multiple_rejects_nonpositive_divisor():
    with pytest.raises(ValueError):
        snap_to_multiple(100, 0)
    with pytest.raises(ValueError):
        snap_to_multiple(100, -16)


# ----- compute_from_aspect -----

def test_compute_from_aspect_square():
    w, h = compute_from_aspect(1024 * 1024, aspect=1.0, divisor=64)
    assert (w, h) == (1024, 1024)


def test_compute_from_aspect_16_9_sdxl():
    """The textbook SDXL 16:9 bucket at ~1 MP is 1344x768 — the math
    should reproduce it for a source aspect of 16:9 (1.777)."""
    w, h = compute_from_aspect(1024 * 1024, aspect=16 / 9, divisor=64)
    assert (w, h) == (1344, 768)
    assert h * w >= 1024 * 768  # within ±64² of target


def test_compute_from_aspect_9_16_sdxl():
    """Portrait 9:16 source mirrors the landscape 16:9 result."""
    w, h = compute_from_aspect(1024 * 1024, aspect=9 / 16, divisor=64)
    assert (w, h) == (768, 1344)


def test_compute_from_aspect_flux_divisor_16():
    w, h = compute_from_aspect(1024 * 1024, aspect=16 / 9, divisor=16)
    assert w % 16 == 0 and h % 16 == 0
    assert w > h  # landscape preserved
    # Finer divisor → closer to the analytic target (≈1365×768).
    assert 1360 <= w <= 1376
    assert 760 <= h <= 776


def test_compute_from_aspect_qwen_divisor_32():
    w, h = compute_from_aspect(1024 * 1024, aspect=16 / 9, divisor=32)
    assert w % 32 == 0 and h % 32 == 0
    assert w > h


def test_compute_from_aspect_rejects_nonpositive_aspect():
    with pytest.raises(ValueError):
        compute_from_aspect(1024 * 1024, aspect=0.0, divisor=16)
    with pytest.raises(ValueError):
        compute_from_aspect(1024 * 1024, aspect=-1.5, divisor=16)


# ----- pick_closest_bucket -----

def test_pick_closest_bucket_exact_aspect_match():
    buckets = [(1024, 1024), (1280, 720), (720, 1280)]
    assert pick_closest_bucket(1.0, buckets) == (1024, 1024)
    assert pick_closest_bucket(16 / 9, buckets) == (1280, 720)


def test_pick_closest_bucket_log_distance_is_symmetric():
    """A source aspect equally far above and below 1.0 in log space
    should not bias toward one direction. With buckets at 4:3 and 3:4,
    aspect=1.0 should be ambiguous; the implementation picks whichever
    comes first under min() — that's fine, but log distance must be
    equal for both."""
    buckets = [(4, 3), (3, 4)]
    log_4_3 = abs(math.log(4 / 3) - math.log(1.0))
    log_3_4 = abs(math.log(3 / 4) - math.log(1.0))
    assert math.isclose(log_4_3, log_3_4)


def test_pick_closest_bucket_picks_aspect_not_pixels():
    """Pixel count should NOT matter — bucket selection is purely
    aspect-based. A 4-megapixel 1:1 bucket should still win over a
    tiny 16:9 bucket when source aspect is 1.0."""
    buckets = [(64, 36), (2048, 2048)]  # 16:9 tiny vs 1:1 huge
    assert pick_closest_bucket(1.0, buckets) == (2048, 2048)


def test_pick_closest_bucket_rejects_empty_list():
    with pytest.raises(ValueError):
        pick_closest_bucket(1.0, [])


# ----- compute_resolution end-to-end per model -----

@pytest.mark.parametrize("model,quality", [
    (m, q) for m in TARGET_MODELS_FOR_RES for q in QUALITY_TIERS
])
def test_compute_resolution_obeys_divisor(model, quality):
    """Every (model, tier) combination must produce dims divisible by
    that model's stated divisor. Catches typos in bucket lists."""
    w, h = compute_resolution(1920, 1080, model, quality)
    divisor = MODEL_SPECS[model].divisor
    assert w % divisor == 0, f"{model}/{quality}: {w} not divisible by {divisor}"
    assert h % divisor == 0, f"{model}/{quality}: {h} not divisible by {divisor}"


@pytest.mark.parametrize("model,quality", [
    (m, q) for m in TARGET_MODELS_FOR_RES for q in QUALITY_TIERS
])
def test_compute_resolution_preserves_orientation(model, quality):
    """Landscape source -> landscape result (or square if model only
    offers a square bucket at that tier). Same for portrait."""
    w_land, h_land = compute_resolution(1920, 1080, model, quality)
    assert w_land >= h_land, f"{model}/{quality} flipped landscape"
    w_port, h_port = compute_resolution(1080, 1920, model, quality)
    assert h_port >= w_port, f"{model}/{quality} flipped portrait"


# Per-model spot checks against the plan's worked examples.

def test_compute_resolution_sdxl_balanced_16_9():
    """Plan spot-check: SDXL Balanced 1920x1080 → 1344x768 (1.03 MP)."""
    assert compute_resolution(1920, 1080, "sd", "Balanced") == (1344, 768)


def test_compute_resolution_pony_matches_sdxl():
    """Pony shares SDXL spec — should produce identical output."""
    assert (
        compute_resolution(1920, 1080, "pony", "Balanced")
        == compute_resolution(1920, 1080, "sd", "Balanced")
    )


def test_compute_resolution_qwen_balanced_picks_official_bucket():
    """Plan spot-check: Qwen Balanced 1920x1080 → 1664x928 (the official
    16:9 bucket). Must not be a computed off-bucket size."""
    assert compute_resolution(1920, 1080, "qwen", "Balanced") == (1664, 928)


def test_compute_resolution_qwen_other_tiers_are_computed():
    """Qwen only has buckets for Balanced. Fast/High/Ultra should fall
    through to compute_from_aspect with divisor=32."""
    for tier in ["Fast", "High", "Ultra"]:
        w, h = compute_resolution(1920, 1080, "qwen", tier)
        assert w % 32 == 0 and h % 32 == 0
        # And the result should NOT be an official bucket
        assert (w, h) not in [
            (1328, 1328), (1664, 928), (928, 1664),
            (1472, 1104), (1104, 1472),
            (1584, 1056), (1056, 1584),
        ]


def test_compute_resolution_zimage_balanced_picks_1024_grid_bucket():
    """Z-Image Balanced for 16:9 should pick (1280, 720) from the
    1024-grid bucket list — that bucket is exactly 16:9."""
    assert compute_resolution(1920, 1080, "zimage", "Balanced") == (1280, 720)


def test_compute_resolution_zimage_high_picks_1280_grid_bucket():
    """Z-Image High for 16:9 should pick the closest aspect from the
    1280-grid list — (1600, 896) at 1.786 aspect."""
    assert compute_resolution(1920, 1080, "zimage", "High") == (1600, 896)


def test_compute_resolution_zimage_fast_no_bucket_falls_through():
    """Z-Image Fast has no bucket list → computed at divisor=16."""
    w, h = compute_resolution(1920, 1080, "zimage", "Fast")
    assert w % 16 == 0 and h % 16 == 0
    # Roughly ~512² in pixels; shouldn't hit 1024-grid bucket sizes.
    assert w < 800


def test_compute_resolution_flux2_ultra_square():
    """Plan spot-check: Flux2 Ultra 1024x1024 → 2048x2048 (4 MP)."""
    assert compute_resolution(1024, 1024, "flux2", "Ultra") == (2048, 2048)


def test_compute_resolution_flux1_ultra_caps_at_1440():
    """Flux1 Dev blurs above ~1 MP; Ultra target is 1440² not 2048²."""
    w, h = compute_resolution(1024, 1024, "flux1", "Ultra")
    assert (w, h) == (1408, 1408)  # 1440 rounded to nearest 64 = 1408 (round(22.5)=22)


def test_compute_resolution_any_fast_768x512():
    """Plan spot-check: any+Fast 768x512 source (3:2 aspect) ≈ ~0.26 MP
    at multiples of 16. Computed: h=sqrt(262144/1.5)≈418, w≈627 →
    snap16: 416, 624."""
    assert compute_resolution(768, 512, "any", "Fast") == (624, 416)


def test_compute_resolution_unknown_model_falls_back_to_any():
    """Unknown model strings should not raise — they use the 'any' spec
    (Flux-style)."""
    fallback = compute_resolution(1920, 1080, "garbage-model", "Balanced")
    expected = compute_resolution(1920, 1080, "any", "Balanced")
    assert fallback == expected


def test_compute_resolution_unknown_quality_falls_back_to_balanced():
    """Unknown quality strings should not raise — they coerce to
    'Balanced'."""
    fallback = compute_resolution(1920, 1080, "sd", "MaxOverdrive")
    expected = compute_resolution(1920, 1080, "sd", "Balanced")
    assert fallback == expected


def test_compute_resolution_rejects_nonpositive_source():
    with pytest.raises(ValueError):
        compute_resolution(0, 1024, "sd", "Balanced")
    with pytest.raises(ValueError):
        compute_resolution(1024, -1, "sd", "Balanced")


# ----- pixel-budget reasonableness sanity check -----

def test_balanced_tier_lands_near_one_megapixel_for_most_models():
    """Sanity check: most models' Balanced tier should produce roughly
    a megapixel for a square source. Qwen is the exception at ~1.76 MP."""
    one_mp = 1024 * 1024
    for model in ["sd", "pony", "flux1", "flux2", "chroma", "zimage", "any"]:
        w, h = compute_resolution(1024, 1024, model, "Balanced")
        assert 0.75 * one_mp <= w * h <= 1.25 * one_mp, (
            f"{model} Balanced produced {w}x{h} = {w*h} px; expected ~1 MP"
        )
