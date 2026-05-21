"""Per-model resolution calculator for the load nodes.

Given a source image's (width, height), a target diffusion model, and a
quality tier, return a (target_w, target_h) that:
  1. Obeys the model's divisibility / bucket spec.
  2. Hits the pixel budget for the chosen tier.
  3. Stays as close as possible to the source aspect ratio.

Pure module — no HTTP, no torch — so the load nodes can share it and
unit tests don't need either dependency.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

QUALITY_TIERS: list[str] = ["Fast", "Balanced", "High", "Ultra"]

# Order chosen to match the metascan target_models endpoint plus "any".
TARGET_MODELS_FOR_RES: list[str] = [
    "sd", "pony", "flux1", "flux2", "zimage", "chroma", "qwen", "any",
]


@dataclass(frozen=True)
class ModelSpec:
    """Resolution rules for one diffusion model.

    ``tier_buckets`` is per-tier — a tier may have a preset bucket list
    (Qwen Balanced, Z-Image grids) or no buckets at all, in which case
    the calculator falls back to ``compute_from_aspect`` against the
    tier's pixel target and the model's divisor.
    """
    divisor: int
    tier_pixels: dict[str, int]
    tier_buckets: dict[str, list[tuple[int, int]]] = field(default_factory=dict)


# --- bucket tables --------------------------------------------------------

# Qwen-Image official aspect buckets at native ~1.76 MP. Off-bucket sizes
# produce noticeably worse results, so we only use these for Balanced and
# compute everything else.
_QWEN_BALANCED_BUCKETS: list[tuple[int, int]] = [
    (1328, 1328),  # 1:1
    (1664, 928),   # 16:9
    (928, 1664),   # 9:16
    (1472, 1104),  # 4:3
    (1104, 1472),  # 3:4
    (1584, 1056),  # 3:2
    (1056, 1584),  # 2:3
]

# Z-Image official training buckets per grid size, from Tongyi-MAI release.
_ZIMAGE_1024_BUCKETS: list[tuple[int, int]] = [
    (1024, 1024),
    (1152, 896), (896, 1152),
    (1152, 864), (864, 1152),
    (1248, 832), (832, 1248),
    (1280, 720), (720, 1280),
    (1344, 576), (576, 1344),
]
_ZIMAGE_1280_BUCKETS: list[tuple[int, int]] = [
    (1280, 1280),
    (1440, 1120), (1120, 1440),
    (1472, 1104), (1104, 1472),
    (1536, 1024), (1024, 1536),
    (1600, 896), (896, 1600),
    (1680, 720), (720, 1680),
]


# --- model spec table -----------------------------------------------------

# Tier pixel budgets are expressed as N*N where N is a representative
# side length; actual W and H are derived from the source aspect ratio.
_SDXL_TIERS = {"Fast": 512*512, "Balanced": 1024*1024, "High": 1280*1280, "Ultra": 1536*1536}
_FLUX_LIKE_TIERS = {"Fast": 512*512, "Balanced": 1024*1024, "High": 1280*1280, "Ultra": 1536*1536}

MODEL_SPECS: dict[str, ModelSpec] = {
    "sd":     ModelSpec(divisor=64, tier_pixels=_SDXL_TIERS),
    "pony":   ModelSpec(divisor=64, tier_pixels=_SDXL_TIERS),
    "flux1":  ModelSpec(divisor=64, tier_pixels={"Fast": 512*512, "Balanced": 1024*1024, "High": 1280*1280, "Ultra": 1440*1440}),
    "flux2":  ModelSpec(divisor=16, tier_pixels={"Fast": 512*512, "Balanced": 1024*1024, "High": 1664*1664, "Ultra": 2048*2048}),
    "chroma": ModelSpec(divisor=16, tier_pixels=_FLUX_LIKE_TIERS),
    "zimage": ModelSpec(
        divisor=16,
        tier_pixels={"Fast": 512*512, "Balanced": 1024*1024, "High": 1280*1280, "Ultra": 1536*1536},
        tier_buckets={
            "Balanced": _ZIMAGE_1024_BUCKETS,
            "High": _ZIMAGE_1280_BUCKETS,
        },
    ),
    "qwen": ModelSpec(
        divisor=32,
        tier_pixels={"Fast": 1024*1024, "Balanced": 1328*1328, "High": 1664*1664, "Ultra": 2048*2048},
        tier_buckets={"Balanced": _QWEN_BALANCED_BUCKETS},
    ),
    "any":    ModelSpec(divisor=16, tier_pixels=_FLUX_LIKE_TIERS),
}


# --- pure helpers ---------------------------------------------------------

def snap_to_multiple(n: float, m: int) -> int:
    """Round ``n`` to the nearest multiple of ``m``, with ``m`` as the
    minimum result. Never returns 0 — even very small inputs round up to
    ``m`` so callers don't get zero-sided latents."""
    if m <= 0:
        raise ValueError("multiple must be positive")
    snapped = int(round(n / m)) * m
    return max(snapped, m)


def compute_from_aspect(
    target_pixels: int, aspect: float, divisor: int
) -> tuple[int, int]:
    """Derive (W, H) from a target area and aspect ratio, snapping each
    side to the divisor. ``aspect`` is width/height. The snap can shift
    the actual pixel count by ±divisor² in either direction; that's
    acceptable — staying on the model's grid matters more than hitting
    the exact pixel target."""
    if aspect <= 0:
        raise ValueError("aspect must be positive")
    # h * (h * aspect) = target_pixels  →  h = sqrt(target_pixels / aspect)
    h = math.sqrt(target_pixels / aspect)
    w = h * aspect
    return snap_to_multiple(w, divisor), snap_to_multiple(h, divisor)


def pick_closest_bucket(
    source_aspect: float, buckets: list[tuple[int, int]]
) -> tuple[int, int]:
    """Among a fixed bucket list, return the (W, H) whose aspect is
    closest to ``source_aspect``. Distance is computed in log-aspect
    space so 16:9 and 9:16 are treated as equally-far from 1:1."""
    if not buckets:
        raise ValueError("buckets list must be non-empty")
    if source_aspect <= 0:
        raise ValueError("source_aspect must be positive")
    log_src = math.log(source_aspect)
    return min(buckets, key=lambda wh: abs(math.log(wh[0] / wh[1]) - log_src))


# --- public entry point ---------------------------------------------------

def compute_resolution(
    source_w: int, source_h: int, model: str, quality: str
) -> tuple[int, int]:
    """Recommend a (width, height) for image generation.

    Looks up the model's spec, picks a target pixel budget for the
    quality tier, and either snaps a computed (W,H) to the divisor or
    picks the best aspect match from a preset bucket list. Unknown
    models fall back to the ``any`` spec (Flux-style, ~1 MP, x16).
    Unknown quality tiers fall back to ``Balanced``."""
    if source_w <= 0 or source_h <= 0:
        raise ValueError("source dimensions must be positive")
    spec = MODEL_SPECS.get(model, MODEL_SPECS["any"])
    if quality not in spec.tier_pixels:
        quality = "Balanced"

    buckets = spec.tier_buckets.get(quality)
    if buckets:
        return pick_closest_bucket(source_w / source_h, buckets)
    return compute_from_aspect(spec.tier_pixels[quality], source_w / source_h, spec.divisor)
