"""Per-model resolution calculator — the canonical shared component.

Given a source image's (width, height), a target diffusion model, and a
quality tier, return a (target_w, target_h) that:
  1. Obeys the model's divisibility / bucket spec.
  2. Hits the pixel budget for the chosen tier.
  3. Stays as close as possible to the source aspect ratio.

**This is the single source of truth for "given a metascan source image,
what (W, H) should I generate at?"** Every node that exposes a metascan
image — directly (Load From Folder, Select Image) or indirectly via a
saved-prompt's source (Load Prompt, Select Prompt) — calls
:func:`compute_resolution` through the same ``target_model`` +
``quality`` widgets. Future video-model rules (frame-aware buckets,
duration / fps tiers) belong here too: extend ``MODEL_SPECS`` and the
helper logic rather than spinning up parallel implementations on a
per-node basis.

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

# I2V video models. Surfaced only by Select Image (the I2V entry point);
# the prompt-oriented nodes don't expose them because metascan's saved
# prompts are scoped to image models. Tiers map to spatial buckets here
# — temporal (frame count / fps) is left for the sampler to decide.
I2V_MODELS_FOR_RES: list[str] = ["wan", "ltx2"]


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

# Wan 2.1 / Wan 2.2 I2V buckets. Both share spatial constraints: VAE
# spatial compression 8 + 2x2 patching → multiples of 16. The model was
# trained on 480p and 720p only (16:9 / 9:16 pairs), so Fast and
# Balanced anchor on those native pairs and add square / 4:3 buckets
# for source aspects the picker might throw at it. High and Ultra
# extrapolate past training resolution — quality may degrade but the
# math still produces grid-aligned dims.
_WAN_FAST_BUCKETS: list[tuple[int, int]] = [
    (832, 480),   # 16:9 (native 480p)
    (480, 832),   # 9:16 (native 480p)
    (624, 624),   # 1:1
    (720, 544),   # 4:3
    (544, 720),   # 3:4
    (768, 512),   # 3:2
    (512, 768),   # 2:3
]
_WAN_BALANCED_BUCKETS: list[tuple[int, int]] = [
    (1280, 720),  # 16:9 (native 720p)
    (720, 1280),  # 9:16 (native 720p)
    (960, 960),   # 1:1
    (1104, 832),  # 4:3
    (832, 1104),  # 3:4
    (1024, 768),  # 4:3 alt
    (768, 1024),
]
_WAN_HIGH_BUCKETS: list[tuple[int, int]] = [
    (1536, 864),  # 16:9
    (864, 1536),  # 9:16
    (1152, 1152), # 1:1
    (1328, 992),  # 4:3
    (992, 1328),
]
_WAN_ULTRA_BUCKETS: list[tuple[int, int]] = [
    (1920, 1088), # ~16:9 (close to 1080p, divisible by 16)
    (1088, 1920),
    (1280, 1280), # 1:1
    (1472, 1104), # 4:3
    (1104, 1472),
]

# LTX-Video / LTX2 buckets. The Lightricks VAE compresses 32x spatially
# so all dims must be multiples of 32. Native LTX-Video training res is
# 768x512 (3:2); LTX2 extends the practical ceiling toward 1080p. Each
# tier covers the common aspects (1:1, 16:9, 9:16, 4:3, 3:4).
_LTX2_FAST_BUCKETS: list[tuple[int, int]] = [
    (768, 512),   # 3:2 (native)
    (512, 768),   # 2:3
    (640, 640),   # 1:1
    (704, 384),   # ~16:9
    (384, 704),
    (704, 544),   # ~4:3
    (544, 704),
]
_LTX2_BALANCED_BUCKETS: list[tuple[int, int]] = [
    (1024, 576),  # 16:9
    (576, 1024),  # 9:16
    (768, 768),   # 1:1
    (896, 672),   # 4:3
    (672, 896),
    (1024, 704),  # 3:2 ish
    (704, 1024),
]
_LTX2_HIGH_BUCKETS: list[tuple[int, int]] = [
    (1280, 704),  # ~16:9
    (704, 1280),
    (960, 960),   # 1:1
    (1152, 864),  # 4:3
    (864, 1152),
]
_LTX2_ULTRA_BUCKETS: list[tuple[int, int]] = [
    (1920, 1088), # ~16:9 (close to 1080p)
    (1088, 1920),
    (1280, 1280), # 1:1
    (1472, 1120), # ~4:3
    (1120, 1472),
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
    # I2V video models — bucket-only across all four tiers because video
    # models have strict aspect/resolution training sets and off-bucket
    # sizes degrade more visibly than for image models.
    "wan": ModelSpec(
        divisor=16,
        tier_pixels={
            "Fast": 832*480, "Balanced": 1280*720,
            "High": 1536*864, "Ultra": 1920*1088,
        },
        tier_buckets={
            "Fast": _WAN_FAST_BUCKETS,
            "Balanced": _WAN_BALANCED_BUCKETS,
            "High": _WAN_HIGH_BUCKETS,
            "Ultra": _WAN_ULTRA_BUCKETS,
        },
    ),
    "ltx2": ModelSpec(
        divisor=32,
        tier_pixels={
            "Fast": 768*512, "Balanced": 1024*576,
            "High": 1280*704, "Ultra": 1920*1088,
        },
        tier_buckets={
            "Fast": _LTX2_FAST_BUCKETS,
            "Balanced": _LTX2_BALANCED_BUCKETS,
            "High": _LTX2_HIGH_BUCKETS,
            "Ultra": _LTX2_ULTRA_BUCKETS,
        },
    ),
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
